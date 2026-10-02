"""Gece bakımı = "veri tabanı tarama ve düzeltme ekibi". Günde bir kez (gece), şu işleri yapar:
- Şüpheli ilanı (uç fiyat, makul olmayan yıl/km) KARANTİNAYA alır: silinmez, emsalden ve bildirimden çıkar; düzelirse geri döner.
- Sonucu `bot_state maint:last` içine yazar; sabah durum mesajında tek satır olarak sahibe gösterilir.
Otomatik düzeltme yalnızca kesin olanlarla sınırlıdır (mükerrer işaretleme, süresi dolanı pasifleştirme: cron_evaluate'te)."""
import json
from collections import Counter
from datetime import datetime, timezone

from domain.normalize import is_car_brand
from domain.quality import REASONS, find_quarantine
from infrastructure.db.repository import Repository

NIGHT_HOURS_UTC = range(0, 4)  # KKTC 03:00–07:00


def run_maintenance(repo: Repository, now: datetime | None = None, force: bool = False) -> dict | None:
    now = now or datetime.now(timezone.utc)
    if not force and (now.hour not in NIGHT_HOURS_UTC or repo.alert_recent("maintenance", 20)):
        return None
    rows = [r for r in repo.listings_for_quality() if is_car_brand(r.get("brand_norm"))]
    found = find_quarantine(rows, now.year)
    changed = repo.set_quarantine(found)
    result = {"at": now.isoformat(), "karantina": len(found), "yeni": changed,
              "nedenler": dict(Counter(found.values())), "kontrol": len(rows)}
    repo.set_state("maint:last", json.dumps(result))
    repo.mark_alerted("maintenance")
    return result


def summary_line(repo: Repository) -> str | None:
    """Sabah durum mesajı için tek satır (en son bakım)."""
    try:
        r = json.loads(repo.get_state("maint:last") or "")
    except ValueError:
        return None
    if not r:
        return None
    short = {"fiyat_yer_tutucu": "fiyat yer tutucu", "fiyat_ucuz_supheli": "fiyat çok ucuz", "fiyat_pahali_supheli": "fiyat çok pahalı",
             "yil_supheli": "yıl makul değil", "km_supheli": "km makul değil"}
    parts = ", ".join(f"{n} {short.get(k, k)}" for k, n in r["nedenler"].items())
    return (f"• Gece bakımı: {r['kontrol']} ilan tarandı, {r['karantina']} şüpheli ilan karantinada"
            + (f" ({parts})" if parts else "") + " — bildirime ve fiyat karşılaştırmasına girmiyorlar.")
