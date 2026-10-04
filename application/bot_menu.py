"""Telegram komut menüsü (sahibin kararı 03.10.2026): görünür komutlar yalnız /durum /son /fiyat /satti /dur /basla; diğerleri menüden GİZLENİR
(yazılırsa çalışmaya devam eder). Menü Telegram'a bir kez yazılır (sürüm anahtarı bot_state'te); değişirse MENU_VERSION artırılır."""
from application.notify import api
from infrastructure.db.repository import Repository

MENU_VERSION = "2026-10-04"
STATE_KEY = "bot:menu"
COMMANDS = [
    ("durum", "Sistem durumu"),
    ("son", "Son fırsatlar"),
    ("fiyat", "Model fiyatı: /fiyat corolla 2014"),
    ("satti", "Gerçek satış: /satti corolla 2014 120000km 7200"),
    ("dur", "Bildirimleri durdur"),
    ("basla", "Bildirimleri yeniden başlat"),
]


def ensure_menu(repo: Repository, token: str) -> bool:
    """Menü bu sürümle yazılmadıysa yazar. True = bu çağrıda yazıldı. Hata bildirimi/değerlendirmeyi engellemez (çağıran yakalar)."""
    if repo.get_state(STATE_KEY) == MENU_VERSION:
        return False
    api(token, "setMyCommands", commands=[{"command": c, "description": d} for c, d in COMMANDS])
    repo.set_state(STATE_KEY, MENU_VERSION)
    return True
