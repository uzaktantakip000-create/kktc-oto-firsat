"""Değerlendir -> (LLM notu) -> bildir -> bot komutlarını işle."""
import os

from application.audit import send_monthly_audit
from application.bot_poll import poll_bot
from application.maintenance import run_maintenance
from application.dedupe import mark_duplicates
from application.digest import send_daily_digest
from application.discovery import send_discovery
from application.estimate_guard import guard_estimates
from application.evaluate import evaluate_new, load_book, pending_alerts
from application.health import check_sources
from application import llm_reader
from application.liveness import recheck_before_send
from application.notify import is_fresh, send_alerts
from application.report import send_weekly_report
from application.settings_store import load_settings
from application.source_alarm import check_source_alarms
from application.source_guard import demote_failing_sources
from application.status import send_morning_status
from domain.comparables import nearest_comparables
from domain.profit import Tier
from domain.settings import Settings
from infrastructure.config import load_env, redact, require
from infrastructure.db.repository import Repository
from infrastructure.fx import frankfurter
from infrastructure.llm.openrouter import check_deal

LOCK = "evaluate"


def main() -> None:
    load_env()
    repo = Repository(require("DATABASE_URL"))
    token = repo.acquire_lock(LOCK)
    if not token:  # başka bir iş akışı şu an değerlendiriyor: çift bildirim olmasın
        print("başka bir değerlendirme çalışıyor, bu tur atlandı")
        return
    try:
        frankfurter.use_store(repo)  # kur servisi çökerse son kayıtlı kur (canlılık kontrolü fiyat çevirir)
        run(repo)
    finally:
        repo.release_lock(LOCK, token)


def run(repo: Repository) -> None:
    token = require("TELEGRAM_BOT_TOKEN")
    owner = require("TELEGRAM_CHAT_ID")
    try:
        poll_bot(repo, token, owner_chat_id=owner)  # önce abone onayları ve komutlar
    except Exception as e:  # bot komutları değerlendirmeyi engellemesin
        print("bot güncellemeleri alınamadı:", type(e).__name__, redact(str(e))[:150])

    try:
        repo.expire_unverifiable()
        mark_duplicates(repo)
    except Exception as e:  # mükerrer işaretleme hatası değerlendirmeyi engellemesin
        print("mükerrer işaretleme başarısız:", type(e).__name__, redact(str(e))[:150])
    try:
        phones, texts = repo.purge_personal_data()  # saklama politikası: pasif ilanda telefon 90 gün, metin 180 gün
        if phones or texts:
            print(f"saklama temizliği: telefon={phones} metin={texts}")
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
    evaluated = evaluate_new(repo, settings, book=book)
    # Yeni 🟢'ler + önceki turlarda gönderilemeyenler (hata, hız sınırı, sonradan onaylanan abone)
    strong = [ev for ev in pending_alerts(repo, book=book)
              if is_fresh(ev.listing["first_seen_at"], ev.listing["posted_at"], price_changed_at=ev.listing.get("price_changed_at"),
                      platform=ev.listing.get("platform"))]
    strong = recheck_before_send(repo, strong)  # satılmış/fiyatı değişmiş ilan gönderilmez
    strong = llm_reader.verify_candidates(repo, llm_reader.from_env(repo), strong)  # sosyal medya 🟢'sini bağımsız okut; uyuşmazsa 🟡

    comps = {}
    if strong:
        s = settings
        pool = repo.market_pool(days=s.comparable_window_days + 30)
        for ev in strong:
            comps[ev.listing["id"]] = nearest_comparables(ev.listing, pool, ev.market, 3, s)

    notes = {}
    key, model = os.environ.get("OPENROUTER_API_KEY"), os.environ.get("OPENROUTER_MODEL")
    if key and model:
        for ev in strong:
            l, m = ev.listing, ev.market
            summary = f"Emsal: {m.n} ilan, medyan £{m.median_gbp:.0f}, aralık £{m.low_gbp:.0f}–£{m.high_gbp:.0f}"
            try:
                notes[l["id"]] = check_deal(key, model, (l["raw_text"] or "")[:1500], summary)
            except Exception as e:  # LLM hatası bildirimi engellemesin
                print("LLM notu alınamadı:", type(e).__name__)
    sent = send_alerts(repo, token, strong, notes, comps=comps)
    est_sent = 0
    if settings.estimated_alerts and book is not None:  # 🟠 tahmini fırsat: ayrı gönderim (tablo yoksa hiç çıkmaz)
        try:
            est = [ev for ev in pending_alerts(repo, tier=Tier.ESTIMATED, book=book)
                   if is_fresh(ev.listing["first_seen_at"], ev.listing["posted_at"], price_changed_at=ev.listing.get("price_changed_at"),
                               platform=ev.listing.get("platform"))]
            est = recheck_before_send(repo, est)
            est = llm_reader.verify_candidates(repo, llm_reader.from_env(repo), est)  # her kaynakta ikinci okuma + gizli sorun kontrolü
            est_sent = send_alerts(repo, token, est, tier=Tier.ESTIMATED, s=settings)
        except Exception as e:  # 🟠 hatası özet/rapor gibi yan işleri engellemesin
            print("tahmini fırsat gönderimi başarısız:", type(e).__name__, redact(str(e))[:150])
    for name, job in (("kaynak düşürme", lambda: demote_failing_sources(repo)),
                      ("tahmini öğrenme", lambda: guard_estimates(repo)),
                      ("özet", lambda: send_daily_digest(repo, token)),
                      ("sabah durumu", lambda: send_morning_status(repo)),
                      ("keşif", lambda: send_discovery(repo, token, owner)),
                      ("denetim", lambda: send_monthly_audit(repo, token, owner)),
                      ("kaynak alarmı", lambda: check_source_alarms(repo)),  # sağlık uyarısından önce: aynı arıza ikinci kez yazılmasın
                      ("kaynak sağlığı", lambda: (check_sources(repo), send_weekly_report(repo)))):
        try:
            job()
        except Exception as e:  # yan işler ana bildirimi bozmasın
            print(f"{name} başarısız:", type(e).__name__, redact(str(e))[:150])
    print(f"değerlendirilen={len(evaluated)} güçlü={len(strong)} bildirilen={sent} tahmini_bildirilen={est_sent}")


if __name__ == "__main__":
    main()
