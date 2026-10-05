"""Anında cevap: Telegram'ı uzun yoklamayla (long polling) sürekli dinler. VPS'te systemd altında çalışır: `python -m entrypoints.bot_listen`.
GitHub turu (tick → cron_evaluate) YEDEK olarak kalır: kalp atışı (bot_state 'bot_listen_seen') tazeyken yoklamayı atlar; dinleyici durunca
(kalp atışı 3 dakikada bayatlar ya da temiz çıkışta silinir) eski 15 dakikalık yoklamaya kendiliğinden döner. Ağ/Telegram/veritabanı hatasında
ÇÖKMEZ: üstel bekleme (5→10→30→60 sn) ve gerekirse veritabanına yeniden bağlanır. SIGTERM/SIGINT: işlenen yoklama biter, süreç 0 ile çıkar."""
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from application import bot_poll
from application.notify import TelegramError
from infrastructure.config import load_env, redact, require
from infrastructure.db.repository import DatabaseDown, Repository
from infrastructure.fx import frankfurter

POLL_TIMEOUT_S = 50  # Telegram'da bu kadar saniye yeni güncelleme beklenir (uzun yoklama); HTTP süresi buna +15 sn eklenir (poll_bot)
HEARTBEAT_EVERY_S = 45  # kalp atışı en sık bu aralıkla yazılır (tazelik sınırı 180 sn: bot_poll.LISTENER_FRESH_SECONDS)
BACKOFF_S = (5, 10, 30, 60)  # üst üste hatalarda bekleme (son değer tavan); bir başarıda başa döner
CONFLICT_WAIT_S = 30  # 409: aynı bot için başka bir getUpdates çalışıyor (eski süreç ya da GitHub turu); bu kadar bekle
CONFIG_EXIT_CODE = 78  # EX_CONFIG: ortam değişkeni eksik; deploy/bot/kktc-bot.service bu kodda yeniden başlatmaz
FX_REFRESH_S = 3600  # süreç günlerce açık kalır: kur önbelleği saatte bir boşaltılır (kısa ömürlü turlarda gerekmez)


class Stop:
    """SIGTERM/SIGINT bayrağı. threading.Event DEĞİL: sinyal işleyicisi içinde kilit almak, bekleyen ana iş parçacığını kilitleyebilir.
    İkinci sinyal (terminalde ikinci Ctrl+C) beklemeden keser."""

    def __init__(self) -> None:
        self.requested = False

    def request(self, *_) -> None:
        if self.requested:
            raise KeyboardInterrupt
        self.requested = True


def pause(seconds: float, stop: Stop, sleep=time.sleep, clock=time.monotonic) -> None:
    """Bekler; durdurma istenirse en geç 1 sn içinde döner (systemd uzun beklemede kalmasın)."""
    end = clock() + seconds
    while not stop.requested and clock() < end:
        sleep(min(1.0, max(end - clock(), 0.0)))


def code_version() -> str:
    """Çalışan kodun git kısa sürümü (açılış satırı için); alınamazsa "?"."""
    root = Path(__file__).resolve().parent.parent
    try:
        out = subprocess.run(["git", "-c", f"safe.directory={root}", "-C", str(root), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5)
        return (out.stdout.strip() or "?") if out.returncode == 0 else "?"
    except Exception:
        return "?"


def _connection_lost(repo, error: Exception) -> bool:
    conn = getattr(repo, "conn", None)
    return isinstance(error, DatabaseDown) or bool(getattr(conn, "closed", False) or getattr(conn, "broken", False))


def _close(repo) -> None:
    try:
        repo.conn.close()
    except Exception:
        pass


def listen(open_repo, token: str, owner: str, stop: Stop, *, poll=None, wait=None, clock=time.monotonic, utcnow=None) -> None:
    """Dinleme döngüsü. `open_repo()` yeni Repository açar (bağlantı kopunca yeniden çağrılır). Diğer parametreler sınama için."""
    poll = poll or bot_poll.poll_bot
    wait = wait or (lambda seconds: pause(seconds, stop))
    utcnow = utcnow or (lambda: datetime.now(timezone.utc))
    repo = None
    owner_synced = False
    fails = 0  # üst üste hata sayısı (bekleme süresini belirler)
    conflict = False  # 409 serisi sürüyor mu (satır başına bir kez loglanır)
    last_beat: float | None = None
    connected = False  # ilk başarılı yoklamada bir satır: kurulumda "dinliyor mu?" sorusu journalctl'dan görülsün
    fx_at = clock()

    def beat() -> None:
        """Kalp atışı: en sık HEARTBEAT_EVERY_S'de bir yazılır. YALNIZ başarılı getUpdates'ten sonra (ve her güncellemeden sonra): 409'da ya da ağ yokken
        "ayaktayım" demesin, yoksa GitHub turu yoklamayı bırakır ve kimse dinlemez."""
        nonlocal last_beat
        now = clock()
        if last_beat is None or now - last_beat >= HEARTBEAT_EVERY_S:
            repo.set_state(bot_poll.LISTENER_STATE_KEY, utcnow().isoformat())
            last_beat = now

    while not stop.requested:
        try:
            if repo is None:
                repo = open_repo()
                frankfurter.use_store(repo)  # kur servisi çökerse son kayıtlı kur (ilan kontrolü fiyat çevirir)
                owner_synced = False
            if not owner_synced:
                bot_poll.sync_owner(repo, owner)  # sahip kaydı bir kez (bağlantı yenilenince yine); her yoklamada değil
                owner_synced = True
            if clock() - fx_at >= FX_REFRESH_S:
                frankfurter.clear_cache()
                fx_at = clock()
            n = poll(repo, token, owner, timeout=POLL_TIMEOUT_S, upsert_owner=False, on_update=beat)
            beat()
            if fails:
                print("bot: bağlantı düzeldi")
            elif not connected:
                print("bot: Telegram'a bağlandı, dinleniyor")
            connected = True
            if n:
                print(f"bot: {n} güncelleme işlendi")
            fails, conflict = 0, False
        except TelegramError as e:
            if e.status == 409:  # başka bir getUpdates çalışıyor: Telegram'a ulaşıyoruz, hata sayacı artmaz; kısa bekle
                if not conflict:
                    print(f"bot: Telegram 409, başka bir getUpdates çalışıyor; {CONFLICT_WAIT_S} sn beklenip yeniden denenecek")
                    conflict = True
                fails = 0
                wait(CONFLICT_WAIT_S)
                continue
            fails = _failed(e, fails, wait)
        except Exception as e:  # ağ, Telegram, veritabanı: hiçbiri süreci düşürmez
            if repo is not None and _connection_lost(repo, e):
                _close(repo)
                repo = None  # sonraki turda yeni bağlantı
            fails = _failed(e, fails, wait)
    if repo is not None:
        try:
            repo.set_state(bot_poll.LISTENER_STATE_KEY, "")  # temiz çıkış: GitHub turu hemen yoklamaya dönsün (3 dk bayatlamayı beklemesin)
        except Exception:
            pass
        _close(repo)


def _failed(error: Exception, fails: int, wait) -> int:
    delay = BACKOFF_S[min(fails, len(BACKOFF_S) - 1)]
    print(f"bot dinleyici hatası: {type(error).__name__} {redact(str(error))[:150]}; {delay} sn sonra yeniden denenecek")
    wait(delay)
    return fails + 1


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)  # journald'da satırlar gecikmeden görünsün
    load_env()
    try:
        token, owner, dsn = require("TELEGRAM_BOT_TOKEN"), require("TELEGRAM_CHAT_ID"), require("DATABASE_URL")
    except RuntimeError as e:  # ayar eksik/boş: yeniden denemek boşuna; 78 = systemd'de RestartPreventExitStatus (döngüye girmez)
        print(f"bot dinleyicisi başlamadı: {e}")
        sys.exit(CONFIG_EXIT_CODE)
    stop = Stop()
    signal.signal(signal.SIGTERM, stop.request)
    signal.signal(signal.SIGINT, stop.request)
    print(f"bot dinleyicisi başladı: sürüm {code_version()}, uzun yoklama {POLL_TIMEOUT_S} sn")
    try:
        listen(lambda: Repository(dsn), token, owner, stop)
    except KeyboardInterrupt:  # ikinci Ctrl+C
        print("bot dinleyicisi zorla durduruldu")
        sys.exit(130)
    print("bot dinleyicisi durdu")


if __name__ == "__main__":
    main()
