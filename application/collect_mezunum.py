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
    unread: int = 0  # geçici nedenle okunamayanlar (işaretlenmedi, sonraki turda yeniden denenir)
    time_limited: bool = False  # süre bütçesi doldu: kalan ilanlar sonraki turda (okunamadı SAYILMAZ)


def read_listing(detail: dict, reader: LlmReader | None = None) -> tuple[dict | None, bool, str]:
    """(ilan alanları | None, yapay zekâ mı okudu, durum). Fiyat JSON-LD'den kesin; marka/model/yıl/km serbest metinden.
    durum: 'ok' | 'retry' (GEÇİCİ: yapay zekâ anahtarı yok/ağ-API hatası/günlük bütçe doldu: işaret konmaz, sonraki turda yeniden denenir)
    | 'arac_degil' (yapay zekâ okudu: araç değil/satılmış/kredi-peşinat) | 'okunamadi' (yapay zekâ okudu ama çıktı geçersiz ya da fiyat uyuşmadı)."""
    text = (detail["title"] + "\n" + detail["description"]).strip()
    known = (detail["price_amount"], detail["currency"])
    p = parse_freetext(text, known_price=known)
    base = {"raw_text": text, "posted_at": detail["posted_at"], "location": detail["location"], "seller_phone": detail["seller_phone"],
            "photo_urls": [], "seller_type": "bireysel", "currency_guess": False}
    if p is not None:
        return base | {"brand": p.brand, "model": p.model, "year": p.year, "km": p.km, "fuel": p.fuel, "transmission": p.transmission,
                       "steering": p.steering, "price_raw": p.price_raw, "price_amount": p.price_amount, "currency": p.currency,
                       "price_gbp": round(p.price_amount * gbp_rate(p.currency), 2), "negotiable": p.negotiable,
                       "extraction_by": "parser_serbest"}, False, "ok"
    if reader is None:
        return None, False, "retry"  # kuralla okunamadı, yapay zekâ yok: araç olmadığı kanıtlanmadı
    read = reader.read(f"{text}\nFiyat: {detail['price_amount']:g} {detail['currency']}")
    if read is None:  # yapay zekâ cevap veremedi: geçici hata ya da bütçe (kalıcı "araç değil" damgası YOK)
        return None, False, ("okunamadi" if reader.last_error == "çıktı geçersiz" else "retry")
    fields = listing_fields(read)
    if fields is None:
        return None, False, "arac_degil"
    if fields["price_amount"] == detail["price_amount"] and fields["currency"] == detail["currency"]:
        return base | fields, True, "ok"  # fiyat sitenin kesin alanıyla aynı olmalı
    return None, False, "okunamadi"


def listing_data(detail: dict, reader: LlmReader | None = None) -> tuple[dict | None, bool]:
    """(ilan alanları | None, yapay zekâ mı okudu). Ayrıntılı durum için read_listing."""
    data, by_llm, _ = read_listing(detail, reader)
    return data, by_llm


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
        repo.mark_alive(source["id"], [e.slug for e in entries if e.slug in known])  # listede görüldü: canlı
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
            data, by_llm, status = read_listing(detail, reader)
            if data is None:
                if status == "retry":
                    stats.unread += 1  # geçici (yapay zekâ yok/hata/bütçe): işaret konmaz, sonraki turda yeniden denenir
                    continue
                stats.not_car += 1
                # yapay zekâ okuyup 'araç değil' dediyse ('arac_degil') ya da okuyup çözemediyse ('okunamadi'): saklanmaz,
                # tekrar çekilmesin diye işaretlenir (iki durum AYRI işaretle: okunamayanlar sonradan gözden geçirilebilir)
                repo.upsert_listing(source["id"], entry.slug, {"url": entry.url, "photo_urls": [], "is_active": False,
                                                                "urgency_signals": [status], "posted_at": detail["posted_at"]})
                continue
            data["url"] = entry.url
            if repo.upsert_listing(source["id"], entry.slug, data):
                stats.new += 1
                stats.llm_read += by_llm
    repo.mark_checked(source["id"], cursor=None, last_post_at=None, listings_7d=repo.count_recent(source["id"]))
    from application.safeguards import check_read_rate
    check_read_rate(source["name"], stats.fetched, stats.failed)
    return stats
