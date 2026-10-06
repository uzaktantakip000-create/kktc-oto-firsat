"""Kendi kendini izleme: sistem kendi sorununu fark edip sahibe BİR kez haber versin (gözetimsiz çalışabilsin).
Taramalar VPS'te, GitHub yedek (application/runner_gate.py). GitHub sessizce durabilir (dış tetikleyicinin anahtarı bitmiş olabilir; GitHub 60 gün
depo hareketsizliğinden sonra zamanlanmış işleri kapatır): bu yüzden yedeğin de canlı olduğu görülür.
KURAL: hiçbir kontrol taramayı çökertemez ya da bekletemez, neyin taranacağını/değerlendirileceğini/fırsat olarak gönderileceğini DEĞİŞTİRMEZ.
Her giriş noktası kendi hatasını yutar; log'a yalnız hata TÜRÜ yazılır (hata metni bağlantı adresi/sır taşıyabilir).

bot_state anahtarları (hepsi UTC ISO zaman; yazılmamış ya da "" = yok):
  vps_tick_seen, vps_browser_seen  VPS'in son BAŞARILI turu / KKTCarabam başarısı            (runner_gate; VPS yazar)
  gh_tick_seen, gh_browser_seen    GitHub tick / collect-browser çalışmasının BAŞLADIĞI an       (burada; atlasa da yazar)
"""
from datetime import datetime, timezone

from application.runner_gate import on_github

GH_TICK_KEY = "gh_tick_seen"
GH_BROWSER_KEY = "gh_browser_seen"


def _safe(name: str, fn):
    """Kontrolü çalıştırır; hata verirse tur bozulmaz, yalnız hata türü yazılır."""
    try:
        return fn()
    except Exception as e:
        print(f"öz-izleme ({name}) başarısız: {type(e).__name__}")


def note_github_start(repo, key: str, now: datetime | None = None) -> None:
    """GitHub'da, tick/collect-browser çalışması BAŞLARKEN (VPS turu görüp atlayacak olsa bile) yedeğin canlı olduğunu yazar:
    `gh_tick_seen` / `gh_browser_seen`. GitHub dışında hiçbir şey yapmaz; yazılamazsa tur etkilenmez."""
    if not on_github():
        return
    _safe("yedek kalp atışı", lambda: repo.set_state(key, (now or datetime.now(timezone.utc)).isoformat()))
