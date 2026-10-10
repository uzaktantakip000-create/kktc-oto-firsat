"""Sessiz arızayı önleyen izleme: toplayıcı hataları ve bayatlayan/susan kaynaklar için sahibe Telegram uyarısı."""
import os
from datetime import datetime, timedelta, timezone

from application import feed_switch
from application.notify import TelegramError, api
from domain.kktc_time import kktc_hour, to_kktc
from infrastructure.config import redact
from infrastructure.db.repository import Repository

REPEAT_HOURS = 12  # aynı uyarı en fazla bu aralıkla tekrarlanır
FX_FALLBACK_ALERT_HOURS = 24  # döviz servisi bu kadar saattir yanıt vermeyip yedek kur kullanılıyorsa sahibe haber


def notify_owner(repo: Repository, key: str, text: str, repeat_hours: int = REPEAT_HOURS, max_chars: int = 3500, **extra) -> bool:
    """Sahibe uyarı yollar; aynı 'key' kısa süre önce gönderildiyse susar. TELEGRAM_* yoksa sessizce atlar.
    `max_chars`: güvenlik kesimi (Telegram sınırı 4096; kendi boyunu ölçen rapor daha yüksek verebilir).
    `extra`: sendMessage'e aynen geçen alanlar (ör. reply_markup düğmeleri, disable_web_page_preview)."""
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat or repo.alert_recent(key, repeat_hours):
        return False
    try:
        api(token, "sendMessage", chat_id=chat, text=redact(text)[:max_chars], **extra)
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
        return 8  # 2 saatte bir (cron-job.org dispatch; 04.10.2026 20:01'den beri düzenli doğrulandı); gecikme payı ~4 tur
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


VOLUME_MIN_PER_DAY = 30  # hacim alarmı yalnız günde ortalama en az bu kadar yeni ilan getiren kaynakta (KibrisArabaAl ~90, KKTCarabam ~55; KKTCar ~6: hariç)
VOLUME_SILENT = timedelta(hours=6)  # bu kaynaklarda gündüz 6 saat hiç yeni ilan yoksa toplayıcı sessizce ilan kaçırıyor olabilir
VOLUME_DAY_KKTC = (10, 22)  # yalnız bu KKTC saatlerinde bakılır
VOLUME_DAY_START = 8  # sessizlik en erken KKTC 08:00'den sayılır: gece boşluğu sayılmaz (geçen haftaya karşı: yalnız 09.10 gerçek gecikmesinde çalardı)


def volume_problems(repo: Repository, now: datetime | None = None) -> list[tuple[str, str]]:
    """Kapsam departmanı (10.10.2026): tarama "başarılı" görünür ama yeni ilan gelmez (KKTCar'da site haritası değişince olduğu gibi). Çok ilan
    getiren kaynakta gündüz VOLUME_SILENT boyunca hiç yeni ilan yoksa sahibe uyarı. Tarama hiç yapılmıyorsa `source_problems` zaten söyler."""
    now = now or datetime.now(timezone.utc)
    if not VOLUME_DAY_KKTC[0] <= kktc_hour(now) < VOLUME_DAY_KKTC[1]:
        return []
    day_start = to_kktc(now).replace(hour=VOLUME_DAY_START, minute=0, second=0, microsecond=0)
    out = []
    for s in repo.source_volume():
        last, per_day = s["last_new"], s["per_day"] or 0
        if (repo.get_state(f"fail:{s['name']}", "0") or "0") != "0":  # tarama hata veriyor: kaynak alarmı (source_alarm) söyler; burada
            continue  # "tarama çalışıyor görünüyor" demek yanlış olur (10.10.2026: KibrisArabaAl Cloudflare 403)
        if per_day >= VOLUME_MIN_PER_DAY and last is not None and now - max(last, day_start) > VOLUME_SILENT:
            hours = (now - last).total_seconds() / 3600
            out.append((f"volume:{s['id']}", f"📉 {s['name']}: {hours:.0f} saattir hiç yeni ilan gelmedi (normalde günde ~{per_day:.0f}). "
                        "Tarama çalışıyor görünüyor ama site düzeni değişmiş, ilanlar kaçıyor olabilir."))
    return out


def check_sources(repo: Repository) -> int:
    sent = 0
    for key, text in source_problems(repo) + volume_problems(repo):
        sent += notify_owner(repo, key, "⚠️ Sistem uyarısı\n" + text, repeat_hours=24)
    return sent


def check_fx(repo: Repository, now: datetime | None = None) -> int:
    """Döviz kuru servisi (Frankfurter) 24 saatten uzun süredir yanıt vermiyor ve son bilinen kur kullanılıyorsa sahibe tek mesaj
    (TL/EUR fiyatlı ilanların £ karşılığı güncel olmayabilir). Gönderilen mesaj sayısını döner."""
    now = now or datetime.now(timezone.utc)
    sent = 0
    for currency, since in repo.state_with_prefix("fx:fallback:").items():
        try:
            started = datetime.fromisoformat(since) if since else None
        except ValueError:
            continue
        if started is None or now - started < timedelta(hours=FX_FALLBACK_ALERT_HOURS):
            continue
        hours = int((now - started).total_seconds() // 3600)
        sent += notify_owner(repo, f"fx_stale:{currency}",
                             f"⚠️ Döviz kuru servisi {hours} saattir yanıt vermiyor; {currency} için son bilinen kur kullanılıyor. "
                             f"{currency} fiyatlı ilanların £ karşılığı güncel olmayabilir.", repeat_hours=24)
    return sent
