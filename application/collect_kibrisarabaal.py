"""KibrisArabaAl toplayıcısı: site haritası + YENİ ilan sayfaları, her ilan bir kez (21.10.2026 izin meclisi: eskiden günde ~2.200 istek,
%90'ı aktif ilanların boşuna yeniden okunmasıydı; o yenileme kaldırıldı). Siteye her istek application/kaa_gate'in kapısından geçer:
izin (KAA_IZIN=1, yalnız VPS), dürüst kimlik, robots.txt, kota, aralık; 403/429/503'te tur HEMEN durur ve 24 saat istek atılmaz.
Satılan/kaldırılan ilan: site haritasından düşünce deactivate_missing pasifleştirir (harita makul büyüklükteyse ve küçülmediyse);
bildirime aday ilan gönderimden hemen önce bir kez daha okunur (application/liveness). Fiyat düşüşü artık yalnız o kontrolde görülür."""
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from application import kaa_gate
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
    requests: int = 0  # siteye giden istek (robots.txt + harita + ilanlar)
    skipped: str | None = None  # tur hiç çalışmadı ya da engelle kesildi: neden (kapı kapalı, engel, kota, robots). Hata da başarı da sayılmaz
    stopped: str | None = None  # ilan okumaları yarıda kaldı (kota/aralık/süre/ağ hatası): kalanlar sonraki turda
    suspect: list[str] = field(default_factory=list)  # toplu "satıldı/kaldırıldı" şüphesi: hiçbiri pasifleştirilmedi, tur sonunda hata


RUN_SECONDS = 150  # ilan okumaları bu süreyi aşmasın (10 sn aralıkla ~14 ilan; tick toplamı < 13 dk)
TRIED_KEY = "kaa_denenen"  # bot_state: okunamayan yeni ilanlar {ilan no: deneme}; MAX_TRIES'tan sonra bir daha istenmez
MAX_TRIES = 2  # her ilan bir kez okunur; okunamazsa (geçici hata) yalnız bir kez daha denenir
DEACTIVATE_MIN = 500  # harita bundan büyükse ve küçülmediyse haritadan düşen ilanlar pasifleştirilir


def _gbp(data: dict) -> float | None:
    return round(data["price_amount"] * gbp_rate(data["currency"]), 2) if data.get("price_amount") and data.get("currency") else None


def _load_tried(repo) -> dict[str, int]:
    try:
        raw = json.loads(repo.get_state(TRIED_KEY) or "{}")
        return {str(k): int(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
    except (TypeError, ValueError):
        return {}


def collect_kibrisarabaal(repo: Repository, source: dict, max_new: int = 10, now: datetime | None = None,
                          max_seconds: float | None = RUN_SECONDS, client=None, sleep=None, clock=None) -> KaaStats:
    """Kapı kapalıysa (izin yok, VPS değil, iletişim adresi yok, engel beklemesi/durdurma, robots.txt yasak) siteye HİÇ istek atılmaz:
    stats.skipped dolu döner (cron_collect bunu ne hata ne başarı sayar; /durum'da nötr satır). Açıksa: harita -> en yeni max_new yeni ilan.
    `client`/`sleep`/`clock` yalnız testler için (sahte istemci ve saat)."""
    now = now or datetime.now(timezone.utc)
    stats = KaaStats()
    reason = kaa_gate.idle_reason(repo, now)
    if reason is not None:
        kaa_gate.log_idle(repo, reason)
        stats.skipped = reason
        return stats
    deadline = time.monotonic() + max_seconds if max_seconds is not None else None
    tried = _load_tried(repo)
    web = None
    try:
        with kaa_gate.open_client(repo, client=client, sleep=sleep, clock=clock) as web:
            entries = kibrisarabaal.fetch_sitemap(web)
            kaa_gate.mark_reachable(repo)
            stats.in_sitemap = len(entries)
            known = repo.known_item_ids(source["id"])
            in_map = {e.item_id for e in entries}
            tried = {k: v for k, v in tried.items() if k in in_map and k not in known}  # kayda geçen/haritadan düşen unutulur
            todo = sorted((e for e in entries if e.item_id not in known and tried.get(e.item_id, 0) < MAX_TRIES),
                          key=lambda e: int(e.item_id), reverse=True)
            closed_new = []  # yeni görülüp zaten kapalı çıkanlar: pasif kayıt yazılmadan önce toplu kontrol
            for entry in todo[:max_new]:
                if deadline is not None and time.monotonic() > deadline:
                    stats.stopped = "süre doldu"
                    break
                try:
                    data = kibrisarabaal.fetch_detail(web, entry)
                except kaa_gate.Disallowed:  # robots.txt bu ilanı yasaklıyor: hiç istenmez
                    tried[entry.item_id] = MAX_TRIES
                    continue
                except kaa_gate.Deferred as e:  # kota/uzun aralık: kalanlar sonraki turda
                    stats.stopped = str(e)
                    break
                except httpx.HTTPError as e:  # zaman aşımı/ağ hatası: site zorlanıyor olabilir, tur nazikçe biter
                    stats.fetched += 1
                    stats.failed += 1
                    tried[entry.item_id] = tried.get(entry.item_id, 0) + 1
                    stats.stopped = f"ağ hatası ({type(e).__name__})"
                    break
                stats.fetched += 1
                if not data:  # okunamadı (5xx/şablon): bir kez daha denenir, sonra bırakılır
                    stats.failed += 1
                    tried[entry.item_id] = tried.get(entry.item_id, 0) + 1
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
    except kibrisarabaal.Blocked as e:  # 403/429/503: tur HEMEN durur, 24 saat istek yok, sahibe 30 günde bir mesaj
        until = kaa_gate.on_blocked(repo, e, now)
        stats.requests = web.requests if web is not None else 0
        stats.skipped = f"site engelledi (HTTP {e.status}), {until:%d.%m %H:%M} UTC'ye kadar istek yok"
        _save_tried(repo, tried)
        return stats
    except (kaa_gate.Deferred, kaa_gate.Disallowed) as e:  # harita isteği bile atılamadı (kota, robots.txt yasağı): bu tur yok
        stats.requests = web.requests if web is not None else 0
        stats.skipped = str(e) if isinstance(e, kaa_gate.Deferred) else "robots.txt site haritasını yasaklıyor"
        kaa_gate.log_idle(repo, stats.skipped)
        return stats
    stats.requests = web.requests
    _save_tried(repo, tried)
    fetched_ok = stats.fetched - stats.failed
    if removed_rate_suspect(fetched_ok, len(closed_new)):
        stats.suspect.append(removed_message(source["name"], fetched_ok, len(closed_new)))  # yazılmaz: sonraki turda yeniden denenir
    else:
        for entry, data in closed_new:
            repo.upsert_listing(source["id"], entry.item_id, {**data, "url": entry.url, "photo_urls": [], "extraction_by": None,
                                                              "inactive_reason": inactive_reason(data.get("urgency_signals"))})
            stats.deactivated += 1
    shrunk = sitemap_shrunk(repo, source["id"], len(entries))
    if len(entries) > DEACTIVATE_MIN and not shrunk:  # satılan/kaldırılan: haritadan düşen ilanlar pasifleşir (yeniden okuma yok)
        stats.deactivated += repo.deactivate_missing(source["id"], in_map)
    repo.mark_checked(source["id"], cursor=None, last_post_at=None, listings_7d=repo.count_recent(source["id"]))
    if shrunk:
        raise RuntimeError(f"{source['name']}: site haritası şüpheli biçimde küçüldü ({len(entries)} ilan), pasifleştirme yapılmadı")
    if stats.suspect:
        raise RuntimeError("; ".join(stats.suspect))
    check_read_rate(source["name"], stats.fetched, stats.failed)
    return stats


def _save_tried(repo, tried: dict[str, int]) -> None:
    try:
        if tried or repo.get_state(TRIED_KEY):
            repo.set_state(TRIED_KEY, json.dumps(tried, sort_keys=True))
    except Exception as e:  # yalnız gereksiz yeniden denemeyi önler: yazılamazsa tur bozulmaz
        print("KibrisArabaAl: denenen ilan listesi yazılamadı:", type(e).__name__)
