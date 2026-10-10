"""Facebook devir hattının sessiz arızaları (sosyal oturumla ortak tasarım, 10.10.2026). Okuyucunun durum dosyası (selfwatch.read_social) ve devir
klasörü okunur; her uyarı sahibe en çok 24 saatte bir gider (notify_owner). Okuyucunun kendi durması/freni/susması selfwatch.social_watch'ın işidir;
burada yalnız şunlar var:
- devir kapalı: durum dosyası `devir.acik = false` diyor (klasör yok): okuyucu ilanları yalnız kendi deneme dosyasına yazıyor, bota gelmiyor;
- devir bayat: okuyucu son 24 saatte gönderi gördü (`gunluk.gonderi` > 0) ama devir dosyası 24 saattir yazılmadı (okuma olmayan saatlerde
  `son_yazim_utc` doğal olarak 4+ saat eskir: eşik bu yüzden 24 saat);
- devir eksik: okuyucunun 24 saatlik penceresindeki gönderi sayısı ile aynı penceredeki devir satırı sayısı tutmuyor (pay: 3 ya da %10);
- aktarım geride: devir dosyasında 1 saatten eski, henüz aktarılmamış satır var (aktarıcı takıldı ya da her turda çöküyor);
- grup sessiz: günlük örneklerde ortalaması en az 5 yeni gönderi olan grup bugün 0 gösteriyor (üyelik iptali, gizlenme; tüm-boş freni tek grubu görmez).
  `yeni_gonderi_24s` okuyucunun gördüğü ana (seen_at) göre sayılır, paylaşım saatine göre değil;
- kesik metin yüksek: iki gün üst üste gönderilerin %70'inden fazlası kesik (FB sayfa düzeni değişmiş olabilir; 10.10'da oran %43).
Günlük örnek (`SAMPLE_KEY`) KKTC 21:00'den sonraki ilk turda bir kez yazılır, son `SAMPLE_KEEP` gün tutulur."""
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from application.health import notify_owner
from application.social_import import (
    ALL_STATUSES,
    HANDOFF_DIR,
    PLATFORM,
    STATE_KEY,
    _cursor,
    source_map,
)
from domain.kktc_time import kktc_hour, to_kktc

REPEAT_H = 24
STALE_WRITE = timedelta(hours=24)
IMPORT_LAG = timedelta(hours=1)
COUNT_SLACK, COUNT_SLACK_PCT = 3, 0.10
SAMPLE_KEY = "sosyal_gunluk_ornek:facebook"
SAMPLE_HOUR = 21  # KKTC
SAMPLE_KEEP = 8
QUIET_MIN_AVG, QUIET_MIN_DAYS = 5, 3  # grup sessiz: önceki en az 3 günlük örneğin ortalaması ≥5 yeni gönderi
CUT_RATIO, CUT_MIN_POSTS = 0.70, 20
SCAN_DAYS = 3  # devir klasöründe yalnız son 3 günün dosyaları taranır (dosyalar 14 gün tutulur)


@dataclass(frozen=True)
class HandoffScan:
    window_lines: int | None  # pencere içindeki devir satırı; klasör pencerenin başından eskiye uzanmıyorsa (yeni kuruldu) None
    stale_pending: int  # 1 saatten eski, henüz aktarılmamış satır


def _time(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        t = datetime.fromisoformat(value)
    except ValueError:
        return None
    return t if t.tzinfo is not None else None


def _int(value) -> int | None:
    return value if type(value) is int and value >= 0 else None


def scan_handoff(folder: Path | str, cursor: tuple[str, int], window_end: datetime | None, now: datetime) -> HandoffScan:
    """Devir dosyalarını (facebook-YYYYMMDD.jsonl, son SCAN_DAYS gün) satır satır sayar: `window_end`ten önceki 24 saatte görülen satır ve imleçten
    sonra kalan (aktarılmamış) 1 saatten eski satır. Yarım (\\n'siz) son satır sayılmaz; okunamayan satır atlanır."""
    d = Path(folder)
    oldest_day = (now - timedelta(days=SCAN_DAYS)).strftime("%Y%m%d")
    files = sorted(f.name for f in d.glob(f"{PLATFORM}-*.jsonl") if f.is_file() and f.name[len(PLATFORM) + 1:-6] >= oldest_day) if d.is_dir() else []
    start = window_end - timedelta(hours=24) if window_end else None
    in_window, pending, covered = 0, 0, False
    for name in files:
        for i, raw in enumerate((d / name).read_bytes().split(b"\n")[:-1]):
            try:
                seen = _time(json.loads(raw).get("goruldu_utc"))
            except (ValueError, AttributeError):
                continue
            if seen is None:
                continue
            if start is not None:
                covered = covered or seen <= start
                in_window += start < seen <= window_end
            unimported = name > cursor[0] or (name == cursor[0] and i >= cursor[1])
            pending += unimported and now - seen > IMPORT_LAG
    return HandoffScan(in_window if covered else None, pending)


def handoff_alerts(fb: dict, scan: HandoffScan | None, now: datetime) -> list[tuple[str, str]]:
    """Saf yargı: (uyarı anahtarı, metin) listesi. Alan yok/bozuksa o kontrol atlanır (eski okuyucu sürümü yeni alanları yazmaz)."""
    out = []
    devir, daily = fb.get("devir"), fb.get("gunluk")
    daily = daily if isinstance(daily, dict) else {}
    posts = _int(daily.get("gonderi"))
    if isinstance(devir, dict) and devir.get("acik") is False:
        out.append(("social_facebook_devir_kapali", "⚠️ Facebook okuyucusu ilanları bota aktarmıyor: devir klasörü kapalı görünüyor. "
                    "Okuyucu yalnız kendi kaydına yazıyor; sunucuda sosyal okuyucunun ayarına bakılmalı."))
    elif isinstance(devir, dict) and devir.get("acik") is True and posts:
        last = _time(devir.get("son_yazim_utc"))
        if last is None or now - last > STALE_WRITE:
            out.append(("social_facebook_devir_bayat", f"⚠️ Facebook okuyucusu son 24 saatte {posts} gönderi gördü ama devir dosyası "
                        + ("hiç yazılmamış" if last is None else f"{(now - last).days or 1} gündür yazılmıyor") + ". İlanlar bota gelmiyor olabilir."))
    if scan is not None and posts is not None and scan.window_lines is not None:
        gap = posts - scan.window_lines
        if abs(gap) > max(COUNT_SLACK, COUNT_SLACK_PCT * posts):
            out.append(("social_facebook_devir_eksik", f"⚠️ Facebook: okuyucu son 24 saatte {posts} gönderi saydı, devir dosyasında {scan.window_lines} satır var. "
                        "Okuyucu ile bot arasında gönderi kayboluyor olabilir."))
    if scan is not None and scan.stale_pending:
        out.append(("social_facebook_aktarim_geride", f"⚠️ Facebook: devir dosyasında 1 saatten eski {scan.stale_pending} gönderi henüz bota aktarılmadı. "
                    "Aktarıcı takılmış olabilir (tur kaydında \"sosyal_devir\" satırına bakılmalı)."))
    return out


def daily_sample(fb: dict, now: datetime) -> dict | None:
    """Bugünün örneği: gönderi, kesik metin ve grup başına yeni gönderi (24 saat). Gerekli alanlar yoksa None."""
    daily, rows = fb.get("gunluk"), fb.get("kaynaklar")
    if not isinstance(daily, dict) or _int(daily.get("gonderi")) is None:
        return None
    groups = {k: _int(v.get("yeni_gonderi_24s")) for k, v in rows.items() if isinstance(v, dict)} if isinstance(rows, dict) else {}
    return {"gun": to_kktc(now).date().isoformat(), "gonderi": daily["gonderi"], "kesik": _int(daily.get("kesik_metin")),
            "gruplar": {k: v for k, v in groups.items() if isinstance(k, str) and v is not None}}


def history_alerts(samples: list[dict], names: dict[str, str]) -> list[tuple[str, str]]:
    """Saf yargı (son örnek bugünün): sessiz grup ve iki gün üst üste yüksek kesik metin oranı. `names`: anahtar -> kaynak adı."""
    if not samples:
        return []
    today, before = samples[-1], samples[:-1]
    out = []
    for key, n in sorted(today.get("gruplar", {}).items()):
        past = [s["gruplar"][key] for s in before if isinstance(s.get("gruplar"), dict) and isinstance(s["gruplar"].get(key), int)]
        if n == 0 and len(past) >= QUIET_MIN_DAYS and sum(past) / len(past) >= QUIET_MIN_AVG:
            name = names.get(key, "bir Facebook grubu")
            out.append((f"social_facebook_grup_sessiz_{key[3:][:40]}", f"⚠️ Facebook: \"{name}\" grubunda son 24 saatte hiç yeni gönderi görülmedi "
                        f"(önceki günler ortalama {sum(past) / len(past):.0f}). Üyelik düşmüş ya da grup gizlenmiş olabilir."))

    def high(s: dict) -> bool:
        return isinstance(s.get("kesik"), int) and s.get("gonderi", 0) >= CUT_MIN_POSTS and s["kesik"] / s["gonderi"] > CUT_RATIO

    if before and high(today) and high(before[-1]) and _consecutive(before[-1]["gun"], today["gun"]):
        out.append(("social_facebook_kesik_yuksek", f"⚠️ Facebook: iki gündür gönderilerin %{today['kesik'] / today['gonderi'] * 100:.0f}'inin metni kesik "
                    "okunuyor (olağan ~%45). Facebook sayfa düzeni değişmiş olabilir; sosyal oturuma bakılmalı."))
    return out


def _consecutive(a: str, b: str) -> bool:
    try:
        return datetime.fromisoformat(b).date() - datetime.fromisoformat(a).date() == timedelta(days=1)
    except ValueError:
        return False


def _samples(repo) -> list[dict]:
    try:
        got = json.loads(repo.get_state(SAMPLE_KEY) or "[]")
    except ValueError:
        return []
    return [s for s in got if isinstance(s, dict) and isinstance(s.get("gun"), str)] if isinstance(got, list) else []


def watch(repo, data: dict | None, now: datetime, folder: Path | str | None = None) -> None:
    """VPS turunun öz-izleme adımı (selfwatch.after_tick, KKTC gündüzü). `data`: okunmuş durum dosyası (selfwatch.read_social) ya da None."""
    fb = (data or {}).get("platformlar", {}).get(PLATFORM) if isinstance(data, dict) else None
    if not isinstance(fb, dict):
        return
    daily = fb.get("gunluk") if isinstance(fb.get("gunluk"), dict) else {}
    scan = scan_handoff(folder or HANDOFF_DIR, _cursor(repo.get_state(STATE_KEY)), _time(daily.get("pencere_bitis_utc")), now)
    alerts = handoff_alerts(fb, scan, now)
    sample = daily_sample(fb, now) if kktc_hour(now) >= SAMPLE_HOUR else None
    samples = _samples(repo)
    if sample is not None and (not samples or samples[-1]["gun"] != sample["gun"]):
        samples = (samples + [sample])[-SAMPLE_KEEP:]
        repo.set_state(SAMPLE_KEY, json.dumps(samples))
        names = {k: r["name"] for k, r in source_map(repo.sources(PLATFORM, ALL_STATUSES)).items()}
        alerts += history_alerts(samples, names)
    for key, text in alerts:
        notify_owner(repo, key, text, repeat_hours=REPEAT_H)
