import time
from dataclasses import dataclass, field

import httpx

from application.safeguards import check_read_rate, removed_message, removed_rate_suspect, sitemap_shrunk
from domain.lifecycle import inactive_reason
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
    refresh_failed: int = 0  # yenilemede okunamayan (HTTP != 200 ya da şablon değişti): okuma oranı korumasına sayılır
    time_limited: bool = False  # süre bütçesi dolduğu için kalan ilanlar sonraki tura bırakıldı (okunamadı SAYILMAZ)
    suspect: list[str] = field(default_factory=list)  # toplu "satıldı/arşiv" şüphesi: hiçbiri pasifleştirilmedi, tur sonunda hata


# Site yavaşlarsa tur 13 dk bütçesini / iş akışı 20 dk sınırını aşmasın (en kötü durum eskiden ~26 dk: 50 istek x 30 sn zaman aşımı)
NEW_SECONDS = 150
REFRESH_SECONDS = 90
MIN_SITEMAP = 200  # bundan küçük site haritasında kaybolanlar pasifleştirilmez (yarım yanıt/engel; %70 küçülme koruması ayrıca)


def _gbp(data: dict) -> float | None:
    return round(data["price_amount"] * gbp_rate(data["currency"]), 2) if data.get("price_amount") else None


def refresh_active(repo: Repository, source: dict, client, stats: KktcarStats, limit: int = 25, hours: int = 6,
                   max_seconds: float = REFRESH_SECONDS, clock=time.monotonic) -> None:
    """Aktif ilanların en eski kontrol edilenlerini yeniden okur: fiyat düştü mü, satıldı/arşivlendi mi.
    Süre dolarsa kalanlar sırada (touch edilmeden) bekler, sonraki turda en eskiden devam edilir.
    Toplu koruma (KibrisArabaAl ile aynı kural): okunanların yarısı (en az 5 okunduysa) aynı anda "satıldı/arşiv" çıkarsa şablon/engel
    şüphesi: hiçbiri pasifleştirilmez ("satıldı" işareti emsale girer), tur sonunda hata verilir, sonraki turda yeniden denenir."""
    deadline = clock() + max_seconds
    read_ok, closing = 0, []  # closing: kapanış adayları; toplu karar tur sonunda
    for row in repo.stale_active(source["id"], hours, limit):
        if clock() > deadline:
            stats.time_limited = True
            break
        try:
            data = kktcar.fetch_detail(client, kktcar.SitemapEntry(row["url"], row["source_item_id"], None))
        except httpx.HTTPError:  # zaman aşımı/ağ hatası: geçici, tek ilan yüzünden tur durmasın
            data = None
        kktcar.polite_sleep()
        if not data:
            repo.touch(row["id"])  # okunamadı: sırayı kaydır, sonra tekrar dene (sitemap'ten kaybolursa zaten pasifleşir)
            stats.refresh_failed += 1
            continue
        stats.refreshed += 1
        read_ok += 1
        if data.get("is_active") is False:  # satıldı/arşiv: yazmadan önce toplu kontrol
            closing.append((row, data))
            continue
        data["price_gbp"] = _gbp(data)
        change = repo.apply_refresh(row["id"], row, data)
        stats.price_changes += change == "fiyat"
    if removed_rate_suspect(read_ok, len(closing)):
        stats.suspect.append(removed_message(f"{source['name']} (yenileme)", read_ok, len(closing)))
    else:
        for row, data in closing:
            data["price_gbp"] = _gbp(data)
            stats.went_inactive += repo.apply_refresh(row["id"], row, data) == "pasif"


def collect_kktcar(repo: Repository, source: dict, max_new: int = 25, clock=time.monotonic) -> KktcarStats:
    stats = KktcarStats()
    with kktcar.new_client() as client:
        entries = kktcar.fetch_sitemap(client)
        stats.in_sitemap = len(entries)
        known = repo.known_item_ids(source["id"])
        todo = [e for e in entries if e.slug not in known]
        # En yeni değişenler önce; ilk çalıştırmada geçmiş her turda max_new kadar tamamlanır
        todo.sort(key=lambda e: e.lastmod.timestamp() if e.lastmod else 0, reverse=True)
        new_deadline = clock() + NEW_SECONDS
        for entry in todo[:max_new]:
            if clock() > new_deadline:
                stats.time_limited = True  # kalan ilanlar bilinmiyor sayılır, sonraki turda denenir (okunamadı sayılmaz)
                break
            stats.fetched += 1
            try:
                data = kktcar.fetch_detail(client, entry)
            except httpx.HTTPError:  # geçici hata: bu ilan sonraki turda yeniden denenir (okuma oranı korumasına sayılır)
                data = None
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
            if data.get("is_active") is False:  # ilk okumada zaten satılmış/arşivde: neden kaydedilir (pasifleşme AN'ı bilinmez: inactive_at boş kalır)
                data["inactive_reason"] = inactive_reason(data.get("urgency_signals"))
            if repo.upsert_listing(source["id"], entry.slug, data):
                stats.new += 1
        # Güvenlik: sitemap makul büyüklükteyse kaybolanları pasifleştir. 08.10.2026: site haritası artık yalnız yayındaki ilanlar
        # (~500; eskisi satılmışlarla ~3.400): küçülme ölçüsü yeni anahtarda yeniden başlar, alt sınır 500 -> 200.
        shrunk = sitemap_shrunk(repo, f"{source['id']}:yayinda", len(entries))
        if len(entries) > MIN_SITEMAP and not shrunk:
            stats.deactivated = repo.deactivate_missing(source["id"], {e.slug for e in entries})
        refresh_active(repo, source, client, stats, clock=clock)
    repo.mark_checked(source["id"], cursor=None, last_post_at=None, listings_7d=repo.count_recent(source["id"]))
    if shrunk:
        raise RuntimeError(f"{source['name']}: site haritası şüpheli biçimde küçüldü ({len(entries)} ilan), pasifleştirme yapılmadı")
    if stats.suspect:
        raise RuntimeError("; ".join(stats.suspect))
    check_read_rate(source["name"], stats.fetched, stats.failed)
    check_read_rate(f"{source['name']} (yenileme)", stats.refreshed + stats.refresh_failed, stats.refresh_failed)
    return stats
