"""Tek zamanlayıcı girişi. Dışarıdan her 15 dakikada tetiklenir (GitHub kendi saati ek yedek).
Her tetiklemede: sırası gelen toplayıcıları çalıştırır, sonra değerlendirir (bildirimler, bot komutları).
Hangi işin ne zaman çalıştığı veritabanında (bot_state 'tick:<iş>') tutulur: iki tetikleme üst üste gelse de iş tekrar etmez."""
import sys
import time
from datetime import datetime, timedelta, timezone

from application import feed_switch, selfwatch, social_import, source_export
from application.health import notify_owner, report_collect_errors
from application.notify import TelegramError, api
from application.runner_gate import TICK_FRESH, VPS_TICK_KEY, github_should_skip, mark_vps
from domain.kktc_time import kktc_hour
from entrypoints import cron_collect, cron_evaluate
from infrastructure.config import load_env, redact, require
from infrastructure.db.repository import Repository
from infrastructure.fx import frankfurter

TOLERANCE = timedelta(minutes=3)  # dış tetikleyici birkaç dakika kayabilir
GAP_ALERT_MIN = 45  # bu kadar dakika hiç çalışma olmazsa sahibine haber verilir
EVAL_STALE_MIN = 45  # tick çalışıyor ama son BAŞARILI değerlendirme bu kadar dakikadan eskiyse sahibine haber verilir (cron_evaluate.EVAL_LAST_KEY)
DAY_KKTC = range(8, 24)  # gündüz = KKTC yerel saatiyle 08:00–24:00 (yaz-kış aynı; ilanlar yerel gündüz saatinde girilir)
# iş -> (gündüz aralığı dk, gece aralığı dk)
SCHEDULE = {
    "kktcar": (15, 30),
    "kibrisarabaal": (15, 30),
    "mezunum": (30, 60),  # küçük site; 3 sn aralıklı nazik tarama
    "kibriscars": (30, 60),  # durgun site (ayda birkaç ilan); 3 sn aralıklı nazik tarama
    "pazarkibris": (30, 60),  # liste sayfası başına tek istek (veri sayfaya gömülü)
    "sahibindenarabakibris": (60, 120),  # durgun site (Mart'tan beri ~4 ilan); GitHub IP'sine 429 veriyordu, tarayıcı parmak izli istemciyle (browserlike) geri döndü
    "instagram": (15, 120),  # özel satıcılar burada; ücret gönderi başına ve imleçle yalnızca yeni gönderi çekilir: sık bakmak ucuz
    "facebook": (120, 480),  # gündüz 2 saatte bir, gece 8 saatte bir (kullanıcı kararı); gönderi başına ücret, aylık tavan collect_facebook'ta
}


SLOW_JOBS = ("facebook", "instagram")  # Apify çalıştırmaları dakikalar sürebilir; seyrek olan (Facebook) önce: zaman payı ona öncelikli
TICK_BUDGET_S = 13 * 60  # tüm tur bu süreyi aşmamalı (iş akışı sınırı 20 dk; 14,4 dk'da iptal olan turlar bu yüzden eklendi)
# Bir yavaş işe başlamak için kalması gereken en az süre (en kötü durum: Apify süre sınırı + bekleme payı + okuma aşaması + değerlendirme).
# Yetmezse iş ATLANIR ve 'tick:<iş>' yazılmaz: bir sonraki tetiklemede (15 dk) çalışır.
MIN_LEFT_S = {"facebook": 10 * 60, "instagram": 6 * 60}
# VPS'teki sosyal okuyucunun (kktc-social) yeni gönderileri yazdığı klasör (kktc-social:kktc-bot 2750). GitHub'da yoktur: aktarıcı boş geçer.
SOCIAL_HANDOFF_DIR = social_import.HANDOFF_DIR


def due_jobs(now: datetime, last_runs: dict[str, datetime | None]) -> list[str]:
    day = kktc_hour(now) in DAY_KKTC
    out = []
    for job, (day_min, night_min) in SCHEDULE.items():
        last = last_runs.get(job)
        every = timedelta(minutes=day_min if day else night_min)
        if last is None or now - last >= every - TOLERANCE:
            out.append(job)
    return out


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def export_sources(repo: Repository) -> None:
    """Sosyal kaynak listesini VPS'teki sosyal okuyucunun dosyasına yazar (yalnız VPS'te; değişiklik yoksa yazmaz). Hata turu bozmaz: okuyucu
    eski dosyayla ya da kendi son iyi listesiyle devam eder."""
    try:
        if source_export.export_social_sources(repo):
            print("sosyal kaynak listesi güncellendi")
    except Exception as e:
        print("sosyal kaynak listesi yazılamadı:", type(e).__name__, redact(str(e))[:100])


def heartbeat_gap(now: datetime, last_tick: datetime | None) -> int | None:
    """Önceki çalışmadan beri geçen dakika; uyarı gerekmiyorsa None."""
    if last_tick is None:
        return None
    minutes = int((now - last_tick).total_seconds() // 60)
    return minutes if GAP_ALERT_MIN <= minutes <= 3 * 24 * 60 else None


def eval_stale_minutes(now: datetime, last_eval: datetime | None, gap: int | None) -> int | None:
    """Son başarılı değerlendirmeden beri geçen dakika; uyarı gerekmiyorsa None. Kesinti uyarısı (gap) zaten verildiyse ya da hiç
    kayıt yoksa (ilk kurulum) uyarı yok; çok uzun duraklama (3 gün+) bilinçli kapatma sayılır."""
    if last_eval is None or gap:
        return None
    minutes = int((now - last_eval).total_seconds() // 60)
    return minutes if EVAL_STALE_MIN <= minutes <= 3 * 24 * 60 else None


def has_time(left_s: float, job: str) -> bool:
    return left_s >= MIN_LEFT_S.get(job, 0)


def run_batch(batch: list[str], repo, now: datetime, started: float, errors: list[tuple[str, str]],
              runner=cron_collect.run, clock=time.monotonic) -> None:
    """İşleri sırayla çalıştırır. `tick:<iş>` YALNIZCA iş bitince (başarı ya da yakalanmış hata) yazılır: tur ortada
    iptal edilirse iş bir sonraki tetiklemede yeniden denenir. Her işin süresi log'a yazılır."""
    for job in batch:
        left = TICK_BUDGET_S - (clock() - started)
        if not has_time(left, job):
            print(f"iş {job}: atlandı (kalan {int(left)} sn < gereken {MIN_LEFT_S[job]} sn), sonraki turda", flush=True)
            continue
        t0 = clock()
        try:
            errors.extend(runner(job, repo))
        except Exception as e:  # bir kaynağın çökmesi diğerlerini ve değerlendirmeyi durdurmasın
            msg = f"{type(e).__name__}: {redact(str(e))[:150]}"  # önce maskele, sonra kırp
            print(f"{job}: HATA {msg}", flush=True)
            errors.append((job, msg))
        repo.set_state(f"tick:{job}", now.isoformat())  # hata olsa da hemen tekrar denenmesin
        print(f"iş {job}: {clock() - t0:.0f} sn", flush=True)


def import_social(repo, errors: list[tuple[str, str]], devir_dir: str = SOCIAL_HANDOFF_DIR) -> None:
    """Sosyal okuyucunun devir klasöründeki yeni Facebook gönderilerini ilan olarak yazar (application/social_import). Gölge/yeşil ayrımı
    sources.alert_level'da; feed:facebook anahtarı eski Apify işini durdurur, bunu değil. Çökerse tur sürer (imleç ilerlemez, satırlar
    bir sonraki turda yeniden gelir)."""
    t0 = time.monotonic()
    try:
        social_import.import_facebook(repo, devir_dir, log=print)
    except Exception as e:
        msg = f"{type(e).__name__}: {redact(str(e))[:150]}"
        print(f"sosyal_devir: HATA {msg}", flush=True)
        errors.append(("sosyal_devir", msg))
    print(f"iş sosyal_devir: {time.monotonic() - t0:.0f} sn", flush=True)


def timed_evaluate(label: str) -> bool:
    """Değerlendirmeyi çalıştırır. Çökerse False döner: turun geri kalanı (yavaş işler, hata raporu) yine çalışır,
    tur sonunda iş akışı hata ile biter (başarılı görünüp sessizce çökmesin)."""
    t0 = time.monotonic()
    try:
        cron_evaluate.main()
        ok = True
    except Exception as e:
        print(f"{label}: HATA {type(e).__name__}: {redact(str(e))[:150]}", flush=True)
        ok = False
    print(f"iş {label}: {time.monotonic() - t0:.0f} sn", flush=True)
    return ok


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)  # iş akışı iptal edilse bile loglar kaybolmasın
    started = time.monotonic()
    load_env()
    repo = Repository(require("DATABASE_URL"))
    selfwatch.note_github_start(repo, selfwatch.GH_TICK_KEY)  # yedek canlı mı: GitHub turu başladı (VPS görüp atlasa da yazılır)
    if github_should_skip(repo, VPS_TICK_KEY, TICK_FRESH):  # VPS turları sağlıklı: GitHub yalnız yedek kalp atışını yazıp çıkar (tick:last yazmaz)
        print("VPS turları çalışıyor: GitHub turu atlandı (VPS durursa en geç 35 dk içinde GitHub devralır)")
        return
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
    stale = eval_stale_minutes(now, _parse(repo.get_state(cron_evaluate.EVAL_LAST_KEY)), gap)
    if stale:
        try:
            notify_owner(repo, "eval_stale", f"⚠️ Son başarılı değerlendirme {stale} dakika önce yapıldı (normalde 15 dakikada bir). "
                                              "Değerlendirme çalışmıyor olabilir; yeni fırsatlar bildirilmeyebilir.", repeat_hours=6)
        except Exception as e:  # uyarı toplamayı engellemesin
            print("değerlendirme uyarısı gönderilemedi:", type(e).__name__)

    jobs = due_jobs(now, {j: _parse(repo.get_state(f"tick:{j}")) for j in SCHEDULE})
    paused = feed_switch.paused_platforms(repo, now)
    if paused:
        print("duraklatılmış sosyal kaynaklar:", ", ".join(f"{p} ({why})" for p, why in paused.items()))
        try:
            feed_switch.note_pauses(repo, paused)  # duraklama bitince ilk toplamaya kadar sahte "taranamıyor" alarmı çıkmasın
            feed_switch.announce_pause(repo, notify_owner, now)  # sahibe tek mesaj (aynı durum için tekrar yazmaz)
        except Exception as e:  # duyuru toplamayı engellemesin
            print("duraklatma duyurusu gönderilemedi:", type(e).__name__)
        jobs = [j for j in jobs if j not in paused]
    print("sırası gelen işler:", jobs or "yok")
    errors: list[tuple[str, str]] = []

    # Hızlı siteler önce toplanıp değerlendirilir: yavaş bir Apify turu site bildirimlerini geciktirmesin
    run_batch([j for j in jobs if j not in SLOW_JOBS], repo, now, started, errors)
    import_social(repo, errors)  # sosyal okuyucunun yeni gönderileri de bu turun değerlendirmesine girsin
    eval_ok = timed_evaluate("değerlendirme")
    slow = [j for j in SLOW_JOBS if j in jobs]
    if slow:
        run_batch(slow, repo, now, started, errors)
        eval_ok = timed_evaluate("değerlendirme (2)") and eval_ok
    report_collect_errors(repo, errors)
    print(f"tur toplam: {time.monotonic() - started:.0f} sn", flush=True)
    beat = mark_vps(repo, VPS_TICK_KEY) if eval_ok else False  # kalp atışı YALNIZ başarılı turdan sonra (VPS'te); hiç başta değil
    selfwatch.after_tick(repo, beat)  # öz-izleme (hata yutar): GitHub'da "sunucu turları durdu" uyarısı, VPS'te "yeniden çalışıyor" bildirimi
    export_sources(repo)
    if not eval_ok:
        sys.exit(1)  # toplama ve raporlar bitti; değerlendirme çöktüyse iş akışı kırmızı olsun


if __name__ == "__main__":
    main()
