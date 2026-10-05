"""Adım 1: değerlendirme çökerse yan işler (kaynak alarmı) yine çalışır ve tur hata verir; başarılı turda `eval:last` yazılır;
tek tek ilan hataları sahibe (en çok günde 1) haber verilir; veritabanı yoksa hata hemen yükselir."""
import psycopg
import pytest

from application import send_gate
from application.evaluate import EvaluationFailure
from domain.profit import Tier
from entrypoints import cron_evaluate


class Repo:
    def __init__(self):
        self.state = {}

    def get_state(self, key, default=None):
        return self.state.get(key, default)

    def set_state(self, key, value):
        self.state[key] = value

    def market_pool(self, days):
        return []

    def expire_unverifiable(self):
        pass

    def release_orphan_duplicates(self):
        return 0

    def purge_personal_data(self):
        return 0, 0, 0


def wire(monkeypatch, evaluate_new):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    nop = lambda *a, **k: None  # noqa: E731
    for name in ("apply_rules_version", "poll_bot", "ensure_menu", "mark_duplicates", "run_maintenance"):
        monkeypatch.setattr(cron_evaluate, name, nop)
    monkeypatch.setattr(cron_evaluate, "load_settings", lambda repo: cron_evaluate.Settings())
    monkeypatch.setattr(cron_evaluate, "load_book", lambda repo: object())
    monkeypatch.setattr(cron_evaluate, "evaluate_new", evaluate_new)
    monkeypatch.setattr(cron_evaluate, "pending_alerts", lambda repo, tier=Tier.STRONG, book=None: [])
    monkeypatch.setattr(send_gate, "recheck_before_send", lambda repo, evs: evs)
    monkeypatch.setattr(send_gate.llm_reader, "from_env", lambda repo: None)
    monkeypatch.setattr(send_gate.llm_reader, "verify_candidates", lambda repo, reader, evs: evs)
    monkeypatch.setattr(cron_evaluate, "send_alerts", lambda *a, **k: 0)
    monkeypatch.setattr("application.price_book_job.run_price_book", nop)
    side = []
    for name in ("demote_failing_sources", "guard_estimates", "send_daily_digest", "send_morning_status", "send_discovery",
                 "send_monthly_audit", "check_sources", "send_weekly_report"):
        monkeypatch.setattr(cron_evaluate, name, nop)
    monkeypatch.setattr(cron_evaluate, "check_source_alarms", lambda repo: side.append("alarm"))
    sent = []
    monkeypatch.setattr(cron_evaluate, "notify_owner", lambda repo, key, text, repeat_hours=12: sent.append((key, text, repeat_hours)) or True)
    return side, sent


def test_successful_round_records_eval_last_and_reports_single_listing_failures(monkeypatch):
    def evaluate_new(repo, settings, book=None, failures=None, quick=False):
        failures.extend([("a1b2c3d4", "ValueError"), ("e5f6a7b8", "ValueError")])
        return []

    side, sent = wire(monkeypatch, evaluate_new)
    repo = Repo()
    cron_evaluate.run(repo)  # hata fırlatmaz
    assert "eval:last" in repo.state and side == ["alarm"]
    assert len(sent) == 1 and sent[0][0] == "eval_errors" and "2 ilan" in sent[0][1] and sent[0][2] == 24
    assert "a1b2c3d4" not in sent[0][1]  # mesajda ilan kimliği/içeriği yok


def test_crashed_evaluation_still_runs_side_jobs_then_fails(monkeypatch):
    def evaluate_new(repo, settings, book=None, failures=None, quick=False):
        raise EvaluationFailure("10 ilandan 9'u değerlendirilemedi")

    side, _ = wire(monkeypatch, evaluate_new)
    repo = Repo()
    with pytest.raises(EvaluationFailure):
        cron_evaluate.run(repo)
    assert side == ["alarm"]  # kaynak alarmı yine çalıştı (değerlendirme çöktü diye susmadı)
    assert "eval:last" not in repo.state  # başarısız tur "son başarılı değerlendirme" sayılmaz


def test_database_outage_propagates_immediately(monkeypatch):
    def evaluate_new(repo, settings, book=None, failures=None, quick=False):
        raise psycopg.OperationalError("sunucu yok")

    side, _ = wire(monkeypatch, evaluate_new)
    with pytest.raises(psycopg.OperationalError):
        cron_evaluate.run(Repo())
    assert side == []


# --- Adım 2h: saatlik tam tur / aradaki hızlı turlar ---

from datetime import datetime, timedelta, timezone  # noqa: E402

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


def test_full_pass_due_hourly_and_safe_by_default():
    repo = Repo()
    assert cron_evaluate.full_pass_due(repo, "eval:full", NOW)  # kayıt yok: tam tur
    repo.state["eval:full"] = (NOW - timedelta(minutes=10)).isoformat()
    assert not cron_evaluate.full_pass_due(repo, "eval:full", NOW)
    repo.state["eval:full"] = (NOW - timedelta(minutes=56)).isoformat()
    assert cron_evaluate.full_pass_due(repo, "eval:full", NOW)
    repo.state["eval:full"] = "bozuk değer"
    assert cron_evaluate.full_pass_due(repo, "eval:full", NOW)  # okunamıyorsa en güvenlisi: tam tur
    assert cron_evaluate.full_pass_due(object(), "eval:full", NOW)  # durum okunamıyorsa da


def test_runs_alternate_between_full_and_quick_and_failed_full_round_is_retried(monkeypatch):
    seen, dedupe_seen = [], []

    def evaluate_new(repo, settings, book=None, failures=None, quick=False):
        seen.append(quick)
        if seen[-1] is False and len(seen) == 3:
            raise EvaluationFailure("tam tur başarısız")
        return []

    wire(monkeypatch, evaluate_new)
    monkeypatch.setattr(cron_evaluate, "mark_duplicates", lambda repo, quick=False: dedupe_seen.append(quick))
    repo = Repo()
    cron_evaluate.run(repo)  # 1: kayıt yok → tam tur (ve tam mükerrer taraması)
    assert seen == [False] and dedupe_seen == [False] and "eval:full" in repo.state and "dedupe:full" in repo.state
    cron_evaluate.run(repo)  # 2: hemen sonra → hızlı
    assert seen == [False, True] and dedupe_seen == [False, True]
    repo.state["eval:full"] = (datetime.now(timezone.utc) - timedelta(minutes=70)).isoformat()
    marked = repo.state["eval:full"]
    with pytest.raises(EvaluationFailure):
        cron_evaluate.run(repo)  # 3: tam tur zamanı ama başarısız oldu
    assert repo.state["eval:full"] == marked  # başarısız tam tur "yapıldı" sayılmaz: sonraki tick yeniden dener
