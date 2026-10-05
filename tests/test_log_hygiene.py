"""Herkese açık Actions günlüğü: abone sohbet kimliği tam yazılmaz; hata metni önce maskelenir (redact), sonra kırpılır."""
from datetime import datetime, timezone

import pytest

from application import bot_poll, digest, evaluate, notify
from domain.profit import Tier
from domain.settings import Settings
from entrypoints import cron_collect, tick
from infrastructure.config import mask_chat
from infrastructure.db import price_book_store
from tests.test_notify import FakeRepo, ev, patch_api
from tests.test_phase1 import row

CHAT = "987654321"
# Sır, kırpma sınırını ortadan bölecek yerde: önce kırpılırsa baştan 10 hanesi maskelenmeden açıkta kalırdı.
SECRET = "sk-or-GIZLI-DEGER-123456"
LONG_ERROR = "x" * 140 + SECRET + " son"


@pytest.fixture
def secret_env(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", SECRET)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)  # sahibe uyarı yolu kapalı


def assert_no_secret(text: str):
    assert SECRET not in text and SECRET[:8] not in text, text  # kırpılmış parça da yok


def test_mask_chat_keeps_only_last_three_digits():
    assert mask_chat(CHAT) == "chat …321" and mask_chat(-1001234567890) == "chat …890"
    assert mask_chat("12") == "chat …"  # kısa kimlik tümüyle gizlenir


def test_alert_failure_log_does_not_print_the_full_chat_id(monkeypatch, capsys):
    patch_api(monkeypatch, {CHAT: 500})
    assert notify.send_alerts(FakeRepo([CHAT]), "t", [ev(1)]) == 0
    out = capsys.readouterr().out
    assert "bildirim gönderilemedi" in out and CHAT not in out and "chat …321" in out


def test_burst_summary_failure_log_does_not_print_the_full_chat_id(monkeypatch, capsys):
    patch_api(monkeypatch, {CHAT: 500})
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    evs = [ev(1, tier=Tier.ESTIMATED), ev(2, tier=Tier.ESTIMATED)]
    notify.send_alerts(FakeRepo([CHAT]), "t", evs, tier=Tier.ESTIMATED, s=Settings(est_burst_limit=1))  # sınırı aşınca tek özet gider
    out = capsys.readouterr().out
    assert "özet gönderilemedi" in out and CHAT not in out and "chat …321" in out


def test_digest_failure_log_does_not_print_the_full_chat_id(monkeypatch, capsys):
    now = datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc)

    class Repo:
        def approved_subscribers(self):
            return [{"chat_id": CHAT}]

        def pending_negotiable(self, chat_id):
            return [row("a", first_seen=now)]

        def alert_recent(self, key, hours):
            return False

    def boom(token, method, **kw):
        raise notify.TelegramError(method, 500, "x")

    monkeypatch.setattr(digest, "ENABLED", True)
    monkeypatch.setattr(digest, "api", boom)
    assert digest.send_daily_digest(Repo(), "t", now) == 0
    out = capsys.readouterr().out
    assert "özet gönderilemedi" in out and CHAT not in out and "chat …321" in out


def test_bot_update_failure_log_is_redacted(monkeypatch, secret_env, capsys):
    class Repo:
        def get_state(self, key, default=None):
            return "0"

        def set_state(self, key, value):
            pass

    def boom(*a, **kw):
        raise RuntimeError(SECRET + " x" * 100)

    monkeypatch.setattr(bot_poll, "_upsert_owner", lambda repo, chat: None)
    monkeypatch.setattr(bot_poll, "api", lambda token, method, **kw: [{"update_id": 1, "message": {}}] if method == "getUpdates" else {})
    monkeypatch.setattr(bot_poll, "_handle_message", boom)
    assert bot_poll.poll_bot(Repo(), "t", "1") == 1
    out = capsys.readouterr().out
    assert "bot güncellemesi işlenemedi" in out and "***" in out
    assert_no_secret(out)


def test_book_load_failure_log_is_redacted(monkeypatch, secret_env, capsys):
    class Boom:
        def __init__(self, conn):
            raise RuntimeError(SECRET + " tablo")

    monkeypatch.setattr(price_book_store, "PriceBookStore", Boom)
    assert evaluate.load_book(type("R", (), {"conn": None})()) is None
    out = capsys.readouterr().out
    assert "değer tablosu yüklenemedi" in out and "***" in out
    assert_no_secret(out)


@pytest.mark.parametrize("job", ["instagram", "facebook", "kktcar"])
def test_collect_error_is_redacted_before_it_is_truncated(monkeypatch, secret_env, capsys, job):
    """Eskiden önce 150 karaktere kırpılıyor, sonra maskeleniyordu: kırpma sırrı ortadan bölerse maske eşleşmez, parçası günlüğe düşerdi."""
    def boom(*a, **kw):
        raise RuntimeError(LONG_ERROR)

    class Repo:
        def get_state(self, key, default=None):
            return "on"

        def sources(self, platform, statuses):
            return [{"name": "KKTCar", "url": "https://www.kktcar.com"}]

    monkeypatch.setenv("APIFY_TOKEN", "apify-test-degeri")
    monkeypatch.setattr(cron_collect.feed_switch, "paused_platforms", lambda repo: {})
    monkeypatch.setattr(cron_collect.llm_reader, "from_env", lambda repo: None)
    monkeypatch.setattr(cron_collect, "track_collect", lambda *a, **kw: None)
    for fn in ("collect_sources", "collect_facebook_groups", "collect_kktcar"):
        monkeypatch.setattr(cron_collect, fn, boom)
    errors = cron_collect.run(job, Repo())
    out = capsys.readouterr().out
    assert len(errors) == 1 and "HATA" in out and "***" in errors[0][1]
    assert errors[0][1].startswith("RuntimeError: ") and len(errors[0][1]) <= len("RuntimeError: ") + 150
    assert_no_secret(out + errors[0][1])


def test_tick_batch_error_is_redacted_before_it_is_truncated(secret_env, capsys):
    class Repo:
        def set_state(self, key, value):
            pass

    def boom(job, repo):
        raise RuntimeError(LONG_ERROR)

    errors: list = []
    tick.run_batch(["kktcar"], Repo(), datetime.now(timezone.utc), 0.0, errors, runner=boom, clock=lambda: 0.0)
    out = capsys.readouterr().out
    assert len(errors) == 1 and "***" in errors[0][1]
    assert_no_secret(out + errors[0][1])
