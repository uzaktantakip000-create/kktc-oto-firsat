"""Tek zamanlayıcı girişi. Dışarıdan her 15 dakikada tetiklenir (GitHub kendi saati ek yedek).
Her tetiklemede: sırası gelen toplayıcıları çalıştırır, sonra değerlendirir (bildirimler, bot komutları).
Hangi işin ne zaman çalıştığı veritabanında (bot_state 'tick:<iş>') tutulur: iki tetikleme üst üste gelse de iş tekrar etmez."""
from datetime import datetime, timedelta, timezone

from application.health import report_collect_errors
from application.notify import TelegramError, api
from entrypoints import cron_collect, cron_evaluate
from infrastructure.config import load_env, redact, require
from infrastructure.db.repository import Repository
from infrastructure.fx import frankfurter

TOLERANCE = timedelta(minutes=3)  # dış tetikleyici birkaç dakika kayabilir
GAP_ALERT_MIN = 45  # bu kadar dakika hiç çalışma olmazsa sahibine haber verilir
DAY_UTC = range(5, 21)  # KKTC 08:00–24:00
# iş -> (gündüz aralığı dk, gece aralığı dk)
SCHEDULE = {
    "kktcar": (15, 30),
    "kibrisarabaal": (15, 30),
    "mezunum": (30, 60),  # küçük site; 3 sn aralıklı nazik tarama
    "instagram": (15, 120),  # özel satıcılar burada; ücret gönderi başına ve imleçle yalnızca yeni gönderi çekilir: sık bakmak ucuz
    "facebook": (120, 480),  # gündüz 2 saatte bir, gece 8 saatte bir (kullanıcı kararı); gönderi başına ücret, aylık tavan collect_facebook'ta
}


SLOW_JOBS = ("instagram", "facebook")  # Apify çalıştırmaları dakikalar sürebilir


def due_jobs(now: datetime, last_runs: dict[str, datetime | None]) -> list[str]:
    day = now.hour in DAY_UTC
    out = []
    for job, (day_min, night_min) in SCHEDULE.items():
        last = last_runs.get(job)
        every = timedelta(minutes=day_min if day else night_min)
        if last is None or now - last >= every - TOLERANCE:
            out.append(job)
    return out


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def heartbeat_gap(now: datetime, last_tick: datetime | None) -> int | None:
    """Önceki çalışmadan beri geçen dakika; uyarı gerekmiyorsa None."""
    if last_tick is None:
        return None
    minutes = int((now - last_tick).total_seconds() // 60)
    return minutes if GAP_ALERT_MIN <= minutes <= 3 * 24 * 60 else None


def main() -> None:
    load_env()
    repo = Repository(require("DATABASE_URL"))
    frankfurter.use_store(repo)
    now = datetime.now(timezone.utc)

    gap = heartbeat_gap(now, _parse(repo.get_state("tick:last")))
    if gap:
        try:
            api(require("TELEGRAM_BOT_TOKEN"), "sendMessage", chat_id=require("TELEGRAM_CHAT_ID"),
                text=f"⚠️ Sistem {gap} dakika boyunca çalışmadı (zamanlayıcı kesintisi). Şimdi yeniden devrede.")
        except TelegramError as e:
            print("kesinti uyarısı gönderilemedi:", e.status)
    repo.set_state("tick:last", now.isoformat())

    jobs = due_jobs(now, {j: _parse(repo.get_state(f"tick:{j}")) for j in SCHEDULE})
    print("sırası gelen işler:", jobs or "yok")
    errors: list[tuple[str, str]] = []

    def run_jobs(batch: list[str]) -> None:
        for job in batch:
            repo.set_state(f"tick:{job}", now.isoformat())  # hata olsa da hemen tekrar denenmesin
            try:
                errors.extend(cron_collect.run(job, repo))
            except Exception as e:  # bir kaynağın çökmesi diğerlerini ve değerlendirmeyi durdurmasın
                msg = redact(f"{type(e).__name__}: {str(e)[:150]}")
                print(f"{job}: HATA {msg}")
                errors.append((job, msg))

    # Hızlı siteler önce toplanıp değerlendirilir: yavaş bir Apify turu site bildirimlerini geciktirmesin
    run_jobs([j for j in jobs if j not in SLOW_JOBS])
    cron_evaluate.main()
    slow = [j for j in jobs if j in SLOW_JOBS]
    if slow:
        run_jobs(slow)
        cron_evaluate.main()
    report_collect_errors(repo, errors)


if __name__ == "__main__":
    main()
