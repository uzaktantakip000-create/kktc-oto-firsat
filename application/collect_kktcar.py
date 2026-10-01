from dataclasses import dataclass


from infrastructure.collectors import kktcar
from infrastructure.db.repository import Repository
from infrastructure.fx.frankfurter import gbp_rate


@dataclass
class KktcarStats:
    in_sitemap: int = 0
    fetched: int = 0
    new: int = 0
    failed: int = 0
    deactivated: int = 0
    refreshed: int = 0
    price_changes: int = 0
    went_inactive: int = 0


def _gbp(data: dict) -> float | None:
    return round(data["price_amount"] * gbp_rate(data["currency"]), 2) if data.get("price_amount") else None


def refresh_active(repo: Repository, source: dict, client, stats: KktcarStats, limit: int = 25, hours: int = 6) -> None:
    """Aktif ilanların en eski kontrol edilenlerini yeniden okur: fiyat düştü mü, satıldı/arşivlendi mi."""
    for row in repo.stale_active(source["id"], hours, limit):
        data = kktcar.fetch_detail(client, kktcar.SitemapEntry(row["url"], row["source_item_id"], None))
        kktcar.polite_sleep()
        if not data:
            repo.touch(row["id"])  # okunamadı: sırayı kaydır, sonra tekrar dene (sitemap'ten kaybolursa zaten pasifleşir)
            continue
        data["price_gbp"] = _gbp(data)
        stats.refreshed += 1
        change = repo.apply_refresh(row["id"], row, data)
        stats.price_changes += change == "fiyat"
        stats.went_inactive += change == "pasif"


def collect_kktcar(repo: Repository, source: dict, max_new: int = 25) -> KktcarStats:
    stats = KktcarStats()
    with kktcar.new_client() as client:
        entries = kktcar.fetch_sitemap(client)
        stats.in_sitemap = len(entries)
        known = repo.known_item_ids(source["id"])
        todo = [e for e in entries if e.slug not in known]
        # En yeni değişenler önce; ilk çalıştırmada geçmiş her turda max_new kadar tamamlanır
        todo.sort(key=lambda e: e.lastmod.timestamp() if e.lastmod else 0, reverse=True)
        for entry in todo[:max_new]:
            stats.fetched += 1
            data = kktcar.fetch_detail(client, entry)
            kktcar.polite_sleep()
            if not data:
                stats.failed += 1
                continue
            data.pop("swap", None)
            data["price_gbp"] = _gbp(data)
            data["url"] = entry.url
            data["data_as_of"] = data.get("posted_at") or entry.lastmod  # emsal yaşı için (arşiv sayfalarında yayın tarihi yok)
            data["seller_type"] = "bilinmiyor"
            data["extraction_by"] = "parser"
            data["photo_urls"] = []
            if repo.upsert_listing(source["id"], entry.slug, data):
                stats.new += 1
        # Güvenlik: sitemap makul büyüklükteyse kaybolanları pasifleştir
        if len(entries) > 500:
            stats.deactivated = repo.deactivate_missing(source["id"], {e.slug for e in entries})
        refresh_active(repo, source, client, stats)
    repo.mark_checked(source["id"], cursor=None, last_post_at=None, listings_7d=repo.count_recent(source["id"]))
    return stats
