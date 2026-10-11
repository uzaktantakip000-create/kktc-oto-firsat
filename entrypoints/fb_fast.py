"""⚡ Facebook hızlı şerit (11.10.2026; sahip: "insanlar acil satıyor… en erken bulan kazanır"). Sosyal oturumla ortak tasarım: okuyucu yeni
gönderiyi devir klasörüne yazınca (VPS: kktc-fb-fast.path, PathChanged=/var/lib/kktc-social-devir) bu giriş çalışır; Facebook ilanı 15
dakikalık turu beklemez. Okuyucunun "açık sekme" denemesiyle (sahip onayı 11.10, deneme 14–16.10) hedef: gönderi paylaşıldıktan en geç 15 dk
sonra sahibin telefonunda.
Adımlar: SETTLE_S bekle (okuyucu bir grubun satırlarını ~1 sn içinde art arda yazar, her satır ayrı kapanış = ayrı tetik: tek çalışmada
toplansın) → devir aktarımı (turla ortak kilit, application/social_import.import_facebook_locked) → yeni ilan yoksa dur → değerlendirme
kilidi (cron_evaluate.LOCK; tur değerlendiriyorsa en çok LOCK_WAIT_S bekler, alamazsa durur: ilanları tur işler) → yalnız az önce aktarılan
ilanların (marka, model, yıl) gruplarında mükerrer taraması (aynı aracın başka gruptaki paylaşımı iki kez gitmesin) → yalnız bu ilanların
değerlendirmesi → gönderim (cron_evaluate.deliver: 🟢/🟠 yalnız Facebook, ⚡ ACİL SATICI) + 🖼 fotoğrafta fiyat + 🎯 av. Çalışırken yeni
satır geldiyse en çok MAX_ROUNDS tur döner (systemd çalışan birimi yeniden başlatmaz).
Turun yan işleri (bakım, raporlar, sağlık, tam taramalar, kural sürümü, kaynaklar arası ikizler, bot yoklaması, sitelerin canlılık kontrolü)
burada YOK: 15 dakikalık tur yapar; kaçan her şeyi de o yakalar (aynı satırlar, aynı değerlendirme).
Veritabanı çıkışı (ücretsiz plan): her okuma `since` ile daraltılır; KKTC günü başına en çok MAX_RUNS_PER_DAY çalışma (fazlası turu bekler)."""
import sys
import time
from datetime import datetime, timedelta, timezone

from application import ask_price, hunt, social_import
from application.dedupe import mark_duplicates
from application.evaluate import LazyBook, evaluate_new, load_book
from application.notify import api
from application.settings_store import load_settings
from domain.kktc_time import to_kktc
from entrypoints import cron_evaluate
from infrastructure.config import load_env, redact, require
from infrastructure.db.repository import Repository
from infrastructure.fx import frankfurter

PLATFORM = "facebook"
SETTLE_S = 20  # sosyal oturumun önerisi: grubun satırları birleşsin
LOCK_WAIT_S = 240  # tur değerlendirmesi ~1–3 dk
LOCK_POLL_S = 10
MAX_ROUNDS = 3
ROUND_GAP_S = 5
MAX_RUNS_PER_DAY = 400  # gündüz saatlik okuma ~8 grup/saat; açık sekmede gönderi başına bir çalışma
DAY_KEY = "hizli:gun:{}"  # bot_state: o KKTC gününde başlayan çalışma sayısı
SKEW = timedelta(minutes=2)  # ilk görülme zamanını veritabanı yazar: sunucu saatleri arasındaki fark payı


def wait_lock(repo, name: str):
    """Değerlendirme kilidi: tur değerlendiriyorsa en çok LOCK_WAIT_S saniye bekler (cron_evaluate.wait_lock). Alamazsa None."""
    return cron_evaluate.wait_lock(repo, name, LOCK_WAIT_S, LOCK_POLL_S)


def take_run(repo, now: datetime) -> bool:
    """Günlük sayaçtan bir çalışma düşer; sınır dolduysa False (ilanları 15 dakikalık tur işler)."""
    key = DAY_KEY.format(to_kktc(now).date().isoformat())
    try:
        used = int(repo.get_state(key) or 0)
    except ValueError:
        used = 0
    if used >= MAX_RUNS_PER_DAY:
        return False
    repo.set_state(key, str(used + 1))
    return True


def _side(name: str, job) -> int:
    try:
        return job()
    except Exception as e:  # yan iş: 🟢 yolunu bozmasın
        print(f"hızlı şerit {name}: HATA {type(e).__name__}: {redact(str(e))[:150]}", flush=True)
        return 0


def run_round(repo, devir_dir, token: str, owner: str, wait=wait_lock, log=print) -> bool:
    """Bir aktarım + işleme. Dönen: False = öbür yol kilitli (tur aktarıyor ya da değerlendiriyor), devam turu yok; True = iş bitti."""
    since = datetime.now(timezone.utc) - SKEW
    rep = social_import.import_facebook_locked(repo, devir_dir, log=log)
    if rep is None:
        log("hızlı şerit: tur şu an aktarıyor, satırları o işleyecek")
        return False
    if not rep.new_listings:
        return True  # araç olmayan gönderiler ya da bilinen ilanlar
    ev_token = wait(repo, cron_evaluate.LOCK)
    if not ev_token:
        log(f"hızlı şerit: {rep.new_listings} yeni ilan aktarıldı; değerlendirme kilidi alınamadı, 15 dakikalık tur işleyecek")
        return False
    try:
        settings = load_settings(repo)
        now = datetime.now(timezone.utc)
        marked = mark_duplicates(repo, now=now, since=since)
        book = LazyBook(lambda: load_book(repo) if settings.estimated_alerts else None)
        evaluated = evaluate_new(repo, settings, book=book, since=since, platform=PLATFORM)
        strong, sent, est_sent = cron_evaluate.deliver(repo, token, owner, settings, evaluated, book, platform=PLATFORM)

        def send(text):
            return api(token, "sendMessage", chat_id=owner, text=text)

        cards = _side("fotoğrafta fiyat", lambda: ask_price.run(repo, send, settings, since=since))
        hunts = _side("av", lambda: hunt.run(repo, owner, send, settings))
    finally:
        repo.release_lock(cron_evaluate.LOCK, ev_token)
    log(f"hızlı şerit: yeni={rep.new_listings} kopya={marked} değerlendirilen={len(evaluated)} güçlü={strong} bildirilen={sent} "
        f"tahmini_bildirilen={est_sent} foto_kart={cards} av={hunts}")
    return True


def main(devir_dir=social_import.HANDOFF_DIR, sleep=time.sleep) -> None:
    sys.stdout.reconfigure(line_buffering=True)
    sleep(SETTLE_S)
    load_env()
    repo = Repository(require("DATABASE_URL"))
    token, owner = require("TELEGRAM_BOT_TOKEN"), require("TELEGRAM_CHAT_ID")
    if not take_run(repo, datetime.now(timezone.utc)):
        print(f"hızlı şerit: bugünkü üst sınır ({MAX_RUNS_PER_DAY} çalışma) doldu; ilanları 15 dakikalık tur işleyecek")
        return
    frankfurter.use_store(repo)  # TL fiyatlı gönderi: kur servisi çökerse son kayıtlı kur
    t0 = time.monotonic()
    try:
        for i in range(MAX_ROUNDS):
            if i and not social_import.has_new_lines(repo, devir_dir):
                break
            if i:
                sleep(ROUND_GAP_S)
            if not run_round(repo, devir_dir, token, owner):
                break
    except Exception as e:  # kilitler bırakıldı (finally); aktarılan ilanları 15 dakikalık turun değerlendirmesi alır (son 3 saat)
        print(f"hızlı şerit: HATA {type(e).__name__}: {redact(str(e))[:150]}")
        sys.exit(1)
    print(f"hızlı şerit: {time.monotonic() - t0:.0f} sn")


if __name__ == "__main__":
    main()
