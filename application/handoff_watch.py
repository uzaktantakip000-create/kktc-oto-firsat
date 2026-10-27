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
- kesik metin yüksek: iki gün üst üste gönderilerin %70'inden fazlası kesik (FB sayfa düzeni değişmiş olabilir; 10.10'da oran %43);
- satış kutusu fiyatı gelmiyor: dünkü (KKTC) aktarımda en az 500 gönderi satırı var ama fiyatlı olanı %5'ten az (27.10.2026: 24.10'dan beri
  böyleydi, önceden günde ~900 satır, %24-35'i fiyatlıydı; kesinti sırasında bile gürültü satırları yüzünden %0,4-1,2 fiyatlı görünüyor, bu yüzden
  "hiç yok" değil oran sınırı; sayaç social_import.BOX_KEY; botun kendi sayacıdır, okuyucunun değil);
- grup okunmadı: okuyucu çalışıyor (son turu 6 saatten yeni) ama son 7 günde ilan/araç görülmüş en az bir Facebook kaynağı kendi okuma aralığını
  (okuyucunun "okuma_siniri_saat"i) 8 saatten fazla aştı
  (tek toplu uyarı, kaynak adlarıyla (ad bilinmiyorsa fb-NN); yalnız izleme, okuma sıklığı ya da kararlar değişmez);
- okumalar sınırda bitiyor: dünkü (UTC, okuyucunun "sinir_gunluk"u) en az 20 okumanın %30'undan fazlası gönderi/kaydırma sınırında durdu.
Günlük örnek (`SAMPLE_KEY`) KKTC 21:00'den sonraki ilk turda bir kez yazılır, son `SAMPLE_KEEP` gün tutulur."""
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from application.health import notify_owner
from application.social_import import (
    ALL_STATUSES,
    BOX_KEY,
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
BOX_MIN_LINES = 500  # satış kutusu: dün en az bu kadar satır okunduysa fiyatsız kutu tesadüf değildir
BOX_MIN_RATIO = 0.05  # sağlıklı oran %24-35; kesintide %0,4-1,2 (27.10.2026): altı "fiyat gelmiyor" demektir
# Kaynak okunmadı: okuyucunun o kaynak için verdiği en uzun okuma aralığı ("okuma_siniri_saat": 18, sessiz kaynakta 24; okuyucu b0da4fa,
# 27.10.2026) + gece boşluğu ve bir tur payı. Alan yoksa/bozuksa UNREAD_DEFAULT (18 + 8).
UNREAD_SLACK = timedelta(hours=8)
UNREAD_DEFAULT = timedelta(hours=26)
LIMIT_SANE_H = (1, 72)  # okuma_siniri_saat bu aralık dışındaysa bozuk sayılır
# Okumaların sınırda bitmesi (okuyucu "sinir_gunluk", UTC günü): dün en az LIMIT_MIN_READS okumanın LIMIT_MAX_RATIO'dan fazlası gönderi/kaydırma
# sınırında durduysa yeni gönderilerin bir kısmı okunmadan kalıyor demektir (27.10 ölçümü: 48 saatte %18).
LIMIT_MIN_READS, LIMIT_MAX_RATIO = 20, 0.30
READER_FRESH = timedelta(hours=6)  # okuyucunun kendisi çalışıyor sayılır: son_tur_utc bundan yeniyse (durmuşsa selfwatch.social_watch uyarır)
UNREAD_LIST_MAX = 10  # mesajda sayılan takma adların en çok sayısı


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


def unread_sources(fb: dict, now: datetime) -> list[tuple[str, str]]:
    """Okuyucu çalışırken (son turu READER_FRESH'ten yeni) kendi okuma aralığı + UNREAD_SLACK'ten uzun süredir okunmamış, son 7 günde ilan/araç görülmüş kaynaklar:
    (kaynak anahtarı, takma ad "fb-NN" = durum dosyasındaki anahtarların sıralı sırası; mesajda kaynak adı bilinmiyorsa bu yazılır). Kaynak başına alan yok/bozuksa o kaynak atlanır; üyelik
    gerekenler okunamaz (başka neden), atlanır. Okuyucunun turu eskiyse ya da dosya biçimi bekleneni vermiyorsa boş."""
    last_round, rows = _time(fb.get("son_tur_utc")), fb.get("kaynaklar")
    if last_round is None or not timedelta(0) <= now - last_round < READER_FRESH or not isinstance(rows, dict):
        return []
    keys = sorted(k for k, v in rows.items() if isinstance(k, str) and isinstance(v, dict))
    out = []
    for n, key in enumerate(keys, 1):
        row = rows[key]
        read = _time(row.get("son_okuma_utc"))
        active = (_int(row.get("yeni_ilan_7g")) or 0) > 0 or (_int(row.get("arac_7g")) or 0) > 0
        limit_h = _int(row.get("okuma_siniri_saat"))
        after = timedelta(hours=limit_h) + UNREAD_SLACK if limit_h is not None and LIMIT_SANE_H[0] <= limit_h <= LIMIT_SANE_H[1] else UNREAD_DEFAULT
        if read is not None and active and row.get("uyelik_gerekli") is not True and now - read > after:
            out.append((key, f"fb-{n:02d}"))
    return out


def limit_alert(fb: dict, now: datetime) -> tuple[str, str] | None:
    """Dünkü (UTC) okumaların sınırda bitme oranı (okuyucu "sinir_gunluk": {gün: {okuma, gonderi_siniri, kaydirma_siniri}}). Alan yok/bozuksa None."""
    days = fb.get("sinir_gunluk")
    row = days.get((now.date() - timedelta(days=1)).isoformat()) if isinstance(days, dict) else None
    if not isinstance(row, dict):
        return None
    reads, posts, scrolls = _int(row.get("okuma")), _int(row.get("gonderi_siniri")), _int(row.get("kaydirma_siniri"))
    if reads is None or posts is None or scrolls is None or reads < LIMIT_MIN_READS or posts + scrolls > reads:
        return None
    if (posts + scrolls) / reads <= LIMIT_MAX_RATIO:
        return None
    return ("fb_okuma_sinirda", f"⚠️ Facebook: dün {reads} grup okumasının {posts + scrolls}'i sınırda bitti (önceki okumaya inemeden durdu); "
            "aradaki gönderiler okunmamış olabilir. Sosyal okuyucu tarafında bakılmalı.")


def handoff_alerts(fb: dict, scan: HandoffScan | None, now: datetime, names: dict[str, str] | None = None) -> list[tuple[str, str]]:
    """Saf yargı: (uyarı anahtarı, metin) listesi. Alan yok/bozuksa o kontrol atlanır (eski okuyucu sürümü yeni alanları yazmaz).
    `names`: kaynak anahtarı -> ad (okunmayan kaynak uyarısında; yoksa takma ad)."""
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
    limit = limit_alert(fb, now)
    out += [limit] if limit is not None else []
    unread = [(names or {}).get(key) or alias for key, alias in unread_sources(fb, now)]
    if unread:
        shown = ", ".join(unread[:UNREAD_LIST_MAX]) + (f" ve {len(unread) - UNREAD_LIST_MAX} tane daha" if len(unread) > UNREAD_LIST_MAX else "")
        out.append(("fb_grup_okunmadi", f"⚠️ Facebook: {len(unread)} kaynak olağan okuma aralığını 8 saatten fazla aştı, okunmadı ({shown}); "
                    "okuyucu çalışıyor görünüyor. Bu kaynaklardan gelecek ilanlar gecikir; sosyal okuyucu tarafında kaynak sırasına bakılmalı."))
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
            page = key.startswith("fbp:")  # Facebook sayfası (22.10.2026): katılım yok, "üyelik düşmüş" denmez
            name = names.get(key, "bir Facebook sayfası" if page else "bir Facebook grubu")
            out.append((f"social_facebook_grup_sessiz_{key.split(':', 1)[-1][:40]}",
                        f"⚠️ Facebook: \"{name}\" {'sayfasında' if page else 'grubunda'} son 24 saatte hiç yeni gönderi görülmedi "
                        f"(önceki günler ortalama {sum(past) / len(past):.0f}). "
                        + ("Sayfa paylaşımı durdurmuş ya da gizlenmiş olabilir." if page else "Üyelik düşmüş ya da grup gizlenmiş olabilir.")))

    def high(s: dict) -> bool:
        return isinstance(s.get("kesik"), int) and s.get("gonderi", 0) >= CUT_MIN_POSTS and s["kesik"] / s["gonderi"] > CUT_RATIO

    if before and high(today) and high(before[-1]) and _consecutive(before[-1]["gun"], today["gun"]):
        out.append(("social_facebook_kesik_yuksek", f"⚠️ Facebook: iki gündür gönderilerin %{today['kesik'] / today['gonderi'] * 100:.0f}'inin metni kesik "
                    "okunuyor (olağan ~%45). Facebook sayfa düzeni değişmiş olabilir; sosyal oturuma bakılmalı."))
    return out


def box_alert(repo, now: datetime) -> tuple[str, str] | None:
    """Dünkü (KKTC) satış kutusu sayacı (social_import.BOX_KEY): ≥BOX_MIN_LINES satır ve fiyatlı olanların oranı BOX_MIN_RATIO'dan azsa uyarı.
    Sayaç yok/bozuksa (lines ya da priced geçerli tamsayı değilse) None."""
    day = (to_kktc(now).date() - timedelta(days=1)).isoformat()
    try:
        got = json.loads(repo.get_state(BOX_KEY + day) or "{}")
    except ValueError:
        return None
    if not isinstance(got, dict):
        return None
    lines, priced = _int(got.get("lines")), _int(got.get("priced"))
    if lines is None or priced is None or lines < BOX_MIN_LINES or priced / lines >= BOX_MIN_RATIO:
        return None
    return ("fb_satis_kutusu", f"⚠️ Facebook satış kutusu fiyatı dün neredeyse hiç gelmedi ({priced}/{lines} gönderide fiyat; olağan ~%25). "
            "Fiyatlı Facebook ilanı azalır; sosyal okuyucu tarafında kontrol edilmeli.")


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
    names = ({k: r["name"] for k, r in source_map(repo.sources(PLATFORM, ALL_STATUSES)).items()}
             if unread_sources(fb, now) else None)  # kaynak adları yalnız okunmayan kaynak varsa okunur
    alerts = handoff_alerts(fb, scan, now, names)
    box = box_alert(repo, now)
    alerts += [box] if box is not None else []
    sample = daily_sample(fb, now) if kktc_hour(now) >= SAMPLE_HOUR else None
    samples = _samples(repo)
    if sample is not None and (not samples or samples[-1]["gun"] != sample["gun"]):
        samples = (samples + [sample])[-SAMPLE_KEEP:]
        repo.set_state(SAMPLE_KEY, json.dumps(samples))
        names = names or {k: r["name"] for k, r in source_map(repo.sources(PLATFORM, ALL_STATUSES)).items()}
        alerts += history_alerts(samples, names)
    for key, text in alerts:
        notify_owner(repo, key, text, repeat_hours=REPEAT_H)
