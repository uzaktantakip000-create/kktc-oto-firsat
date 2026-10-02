"""Anlık kaynak alarmı: bir kaynak okunamaz hale gelince sabahı/haftayı beklemeden sahibe tek mesaj, düzelince tek mesaj.
Sayaç: bot_state 'fail:<kaynak adı>' = üst üste hatalı tur sayısı (başarılı turda sıfırlanır), 'failmsg:<ad>' = son hata,
'srcalarm:<ad>' = alarm verildi (düzelme mesajı bekliyor). Alarm koşulu: kaynak 'aktif' ve (3 tur üst üste hata VEYA
normal sınırdan uzun süredir başarılı tarama yok)."""
import re

from application import feed_switch
from application.health import notify_owner, source_limit_hours
from infrastructure.config import redact
from infrastructure.db.repository import Repository

FAIL_LIMIT = 3
COLLECTIVE = ("Instagram (toplu)", "Facebook grupları")  # tek kaynak değil, toplu çalıştırma adları (cron_collect)
ALARM_REPEAT_HOURS = 48


def track_collect(repo: Repository, name: str, error: str | None = None) -> None:
    """Toplayıcı turunun sonucunu kaydeder: hata -> sayaç +1, başarı -> sıfır. Asla toplamayı bozmaz."""
    try:
        if error is None:
            if repo.get_state(f"fail:{name}", "0") not in ("0", "", None):
                repo.set_state(f"fail:{name}", "0")
            return
        try:
            n = int(repo.get_state(f"fail:{name}", "0") or 0)
        except ValueError:
            n = 0
        repo.set_state(f"fail:{name}", str(n + 1))
        repo.set_state(f"failmsg:{name}", redact(error)[:200])
    except Exception as e:  # sayaç hatası toplamayı etkilemesin
        print("hata sayacı yazılamadı:", type(e).__name__)


def _error_word(msg: str | None) -> str:
    """Sahibe kısa neden: HTTP kodu varsa o ('403'), yoksa hata türü."""
    m = re.search(r"\b([45]\d\d)\b", msg or "")
    if m:
        return m.group(1)
    return (msg or "bilinmiyor").split(":")[0][:40] or "bilinmiyor"


def _count(v: str | None) -> int:
    try:
        return int(v or 0)
    except ValueError:
        return 0


def source_alarms(repo: Repository, sources: dict[str, dict],
                  skip: frozenset[str] = frozenset()) -> tuple[list[tuple[str, str]], set[str]]:
    """([(ad, mesaj)] alarm verilecekler, sorunsuz çalışan adlar). Kendi başına mesaj göndermez. sources: ad -> kaynak satırı.
    skip: duraklatılmış kaynak/toplu iş adları; ne alarm ne "tekrar çalışıyor" üretir."""
    fails = repo.state_with_prefix("fail:")
    msgs = repo.state_with_prefix("failmsg:")
    bad: dict[str, str] = {}
    for name in sorted(set(fails) | set(sources)):
        if name in skip:
            continue
        s = sources.get(name)
        if s is None and name not in COLLECTIVE:
            continue  # kapalı/aday kaynağın eski sayacı
        n = _count(fails.get(name))
        if n >= FAIL_LIMIT:
            bad[name] = f"{name} {n} turdur okunamıyor (hata: {_error_word(msgs.get(name))})."
        elif s is not None and s["hours_since_check"] is not None and s["hours_since_check"] > source_limit_hours(s):
            bad[name] = f"{name} {s['hours_since_check']:.0f} saattir taranamıyor."
    ok = (set(sources) | set(COLLECTIVE)) - set(bad) - skip
    alarms = []
    for name, text in bad.items():
        others = len(bad) - 1
        tail = "Diğer kaynaklar çalışıyor." if not others else f"{others} kaynakta daha sorun var."
        alarms.append((name, f"⚠️ {text} {tail}"))
    return alarms, ok


def check_source_alarms(repo: Repository) -> int:
    """Her tur çalışır. Gönderilen mesaj sayısını döner (alarm + düzelme)."""
    paused = feed_switch.paused_platforms(repo)  # duraklatılmış sosyal kaynaklar arıza sayılmaz
    quiet = feed_switch.quiet_platforms(repo)  # + duraklamadan yeni çıkıp henüz toplanmamışlar: kaynak başına "taranamıyor" uyarısı yok
    sources = {s["name"]: s for s in repo.alarm_sources() if s["platform"] not in quiet}
    alarms, ok = source_alarms(repo, sources, skip=frozenset(feed_switch.COLLECTIVE[p] for p in paused))
    sent = 0
    for name, text in alarms:
        if repo.get_state(f"srcalarm:{name}"):
            continue  # tek mesaj: aynı arıza için tekrar yazmam (düzelince haber veririm)
        if notify_owner(repo, f"src_alarm:{name}", text, repeat_hours=ALARM_REPEAT_HOURS):
            repo.set_state(f"srcalarm:{name}", "1")
            if name in sources:
                repo.mark_alerted(f"stale:{sources[name]['id']}")  # eski "saattir tarama yok" uyarısı aynı şeyi ikinci kez yazmasın
            sent += 1
    for name in sorted(ok):
        if repo.get_state(f"srcalarm:{name}") and notify_owner(repo, f"src_ok:{name}", f"✅ {name} tekrar çalışıyor.", repeat_hours=1):
            repo.set_state(f"srcalarm:{name}", "")
            sent += 1
    return sent
