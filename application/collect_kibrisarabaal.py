import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx

from application.safeguards import check_read_rate, removed_message, removed_rate_suspect, sitemap_shrunk
from domain.lifecycle import inactive_reason
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
    suspect: list[str] = field(default_factory=list)  # toplu "satıldı/kaldırıldı" şüphesi: hiçbiri pasifleştirilmedi, tur sonunda hata


BLOCK_KEY = "kaa_blocked_until"  # bot_state: site haritası 403 (Cloudflare engeli, 10.10.2026) verince bu ana kadar (UTC ISO) siteye istek yok
BLOCK_PAUSE = timedelta(hours=2)  # engel sürerken günde 96 yerine 12 deneme; engel kalkınca en geç 2 saatte normale döner
REFRESH_SECONDS = 120  # yenileme bu süreyi aşmasın (5 sn aralıklı tarama; tick toplamı < 13 dk kalsın)


def _gbp(data: dict) -> float | None:
    return round(data["price_amount"] * gbp_rate(data["currency"]), 2) if data.get("price_amount") and data.get("currency") else None


def refresh_active(repo: Repository, source: dict, client, stats: KaaStats, limit: int = 25, hours: int = 6,
                   max_seconds: float = REFRESH_SECONDS) -> None:
    """Aktif ilanların en eski kontrol edilenlerini yeniden okur: fiyat düştü mü, satıldı/kaldırıldı mı (listing_history'ye yazılır)."""
    deadline = time.monotonic() + max_seconds
    read_ok, closing = 0, []  # closing: kapanış adayları (satıldı/kaldırıldı). Toplu karar tur sonunda: yarısı kapanıyorsa hiçbiri yazılmaz
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
        read_ok += 1
        if data.get("is_active") is False:  # OutOfStock (satıldı) ya da kaldırıldı: yazmadan önce toplu kontrol
            closing.append((row, data))
            continue
        data["price_gbp"] = _gbp(data)
        change = repo.apply_refresh(row["id"], row, data)
        stats.price_changes += change == "fiyat"
    if removed_rate_suspect(read_ok, len(closing)):
        stats.suspect.append(removed_message(f"{source['name']} (yenileme)", read_ok, len(closing)))
    else:
        for row, data in closing:
            stats.went_inactive += repo.apply_refresh(row["id"], row, data) == "pasif"


def _blocked_until(repo: Repository, now: datetime) -> datetime | None:
    """Engel beklemesi sürüyorsa bitiş anı; kayıt yok/bozuk/saat dilimsiz/geçmiş ya da çok ileri tarihli ise None (normal tur). Hata fırlatmaz."""
    try:
        raw = repo.get_state(BLOCK_KEY)
        until = datetime.fromisoformat(raw) if raw else None
    except Exception:
        return None
    if until is None or until.tzinfo is None:
        return None
    return until if now < until <= now + BLOCK_PAUSE + timedelta(minutes=5) else None


def collect_kibrisarabaal(repo: Repository, source: dict, max_new: int = 10, now: datetime | None = None) -> KaaStats:
    """Her turda en yeni ilanlar (en çok max_new, 5 sn aralıkla: robots.txt Crawl-delay). Geçmiş doldurma yerelde: admin_cli kaa.
    Site haritası 403 verirse (10.10.2026'dan beri Cloudflare botu engelliyor) BLOCK_PAUSE boyunca siteye hiç istek atılmaz; tur yine HATA
    sayılır (kaynak alarmı sürer, sahte "düzeldi" mesajı gitmez). Engel AŞILMAZ: tarayıcı kimliği taklidi yok, karar sahibin."""
    now = now or datetime.now(timezone.utc)
    until = _blocked_until(repo, now)
    if until is not None:
        raise RuntimeError(f"{source['name']}: site botu engelliyor (403); {until:%H:%M} UTC'ye kadar istek atılmıyor")
    stats = KaaStats()
    with kibrisarabaal.new_client() as client:
        try:
            entries = kibrisarabaal.fetch_sitemap(client)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                try:
                    repo.set_state(BLOCK_KEY, (now + BLOCK_PAUSE).isoformat())
                except Exception as err:  # kayıt yazılamazsa eski davranış: her tur denenir
                    print(f"KibrisArabaAl: engel beklemesi yazılamadı ({type(err).__name__})")
            raise
        stats.in_sitemap = len(entries)
        known = repo.known_item_ids(source["id"])
        todo = sorted((e for e in entries if e.item_id not in known), key=lambda e: int(e.item_id), reverse=True)
        closed_new = []  # yeni görülüp zaten kapalı çıkanlar: pasif kayıt yazılmadan önce toplu kontrol
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
            if data.get("is_active") is False:  # kaldırılmış/satılmış: pasif kayıt (her turda yeniden denenmesin), toplu kontrolden sonra
                closed_new.append((entry, data))
                continue
            data["price_gbp"] = _gbp(data)
            data["url"] = entry.url
            data["data_as_of"] = data.get("posted_at") or entry.lastmod
            data["extraction_by"] = "parser"
            data["photo_urls"] = []
            if repo.upsert_listing(source["id"], entry.item_id, data):
                stats.new += 1
        fetched_ok = stats.fetched - stats.failed
        if removed_rate_suspect(fetched_ok, len(closed_new)):
            stats.suspect.append(removed_message(source["name"], fetched_ok, len(closed_new)))  # yazılmaz: sonraki turda yeniden denenir
        else:
            for entry, data in closed_new:
                repo.upsert_listing(source["id"], entry.item_id, {**data, "url": entry.url, "photo_urls": [], "extraction_by": None,
                                                                  "inactive_reason": inactive_reason(data.get("urgency_signals"))})
                stats.deactivated += 1
        shrunk = sitemap_shrunk(repo, source["id"], len(entries))
        if len(entries) > 500 and not shrunk:  # sitemap makul büyüklükteyse kaybolan (kaldırılan) ilanları pasifleştir
            stats.deactivated += repo.deactivate_missing(source["id"], {e.item_id for e in entries})
        refresh_active(repo, source, client, stats)
    repo.mark_checked(source["id"], cursor=None, last_post_at=None, listings_7d=repo.count_recent(source["id"]))
    if shrunk:
        raise RuntimeError(f"{source['name']}: site haritası şüpheli biçimde küçüldü ({len(entries)} ilan), pasifleştirme yapılmadı")
    if stats.suspect:
        raise RuntimeError("; ".join(stats.suspect))
    check_read_rate(source["name"], stats.fetched, stats.failed)
    check_read_rate(f"{source['name']} (yenileme)", stats.refreshed, stats.refresh_failed)
    return stats
