"""Eski Apify sosyal medya toplayıcısı emekli (07.10.2026): bot_state'teki anahtar "on" yazılsa bile eski yol çalışmaz (aynı hesaplar ikinci kez,
ücretli okunmasın). Eski yolun davranış testleri kendi dosyalarında `feed_switch.LEGACY_APIFY = True` ile çalışır."""
from application import feed_switch


def test_legacy_apify_social_path_stays_off_even_if_the_switch_says_on():
    class R:
        def get_state(self, k, default=None):
            return {"feed:instagram": "on", "feed:facebook": "on"}.get(k, default)
    assert feed_switch.paused_platforms(R()) == {"instagram": "kapali", "facebook": "kapali"}
