"""İş 2: emsal < 8 ise 🟢/🟠 gitmez; 🟡 özet ve 🆕 etiketi kapalı."""
import pytest

from application import digest
from application.evaluate import Evaluated, apply_send_floor
from domain.alert_policy import MIN_COMPARABLES_TO_SEND, send_floor_ok
from domain.comparables import Market
from domain.profit import Confidence, ProfitResult, Tier
from entrypoints import cron_evaluate


def test_green_needs_eight_direct_comparables():
    assert MIN_COMPARABLES_TO_SEND == 8
    assert not send_floor_ok(Tier.STRONG, "A", 7)
    assert send_floor_ok(Tier.STRONG, "A", 8)
    assert send_floor_ok(Tier.STRONG, "A", 30)


def test_estimated_orange_cannot_pass_while_it_means_few_comparables():
    # 🟠 (yöntem B) tanım gereği doğrudan emsal <8 iken doğar; eğri satır sayısı (market.n) 8+ olsa da geçmez
    assert not send_floor_ok(Tier.ESTIMATED, "B", 40)
    assert not send_floor_ok(Tier.ESTIMATED, "A", 40)


def test_green_from_value_table_alone_is_not_enough():
    assert not send_floor_ok(Tier.STRONG, "B", 40)


def ev(i, tier, method, n):
    l = {"id": i, "first_seen_at": None, "posted_at": None}
    m = Market(n, 8000, 7000, 9000, 1, 0.0)
    return Evaluated(l, m, ProfitResult(7600, 2600, 0.5, Confidence.MEDIUM, tier), [], [], [], method=method)


def test_apply_send_floor_filters_and_logs(capsys):
    items = [ev(1, Tier.STRONG, "A", 7), ev(2, Tier.STRONG, "A", 8), ev(3, Tier.ESTIMATED, "B", 20)]
    assert [e.listing["id"] for e in apply_send_floor(items, "🟢")] == [2]
    assert "emsal kapısı (🟢): 2 ilan gönderilmedi" in capsys.readouterr().out
    assert apply_send_floor([ev(2, Tier.STRONG, "A", 9)]) != [] and capsys.readouterr().out == ""


def test_digest_is_disabled_and_touches_nothing():
    assert digest.ENABLED is False

    class Boom:
        def __getattr__(self, name):
            pytest.fail(f"özet kapalıyken repo'ya dokunmamalı: {name}")

    assert digest.send_daily_digest(Boom(), "token") == 0


# --- cron_evaluate bağlantısı: kapı gerçekten gönderim yoluna takılı mı ---

class Repo:
    def market_pool(self, days):
        return []

    def expire_unverifiable(self):
        pass

    def release_orphan_duplicates(self):
        return 0

    def purge_personal_data(self):
        return 0, 0, 0


def test_cron_evaluate_sends_only_what_passes_the_floor(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    low, ok, orange = ev(1, Tier.STRONG, "A", 7), ev(2, Tier.STRONG, "A", 9), ev(3, Tier.ESTIMATED, "B", 20)
    for e in (low, ok, orange):
        e.listing |= {"first_seen_at": None, "posted_at": None, "platform": "web"}
    nop = lambda *a, **k: None  # noqa: E731
    for name in ("apply_rules_version", "poll_bot", "mark_duplicates", "run_maintenance"):
        monkeypatch.setattr(cron_evaluate, name, nop)
    monkeypatch.setattr(cron_evaluate, "load_settings", lambda repo: cron_evaluate.Settings())
    monkeypatch.setattr(cron_evaluate, "load_book", lambda repo: object())
    monkeypatch.setattr(cron_evaluate, "evaluate_new", lambda *a, **k: [])
    monkeypatch.setattr(cron_evaluate, "pending_alerts", lambda repo, tier=Tier.STRONG, book=None: [orange] if tier is Tier.ESTIMATED else [low, ok])
    monkeypatch.setattr(cron_evaluate, "is_fresh", lambda *a, **k: True)
    monkeypatch.setattr(cron_evaluate, "recheck_before_send", lambda repo, evs: evs)
    monkeypatch.setattr(cron_evaluate.llm_reader, "from_env", lambda repo: None)
    monkeypatch.setattr(cron_evaluate.llm_reader, "verify_candidates", lambda repo, reader, evs: evs)
    monkeypatch.setattr(cron_evaluate, "nearest_comparables", lambda *a, **k: [])
    seen = []
    monkeypatch.setattr(cron_evaluate, "send_alerts", lambda repo, token, evs, *a, **k: seen.append([e.listing["id"] for e in evs]) or len(evs))

    monkeypatch.setattr("application.price_book_job.run_price_book", nop)
    for name in ("demote_failing_sources", "guard_estimates", "send_daily_digest", "send_morning_status", "send_discovery",
                 "send_monthly_audit", "check_source_alarms", "check_sources", "send_weekly_report"):
        monkeypatch.setattr(cron_evaluate, name, nop)
    cron_evaluate.run(Repo())
    assert seen == [[2], []]  # 🟢: yalnızca 9 emsallisi; 🟠: hiçbiri


def test_alert_market_falls_back_to_the_stored_market_when_decide_fails():
    """Mesaj emsalleri bildirimi asla engellemez: karar kurulamazsa kayıtlı piyasa kullanılır."""
    stored = object()
    bad = type("E", (), {"listing": {"id": "x"}, "market": stored})()  # price_gbp yok: decide() KeyError verir
    assert cron_evaluate._alert_market(bad, [], cron_evaluate.Settings()) is stored
