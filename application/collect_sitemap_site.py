"""Site haritalı küçük ilan siteleri (kibriscars, sahibindenarabakibris) için ortak toplayıcı: kibrisarabaal ile aynı kalıp.
Site haritasındaki yeni adresler -> ilan sayfası -> ilan satırı. Korumalar: harita küçülürse toplu pasifleştirme yok, okuma oranı çökerse uyarı."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from application.safeguards import check_read_rate, sitemap_shrunk
from infrastructure.db.repository import Repository
from infrastructure.fx.frankfurter import gbp_rate

MAX_AGE_DAYS = 60       # site haritası son değişiklik tarihi bundan eskiyse ilan hiç çekilmez (bu siteler durgun: eski fiyatlar emsali bozar)
MIN_SITEMAP_FOR_DEACTIVATE = 100  # harita bu kadar ilan içeriyorsa kaybolanlar pasifleştirilir


@dataclass
class SiteStats:
    in_sitemap: int = 0
    fetched: int = 0
    new: int = 0
    failed: int = 0
    deactivated: int = 0
    skipped_old: int = 0


def collect_sitemap_site(repo: Repository, source: dict, site, max_new: int = 10, now: datetime | None = None,
                         max_age_days: int | None = MAX_AGE_DAYS) -> SiteStats:
    """`site`: fetch_sitemap / fetch_detail / new_client / polite_sleep sağlayan toplayıcı modülü (infrastructure.collectors.*)."""
    stats = SiteStats()
    now = now or datetime.now(timezone.utc)
    with site.new_client() as client:
        entries = site.fetch_sitemap(client)
        stats.in_sitemap = len(entries)
        known = repo.known_item_ids(source["id"])
        fresh = lambda e: max_age_days is None or e.lastmod is None or now - e.lastmod <= timedelta(days=max_age_days)
        todo = sorted((e for e in entries if e.item_id not in known and fresh(e)), key=lambda e: e.lastmod or now, reverse=True)
        stats.skipped_old = sum(1 for e in entries if e.item_id not in known and not fresh(e))
        for entry in todo[:max_new]:
            stats.fetched += 1
            try:
                data = site.fetch_detail(client, entry)
            except httpx.HTTPError:  # zaman aşımı/ağ hatası: geçici, bu ilan sonraki turda yeniden denenir
                data = None
            site.polite_sleep()
            if not data:
                stats.failed += 1
                continue
            if data.get("is_active") is False:  # kaldırılmış: pasif kayıt (her turda yeniden denenmesin)
                repo.upsert_listing(source["id"], entry.item_id, {**data, "url": entry.url, "photo_urls": [], "extraction_by": None})
                stats.deactivated += 1
                continue
            posted = data.get("posted_at")
            if max_age_days is not None and posted and now - posted > timedelta(days=max_age_days):
                repo.upsert_listing(source["id"], entry.item_id, {"url": entry.url, "photo_urls": [], "is_active": False,
                                                                   "urgency_signals": ["eski"], "posted_at": posted})
                stats.skipped_old += 1
                continue
            data["price_gbp"] = round(data["price_amount"] * gbp_rate(data["currency"]), 2) if data.get("price_amount") and data.get("currency") else None
            data["url"] = entry.url
            data["data_as_of"] = posted or entry.lastmod
            data.setdefault("extraction_by", "parser")
            data["photo_urls"] = []
            if repo.upsert_listing(source["id"], entry.item_id, data):
                stats.new += 1
        shrunk = sitemap_shrunk(repo, source["id"], len(entries))
        if len(entries) > MIN_SITEMAP_FOR_DEACTIVATE and not shrunk:  # kaybolan (kaldırılan) ilanları pasifleştir
            stats.deactivated += repo.deactivate_missing(source["id"], {e.item_id for e in entries})
    repo.mark_checked(source["id"], cursor=None, last_post_at=None, listings_7d=repo.count_recent(source["id"]))
    if shrunk:
        raise RuntimeError(f"{source['name']}: site haritası şüpheli biçimde küçüldü ({len(entries)} ilan), pasifleştirme yapılmadı")
    check_read_rate(source["name"], stats.fetched, stats.failed)
    return stats
