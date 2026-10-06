"""Taramaların VPS'te çalışması, GitHub'ın yedek kalması: kalp atışı kapısı.
VPS (KKTC_RUNNER=vps) her BAŞARILI turdan sonra bot_state'e zaman yazar ('vps_tick_seen' / 'vps_browser_seen'); GitHub (GITHUB_ACTIONS=true)
bu kayıt tazeyken turunu atlar, bayatlayınca (VPS durdu, ağ yok, engellendi) kendiliğinden eskisi gibi çalışır. Her hata GitHub lehine
çözülür: kayıt okunamıyor/bozuk/ileri tarihli ise GitHub taramaya devam eder (iki tarafın üst üste çalışması zararsız: işler veritabanı
saatiyle, değerlendirme kilitle korunur; durmak ise zararlı). Kalp atışı yalnızca başarılı turdan SONRA yazılır, hiç başta değil."""
import os
from datetime import datetime, timedelta, timezone

VPS_TICK_KEY = "vps_tick_seen"  # VPS tick'inin son başarılı turu (UTC ISO); yalnız entrypoints/tick yazar
VPS_BROWSER_KEY = "vps_browser_seen"  # VPS'te KKTCarabam tarayıcı toplamasının son GERÇEK başarısı; yalnız entrypoints/cron_collect yazar
TICK_FRESH = timedelta(minutes=35)  # VPS tick'i 15 dakikada bir: iki tur kaçarsa GitHub devralır (tick.py'nin 45 dk'lık kesinti uyarısından önce)
BROWSER_FRESH = timedelta(minutes=150)  # VPS tarayıcı turu 2 saatte bir: bir tur kaçarsa GitHub devralır
SKEW = timedelta(minutes=5)  # saatler arası fark payı: kayıt bundan fazla "gelecekte" ise güvenilmez


def on_github() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


def on_vps() -> bool:
    return os.environ.get("KKTC_RUNNER") == "vps"


def fresh(seen_iso: str | None, now: datetime, max_age: timedelta) -> bool:
    """Kalp atışı taze mi? Kayıt yok/boş/bozuk/saat dilimsiz ya da SKEW'den fazla ileri tarihli ise False (güvenli taraf: GitHub çalışır)."""
    if not seen_iso:
        return False
    try:
        seen = datetime.fromisoformat(seen_iso)
        if seen.tzinfo is None or now.tzinfo is None:
            return False
        age = now - seen
    except Exception:  # bozuk metin, tür hatası, taşma: hepsi "taze değil"
        return False
    return -SKEW <= age < max_age


def github_should_skip(repo, key: str, max_age: timedelta, now: datetime | None = None) -> bool:
    """GitHub'da çalışırken VPS kalp atışı taze mi? Yalnız GitHub'da True olabilir (VPS'te/yerelde hiç atlanmaz). Okuma hatası: False."""
    if not on_github():
        return False
    try:
        return fresh(repo.get_state(key), now or datetime.now(timezone.utc), max_age)
    except Exception as e:  # veritabanı/okuma sorunu: VPS'e güvenme, GitHub taramaya devam etsin
        print(f"VPS kalp atışı okunamadı ({type(e).__name__}): GitHub taramaya devam ediyor")
        return False


def mark_vps(repo, key: str, now: datetime | None = None) -> None:
    """VPS'te, başarılı turdan sonra kalp atışını yazar; başka yerde hiçbir şey yapmaz. Yazılamazsa tur bozulmaz (GitHub yalnız devralır)."""
    if not on_vps():
        return
    try:
        repo.set_state(key, (now or datetime.now(timezone.utc)).isoformat())
    except Exception as e:
        print(f"VPS kalp atışı yazılamadı ({type(e).__name__})")
