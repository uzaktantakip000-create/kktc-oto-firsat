from datetime import datetime, timedelta, timezone

import pytest

from application import notify
from application.evaluate import Evaluated
from domain.comparables import Market
from domain.profit import Confidence, ProfitResult, Tier
from infrastructure.db.repository import unsaved_alert_key

NOW = datetime.now(timezone.utc)


class FakeRepo:
    def __init__(self, subs):
        self.subs, self.alerts, self.blocked = subs, set(), []
        self.saved_alerts, self.state, self.save_failures = [], {}, 0
        self.conn = self

    def approved_subscribers(self):
        return [{"chat_id": c} for c in self.subs]

    def alerts_sent_since(self, tier, hours=24):
        return getattr(self, "sent_today", 0)

    def alert_exists(self, listing_id, chat_id, tier):
        return (listing_id, chat_id) in self.alerts or unsaved_alert_key(listing_id, chat_id) in self.state

    def set_state(self, key, value):
        self.state[key] = value

    def save_alert(self, listing_id, chat_id, tier, msg_id, *, evaluation_id, price_gbp):
        if self.save_failures > 0:  # veritabanı yazması geçici/kalıcı patlıyor
            self.save_failures -= 1
            raise RuntimeError("bağlantı koptu")
        self.alerts.add((listing_id, chat_id))
        self.saved_alerts.append({"listing": listing_id, "chat": chat_id, "tier": tier, "evaluation_id": evaluation_id, "price_gbp": price_gbp})

    def execute(self, sql, params):  # subscribers durumu güncellemesi
        self.blocked.append(params[0])


def ev(i, tier=Tier.STRONG, first_seen=NOW, posted=None):
    l = dict(id=i, first_seen_at=first_seen, posted_at=posted, year=2015, brand="Toyota", model="Vitz", km=1000,
             transmission="otomatik", steering="RHD", location="Girne", source_name="x", price_gbp=5000,
             currency_guess=False, currency="GBP", seller_phone=None, url=None)
    m = Market(5, 8000, 7000, 9000, 1, 0.0)
    p = ProfitResult(7600, 2600, 0.52, Confidence.LOW, tier)
    return Evaluated(l, m, p, [], [], [])


def patch_api(monkeypatch, fail):
    sent = []

    def fake(token, method, **kw):
        chat = kw["chat_id"]
        if chat in fail:
            raise notify.TelegramError(method, fail[chat], "x")
        sent.append(chat)
        return {"message_id": 1}

    monkeypatch.setattr(notify, "api", fake)
    return sent


def test_one_blocked_subscriber_does_not_stop_others(monkeypatch):
    sent = patch_api(monkeypatch, {"a": 403})
    repo = FakeRepo(["a", "b"])
    assert notify.send_alerts(repo, "t", [ev(1), ev(2)]) == 2
    assert sent == ["b", "b"] and repo.blocked == ["a"]
    assert (1, "a") not in repo.alerts and (1, "b") in repo.alerts  # başarısız gönderim kaydedilmedi


def test_rate_limit_stops_run_but_leaves_alert_unrecorded(monkeypatch):
    patch_api(monkeypatch, {"a": 429})
    repo = FakeRepo(["a"])
    assert notify.send_alerts(repo, "t", [ev(1), ev(2)]) == 0
    assert repo.alerts == set()  # sonraki turda yeniden denenir


def test_already_sent_not_resent(monkeypatch):
    sent = patch_api(monkeypatch, {})
    repo = FakeRepo(["a"])
    repo.alerts.add((1, "a"))
    notify.send_alerts(repo, "t", [ev(1)])
    assert sent == []


@pytest.mark.parametrize("first_seen,posted,expected", [
    (NOW, None, True),
    (NOW - timedelta(hours=40), None, False),
    (NOW, NOW - timedelta(days=10), False),   # geçmiş doldurmadan gelen eski ilan
    (NOW, NOW - timedelta(days=2), True),
])
def test_freshness(first_seen, posted, expected):
    assert notify.is_fresh(first_seen, posted, NOW) is expected


@pytest.mark.parametrize("tier", [Tier.STRONG, Tier.ESTIMATED])
def test_send_alerts_skips_a_fresh_looking_ad_with_an_old_kktcarabam_photo(monkeypatch, tier):
    """`send_alerts`ın kendi tazelik kontrolü (🟢 ve 🟠 yolu aynı `is_fresh`): kapak fotoğrafı 36 saatten eski KKTCarabam ilanı gitmez, yenisi gider."""
    def photo(hours_ago):
        local = NOW - timedelta(hours=hours_ago) + timedelta(hours=3)
        return [f"https://www.kktcarabam.com/uploads/images/{local:%Y/%m/%d/%H}/a-6abe346a6b863-270_200.jpg"]

    sent = patch_api(monkeypatch, {})
    repo = FakeRepo(["a"])
    old, new, plain = ev("old", tier), ev("new", tier), ev("plain", tier)  # plain: fotoğraf alanı yok (eskisi gibi gider)
    old.listing["photo_urls"], new.listing["photo_urls"] = photo(120), photo(2)
    assert notify.send_alerts(repo, "t", [old, new, plain], tier=tier) == 2
    assert repo.alerts == {("new", "a"), ("plain", "a")} and sent == ["a", "a"]


def test_guessed_currency_message_names_real_currency():
    e = ev(1)
    e.listing.update(currency_guess=True, currency="TRY")
    assert "TL varsayıldı" in notify.format_alert(e)


def test_whatsapp_button_on_top_when_phone_valid():
    url = notify.whatsapp_url("905330000021", "Merhaba, 2015 Toyota Vitz ilanınız hâlâ satılık mı?")
    assert url.startswith("https://wa.me/905330000021?text=") and " " not in url
    kb = notify.keyboard("L1", url)["inline_keyboard"]
    assert kb[0][0]["url"] == url and kb[1][0]["callback_data"].startswith("fb:")
    assert [b["text"] for b in kb[1]] == ["👍 İşe yarar", "👎 Yanlış"] and len(kb) == 2  # sahibin kararı: 2 düğme


def test_no_whatsapp_button_without_valid_phone():
    assert notify.whatsapp_url(None, "x") is None and notify.whatsapp_url("123", "x") is None
    assert not any("url" in b for row in notify.keyboard("L1", None)["inline_keyboard"] for b in row)


def test_sold_comparables_are_described_as_sold_not_archived():
    e = ev(1)
    e.market = Market(5, 8000, 7000, 9000, 1, 0.8)  # emsallerin %80'i satılmış (pasif) ilan
    msg = notify.format_alert(e)
    assert "satılmış ilan" in msg and "arşiv" not in msg  # "arşiv" yanıltıcıydı: bunlar sitenin satıldı işaretli ilanları


def test_message_is_short_and_has_exactly_the_decided_parts():
    """Sahibin kararı (03.10.2026): araç, fiyat, piyasa ortası + emsal sayısı, tek satır neden, link. Güven etiketi/telefon/emsal listesi/gümrük hatırlatması YOK."""
    e = ev(1)
    e.listing.update(url="https://x/1", seller_phone="905330000021", raw_text="")
    e.urgency = ["acil"]
    msg = notify.format_alert(e)
    lines = msg.splitlines()
    assert lines[0].startswith("🟢 FIRSAT") and "£5.000" in lines[1] and "Piyasa ortası £8.000 (5 emsal)" in msg
    assert sum(l.startswith("💡 Neden:") for l in lines) == 1 and "ilanda 'acil' yazıyor" in msg and lines[-1] == "🔗 https://x/1"
    for gone in ("Güven", "En yakın emsaller", "📞", "Gümrük", "🆕", "🔥", "GÜÇLÜ"):
        assert gone not in msg, gone
    assert len(lines) <= 7  # kısa


def test_alert_record_links_the_evaluation_and_stores_the_price_at_send_time(monkeypatch):
    """Migration 019: alerts.evaluation_id (bildirimi doğuran değerlendirme) ve fiyat_gonderimde (£) yazılır."""
    patch_api(monkeypatch, {})
    repo = FakeRepo(["a"])
    e = ev(1)
    e.listing["evaluation_id"] = "eval-uuid-1"
    notify.send_alerts(repo, "t", [e])
    assert repo.saved_alerts == [{"listing": 1, "chat": "a", "tier": "guclu", "evaluation_id": "eval-uuid-1", "price_gbp": 5000.0}]
    repo2 = FakeRepo(["a"])
    notify.send_alerts(repo2, "t", [ev(2)])  # değerlendirme kimliği olmayan satır (eski yol): None yazılır, gönderim engellenmez
    assert repo2.saved_alerts[0]["evaluation_id"] is None and repo2.saved_alerts[0]["price_gbp"] == 5000.0


def test_estimated_alerts_respect_the_daily_limit(monkeypatch):
    """Sahibin kararı (03.10.2026): 🟠 KONTROL ET günde en çok 3; son 24 saatte gidenler sayılır."""
    sent = patch_api(monkeypatch, {})
    repo = FakeRepo(["a"])
    repo.sent_today = 1  # bugün zaten 1 tane gitti: kalan hak 2
    evs = [ev(i, tier=Tier.ESTIMATED) for i in range(1, 6)]
    assert notify.send_alerts(repo, "t", evs, tier=Tier.ESTIMATED) == 2 and len(sent) == 2
    repo = FakeRepo(["a"])
    repo.sent_today = 3  # hak bitti
    assert notify.send_alerts(repo, "t", [ev(9, tier=Tier.ESTIMATED)], tier=Tier.ESTIMATED) == 0
    repo = FakeRepo(["a"])
    repo.sent_today = 3  # 🟢 sınırdan etkilenmez
    assert notify.send_alerts(repo, "t", [ev(10)]) == 1


def test_failed_alert_record_is_retried_once_and_the_message_is_not_duplicated(monkeypatch):
    """Mesaj gitti ama `alerts` kaydı ilk denemede yazılamadı (geçici bağlantı hatası): bir kez yeniden denenir, kayıt düşer, tek mesaj."""
    sent = patch_api(monkeypatch, {})
    repo = FakeRepo(["a"])
    repo.save_failures = 1
    assert notify.send_alerts(repo, "t", [ev(1)]) == 1
    assert sent == ["a"] and (1, "a") in repo.alerts and repo.state == {}
    assert notify.send_alerts(repo, "t", [ev(1)]) == 0 and sent == ["a"]


def test_persistently_failing_alert_record_leaves_a_fallback_trace_so_the_message_is_not_resent_every_round(monkeypatch, capsys):
    """Kayıt iki denemede de yazılamazsa (örn. eksik sütun) hata turu çökertmez, aynı turun diğer ilanı gider, `bot_state`'e yedek iz
    bırakılır ve sonraki turda AYNI mesaj tekrar gitmez (eskiden her 15 dakikada bir giderdi)."""
    sent = patch_api(monkeypatch, {})
    repo = FakeRepo(["a"])
    repo.save_failures = 2  # yalnız ilk ilanın iki denemesi patlar
    assert notify.send_alerts(repo, "t", [ev(1), ev(2)]) == 2  # ikisi de gitti, ikincisinin kaydı düştü
    assert sent == ["a", "a"] and unsaved_alert_key(1, "a") in repo.state and (2, "a") in repo.alerts
    assert "bildirim gitti ama kaydı yazılamadı" in capsys.readouterr().out
    assert notify.send_alerts(repo, "t", [ev(1), ev(2)]) == 0 and sent == ["a", "a"]  # tekrar yok

