"""Sessiz arızayı önleyen izleme: toplayıcı hataları ve bayatlayan/susan kaynaklar için sahibe Telegram uyarısı."""
import os

from application import feed_switch
from application.notify import TelegramError, api
from infrastructure.config import redact
from infrastructure.db.repository import Repository

REPEAT_HOURS = 12  # aynı uyarı en fazla bu aralıkla tekrarlanır


def notify_owner(repo: Repository, key: str, text: str, repeat_hours: int = REPEAT_HOURS) -> bool:
    """Sahibe uyarı yollar; aynı 'key' kısa süre önce gönderildiyse susar. TELEGRAM_* yoksa sessizce atlar."""
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat or repo.alert_recent(key, repeat_hours):
        return False
    try:
        api(token, "sendMessage", chat_id=chat, text=redact(text)[:3500])
    except TelegramError:
        return False
    repo.mark_alerted(key)
    return True


def report_collect_errors(repo: Repository, errors: list[tuple[str, str]]) -> None:
    """Tek seferlik hata sahibe yazılmaz (gürültü): 3 tur üst üste süren arızayı application/source_alarm.py bildirir."""
    for name, message in errors:
        print(f"toplama hatası — {name}: {message[:300]}")


def source_limit_hours(source: dict) -> int:
    """Bu süreyi aşan sessizlik arıza sayılır (çalışma aralığının yaklaşık 3-6 katı; tetikleyici/Actions gecikebilir)."""
    if "kktcarabam" in source["url"]:
        return 14  # 6 saatte bir
    if "sahibindenarabakibris" in source["url"]:
        return 6  # gündüz 60 dk, gece 120 dk
    if source["platform"] == "facebook":
        return 12  # gündüz 2 saatte bir, gece 8 saatte bir
    if source["platform"] == "instagram":
        return 6  # gündüz 30 dk, gece 2 saatte bir
    return 3  # kktcar, kibrisarabaal: 15-30 dakikada bir


def source_problems(repo: Repository) -> list[tuple[str, str]]:
    """(anahtar, mesaj) listesi: uzun süredir kontrol edilmeyen veya haftadır yeni ilan getirmeyen kaynaklar."""
    problems = []
    quiet = feed_switch.quiet_platforms(repo)  # duraklatılmış (ya da yeni devam eden, henüz toplanmamış) sosyal kaynak uyarı vermez
    for s in repo.stale_sources():
        if s["platform"] in quiet:
            continue
        limit_h = source_limit_hours(s)
        if s["hours_since_check"] > limit_h:
            problems.append((f"stale:{s['id']}", f"⏱ {s['name']}: {s['hours_since_check']:.0f} saattir başarılı tarama yok"))
        elif (s["listings_7d"] or 0) == 0 and s["hours_since_check"] < limit_h and _older_than_week(repo, s):
            problems.append((f"silent:{s['id']}", f"🔇 {s['name']}: 7 gündür yeni ilan gelmedi (hesap kapanmış/engellenmiş olabilir)"))
    return problems


def _older_than_week(repo: Repository, source: dict) -> bool:
    return repo.conn.execute("SELECT %s < NOW() - interval '7 days' AS old", (source["created_at"],)).fetchone()["old"]


def check_sources(repo: Repository) -> int:
    sent = 0
    for key, text in source_problems(repo):
        sent += notify_owner(repo, key, "⚠️ Sistem uyarısı\n" + text, repeat_hours=24)
    return sent
