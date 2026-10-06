"""Kendi kendini izleme (application/selfwatch.py): yedeğin kalp atışı, sunucu kesintisi/dönüşü uyarısı, dinleyici bekçisi, sunucu kaynakları,
sabah mesajındaki satırlar. Gerçek ağ yok: sahte repo + sahte Telegram. Ortak kural: hiçbir kontrol taramayı çökertmez/bekletmez, uyarı
tekrarlanmaz, hata metni (bağlantı adresi) log'a girmez."""
from datetime import datetime, timedelta, timezone

import pytest

from application import health, selfwatch
from application.runner_gate import VPS_TICK_KEY
from application.selfwatch import GH_BROWSER_KEY, GH_TICK_KEY
from entrypoints import cron_collect, tick
from tests.test_runner_gate import GOOD, SECRET_DSN, Collect, Repo as GateRepo, Tick, collect_world, real_ago, where

NOW = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)


def ago(minutes: float, now: datetime = NOW) -> str:
    return (now - timedelta(minutes=minutes)).isoformat()


class Repo(GateRepo):
    """bot_state sahtesi + uyarı tekrar sınırı (health.notify_owner'ın kullandığı alert_recent/mark_alerted): `now` testin saatidir."""

    def __init__(self, state=None, now=NOW, **kw):
        super().__init__(state, **kw)
        self.now, self.alerted = now, {}

    def alert_recent(self, key, hours):
        t = self.alerted.get(key)
        return t is not None and t > self.now - timedelta(hours=hours)

    def mark_alerted(self, key):
        self.alerted[key] = self.now


@pytest.fixture
def tg(monkeypatch):
    """Sahte Telegram: gönderilen mesaj metinleri listesi."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    sent = []
    monkeypatch.setattr(health, "api", lambda token, method, **kw: sent.append(kw["text"]))
    return sent


# --- 1. yedeğin kalp atışı: gh_tick_seen / gh_browser_seen --------------------------------------------------------------------
def test_the_fallback_heartbeat_keys_are_short_and_fixed():
    assert GH_TICK_KEY == "gh_tick_seen" and GH_BROWSER_KEY == "gh_browser_seen"


def test_note_github_start_writes_a_utc_iso_time_only_on_github(monkeypatch):
    where(monkeypatch, "github")
    repo = Repo()
    selfwatch.note_github_start(repo, GH_TICK_KEY, NOW)
    assert repo.writes == [GH_TICK_KEY] and repo.state[GH_TICK_KEY] == NOW.isoformat()
    selfwatch.note_github_start(repo, GH_BROWSER_KEY)  # `now` verilmezse şimdiki an, saat dilimli
    stamp = datetime.fromisoformat(repo.state[GH_BROWSER_KEY])
    assert stamp.utcoffset() == timedelta(0) and datetime.now(timezone.utc) - stamp < timedelta(seconds=5)


@pytest.mark.parametrize("runner", ["vps", "yerel"])
def test_note_github_start_does_nothing_off_github(monkeypatch, runner):
    where(monkeypatch, runner)
    repo = Repo()
    selfwatch.note_github_start(repo, GH_TICK_KEY, NOW)
    assert repo.writes == [] and repo.reads == []


def test_note_github_start_swallows_write_errors_and_logs_only_the_error_type(monkeypatch, capsys):
    where(monkeypatch, "github")
    selfwatch.note_github_start(Repo(fail_write={GH_TICK_KEY}), GH_TICK_KEY, NOW)  # fırlatmaz
    out = capsys.readouterr().out
    assert "OperationalError" in out and "GIZLI-PAROLA" not in out and "kullanici" not in out and SECRET_DSN not in out


def test_a_github_tick_records_the_fallback_heartbeat_first_even_when_it_does_not_skip(monkeypatch):
    where(monkeypatch, "github")
    repo = Repo({VPS_TICK_KEY: real_ago(90)})  # bayat: GitHub taramaya devam eder
    world = Tick(monkeypatch, repo)
    monkeypatch.setattr(tick, "due_jobs", lambda *a: [])
    tick.main()
    assert repo.writes[0] == GH_TICK_KEY and repo.writes.index(GH_TICK_KEY) < repo.writes.index("tick:last")  # turdan ÖNCE yazıldı
    assert world.eval_calls == [1] and datetime.fromisoformat(repo.state[GH_TICK_KEY]).tzinfo is not None


def test_a_github_tick_that_skips_still_records_the_fallback_heartbeat(monkeypatch):
    where(monkeypatch, "github")
    repo = Repo({VPS_TICK_KEY: real_ago(5)})
    world = Tick(monkeypatch, repo)
    tick.main()
    assert repo.writes == [GH_TICK_KEY] and world.eval_calls == []  # atladı ama "GitHub canlı" notunu bıraktı


@pytest.mark.parametrize("runner", ["vps", "yerel"])
def test_ticks_off_github_never_write_the_fallback_heartbeat(monkeypatch, runner):
    where(monkeypatch, runner)
    repo = Repo()
    Tick(monkeypatch, repo)
    monkeypatch.setattr(tick, "due_jobs", lambda *a: [])
    tick.main()
    assert GH_TICK_KEY not in repo.state


def test_a_github_tick_runs_normally_when_the_fallback_heartbeat_cannot_be_written(monkeypatch, capsys):
    where(monkeypatch, "github")
    repo = Repo(fail_write={GH_TICK_KEY})
    world = Tick(monkeypatch, repo)
    monkeypatch.setattr(tick, "due_jobs", lambda *a: [])
    tick.main()  # fırlatmaz, çıkış 0
    assert world.eval_calls == [1] and "tick:last" in repo.state and GH_TICK_KEY not in repo.state
    assert "GIZLI-PAROLA" not in capsys.readouterr().out


def test_a_github_browser_run_records_the_fallback_heartbeat_before_deciding_to_skip(monkeypatch):
    where(monkeypatch, "github")
    skipping = Collect({"vps_browser_seen": real_ago(100)})
    calls = collect_world(monkeypatch, skipping, GOOD)
    cron_collect.main("kktcarabam")
    assert calls == [] and skipping.writes == [GH_BROWSER_KEY]  # atladı ama not düştü
    running = Collect({"vps_browser_seen": real_ago(200)})
    calls = collect_world(monkeypatch, running, GOOD)
    cron_collect.main("kktcarabam")
    assert calls == ["KKTCarabam"] and running.writes == [GH_BROWSER_KEY]  # taradı: yine yalnız bu not (VPS kalp atışını yazmaz)


def test_other_collect_jobs_and_the_vps_never_write_the_browser_fallback_heartbeat(monkeypatch):
    where(monkeypatch, "github")
    repo = Collect(sources=[{"id": 1, "name": "KKTCar", "url": "https://www.kktcar.com/", "platform": "web"}])
    collect_world(monkeypatch, repo, GOOD)
    cron_collect.main("kktcar")
    assert GH_BROWSER_KEY not in repo.state  # yalnız tarayıcılı iş (KKTCarabam) yazar
    where(monkeypatch, "vps")
    repo = Collect()
    collect_world(monkeypatch, repo, GOOD)
    cron_collect.main("kktcarabam")
    assert GH_BROWSER_KEY not in repo.state


def test_a_github_browser_run_collects_normally_when_the_fallback_heartbeat_cannot_be_written(monkeypatch):
    where(monkeypatch, "github")
    repo = Collect(fail_write={GH_BROWSER_KEY})
    calls = collect_world(monkeypatch, repo, GOOD)
    cron_collect.main("kktcarabam")  # fırlatmaz
    assert calls == ["KKTCarabam"]
