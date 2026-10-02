from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from application.collect_mezunum import listing_data
from application.llm_reader import LlmReader
from application.safeguards import sitemap_shrunk
from infrastructure.collectors import pazarkibris
from infrastructure.db.repository import Repository

MAX_PAGES = 2        # liste en yeniden eskiye sıralı; her turda ilk 2 sayfa (32 ilan = 2 istek)
MAX_AGE_DAYS = 60    # bundan eski ilan alınmaz (eski, güncelliğini yitirmiş fiyatlar emsali bozmasın)


@dataclass
class PazarStats:
    seen: int = 0
    new: int = 0
    no_price: int = 0
    not_car: int = 0
    stale: int = 0
    failed: int = 0
    llm_read: int = 0


def collect_pazarkibris(repo: Repository, source: dict, reader: LlmReader | None = None, now: datetime | None = None) -> PazarStats:
    """Liste sayfaları ilan verisini içerdiği için ayrı ilan sayfası çekilmez. Fiyatsız / araç olmayan / eski ilan saklanmaz
    ama tekrar işlenmesin diye pasif işaretle kaydedilir."""
    stats = PazarStats()
    now = now or datetime.now(timezone.utc)
    known = repo.known_item_ids(source["id"])
    total = 0
    with pazarkibris.new_client() as client:
        for page in range(1, MAX_PAGES + 1):
            try:
                r = client.get(pazarkibris.LIST_URL, params={"locale": "tr", **({"page": page} if page > 1 else {})})
            except httpx.HTTPError:  # geçici ağ hatası
                stats.failed += 1
                continue
            items, total_page = pazarkibris.parse_list_page(r.text) if r.status_code == 200 else ([], 0)
            if not items:  # engellenme / şablon değişikliği: tur başarısız sayılır
                stats.failed += 1
                pazarkibris.polite_sleep()
                continue
            total = total or total_page
            for it in items:
                stats.seen += 1
                if it["item_id"] in known:
                    continue
                _store(repo, source, it, stats, reader, now)
                known.add(it["item_id"])
            pazarkibris.polite_sleep()
    repo.mark_checked(source["id"], cursor=None, last_post_at=None, listings_7d=repo.count_recent(source["id"]))
    if total and sitemap_shrunk(repo, source["id"], total):
        raise RuntimeError(f"{source['name']}: ilan sayısı şüpheli biçimde küçüldü ({total} ilan)")
    if stats.failed >= MAX_PAGES:  # hiçbir liste sayfası okunamadı: engellenme ya da şablon değişikliği
        raise RuntimeError(f"{source['name']}: liste sayfaları okunamadı — site şablonu değişmiş ya da erişim engellenmiş olabilir")
    return stats


def _store(repo: Repository, source: dict, it: dict, stats: PazarStats, reader: LlmReader | None, now: datetime) -> None:
    marker = {"url": it["url"], "photo_urls": [], "is_active": False, "posted_at": it["posted_at"]}
    if it["posted_at"] and now - it["posted_at"] > timedelta(days=MAX_AGE_DAYS):
        stats.stale += 1
        repo.upsert_listing(source["id"], it["item_id"], {**marker, "urgency_signals": ["eski"]})
        return
    if not it["active"]:
        repo.upsert_listing(source["id"], it["item_id"], {**marker, "urgency_signals": ["kaldirildi"]})
        return
    if not it["price_amount"]:  # çoğu ilanda fiyat yok: fiyatsız ilan değerlendirilemez
        stats.no_price += 1
        repo.upsert_listing(source["id"], it["item_id"], {**marker, "urgency_signals": ["fiyatsiz"]})
        return
    data, by_llm = listing_data(it, reader)
    if data is None:
        stats.not_car += 1
        repo.upsert_listing(source["id"], it["item_id"], {**marker, "urgency_signals": ["arac_degil"]})
        return
    data.update(url=it["url"], photo_urls=it["photo_urls"], seller_type="bilinmiyor", data_as_of=it["posted_at"] or now)
    if repo.upsert_listing(source["id"], it["item_id"], data):
        stats.new += 1
        stats.llm_read += by_llm
