"""Değerlendir -> (LLM notu) -> bildir -> bot komutlarını işle."""
import os
from collections import Counter
from datetime import datetime, timedelta, timezone

from application.audit import send_monthly_audit
from application.bot_menu import ensure_menu
from application.bot_poll import listener_running, poll_bot
from application.maintenance import run_maintenance
from application.dedupe import mark_duplicates
from application.digest import send_daily_digest
from application.discovery import send_discovery
from application.estimate_guard import guard_estimates
from application.evaluate import evaluate_new, load_book, pending_alerts
from application.health import check_fx, check_sources, notify_owner
from application.llm_reader import deal_notes
from application.notify import send_alerts
from application.report import send_weekly_report
from application.runner_gate import TICK_FRESH, VPS_TICK_KEY, github_should_skip
from application.send_gate import gonderim_kontrol
from application.settings_store import load_settings
from application.source_alarm import check_source_alarms
from application.source_guard import demote_failing_sources
from application.status import send_morning_status
from domain.profit import Tier
from domain.settings import RULES_VERSION, Settings
from infrastructure.config import load_env, redact, require
from infrastructure.db.repository import DatabaseDown, Repository
from infrastructure.fx import frankfurter
from infrastructure.llm.openrouter import check_deal

LOCK = "evaluate"
EVAL_LAST_KEY = "eval:last"  # son BAŞARILI değerlendirme zamanı: tick başında bayatlarsa sahibe haber (entrypoints/tick.py)
FULL_PASS_MINUTES = 55  # tam değerlendirme/mükerrer taraması saatte bir; aradaki turlar yalnız yeni ilanlara bakar (veritabanı okuma hacmi)
BACKLOG_PASS_MINUTES = 24 * 60 - 5  # hiç değerlendirilemeyen ESKİ ilan birikimi (bildirim üretemez) günde bir, bir tam turla birlikte
EVAL_FULL_KEY, DEDUPE_FULL_KEY, EVAL_BACKLOG_KEY = "eval:full", "dedupe:full", "eval:backlog"


def main(vps_gate: bool = False) -> None:
    """`vps_gate`: yalnız tek başına çalıştırmada (`python -m entrypoints.cron_evaluate`, GitHub'daki collect-browser) açılır: VPS turları
    sağlıklıysa GitHub değerlendirmeyi atlar. tick.py bunu kapıyla ÇAĞIRMAZ (kendi başındaki kapıyı zaten geçti), davranışı değişmez."""
    load_env()
    repo = Repository(require("DATABASE_URL"))
    if vps_gate and github_should_skip(repo, VPS_TICK_KEY, TICK_FRESH):
        print("VPS turları çalışıyor: GitHub değerlendirmesi atlandı (VPS durursa en geç 35 dk içinde GitHub devralır)")
        return
    token = repo.acquire_lock(LOCK)
    if not token:  # başka bir iş akışı şu an değerlendiriyor: çift bildirim olmasın
        print("başka bir değerlendirme çalışıyor, bu tur atlandı")
        return
    try:
        frankfurter.use_store(repo)  # kur servisi çökerse son kayıtlı kur (canlılık kontrolü fiyat çevirir)
        run(repo)
    finally:
        repo.release_lock(LOCK, token)


def apply_rules_version(repo: Repository) -> int:
    """Kural sürümü bot_state'tekinden YENİYSE sürümü yazar ve bir sonraki turu TAM tur yapar: son değerlendirmesi başka sürümle yapılmış
    bildirime aday ilanlar yeniden değerlendirilir (`unevaluated_active(rules_version=...)`). Hiçbir şey SİLİNMEZ (değerlendirmeler
    ekleme-yalnız); eski sürüm satırları kalır ama `pending_strong` onları göndermez. Yalnız İLERİ yazar: yayın anında hâlâ çalışan eski kod
    sürümü geri almasın. Dönen: son değerlendirmesi başka sürümle yapılmış aktif ilan sayısı (bilgi)."""
    stored = repo.get_state("rules_version")
    if stored is not None and stored >= RULES_VERSION:  # aynı ya da daha yeni (sürüm adları tarih+harf: sözlük sırası = zaman sırası)
        return 0
    n = repo.count_stale_rules(RULES_VERSION)
    repo.set_state("rules_version", RULES_VERSION)
    repo.set_state(EVAL_FULL_KEY, "")  # sürüm dalı yalnız tam turda: saatlik turu beklemesin, hemen yenilensin
    repo.set_state(EVAL_BACKLOG_KEY, "")  # eski birikim de yeni kurallarla hemen bir kez denensin (günlük turu beklemesin)
    return n


def full_pass_due(repo: Repository, key: str, now: datetime, minutes: int = FULL_PASS_MINUTES) -> bool:
    """Saatlik TAM tur (ya da `minutes` ile günlük birikim turu) zamanı geldi mi? Kayıt yoksa/okunamazsa True (en güvenli: tam tur)."""
    try:
        raw = repo.get_state(key)
        return not raw or now - datetime.fromisoformat(raw) >= timedelta(minutes=minutes)
    except Exception:
        return True


def mark_full(repo: Repository, key: str, now: datetime) -> None:
    """Tam tur BAŞARIYLA bitti. Yazılamazsa tur bozulmaz (bir sonraki tick yine tam tur dener)."""
    try:
        repo.set_state(key, now.isoformat())
    except Exception as e:
        print(f"{key} yazılamadı:", type(e).__name__)


def mark_evaluated(repo: Repository) -> None:
    """Değerlendirme turu tamamlandı (tek tek ilan hataları olsa da): `eval:last` güncellenir. Yazılamazsa tur bozulmaz."""
    try:
        repo.set_state(EVAL_LAST_KEY, datetime.now(timezone.utc).isoformat())
    except Exception as e:
        print("eval:last yazılamadı:", type(e).__name__)


def report_eval_failures(repo: Repository, failures: list[tuple[str, str]]) -> None:
    """Değerlendirilemeyen ilanlar için sahibe 24 saatte en çok 1 mesaj (ilan içeriği değil, yalnız sayı ve hata türü)."""
    kinds = ", ".join(f"{k} ×{n}" for k, n in Counter(k for _, k in failures).most_common(3))
    try:
        notify_owner(repo, "eval_errors", f"⚠️ {len(failures)} ilan değerlendirilemedi (hata türü: {kinds}). Diğer ilanlar normal değerlendirildi.",
                     repeat_hours=24)
    except Exception as e:
        print("değerlendirme hatası bildirilemedi:", type(e).__name__)


def run(repo: Repository) -> None:
    token = require("TELEGRAM_BOT_TOKEN")
    owner = require("TELEGRAM_CHAT_ID")
    try:
        n = apply_rules_version(repo)
        if n:
            print(f"kural sürümü {RULES_VERSION}: son değerlendirmesi eski sürümle yapılmış {n} aktif ilan (bildirime aday olanlar yenilenecek)")
    except Exception as e:  # sürüm işi değerlendirmeyi engellemesin
        print("kural sürümü uygulanamadı:", type(e).__name__, redact(str(e))[:150])
    try:
        try:
            listening = listener_running(repo)  # VPS'teki anında dinleyici ayakta mı (kalp atışı taze mi)?
        except Exception:  # kalp atışı okunamadı: ayakta saymayız, eskisi gibi yoklarız
            listening = False
        if listening:  # iki getUpdates çakışmasın (409); dinleyici durursa 3 dk içinde bu yol yeniden devreye girer
            print("bot: anında dinleyici çalışıyor, bu turda yoklama atlandı")
        else:
            poll_bot(repo, token, owner_chat_id=owner)  # önce abone onayları ve komutlar
    except Exception as e:  # bot komutları değerlendirmeyi engellemesin
        print("bot güncellemeleri alınamadı:", type(e).__name__, redact(str(e))[:150])
    try:
        ensure_menu(repo, token, owner)  # komut menüsü (bir kez): sahip sohbetine 8 komut, diğer herkese yardim/dur/basla
    except Exception as e:
        print("komut menüsü yazılamadı:", type(e).__name__, redact(str(e))[:150])

    now = datetime.now(timezone.utc)
    try:
        repo.expire_unverifiable()
        released = repo.release_orphan_duplicates()  # kanoniği pasifleşen aktif kopya ya da farklı modele bağlı yanlış kopya serbest kalır
        if released:
            print(f"mükerrer: {released} ilan serbest bırakıldı (kanoniği pasifleşmiş ya da farklı model)")
        dedupe_full = full_pass_due(repo, DEDUPE_FULL_KEY, now)
        mark_duplicates(repo, quick=not dedupe_full, now=now)  # saatte bir tam tarama, arada yalnız yeni ilanın değdiği gruplar
        if dedupe_full:
            mark_full(repo, DEDUPE_FULL_KEY, now)
    except Exception as e:  # mükerrer işaretleme hatası değerlendirmeyi engellemesin
        print("mükerrer işaretleme başarısız:", type(e).__name__, redact(str(e))[:150])
    try:
        phones, handles, texts = repo.purge_personal_data()  # saklama politikası: pasif ilanda telefon 90, satıcı adı 150, metin 180 gün
        if phones or handles or texts:
            print(f"saklama temizliği: telefon={phones} satıcı_adı={handles} metin={texts}")
    except Exception as e:
        print("saklama temizliği başarısız:", type(e).__name__, redact(str(e))[:150])
    settings = load_settings(repo)  # kullanıcının Telegram'dan verdiği kararlar
    try:
        run_maintenance(repo)  # geceleri günde bir: şüpheli ilanlar karantinaya (emsalden/bildirimden çıkar)
    except Exception as e:
        print("gece bakımı başarısız:", type(e).__name__, redact(str(e))[:150])
    try:
        from application.price_book_job import run_price_book  # gece değer tablosu (kendi içinde günde bire sınırlı)
        run_price_book(repo)
    except ImportError:
        pass
    except Exception as e:  # tablo kurulamasa da değerlendirme sürer (eski tablo/emsal yolu)
        print("değer tablosu kurulamadı:", type(e).__name__, redact(str(e))[:150])
    book = load_book(repo) if settings.estimated_alerts else None  # tek kez yüklenir
    failures: list[tuple[str, str]] = []
    eval_error: Exception | None = None
    eval_full = full_pass_due(repo, EVAL_FULL_KEY, now)
    # Tam turda bildirim üretebilecek her ilan (yeni / fiyatı değişmiş / sürümü eski aday / 3 günlük yeniden bakış) her saat değerlendirilir;
    # hiç değerlendirilemeyen ESKİ birikim (ilk görülme ve fiyat değişikliği >72 saat: tazelik kapısı yüzünden bildirim üretemez) günde bir.
    eval_backlog = eval_full and full_pass_due(repo, EVAL_BACKLOG_KEY, now, BACKLOG_PASS_MINUTES)
    try:
        evaluated = evaluate_new(repo, settings, book=book, failures=failures, quick=not eval_full, backlog=eval_backlog)
        mark_evaluated(repo)
        if eval_full:
            mark_full(repo, EVAL_FULL_KEY, now)
        if eval_backlog:
            mark_full(repo, EVAL_BACKLOG_KEY, now)
    except DatabaseDown:
        raise  # veritabanı yoksa yan işler de çalışamaz: tur zaten hata verir
    except Exception as e:  # turun çoğu patladı: yan işler (alarm, rapor) yine çalışsın, tur sonunda hata verilir
        eval_error, evaluated = e, []
        print("değerlendirme turu başarısız:", type(e).__name__, redact(str(e))[:150])
    if failures:
        report_eval_failures(repo, failures)
    # Yeni 🟢'ler + önceki turlarda gönderilemeyenler (hata, hız sınırı, sonradan onaylanan abone). Gönderim kontrolü (send_gate):
    # tazelik → emsal ≥ 8 → sitede canlı mı → yapay zekâ okuması. 🟢 yolu try DIŞINDA (eskisi gibi): hata turu durdurur
    strong, _ = gonderim_kontrol(repo, pending_alerts(repo, book=book), "🟢")

    notes = {}
    key, model = os.environ.get("OPENROUTER_API_KEY"), os.environ.get("OPENROUTER_MODEL")
    if key and model:  # fırsat notu: okuyucuyla aynı günlük bütçe, (ilan, fiyat) başına tek soru (application/llm_reader.deal_notes)
        try:
            notes = deal_notes(repo, strong, key, model, call=check_deal)
        except Exception as e:  # not alınamazsa bildirim notsuz gider: gönderim bu nota hiçbir durumda bağlı değil
            print("LLM notu alınamadı:", type(e).__name__)
    sent = send_alerts(repo, token, strong, notes)
    est_sent = 0
    if settings.estimated_alerts and book is not None:  # 🟠 tahmini fırsat: ayrı gönderim (tablo yoksa hiç çıkmaz)
        try:
            # aynı gönderim kontrolü; 🟠 doğrudan emsal < 8 iken doğar: dürüst 🟠 KONTROL ET gelene kadar emsal kapısında kalır
            est, _ = gonderim_kontrol(repo, pending_alerts(repo, tier=Tier.ESTIMATED, book=book), "🟠")
            est_sent = send_alerts(repo, token, est, tier=Tier.ESTIMATED, s=settings)
        except Exception as e:  # 🟠 hatası özet/rapor gibi yan işleri engellemesin
            print("tahmini fırsat gönderimi başarısız:", type(e).__name__, redact(str(e))[:150])
    for name, job in (("kaynak düşürme", lambda: demote_failing_sources(repo)),
                      ("tahmini öğrenme", lambda: guard_estimates(repo)),
                      ("özet", lambda: send_daily_digest(repo, token)),  # 🟡 özet kapalı (digest.ENABLED)
                      ("sabah durumu", lambda: send_morning_status(repo)),
                      ("keşif", lambda: send_discovery(repo, token, owner)),
                      ("denetim", lambda: send_monthly_audit(repo, token, owner)),
                      ("kaynak alarmı", lambda: check_source_alarms(repo)),  # sağlık uyarısından önce: aynı arıza ikinci kez yazılmasın
                      ("kur izleme", lambda: check_fx(repo)),
                      ("kaynak sağlığı", lambda: (check_sources(repo), send_weekly_report(repo)))):
        try:
            job()
        except Exception as e:  # yan işler ana bildirimi bozmasın
            print(f"{name} başarısız:", type(e).__name__, redact(str(e))[:150])
    print(f"değerlendirilen={len(evaluated)} güçlü={len(strong)} bildirilen={sent} tahmini_bildirilen={est_sent}")
    if eval_error is not None:
        raise eval_error  # iş akışı "başarılı" görünmesin; yan işler ve bildirimler yukarıda zaten çalıştı


if __name__ == "__main__":
    main(vps_gate=True)
