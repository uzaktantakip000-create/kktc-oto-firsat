"""Telegram komut menüsü (sahibin kararı 03.10.2026, 05.10.2026'da genişletildi): iki ayrı menü yazılır.
- SAHİP sohbeti (chat kapsamı): durum, son, fiyat, satti, ayarlar, kaynaklar (07.10.2026), yardim, dur, basla. Diğer sahip komutları (/esik, /butce...) menüden
  GİZLİ kalır ama yazılırsa çalışır; hepsi /yardim'da listelenir.
- DİĞER HERKES (varsayılan kapsam): yardim, dur, basla. Abonelerin yalnız bunlar çalışır; sahip komutlarını menüde görüp cevapsız kalmasınlar.
Menü Telegram'a bir kez yazılır (sürüm anahtarı bot_state'te: sürüm|sahip); sürüm ya da sahip değişirse yeniden yazılır. Ayrıca sahip sohbetinde
menü düğmesi "komutlar" olarak sabitlenir (sol alttaki liste)."""
from application.notify import api
from infrastructure.db.repository import Repository

MENU_VERSION = "2026-10-07"
STATE_KEY = "bot:menu"
OWNER_COMMANDS = [
    ("durum", "Sistem çalışıyor mu?"),
    ("son", "Son 10 fırsat"),
    ("fiyat", "Araç değeri: /fiyat corolla 2014"),
    ("satti", "Satış gir: /satti corolla 2014 120000km 7200"),
    ("ayarlar", "Eşik, bütçe, istenmeyen markalar"),
    ("kaynaklar", "Taranan siteler ve hesaplar: aç/kapat"),
    ("yardim", "Tüm komutlar ve örnekler"),
    ("dur", "Bildirimleri durdur"),
    ("basla", "Bildirimleri yeniden aç"),
]
DEFAULT_COMMANDS = [
    ("yardim", "Bu bot ne yapar?"),
    ("dur", "Bildirimleri durdur"),
    ("basla", "Bildirimleri yeniden aç"),
]


def _payload(commands: list[tuple[str, str]]) -> list[dict]:
    return [{"command": c, "description": d} for c, d in commands]


def ensure_menu(repo: Repository, token: str, owner_chat_id: str) -> bool:
    """Menü bu sürüm+sahiple yazılmadıysa yazar. True = bu çağrıda yazıldı. Hata bildirimi/değerlendirmeyi engellemez (çağıran yakalar).
    Üç çağrının biri başarısız olursa durum anahtarı YAZILMAZ: sonraki turda hepsi yeniden denenir (tekrarı zararsızdır)."""
    wanted = f"{MENU_VERSION}|{owner_chat_id}"
    if repo.get_state(STATE_KEY) == wanted:
        return False
    chat = int(owner_chat_id)
    api(token, "setMyCommands", commands=_payload(OWNER_COMMANDS), scope={"type": "chat", "chat_id": chat})
    api(token, "setMyCommands", commands=_payload(DEFAULT_COMMANDS))
    api(token, "setChatMenuButton", chat_id=chat, menu_button={"type": "commands"})
    repo.set_state(STATE_KEY, wanted)
    return True
