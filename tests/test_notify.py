from datetime import datetime, timedelta, timezone

import pytest

from application import notify
from application.evaluate import Evaluated
from domain.comparables import Market
from domain.profit import Confidence, ProfitResult, Tier

NOW = datetime.now(timezone.utc)


class FakeRepo:
    def __init__(self, subs):
        self.subs, self.alerts, self.blocked = subs, set(), []
        self.conn = self

    def approved_subscribers(self):
        return [{"chat_id": c} for c in self.subs]

    def alert_exists(self, listing_id, chat_id, tier):
        return (listing_id, chat_id) in self.alerts

    def save_alert(self, listing_id, chat_id, tier, msg_id):
        self.alerts.add((listing_id, chat_id))

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


def test_guessed_currency_message_names_real_currency():
    e = ev(1)
    e.listing.update(currency_guess=True, currency="TRY")
    assert "TL varsayıldı" in notify.format_alert(e)


def test_whatsapp_button_on_top_when_phone_valid():
    url = notify.whatsapp_url("905330000021", "Merhaba, 2015 Toyota Vitz ilanınız hâlâ satılık mı?")
    assert url.startswith("https://wa.me/905330000021?text=") and " " not in url
    kb = notify.keyboard("L1", url)["inline_keyboard"]
    assert kb[0][0]["url"] == url and kb[1][0]["callback_data"].startswith("fb:")


def test_no_whatsapp_button_without_valid_phone():
    assert notify.whatsapp_url(None, "x") is None and notify.whatsapp_url("123", "x") is None
    assert not any("url" in b for row in notify.keyboard("L1", None)["inline_keyboard"] for b in row)
