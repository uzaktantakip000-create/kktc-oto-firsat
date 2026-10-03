import time
from dataclasses import dataclass

from application.llm_reader import LlmReader, listing_fields
from domain.freetext_parser import parse_freetext
from infrastructure.collectors import mezunum
from infrastructure.db.repository import Repository
from infrastructure.fx.frankfurter import gbp_rate

MAX_PAGES = 2     # liste en yeniden eskiye sıralı; her turda ilk 2 sayfa (≈72 ilan)
MAX_NEW = 12      # tur başına en çok yeni ilan detayı (nazik hız: 3 sn aralık)
BUDGET_SECONDS = 150  # sayfa+detay okuma bu süreyi aşmasın (yavaş site turu/iş akışı sınırını tutmasın); kalanlar sonraki tura


@dataclass
class MezunumStats:
    seen: int = 0
    fetched: int = 0
    new: int = 0
    not_car: int = 0
    failed: int = 0
    llm_read: int = 0
    time_limited: bool = False  # süre bütçesi doldu: kalan ilanlar sonraki turda (okunamadı SAYILMAZ)


def listing_data(detail: dict, reader: LlmReader | None = None) -> tuple[dict | None, bool]:
    """(ilan alanları | None, yapay zekâ mı okudu). Fiyat JSON-LD'den kesin; marka/model/yıl/km serbest metinden."""
    text = (detail["title"] + "\n" + detail["description"]).strip()
    known = (detail["price_amount"], detail["currency"])
    p = parse_freetext(text, known_price=known)
    base = {"raw_text": text, "posted_at": detail["posted_at"], "location": detail["location"], "seller_phone": detail["seller_phone"],
            "photo_urls": [], "seller_type": "bireysel", "currency_guess": False}
    if p is not None:
        return base | {"brand": p.brand, "model": p.model, "year": p.year, "km": p.km, "fuel": p.fuel, "transmission": p.transmission,
                       "steering": p.steering, "price_raw": p.price_raw, "price_amount": p.price_amount, "currency": p.currency,
                       "price_gbp": round(p.price_amount * gbp_rate(p.currency), 2), "negotiable": p.negotiable,
                       "extraction_by": "parser_serbest"}, False
    if reader is not None:
        fields = listing_fields(reader.read(f"{text}\nFiyat: {detail['price_amount']:g} {detail['currency']}"))
        if fields and fields["price_amount"] == detail["price_amount"] and fields["currency"] == detail["currency"]:
            return base | fields, True  # fiyat sitenin kesin alanıyla aynı olmalı
    return None, False


def collect_mezunum(repo: Repository, source: dict, reader: LlmReader | None = None, clock=time.monotonic) -> MezunumStats:
    stats = MezunumStats()
    known = repo.known_item_ids(source["id"])
    deadline = clock() + BUDGET_SECONDS
    with mezunum.new_client() as client:
        entries = []
        for page in range(1, MAX_PAGES + 1):
            if page > 1 and clock() > deadline:
                stats.time_limited = True
                break
            r = client.get(mezunum.LIST_URL, params={"page": page} if page > 1 else None)
            r.raise_for_status()
            entries += mezunum.parse_list(r.text)
            mezunum.polite_sleep()
        stats.seen = len(entries)
        for entry in [e for e in entries if e.slug not in known][:MAX_NEW]:
            if clock() > deadline:
                stats.time_limited = True
                break
            stats.fetched += 1
            r = client.get(entry.url)
            mezunum.polite_sleep()
            detail = mezunum.parse_detail(r.text) if r.status_code == 200 else None
            if detail is None:
                stats.failed += 1
                continue
            data, by_llm = listing_data(detail, reader)
            if data is None:
                stats.not_car += 1
                # araç olmayan/okunamayan ilan saklanmaz ama tekrar çekilmesin diye işaretlenir
                repo.upsert_listing(source["id"], entry.slug, {"url": entry.url, "photo_urls": [], "is_active": False,
                                                                "urgency_signals": ["arac_degil"], "posted_at": detail["posted_at"]})
                continue
            data["url"] = entry.url
            if repo.upsert_listing(source["id"], entry.slug, data):
                stats.new += 1
                stats.llm_read += by_llm
    repo.mark_checked(source["id"], cursor=None, last_post_at=None, listings_7d=repo.count_recent(source["id"]))
    from application.safeguards import check_read_rate
    check_read_rate(source["name"], stats.fetched, stats.failed)
    return stats
