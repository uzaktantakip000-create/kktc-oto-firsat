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
listener_down, listener_up, vps_resources.
"""
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from application.health import notify_owner
from application.runner_gate import SKEW, TICK_FRESH, VPS_BROWSER_KEY, VPS_TICK_KEY, on_github, on_vps

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


def after_tick(repo, beat_written: bool, now: datetime | None = None) -> None:
    """tick.py'nin son adımı (hata yutar). GitHub (atlamadan tarayan tur): sunucu turları durduysa uyarı.
    VPS: kalp atışı yazıldıysa kesinti sonu bildirimi; her VPS turunda (değerlendirme çökse de) dinleyici bekçisi ve disk/bellek kontrolü."""
    now = now or datetime.now(timezone.utc)
    if on_github():
        _safe("sunucu kesintisi uyarısı", lambda: github_failover(repo, TICK, now))
    elif on_vps():
        if beat_written:
            _safe("sunucu dönüş bildirimi", lambda: vps_recovery(repo, TICK, now))
        _safe("dinleyici bekçisi", lambda: listener_watch(repo, now))
        _safe("kaynak izleme", lambda: resource_watch(repo, now))


def after_browser(repo, beat_written: bool, now: datetime | None = None) -> None:
    """cron_collect kktcarabam'ın son adımı (hata yutar): tick'in aynısı, KKTCarabam tarayıcı işi için (uyarı eşiği BROWSER_ALERT_AFTER)."""
    now = now or datetime.now(timezone.utc)
    if on_github():
        _safe("KKTCarabam kesinti uyarısı", lambda: github_failover(repo, BROWSER, now))
    elif on_vps() and beat_written:
        _safe("KKTCarabam dönüş bildirimi", lambda: vps_recovery(repo, BROWSER, now))


# --- sabah mesajına eklenen en çok 2 kısa satır (application/status.build_heartbeat) -----------------------------------------------
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


def morning_lines(repo, now: datetime | None = None) -> list[str]:
    """Sabah mesajına eklenecek 0-2 satır. Hiçbir hata sabah mesajını bozmaz (hata veren satır atlanır, öbürü yine yazılır)."""
    now = now or datetime.now(timezone.utc)
    lines = []
    for name, build in (("sabah satırı (taramalar)", _scan_line), ("sabah satırı (yedek)", _backup_line)):
        line = _safe(name, lambda build=build: build(repo, now))
        if line:
            lines.append(line)
    return lines
