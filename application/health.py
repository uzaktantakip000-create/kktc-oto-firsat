"""Sessiz arızayı önleyen izleme: toplayıcı hataları ve bayatlayan/susan kaynaklar için sahibe Telegram uyarısı."""
import os

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
    for name, message in errors:
        notify_owner(repo, f"collect:{name}", f"⚠️ Toplama hatası — {name}\n{message[:300]}", repeat_hours=6)


def _limit_hours(source: dict) -> int:
    """Bu süreyi aşan sessizlik arıza sayılır (çalışma aralığının yaklaşık 2,5 katı; Actions gecikebilir)."""
    if "kktcarabam" in source["url"]:
        return 14  # 6 saatte bir
    if source["platform"] == "instagram":
        return 10  # 4 saatte bir
    return 6  # kktcar, 2 saatte bir


def source_problems(repo: Repository) -> list[tuple[str, str]]:
    """(anahtar, mesaj) listesi: uzun süredir kontrol edilmeyen veya haftadır yeni ilan getirmeyen kaynaklar."""
    problems = []
    for s in repo.stale_sources():
        limit_h = _limit_hours(s)
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
