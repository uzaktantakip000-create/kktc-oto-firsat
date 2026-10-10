"""Kendi kendini izleme: sistem kendi sorununu fark edip sahibe BİR kez haber versin (gözetimsiz çalışabilsin).
Taramalar VPS'te, GitHub yedek (application/runner_gate.py). GitHub sessizce durabilir (dış tetikleyicinin anahtarı bitmiş olabilir; GitHub 60 gün
depo hareketsizliğinden sonra zamanlanmış işleri kapatır): bu yüzden yedeğin de canlı olduğu görülür.
KURAL: hiçbir kontrol taramayı çökertemez ya da bekletemez, neyin taranacağını/değerlendirileceğini/fırsat olarak gönderileceğini DEĞİŞTİRMEZ.
Her giriş noktası kendi hatasını yutar; log'a yalnız hata TÜRÜ yazılır (hata metni bağlantı adresi/sır taşıyabilir).

bot_state anahtarları (hepsi UTC ISO zaman; yazılmamış ya da "" = yok):
  vps_tick_seen, vps_browser_seen  VPS'in son BAŞARILI turu / KKTCarabam başarısı            (runner_gate; VPS yazar)
  gh_tick_seen, gh_browser_seen    GitHub tick / collect-browser çalışmasının BAŞLADIĞI an       (burada; atlasa da yazar)
  fo_tick, fo_browser              açık "sunucu durdu, GitHub devraldı" olayı: kesintinin başı (= son iyi VPS kalp atışı);
                                   YALNIZ "durdu" uyarısı gerçekten gittiyse yazılır, "yeniden çalışıyor" gidince silinir
  ls_miss                          Telegram dinleyicisinin kalp atışı yok/bayat: "ilk_görülen|son_görülen" (kesintisiz kayıp sayacı)
  ls_down                          açık "anında cevap durdu" olayı (uyarı gittiyse; başı = kesintinin ilk görüldüğü an)
  bot_listen_seen                  dinleyici kalp atışı (bot_poll.LISTENER_STATE_KEY; dinleyici yazar, temiz çıkışta siler)
  backup_last_ok                   son BAŞARILI veritabanı yedeği (haftalık yedek işi yazar; burada yalnız okunur, sabah satırı)
Uyarı tekrar sınırı health.notify_owner'ın kendi `alert:<ad>` kayıtlarıdır; adlar: vps_failover, vps_recovered, vps_browser_failover, vps_browser_recovered,
listener_down, listener_up, vps_resources, social_<platform>_<fren|hata|susuyor>, social_okuyucu_durmus.
Sosyal medya okuyucusu (Instagram/Facebook; ayrı program, veritabanımızda değil) aynı VPS'te kendi durum dosyasını yazar (SOCIAL_STATUS_PATH, JSON);
burada yalnız OKUNUR: sabah satırı + anında uyarı. Dosya yoksa (sosyal taraf kurulu değil) hiçbir şey yazılmaz/uyarılmaz.
"""
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

from application.health import notify_owner
from application.runner_gate import SKEW, TICK_FRESH, VPS_BROWSER_KEY, VPS_TICK_KEY, on_github, on_vps
from domain.kktc_time import KKTC, kktc_hour, to_kktc

GH_TICK_KEY = "gh_tick_seen"
GH_BROWSER_KEY = "gh_browser_seen"
FO_TICK_KEY = "fo_tick"
FO_BROWSER_KEY = "fo_browser"
LISTEN_MISS_KEY = "ls_miss"
LISTEN_DOWN_KEY = "ls_down"
BACKUP_OK_KEY = "backup_last_ok"

ALERT_REPEAT_H = 12  # aynı uyarı en erken bu kadar saat sonra tekrar yazılır
LISTEN_BAD_FOR = timedelta(minutes=20)  # dinleyici kalp atışı KESİNTİSİZ bu kadar süre yoksa uyarı (deploy/yeniden başlatmadaki tek kayıp okuma uyarı değil)
LISTEN_GAP_MAX = timedelta(minutes=45)  # iki okuma arası bundan uzunsa (turlar durmuş) "kesintisiz" sayılmaz, sayaç yeniden başlar
LISTEN_DOWN = "⚠️ Telegram'da anında cevap durdu (dinleyici sessiz, en az {t}). Komutlarına yine cevap gelir ama ~15 dk gecikmeyle: tarama turu bakıyor."
LISTEN_UP = "✅ Telegram'da anında cevap yeniden çalışıyor (kesinti ~{t})."
DISK_MIN_FREE_PCT = 15  # sunucuda kökte boş disk bunun altına inerse uyarı (VPS: 77 GB)
MEM_MIN_MB = 400  # kullanılabilir bellek (MemAvailable) bunun altına inerse uyarı (VPS: 8 GB; tarayıcı turu ~2 GB'a kadar çıkar)
RESOURCES = "⚠️ Sunucuda kaynak azalıyor: disk %{pct} boş ({gb} GB), bellek {mem} kullanılabilir. Eşik: disk %{dpct}, bellek {dmem} MB."
GH_TICK_MAX_AGE = timedelta(hours=3)  # sabah satırı: yedek GitHub tick'i bundan uzun süredir görünmüyorsa ⚠️ (dış tetikleyici 15 dk'da bir, GitHub'ın kendi saati 2 saatte bir)
GH_BROWSER_MAX_AGE = timedelta(hours=5)  # aynısı collect-browser için (2 saatte bir)
BACKUP_MAX_AGE = timedelta(days=8)  # sabah satırı: haftalık veritabanı yedeği bundan eskiyse ⚠️
SOCIAL_STATUS_PATH = "/var/lib/kktc-social-durum/durum.json"  # sosyal okuyucunun durum dosyası (~20 dk'da bir, yalnız VPS'te); çağrı anında aranır: testler değiştirir
SOCIAL_SCHEMA = 1  # dosyadaki "surum"; başkası = biçim değişmiş, okunmaz (satır da uyarı da yok)
SOCIAL_DAY = (time(8), time(23))  # okuyucunun tur attığı KKTC gündüzü [08:00, 23:00); gece boşluğu "susma" sayılmaz, uyarı da yalnız bu saatlerde gider
SOCIAL_SILENT_AFTER = timedelta(hours=5)  # "tamam" diyen platform GÜNDÜZ saatiyle bu kadar süredir tur atmadıysa susuyor (turlar ~4 saatte bir)
SOCIAL_FILE_STALE = timedelta(hours=1)  # durum dosyası gündüz saatiyle bundan uzun süredir yazılmadıysa okuyucunun kendisi durmuş (~20 dk'da bir yazar)
SOCIAL_REASON_MAX = 60  # fren nedeninin mesajdaki en çok uzunluğu
SOCIAL_REPEAT_H = 24  # aynı sosyal uyarı en erken bu kadar saat sonra tekrar yazılır
SOCIAL_NAMES = {"instagram": "Instagram", "facebook": "Facebook"}  # satırda önce bunlar (bu sırayla), sonra öbürleri alfabetik; bilinmeyen adın baş harfi büyütülür
SOCIAL_KEY_OK = re.compile(r"[a-z0-9_]{1,24}")  # platform anahtarı uyarı anahtarına ve mesaja girer: başka bir şey taşıyan platform atlanır
BROWSER_ALERT_AFTER = timedelta(hours=6)  # KKTCarabam: GitHub 150 dk'da devralır ama tek aksayan tur (Cloudflare, site) uyarı sebebi değil: 3 tur üst üste


def _safe(name: str, fn):
    """Kontrolü çalıştırır; hata verirse tur bozulmaz, yalnız hata türü yazılır."""
    try:
        return fn()
    except Exception as e:
        print(f"öz-izleme ({name}) başarısız: {type(e).__name__}")


def _age(value, now: datetime) -> timedelta | None:
    """Kayıttan şimdiye geçen süre. Kayıt yok/boş/bozuk/saat dilimsiz ya da SKEW'den fazla ileri tarihli ise None (güvenilmez: ne uyarı ne bildirim).
    SKEW içindeki küçük ileri fark 0 sayılır."""
    if not value:
        return None
    try:
        seen = datetime.fromisoformat(value)
        if seen.tzinfo is None or now.tzinfo is None:
            return None
        age = now - seen
    except Exception:  # bozuk metin, tür hatası, taşma
        return None
    return None if age < -SKEW else max(age, timedelta(0))


def _span(age: timedelta) -> str:
    """Mesajlar için kısa süre: "52 dk", "3 saat", "5 gün"."""
    minutes = int(age.total_seconds() // 60)
    if minutes < 120:
        return f"{max(minutes, 1)} dk"
    return f"{minutes // 60} saat" if minutes < 48 * 60 else f"{minutes // 1440} gün"


def note_github_start(repo, key: str, now: datetime | None = None) -> None:
    """GitHub'da, tick/collect-browser çalışması BAŞLARKEN (VPS turu görüp atlayacak olsa bile) yedeğin canlı olduğunu yazar:
    `gh_tick_seen` / `gh_browser_seen`. GitHub dışında hiçbir şey yapmaz; yazılamazsa tur etkilenmez."""
    if not on_github():
        return
    _safe("yedek kalp atışı", lambda: repo.set_state(key, (now or datetime.now(timezone.utc)).isoformat()))


@dataclass(frozen=True)
class Failover:
    """Bir VPS işinin (tick / KKTCarabam tarayıcısı) "durdu → GitHub devraldı → yeniden çalışıyor" bildirimleri."""
    seen_key: str  # VPS kalp atışı (runner_gate)
    incident_key: str  # açık olay (kesintinin başı); yalnız "durdu" uyarısı gittiyse yazılır
    down_alert: str  # notify_owner tekrar sınırı anahtarları
    up_alert: str
    alert_after: timedelta  # kalp atışı bu kadar eskiyse uyarılır (GitHub'ın devralma eşiğinden küçük olamaz)
    repeat_h: int
    down: str  # {t}: VPS'in son iyi turundan beri geçen süre
    up: str  # {t}: kesinti süresi


TICK = Failover(VPS_TICK_KEY, FO_TICK_KEY, "vps_failover", "vps_recovered", TICK_FRESH, ALERT_REPEAT_H,
                "⚠️ Sunucu turları durdu (son tur {t} önce); GitHub devraldı, tarama sürüyor.",
                "✅ Sunucu turları yeniden çalışıyor (kesinti ~{t}; bu arada GitHub taradı).")
BROWSER = Failover(VPS_BROWSER_KEY, FO_BROWSER_KEY, "vps_browser_failover", "vps_browser_recovered", BROWSER_ALERT_AFTER, 24,
                   "⚠️ Sunucu KKTCarabam'ı okuyamıyor (son başarı {t} önce); GitHub devraldı, tarama sürüyor.",
                   "✅ Sunucu KKTCarabam'ı yeniden okuyor (kesinti ~{t}; bu arada GitHub okudu).")


def github_failover(repo, f: Failover, now: datetime | None = None) -> None:
    """GitHub'da, VPS kalp atışı VAR ama bayatsa (yani GitHub o yüzden taradı) sahibe BİR uyarı; aynı uyarı `f.repeat_h` saat içinde tekrarlanmaz.
    Kalp atışı hiç yazılmamışsa (VPS hiç kurulmadı), bozuksa ya da ileri tarihliyse uyarı YOK. Olay yalnız uyarı gerçekten gittiyse açılır:
    tekrar sınırı yüzünden susan bir kesinti için sonradan "yeniden çalışıyor" mesajı da gelmez (uyarısız ✅ kafa karıştırırdı)."""
    if not on_github():
        return
    now = now or datetime.now(timezone.utc)
    seen = repo.get_state(f.seen_key)
    age = _age(seen, now)
    if age is None or age < f.alert_after:
        return
    sent = notify_owner(repo, f.down_alert, f.down.format(t=_span(age)), repeat_hours=f.repeat_h)
    if sent and not repo.get_state(f.incident_key):  # tekrar uyarısı olayın başlangıcını değiştirmez
        repo.set_state(f.incident_key, seen)


def _resolve(repo, incident_key: str, alert_key: str, text: str, now: datetime) -> None:
    """Açık olay varsa BİR kez "düzeldi" der ve olayı kapatır. Mesaj gitmezse (Telegram hatası) olay açık kalır, sonraki turda yeniden denenir."""
    raw = repo.get_state(incident_key)
    if not raw:
        return
    age = _age(raw, now)
    if age is None:  # bozuk ya da ileri tarihli kayıt: süre bilinmez, sessizce kapat
        repo.set_state(incident_key, "")
        return
    if notify_owner(repo, alert_key, text.format(t=_span(age)), repeat_hours=0):
        repo.set_state(incident_key, "")


def vps_recovery(repo, f: Failover, now: datetime | None = None) -> None:
    """VPS'te, işin kalp atışı yeni yazıldıktan sonra: açık "durdu" olayı varsa BİR kez "yeniden çalışıyor" der ve olayı kapatır."""
    if on_vps():
        _resolve(repo, f.incident_key, f.up_alert, f.up, now or datetime.now(timezone.utc))


def _dec(x: float) -> str:
    return f"{x:.1f}".replace(".", ",")  # Türkçe ondalık virgül


def _mem_text(mb: float) -> str:
    return f"{_dec(mb / 1024)} GB" if mb >= 1024 else f"{mb:.0f} MB"


def read_resources(root: str = "/", meminfo: str = "/proc/meminfo") -> dict | None:
    """Sunucu kaynakları (yalnız standart kütüphane): kökte boş disk (% ve GB) ve kullanılabilir bellek (MB). /proc/meminfo yoksa (macOS, testler),
    bellek satırı bulunamazsa ya da okunamazsa None: kontrol sessizce atlanır."""
    try:
        with open(meminfo) as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    mem_mb = int(line.split()[1]) / 1024  # kB -> MB
                    break
            else:
                return None
        total, _, free = shutil.disk_usage(root)  # `free`: yetkisiz kullanıcının kullanabildiği boş yer
    except (OSError, ValueError, IndexError):
        return None
    if total <= 0:
        return None
    return {"disk_pct": free * 100 / total, "disk_gb": free / 1024 ** 3, "mem_mb": mem_mb}


def resource_watch(repo, now: datetime, read=None) -> None:
    """VPS tick'inde: boş disk < %DISK_MIN_FREE_PCT ya da kullanılabilir bellek < MEM_MIN_MB ise sahibe BİR uyarı (sayılarla; 12 saatte bir tekrar).
    `read`: sınama için ölçüm işlevi (varsayılan read_resources, çağrı anında aranır)."""
    snap = (read or read_resources)()
    if snap is None or (snap["disk_pct"] >= DISK_MIN_FREE_PCT and snap["mem_mb"] >= MEM_MIN_MB):
        return
    text = RESOURCES.format(pct=f"{snap['disk_pct']:.0f}", gb=_dec(snap["disk_gb"]), mem=_mem_text(snap["mem_mb"]), dpct=DISK_MIN_FREE_PCT, dmem=MEM_MIN_MB)
    notify_owner(repo, "vps_resources", text, repeat_hours=ALERT_REPEAT_H)


def _streak(raw) -> tuple[datetime, datetime] | None:
    """`ls_miss` kaydı "ilk|son" -> (ilk, son); yok/bozuk/saat dilimsiz ise None."""
    try:
        first, last = (datetime.fromisoformat(part) for part in raw.split("|"))
    except Exception:
        return None
    return (first, last) if first.tzinfo is not None and last.tzinfo is not None else None


def listener_watch(repo, now: datetime) -> None:
    """VPS tick'inde: Telegram dinleyicisinin kalp atışı (bot_listen_seen) taze mi? Yok/bayat/silinmiş (temiz çıkış) okuma ilk görüldüğünde YALNIZ not edilir
    (`ls_miss`); aynı kayıp KESİNTİSİZ LISTEN_BAD_FOR sürerse BİR uyarı (12 saatte bir tekrar). Taze görülünce sayaç silinir; uyarı gittiyse BİR kez ✅.
    Turlar 15 dk'da bir okuduğu için uyarı kayıp başladıktan ~20-35 dk sonra gelir."""
    from application.bot_poll import LISTENER_STATE_KEY, listener_alive  # geç içe aktarma: bot_poll status'u, status bu modülü içe aktarır
    if listener_alive(repo.get_state(LISTENER_STATE_KEY), now):
        if repo.get_state(LISTEN_MISS_KEY):
            repo.set_state(LISTEN_MISS_KEY, "")
        _resolve(repo, LISTEN_DOWN_KEY, "listener_up", LISTEN_UP, now)
        return
    streak = _streak(repo.get_state(LISTEN_MISS_KEY))
    continuous = streak is not None and streak[0] <= streak[1] <= now and now - streak[1] <= LISTEN_GAP_MAX
    first = streak[0] if continuous else now
    repo.set_state(LISTEN_MISS_KEY, f"{first.isoformat()}|{now.isoformat()}")
    if now - first >= LISTEN_BAD_FOR:
        sent = notify_owner(repo, "listener_down", LISTEN_DOWN.format(t=_span(now - first)), repeat_hours=ALERT_REPEAT_H)
        if sent and not repo.get_state(LISTEN_DOWN_KEY):
            repo.set_state(LISTEN_DOWN_KEY, first.isoformat())


# --- sosyal medya okuyucusu (Instagram/Facebook): ayrı programın durum dosyasını OKUR, hiçbir şeyi değiştirmez -------------------------
@dataclass(frozen=True)
class SocialPlatform:
    key: str
    state: str  # "tamam" | "susuyor" | "fren" | "hata" | "belirsiz"
    part: str  # sabah satırındaki parça: "Instagram ✅ (son tur 2 saat önce, 3 yeni)"
    alert: str | None  # anında uyarı metni: yalnız susuyor/fren/hata durumlarında


@dataclass(frozen=True)
class SocialView:
    stale: timedelta | None  # durum dosyası gündüz saatiyle >SOCIAL_FILE_STALE yazılmadıysa dosyanın gerçek yaşı (okuyucu durmuş), değilse None
    platforms: list[SocialPlatform]


def _day_elapsed(start: datetime, end: datetime) -> timedelta:
    """[start, end] aralığının KKTC gündüz pencereleriyle (SOCIAL_DAY, yerel saat: yaz/kış domain/kktc_time'dan) kesişen süresi: gece boşluğu sayılmaz
    (22:50'de tur atmış okuyucu 08:30'da 40 dk'lıktır, 9 saat 40 dk'lık değil). Yalnız eşik karşılaştırması içindir: son 3 gün dışı kesilir
    (her 3 günde 45 saat gündüz var, eşiklerin çok üstünde); aşırı eski bozuk kayıt döngüyü uzatamaz."""
    start = max(start, end - timedelta(days=3))
    total, day, last = timedelta(0), to_kktc(start).date(), to_kktc(end).date()
    while day <= last:
        opens = datetime.combine(day, SOCIAL_DAY[0], tzinfo=KKTC).astimezone(timezone.utc)  # UTC'ye çevir: aynı saat dilimli çıkarma duvar saatiyle yapılırdı
        closes = datetime.combine(day, SOCIAL_DAY[1], tzinfo=KKTC).astimezone(timezone.utc)
        total += max(timedelta(0), min(end, closes) - max(start, opens))
        day += timedelta(days=1)
    return total


def _social_ages(value, now: datetime) -> tuple[timedelta, timedelta] | None:
    """(gerçek süre, gündüz süresi). Kayıt yok/bozuk/saat dilimsiz/ileri tarihli ise None (_age ile aynı güvenilmezlik kuralı)."""
    age = _age(value, now)
    return None if age is None else (age, _day_elapsed(now - age, now))


def _count(value) -> int | None:
    return value if type(value) is int and value >= 0 else None  # bool/metin/eksi sayı: yok say


def _reason(value) -> str | None:
    """Fren nedeni (başka programın kısa metni): tek satıra indirilir, SOCIAL_REASON_MAX karaktere kısaltılır; metin değilse/boşsa None."""
    text = " ".join(value.split()) if isinstance(value, str) else ""
    if not text:
        return None
    return text if len(text) <= SOCIAL_REASON_MAX else text[:SOCIAL_REASON_MAX - 1].rstrip() + "…"


def _social_platform(key: str, p: dict, now: datetime) -> SocialPlatform:
    name = SOCIAL_NAMES.get(key, key.capitalize())
    result, errors = p.get("sonuc"), _count(p.get("kaynak_hatasi")) or 0
    if result == "tamam":
        ages = _social_ages(p.get("son_tur_utc"), now)
        if ages is None:  # "tamam" diyor ama tur zamanı yok/bozuk: susup susmadığı bilinemez
            return SocialPlatform(key, "belirsiz", f"{name} ⚠️ durum belirsiz", None)
        age, day_age = ages
        if day_age > SOCIAL_SILENT_AFTER:
            return SocialPlatform(key, "susuyor", f"{name} ⚠️ susuyor (son tur {_span(age)} önce)",
                                  f"⚠️ {name} okuyucusu sustu: son tur {_span(age)} önce. Sunucuda sosyal okuyucunun çalışıp çalışmadığına bakılmalı.")
        new = _count(p.get("yeni_ilan"))
        notes = [f"son tur {_span(age)} önce"] + ([f"{new} yeni"] if new is not None else []) + ([f"{errors} kaynak hatası"] if errors else [])
        return SocialPlatform(key, "tamam", f"{name} ✅ ({', '.join(notes)})", None)
    if result == "fren":
        reason = _reason(p.get("fren_nedeni"))
        why = f": {reason.rstrip('.')}" if reason else " (fren)"
        return SocialPlatform(key, "fren", f"{name} ⚠️ fren" + (f" ({reason})" if reason else ""),
                              f"⚠️ {name} okuyucusu durdu{why}. Uzak masaüstünden giriş gerekebilir.")
    if result == "hata":
        return SocialPlatform(key, "hata", f"{name} ⚠️ hata" + (f" ({errors} kaynak hatası)" if errors else ""),
                              f"⚠️ {name} okuyucusu hata veriyor (proxy'ye ulaşılamıyor ya da kaynaklar sürekli hata veriyor). Sunucudaki kaydına bakılmalı.")
    return SocialPlatform(key, "belirsiz", f"{name} ⚠️ durum belirsiz", None)


def read_social(path: str | None = None) -> dict | None:
    """Sosyal okuyucunun durum dosyasını okur. Dosya yok/okunamıyor/JSON bozuk/çok büyük/"surum" başka/biçim beklenenden farklıysa None (hata yok, log yok:
    sosyal taraf kurulu olmayabilir). Dosya yazarı tarafından yeniden adlandırılarak (tmp+rename) yazılır: yarım dosya okunmaz."""
    try:
        with open(path or SOCIAL_STATUS_PATH, encoding="utf-8") as f:
            data = json.loads(f.read(65536))
    except (OSError, ValueError, RecursionError):  # ValueError: bozuk JSON ve UTF-8
        return None
    ok = isinstance(data, dict) and type(data.get("surum")) is int and data["surum"] == SOCIAL_SCHEMA and isinstance(data.get("platformlar"), dict)
    return data if ok else None


def judge_social(data: dict, now: datetime) -> SocialView | None:
    """Saf yargı. Dosyanın yazım zamanı (`yazildi_utc`) yok/bozuk/saat dilimsiz/ileri tarihli ise None (beklenmeyen biçim: satır da uyarı da yok).
    Sözlük olmayan platform ya da geçersiz anahtar atlanır, öbürleri yine değerlendirilir. Sıra: instagram, facebook, sonra alfabetik."""
    ages = _social_ages(data.get("yazildi_utc"), now)
    if ages is None:
        return None
    raw = data.get("platformlar")
    raw = raw if isinstance(raw, dict) else {}
    keys = [k for k, v in raw.items() if isinstance(k, str) and SOCIAL_KEY_OK.fullmatch(k) and isinstance(v, dict)]
    order = list(SOCIAL_NAMES)
    keys.sort(key=lambda k: (order.index(k) if k in order else len(order), k))
    return SocialView(ages[0] if ages[1] > SOCIAL_FILE_STALE else None, [_social_platform(k, raw[k], now) for k in keys])


def social_watch(repo, now: datetime, path: str | None = None) -> None:
    """VPS tick'inde (yalnız KKTC gündüzü 08–23): durum dosyası bir platform için fren/hata/susma ya da okuyucunun kendisi için "durmuş" diyorsa sahibe
    BİR uyarı (`social_<platform>_<durum>` / `social_okuyucu_durmus`, 24 saatte bir tekrar). Okuyucu durmuşsa yalnız o uyarı gider: dosyadaki platform
    bilgisi bayattır, ayrıca "susuyor" demek aynı arızanın kopyası olur. Dosya yoksa/okunamıyorsa/biçim yanlışsa hiçbir şey yapılmaz; düzelince mesaj yok."""
    if not SOCIAL_DAY[0].hour <= kktc_hour(now) < SOCIAL_DAY[1].hour:
        return
    data = read_social(path)
    view = judge_social(data, now) if data is not None else None
    if view is None:
        return
    if view.stale is not None:
        notify_owner(repo, "social_okuyucu_durmus", f"⚠️ Sosyal okuyucu durmuş görünüyor (son yazım {_span(view.stale)} önce). Sunucuda çalışıp çalışmadığına bakılmalı.",
                     repeat_hours=SOCIAL_REPEAT_H)
        return
    for p in view.platforms:
        if p.alert:
            notify_owner(repo, f"social_{p.key}_{p.state}", p.alert, repeat_hours=SOCIAL_REPEAT_H)


def handoff_check(repo, now: datetime, path: str | None = None) -> None:
    """KKTC gündüzü: Facebook devir hattının sessiz arızaları (application/handoff_watch). Durum dosyası yoksa/bozuksa hiçbir şey yapılmaz."""
    if not SOCIAL_DAY[0].hour <= kktc_hour(now) < SOCIAL_DAY[1].hour:
        return
    from application import handoff_watch  # tembel: aktarıcının bağımlılıkları yalnız VPS turunda yüklenir
    handoff_watch.watch(repo, read_social(path), now)


def after_tick(repo, beat_written: bool, now: datetime | None = None) -> None:
    """tick.py'nin son adımı (hata yutar). GitHub (atlamadan tarayan tur): sunucu turları durduysa uyarı.
    VPS: kalp atışı yazıldıysa kesinti sonu bildirimi; her VPS turunda (değerlendirme çökse de) dinleyici bekçisi, disk/bellek ve sosyal okuyucu kontrolü."""
    now = now or datetime.now(timezone.utc)
    if on_github():
        _safe("sunucu kesintisi uyarısı", lambda: github_failover(repo, TICK, now))
    elif on_vps():
        if beat_written:
            _safe("sunucu dönüş bildirimi", lambda: vps_recovery(repo, TICK, now))
        _safe("dinleyici bekçisi", lambda: listener_watch(repo, now))
        _safe("kaynak izleme", lambda: resource_watch(repo, now))
        _safe("sosyal okuyucu uyarısı", lambda: social_watch(repo, now))
        _safe("sosyal devir izleme", lambda: handoff_check(repo, now))


def after_browser(repo, beat_written: bool, now: datetime | None = None) -> None:
    """cron_collect kktcarabam'ın son adımı (hata yutar): tick'in aynısı, KKTCarabam tarayıcı işi için (uyarı eşiği BROWSER_ALERT_AFTER)."""
    now = now or datetime.now(timezone.utc)
    if on_github():
        _safe("KKTCarabam kesinti uyarısı", lambda: github_failover(repo, BROWSER, now))
    elif on_vps() and beat_written:
        _safe("KKTCarabam dönüş bildirimi", lambda: vps_recovery(repo, BROWSER, now))


# --- sabah mesajına eklenen en çok 3 kısa satır (application/status.build_heartbeat) -----------------------------------------------
def _scan_line(repo, now: datetime) -> str | None:
    """"Taramalar: sunucuda ✅ (son tur 4 dk önce) · yedek GitHub ✅ · disk %62 boş, bellek 6,1 GB boş". Kaydı olmayan (ya da bozuk) parça atlanır, ⚠️ yazılmaz;
    hiçbir parça yoksa satır yok. Disk/bellek yalnız sunucuda (VPS) yazılan sabah mesajında görünür."""
    vps = _age(repo.get_state(VPS_TICK_KEY), now)
    gh_tick = _age(repo.get_state(GH_TICK_KEY), now)
    gh_browser = _age(repo.get_state(GH_BROWSER_KEY), now)
    parts = []
    if vps is not None:
        parts.append(f"sunucuda ✅ (son tur {_span(vps)} önce)" if vps < TICK_FRESH
                     else f"⚠️ sunucu turları durdu (son tur {_span(vps)} önce, GitHub tarıyor)")
    sick = []
    if gh_tick is not None and gh_tick > GH_TICK_MAX_AGE:
        sick.append(f"⚠️ yedek GitHub sessiz (son çalışma {_span(gh_tick)} önce)")
    if gh_browser is not None and gh_browser > GH_BROWSER_MAX_AGE:
        sick.append(f"⚠️ KKTCarabam yedeği sessiz (son çalışma {_span(gh_browser)} önce)")
    if sick:
        parts += sick
    elif gh_tick is not None or gh_browser is not None:
        parts.append("yedek GitHub ✅")
    snap = _safe("kaynak ölçümü", read_resources) if on_vps() and parts else None  # ölçüm patlarsa yalnız o parça düşer
    if snap is not None:
        low = snap["disk_pct"] < DISK_MIN_FREE_PCT or snap["mem_mb"] < MEM_MIN_MB
        parts.append(f"{'⚠️ ' if low else ''}disk %{snap['disk_pct']:.0f} boş, bellek {_mem_text(snap['mem_mb'])} boş")
    return "Taramalar: " + " · ".join(parts) if parts else None


def _backup_line(repo, now: datetime) -> str | None:
    """"Son veritabanı yedeği: 3 gün önce" (8 günden eskiyse başında ⚠️); `backup_last_ok` yoksa/bozuksa satır yok."""
    age = _age(repo.get_state(BACKUP_OK_KEY), now)
    if age is None:
        return None
    when = "bugün" if age.days < 1 else f"{age.days} gün önce"
    return f"{'⚠️ ' if age > BACKUP_MAX_AGE else ''}Son veritabanı yedeği: {when}"


def _social_line(repo, now: datetime) -> str | None:
    """"Sosyal: Instagram ✅ (son tur 2 saat önce, 3 yeni) · Facebook ⚠️ fren (doğrulama isteniyor (checkpoint))". Yalnız VPS'te yazılan mesajda (durum dosyası
    orada); dosya yok/okunamıyor/biçim yanlışsa satır yok. Okuyucunun kendisi durmuşsa parçaların başına "⚠️ sosyal okuyucu durmuş görünüyor (son yazım X önce)"."""
    if not on_vps():
        return None
    data = read_social()
    view = judge_social(data, now) if data is not None else None
    if view is None:
        return None
    parts = ([f"⚠️ sosyal okuyucu durmuş görünüyor (son yazım {_span(view.stale)} önce)"] if view.stale is not None else []) + [p.part for p in view.platforms]
    return "Sosyal: " + " · ".join(parts) if parts else None


def morning_lines(repo, now: datetime | None = None) -> list[str]:
    """Sabah mesajına eklenecek 0-3 satır. Hiçbir hata sabah mesajını bozmaz (hata veren satır atlanır, öbürü yine yazılır)."""
    now = now or datetime.now(timezone.utc)
    lines = []
    for name, build in (("sabah satırı (taramalar)", _scan_line), ("sabah satırı (yedek)", _backup_line), ("sabah satırı (sosyal)", _social_line)):
        line = _safe(name, lambda build=build: build(repo, now))
        if line:
            lines.append(line)
    return lines
