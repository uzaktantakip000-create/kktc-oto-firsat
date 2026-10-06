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
uyarı tekrar sınırı health.notify_owner'ın kendi `alert:<ad>` kayıtlarıdır (ad: vps_failover, vps_recovered, vps_browser_failover, ...).
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from application.health import notify_owner
from application.runner_gate import SKEW, TICK_FRESH, VPS_BROWSER_KEY, VPS_TICK_KEY, on_github, on_vps

GH_TICK_KEY = "gh_tick_seen"
GH_BROWSER_KEY = "gh_browser_seen"
FO_TICK_KEY = "fo_tick"
FO_BROWSER_KEY = "fo_browser"

ALERT_REPEAT_H = 12  # aynı uyarı en erken bu kadar saat sonra tekrar yazılır
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


def after_tick(repo, beat_written: bool, now: datetime | None = None) -> None:
    """tick.py'nin son adımı (hata yutar). GitHub (atlamadan tarayan tur): sunucu turları durduysa uyarı. VPS: kalp atışı yazıldıysa kesinti sonu bildirimi."""
    now = now or datetime.now(timezone.utc)
    if on_github():
        _safe("sunucu kesintisi uyarısı", lambda: github_failover(repo, TICK, now))
    elif on_vps() and beat_written:
        _safe("sunucu dönüş bildirimi", lambda: vps_recovery(repo, TICK, now))


def after_browser(repo, beat_written: bool, now: datetime | None = None) -> None:
    """cron_collect kktcarabam'ın son adımı (hata yutar): tick'in aynısı, KKTCarabam tarayıcı işi için (uyarı eşiği BROWSER_ALERT_AFTER)."""
    now = now or datetime.now(timezone.utc)
    if on_github():
        _safe("KKTCarabam kesinti uyarısı", lambda: github_failover(repo, BROWSER, now))
    elif on_vps() and beat_written:
        _safe("KKTCarabam dönüş bildirimi", lambda: vps_recovery(repo, BROWSER, now))
