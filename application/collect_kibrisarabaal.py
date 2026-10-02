import time
from dataclasses import dataclass

import httpx

from application.safeguards import check_read_rate, sitemap_shrunk
from infrastructure.collectors import kibrisarabaal
from infrastructure.db.repository import Repository
from infrastructure.fx.frankfurter import gbp_rate


@dataclass
class KaaStats:
    in_sitemap: int = 0
    fetched: int = 0
    new: int = 0
    failed: int = 0
    deactivated: int = 0
    refreshed: int = 0
    refresh_failed: int = 0
    price_changes: int = 0
    went_inactive: int = 0


REFRESH_SECONDS = 120  # yenileme bu süreyi aşmasın (5 sn aralıklı tarama; tick toplamı < 13 dk kalsın)


def _gbp(data: dict) -> float | None:
    return round(data["price_amount"] * gbp_rate(data["currency"]), 2) if data.get("price_amount") and data.get("currency") else None


def refresh_active(repo: Repository, source: dict, client, stats: KaaStats, limit: int = 25, hours: int = 6,
                   max_seconds: float = REFRESH_SECONDS) -> None:
    """Aktif ilanların en eski kontrol edilenlerini yeniden okur: fiyat düştü mü, satıldı/kaldırıldı mı (listing_history'ye yazılır)."""
    deadline = time.monotonic() + max_seconds
    for row in repo.stale_active(source["id"], hours, limit):
        if time.monotonic() > deadline:
            break
        try:
            data = kibrisarabaal.fetch_detail(client, kibrisarabaal.Entry(row["url"], row["source_item_id"], None))
        except httpx.HTTPError:  # geçici hata: sırayı kaydır, sonra tekrar dene
            data = None
        kibrisarabaal.polite_sleep()
        stats.refreshed += 1
        if not data:  # okunamadı (geçici hata/şablon): kaldırıldı SAYILMAZ, sırayı kaydır
            repo.touch(row["id"])
            stats.refresh_failed += 1
            continue
        data["price_gbp"] = _gbp(data)
        change = repo.apply_refresh(row["id"], row, data)
        stats.price_changes += change == "fiyat"
        stats.went_inactive += change == "pasif"


def collect_kibrisarabaal(repo: Repository, source: dict, max_new: int = 10) -> KaaStats:
    """Her turda en yeni ilanlar (en çok max_new, 5 sn aralıkla: robots.txt Crawl-delay). Geçmiş doldurma yerelde: admin_cli kaa."""
    stats = KaaStats()
    with kibrisarabaal.new_client() as client:
        entries = kibrisarabaal.fetch_sitemap(client)
        stats.in_sitemap = len(entries)
        known = repo.known_item_ids(source["id"])
        todo = sorted((e for e in entries if e.item_id not in known), key=lambda e: int(e.item_id), reverse=True)
        for entry in todo[:max_new]:
            stats.fetched += 1
            try:
                data = kibrisarabaal.fetch_detail(client, entry)
            except httpx.HTTPError:  # zaman aşımı/ağ hatası: geçici, bu ilan sonraki turda yeniden denenir
                data = None
            kibrisarabaal.polite_sleep()
            if not data:  # geçici hata: sonraki turda yeniden denenir
                stats.failed += 1
                continue
            if data.get("is_active") is False:  # kaldırılmış/satılmış: pasif kayıt (her turda yeniden denenmesin)
                repo.upsert_listing(source["id"], entry.item_id, {**data, "url": entry.url, "photo_urls": [], "extraction_by": None})
                stats.deactivated += 1
                continue
            data["price_gbp"] = _gbp(data)
            data["url"] = entry.url
            data["data_as_of"] = data.get("posted_at") or entry.lastmod
            data["extraction_by"] = "parser"
            data["photo_urls"] = []
            if repo.upsert_listing(source["id"], entry.item_id, data):
                stats.new += 1
        shrunk = sitemap_shrunk(repo, source["id"], len(entries))
        if len(entries) > 500 and not shrunk:  # sitemap makul büyüklükteyse kaybolan (kaldırılan) ilanları pasifleştir
            stats.deactivated += repo.deactivate_missing(source["id"], {e.item_id for e in entries})
        refresh_active(repo, source, client, stats)
    repo.mark_checked(source["id"], cursor=None, last_post_at=None, listings_7d=repo.count_recent(source["id"]))
    if shrunk:
        raise RuntimeError(f"{source['name']}: site haritası şüpheli biçimde küçüldü ({len(entries)} ilan), pasifleştirme yapılmadı")
    check_read_rate(source["name"], stats.fetched, stats.failed)
    check_read_rate(f"{source['name']} (yenileme)", stats.refreshed, stats.refresh_failed)
    return stats
