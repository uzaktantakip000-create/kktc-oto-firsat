"""Taramalar VPS'te, GitHub yedek: kalp atışı kapısı (application/runner_gate.py) ve üç girişteki bağlantısı (tick, cron_evaluate, cron_collect).
Gerçek ağ/DB yok: sahte repo. Her hata GitHub lehine çözülür (GitHub taramaya devam eder); kalp atışı yalnız VPS'te ve yalnız BAŞARIDAN sonra yazılır."""
import runpy
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

import infrastructure.config
import infrastructure.db.repository
from application import runner_gate
from application.selfwatch import GH_BROWSER_KEY, GH_TICK_KEY
from application.collect_kktcarabam import KkaStats
from application.runner_gate import (BROWSER_FRESH, TICK_FRESH, VPS_BROWSER_KEY, VPS_TICK_KEY, fresh, github_should_skip, mark_vps)
from entrypoints import cron_collect, cron_evaluate, tick

NOW = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)
SECRET_DSN = "postgresql://kullanici:GIZLI-PAROLA-987@örnek:5432/db"


def ago(minutes: float) -> str:
    return (NOW - timedelta(minutes=minutes)).isoformat()


def real_ago(minutes: float) -> str:
    """Şimdiki andan `minutes` dk önce (giriş noktaları saati kendileri okur)."""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


class Repo:
    """bot_state'in sahtesi; yazılanlar sırayla `writes`ta. `fail_read`/`fail_write`: o anahtarda veritabanı hatası."""

    def __init__(self, state=None, fail_read=(), fail_write=()):
        self.state, self.writes, self.reads = dict(state or {}), [], []
        self.fail_read, self.fail_write = set(fail_read), set(fail_write)
        self.locks = []

    def get_state(self, key, default=None):
        self.reads.append(key)
        if key in self.fail_read:
            raise psycopg.OperationalError(SECRET_DSN)
        return self.state.get(key, default)

    def set_state(self, key, value):
        if key in self.fail_write:
            raise psycopg.OperationalError(SECRET_DSN)
        self.writes.append(key)
        self.state[key] = value

    def acquire_lock(self, name, minutes=16):
        self.locks.append(name)
        return "belirteç"

    def release_lock(self, name, token):
        self.locks.append(f"-{name}")


def where(monkeypatch, runner):
    """Çalışma yerini kurar: 'github' | 'vps' | 'yerel'."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("KKTC_RUNNER", raising=False)
    if runner == "github":
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
    elif runner == "vps":
        monkeypatch.setenv("KKTC_RUNNER", "vps")


# --- fresh: saf kural -------------------------------------------------------------------------------------------------------
def test_fresh_inside_the_window_only():
    assert fresh(ago(0), NOW, TICK_FRESH) and fresh(ago(34), NOW, TICK_FRESH)
    assert not fresh(ago(35), NOW, TICK_FRESH)  # sınır dahil değil
    assert not fresh(ago(600), NOW, TICK_FRESH)
    assert fresh(ago(149), NOW, BROWSER_FRESH) and not fresh(ago(150), NOW, BROWSER_FRESH)


def test_fresh_is_false_for_missing_garbage_naive_or_non_text_values():
    for bad in (None, "", "bozuk değer", "2026-13-45", "12345", ago(10).replace("+00:00", ""), "2026-10-06", 12345, b"x", [], object()):
        assert not fresh(bad, NOW, TICK_FRESH), bad
    assert not fresh(ago(10), NOW.replace(tzinfo=None), TICK_FRESH)  # şimdiki an saat dilimsiz: güvenilmez


def test_fresh_tolerates_a_little_clock_skew_but_not_a_far_future_heartbeat():
    assert fresh(ago(-4), NOW, TICK_FRESH)  # VPS saati 4 dk ileride: yine taze
    assert not fresh(ago(-6), NOW, TICK_FRESH)  # 5 dk'dan fazla ileri: güvenilmez, GitHub çalışır
    assert not fresh(ago(-600), NOW, TICK_FRESH)


def test_fresh_never_raises_even_on_overflow():
    assert not fresh("0001-01-01T00:00:00+23:59", NOW, TICK_FRESH)
    assert not fresh("9999-12-31T23:59:59-23:59", NOW, TICK_FRESH)


def test_constants_match_the_agreed_cadence():
    assert VPS_TICK_KEY == "vps_tick_seen" and VPS_BROWSER_KEY == "vps_browser_seen"
    assert TICK_FRESH == timedelta(minutes=35) and BROWSER_FRESH == timedelta(minutes=150)
    assert TICK_FRESH < timedelta(minutes=tick.GAP_ALERT_MIN)  # GitHub, tick.py'nin 45 dk'lık kesinti uyarısından ÖNCE devralır


# --- çalışma yeri ------------------------------------------------------------------------------------------------------------
def test_where_it_runs_is_read_from_the_environment(monkeypatch):
    where(monkeypatch, "yerel")
    assert not runner_gate.on_github() and not runner_gate.on_vps()
    where(monkeypatch, "github")
    assert runner_gate.on_github() and not runner_gate.on_vps()
    where(monkeypatch, "vps")
    assert runner_gate.on_vps() and not runner_gate.on_github()
    for value in ("1", "True", "TRUE", "yes", ""):  # yalnız tam "true" / "vps"
        monkeypatch.setenv("GITHUB_ACTIONS", value)
        monkeypatch.setenv("KKTC_RUNNER", value)
        assert not runner_gate.on_github() and not runner_gate.on_vps(), value


# --- github_should_skip ------------------------------------------------------------------------------------------------------
def test_github_skips_only_on_github_and_only_while_the_heartbeat_is_fresh(monkeypatch):
    where(monkeypatch, "github")
    assert github_should_skip(Repo({VPS_TICK_KEY: ago(5)}), VPS_TICK_KEY, TICK_FRESH, NOW)
    assert not github_should_skip(Repo({VPS_TICK_KEY: ago(36)}), VPS_TICK_KEY, TICK_FRESH, NOW)  # bayat: GitHub devralır
    assert not github_should_skip(Repo(), VPS_TICK_KEY, TICK_FRESH, NOW)  # hiç kayıt yok


@pytest.mark.parametrize("runner", ["vps", "yerel"])
def test_it_never_skips_off_github_and_does_not_even_read(monkeypatch, runner):
    where(monkeypatch, runner)
    repo = Repo({VPS_TICK_KEY: ago(1)})
    assert not github_should_skip(repo, VPS_TICK_KEY, TICK_FRESH, NOW)
    assert repo.reads == []


def test_it_fails_open_when_the_heartbeat_cannot_be_read_and_logs_only_the_error_type(monkeypatch, capsys):
    where(monkeypatch, "github")
    repo = Repo({VPS_TICK_KEY: ago(1)}, fail_read={VPS_TICK_KEY})
    assert not github_should_skip(repo, VPS_TICK_KEY, TICK_FRESH, NOW)  # GitHub taramaya devam eder
    out = capsys.readouterr().out
    assert "OperationalError" in out and "GIZLI-PAROLA" not in out and "kullanici" not in out and len(out.splitlines()) == 1


def test_it_fails_open_on_any_other_exception_and_on_garbage(monkeypatch):
    where(monkeypatch, "github")

    class Boom:
        def get_state(self, key, default=None):
            raise RuntimeError("beklenmedik")

    assert not github_should_skip(Boom(), VPS_TICK_KEY, TICK_FRESH, NOW)
    assert not github_should_skip(Repo({VPS_TICK_KEY: "bozuk"}), VPS_TICK_KEY, TICK_FRESH, NOW)
    assert not github_should_skip(Repo({VPS_TICK_KEY: ago(-60)}), VPS_TICK_KEY, TICK_FRESH, NOW)  # ileri tarihli


def test_it_uses_the_current_time_when_none_is_given(monkeypatch):
    where(monkeypatch, "github")
    assert github_should_skip(Repo({VPS_TICK_KEY: datetime.now(timezone.utc).isoformat()}), VPS_TICK_KEY, TICK_FRESH)
    assert not github_should_skip(Repo({VPS_TICK_KEY: ago(24 * 60)}), VPS_TICK_KEY, TICK_FRESH)


# --- mark_vps ----------------------------------------------------------------------------------------------------------------
def test_mark_vps_writes_a_fresh_utc_iso_only_on_the_vps(monkeypatch):
    where(monkeypatch, "vps")
    repo = Repo()
    mark_vps(repo, VPS_TICK_KEY, NOW)
    assert repo.writes == [VPS_TICK_KEY] and repo.state[VPS_TICK_KEY] == NOW.isoformat()
    assert fresh(repo.state[VPS_TICK_KEY], NOW, TICK_FRESH)  # GitHub'ın okuyacağı biçim
    mark_vps(repo, VPS_BROWSER_KEY)  # `now` verilmezse şimdiki an (UTC)
    assert fresh(repo.state[VPS_BROWSER_KEY], datetime.now(timezone.utc), BROWSER_FRESH)


@pytest.mark.parametrize("runner", ["github", "yerel"])
def test_mark_vps_is_a_noop_off_the_vps(monkeypatch, runner):
    where(monkeypatch, runner)
    repo = Repo()
    mark_vps(repo, VPS_TICK_KEY, NOW)
    assert repo.writes == [] and repo.state == {}


def test_mark_vps_never_raises_and_logs_only_the_error_type(monkeypatch, capsys):
    where(monkeypatch, "vps")
    mark_vps(Repo(fail_write={VPS_TICK_KEY}), VPS_TICK_KEY, NOW)  # fırlatmaz
    out = capsys.readouterr().out
    assert "OperationalError" in out and "GIZLI-PAROLA" not in out and "kullanici" not in out


# --- tick --------------------------------------------------------------------------------------------------------------------
class Tick:
    """tick.main()'i sahte dünyada çalıştırır: sıra gelen iş yok, değerlendirme `eval_ok`'a göre; `eval_seen` değerlendirme anında kalp atışı var mıydı."""

    def __init__(self, monkeypatch, repo, eval_ok=True):
        self.repo, self.eval_calls, self.eval_seen = repo, [], []
        monkeypatch.setenv("DATABASE_URL", "postgresql://yerel/test")
        monkeypatch.setattr(tick, "load_env", lambda: None)
        monkeypatch.setattr(tick, "Repository", lambda dsn: repo)
        monkeypatch.setattr(tick.frankfurter, "use_store", lambda r: None)
        monkeypatch.setattr(tick.feed_switch, "paused_platforms", lambda r, now=None: {})
        monkeypatch.setattr(tick, "report_collect_errors", lambda r, errors: None)

        def evaluate():
            self.eval_calls.append(1)
            self.eval_seen.append(VPS_TICK_KEY in repo.state)
            if not eval_ok:
                raise RuntimeError("değerlendirme çöktü")

        monkeypatch.setattr(tick.cron_evaluate, "main", evaluate)
        monkeypatch.setattr(tick, "run_batch", lambda *a, **k: None)


def test_tick_on_github_returns_early_and_writes_only_its_own_fallback_heartbeat_while_the_vps_heartbeat_is_fresh(monkeypatch, capsys):
    where(monkeypatch, "github")
    repo = Repo({VPS_TICK_KEY: datetime.now(timezone.utc).isoformat()})
    world = Tick(monkeypatch, repo)
    monkeypatch.setattr(tick, "due_jobs", lambda *a: pytest.fail("tur başlamamalıydı"))
    tick.main()  # SystemExit yok: çıkış 0
    assert repo.writes == [GH_TICK_KEY] and "tick:last" not in repo.state  # yalnız yedeğin "buradayım" notu; tur kaydı yok
    assert repo.reads == [VPS_TICK_KEY] and world.eval_calls == []  # yalnız VPS kalp atışı okundu
    assert "VPS turları çalışıyor: GitHub turu atlandı" in capsys.readouterr().out


@pytest.mark.parametrize("heartbeat", [None, "", "bozuk", "bayat", "ileri"])
def test_tick_on_github_runs_as_before_when_the_heartbeat_is_missing_stale_or_garbage(monkeypatch, heartbeat):
    where(monkeypatch, "github")
    value = {"bayat": real_ago(60), "ileri": real_ago(-60)}.get(heartbeat, heartbeat)
    repo = Repo({VPS_TICK_KEY: value} if value is not None else {})
    world = Tick(monkeypatch, repo)
    monkeypatch.setattr(tick, "due_jobs", lambda *a: [])
    tick.main()
    assert "tick:last" in repo.state and world.eval_calls == [1]
    assert repo.state.get(VPS_TICK_KEY) == value  # GitHub kalp atışını ASLA yazmaz


def test_tick_on_github_runs_when_the_heartbeat_cannot_be_read(monkeypatch):
    where(monkeypatch, "github")
    repo = Repo({VPS_TICK_KEY: datetime.now(timezone.utc).isoformat()}, fail_read={VPS_TICK_KEY})
    world = Tick(monkeypatch, repo)
    monkeypatch.setattr(tick, "due_jobs", lambda *a: [])
    tick.main()
    assert "tick:last" in repo.state and world.eval_calls == [1]


def test_tick_on_the_vps_writes_the_heartbeat_only_after_a_successful_round(monkeypatch):
    where(monkeypatch, "vps")
    repo = Repo({VPS_TICK_KEY: real_ago(1)})  # kendi eski kalp atışı VPS'i atlatmaz
    world = Tick(monkeypatch, repo)
    monkeypatch.setattr(tick, "due_jobs", lambda *a: [])
    before = repo.state[VPS_TICK_KEY]
    tick.main()
    assert world.eval_calls == [1] and world.eval_seen == [True]  # (eski kayıt zaten vardı)
    assert repo.writes.index("tick:last") < repo.writes.index(VPS_TICK_KEY) and repo.writes[-1] == VPS_TICK_KEY  # en sonda yazıldı
    assert repo.state[VPS_TICK_KEY] != before and fresh(repo.state[VPS_TICK_KEY], datetime.now(timezone.utc), TICK_FRESH)


def test_tick_on_the_vps_does_not_write_the_heartbeat_at_the_start(monkeypatch):
    where(monkeypatch, "vps")
    repo = Repo()
    world = Tick(monkeypatch, repo)
    monkeypatch.setattr(tick, "due_jobs", lambda *a: [])
    tick.main()
    assert world.eval_seen == [False]  # değerlendirme sürerken (tur ortasında) kalp atışı yoktu
    assert VPS_TICK_KEY in repo.state  # tur başarıyla bitince yazıldı


def test_tick_on_the_vps_does_not_write_the_heartbeat_when_the_evaluation_failed(monkeypatch):
    where(monkeypatch, "vps")
    repo = Repo()
    Tick(monkeypatch, repo, eval_ok=False)
    monkeypatch.setattr(tick, "due_jobs", lambda *a: [])
    with pytest.raises(SystemExit) as e:
        tick.main()
    assert e.value.code == 1 and VPS_TICK_KEY not in repo.state and "tick:last" in repo.state  # eski çıkış davranışı korunur


def test_tick_never_writes_the_heartbeat_off_the_vps(monkeypatch):
    where(monkeypatch, "yerel")
    repo = Repo()
    Tick(monkeypatch, repo)
    monkeypatch.setattr(tick, "due_jobs", lambda *a: [])
    tick.main()
    assert VPS_TICK_KEY not in repo.state


def test_a_failing_heartbeat_write_does_not_fail_the_vps_round(monkeypatch):
    where(monkeypatch, "vps")
    repo = Repo(fail_write={VPS_TICK_KEY})
    Tick(monkeypatch, repo)
    monkeypatch.setattr(tick, "due_jobs", lambda *a: [])
    tick.main()  # fırlatmaz, çıkış 0; GitHub 35 dk sonra devralır
    assert "tick:last" in repo.state and VPS_TICK_KEY not in repo.state


# --- cron_evaluate -----------------------------------------------------------------------------------------------------------
class Evaluate:
    def __init__(self, monkeypatch, repo):
        self.repo, self.ran = repo, []
        monkeypatch.setenv("DATABASE_URL", "postgresql://yerel/test")
        monkeypatch.setattr(cron_evaluate, "load_env", lambda: None)
        monkeypatch.setattr(cron_evaluate, "Repository", lambda dsn: repo)
        monkeypatch.setattr(cron_evaluate.frankfurter, "use_store", lambda r: None)
        monkeypatch.setattr(cron_evaluate, "run", lambda r: self.ran.append(r))


def test_cron_evaluate_on_github_skips_before_the_lock_while_the_vps_heartbeat_is_fresh(monkeypatch, capsys):
    where(monkeypatch, "github")
    repo = Repo({VPS_TICK_KEY: datetime.now(timezone.utc).isoformat()})
    world = Evaluate(monkeypatch, repo)
    cron_evaluate.main(vps_gate=True)
    assert world.ran == [] and repo.locks == [] and repo.writes == []  # kilit bile alınmadı, hiçbir şey yazılmadı
    assert "VPS turları çalışıyor: GitHub değerlendirmesi atlandı" in capsys.readouterr().out


def test_the_standalone_entrypoint_runs_with_the_gate_on(monkeypatch):
    """`python -m entrypoints.cron_evaluate` (GitHub'daki collect-browser adımı) kapıyla çalışır."""
    where(monkeypatch, "github")
    repo = Repo({VPS_TICK_KEY: datetime.now(timezone.utc).isoformat()})
    monkeypatch.setenv("DATABASE_URL", "postgresql://yerel/test")
    monkeypatch.setattr(infrastructure.config, "load_env", lambda path=".env": None)
    monkeypatch.setattr(infrastructure.db.repository, "Repository", lambda dsn: repo)
    runpy.run_path(cron_evaluate.__file__, run_name="__main__")
    assert repo.locks == [] and repo.writes == [] and repo.reads == [VPS_TICK_KEY]  # atlandı: kilit yok, yalnız kalp atışı okundu


@pytest.mark.parametrize("heartbeat", [None, "bozuk", "bayat"])
def test_cron_evaluate_on_github_runs_when_the_heartbeat_is_missing_garbage_or_stale(monkeypatch, heartbeat):
    where(monkeypatch, "github")
    value = {"bayat": real_ago(60)}.get(heartbeat, heartbeat)
    repo = Repo({VPS_TICK_KEY: value} if value is not None else {})
    world = Evaluate(monkeypatch, repo)
    cron_evaluate.main(vps_gate=True)
    assert world.ran == [repo] and repo.locks == ["evaluate", "-evaluate"]


def test_cron_evaluate_on_github_runs_when_the_heartbeat_cannot_be_read(monkeypatch):
    where(monkeypatch, "github")
    repo = Repo({VPS_TICK_KEY: datetime.now(timezone.utc).isoformat()}, fail_read={VPS_TICK_KEY})
    world = Evaluate(monkeypatch, repo)
    cron_evaluate.main(vps_gate=True)
    assert world.ran == [repo]


@pytest.mark.parametrize("runner", ["vps", "yerel"])
def test_cron_evaluate_off_github_never_skips(monkeypatch, runner):
    where(monkeypatch, runner)
    repo = Repo({VPS_TICK_KEY: datetime.now(timezone.utc).isoformat()})
    world = Evaluate(monkeypatch, repo)
    cron_evaluate.main(vps_gate=True)  # VPS'in kktc-browser.service'i değerlendirmeyi hep yapar (kilit çift çalışmayı önler)
    assert world.ran == [repo] and repo.reads == []


def test_the_tick_path_calls_evaluation_without_the_gate(monkeypatch):
    """tick.py `cron_evaluate.main()`'i kapısız çağırır: GitHub turu başladıktan sonra değerlendirme ortada atlanmaz."""
    where(monkeypatch, "github")
    repo = Repo({VPS_TICK_KEY: datetime.now(timezone.utc).isoformat()})  # tur başladıktan sonra VPS kalp atışı tazelendi
    world = Evaluate(monkeypatch, repo)
    cron_evaluate.main()
    assert world.ran == [repo] and repo.reads == []


# --- cron_collect: KKTCarabam tarayıcı toplaması -----------------------------------------------------------------------------
KKA_SOURCE = {"id": 7, "name": "KKTCarabam", "url": "https://www.kktcarabam.com/arabalar", "platform": "web"}


class Collect(Repo):
    def __init__(self, state=None, sources=(KKA_SOURCE,), **kw):
        super().__init__(state, **kw)
        self._sources = list(sources)

    def sources(self, platform, statuses):
        return self._sources


def collect_world(monkeypatch, repo, result):
    """Gerçek `run` ve `main` çalışır; yalnız toplayıcı sahte. `result`: KkaStats ya da fırlatılacak hata."""
    calls = []
    monkeypatch.setenv("DATABASE_URL", "postgresql://yerel/test")
    monkeypatch.setattr(cron_collect, "load_env", lambda: None)
    monkeypatch.setattr(cron_collect, "Repository", lambda dsn: repo)
    monkeypatch.setattr(cron_collect.frankfurter, "use_store", lambda r: None)
    monkeypatch.setattr(cron_collect, "track_collect", lambda *a, **k: None)

    def fake(r, source):
        calls.append(source["name"])
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(cron_collect, "collect_kktcarabam", fake)
    monkeypatch.setattr(cron_collect, "collect_kktcar", fake)
    return calls


GOOD = KkaStats(seen=18, new=2, detail_read=2)


def test_vps_writes_the_browser_heartbeat_after_a_real_kktcarabam_success(monkeypatch):
    where(monkeypatch, "vps")
    repo = Collect()
    calls = collect_world(monkeypatch, repo, GOOD)
    cron_collect.main("kktcarabam")
    assert calls == ["KKTCarabam"] and repo.writes == [VPS_BROWSER_KEY]
    assert fresh(repo.state[VPS_BROWSER_KEY], datetime.now(timezone.utc), BROWSER_FRESH)


def test_vps_does_not_write_the_heartbeat_when_the_collector_raised_blocked_or_found_no_cards(monkeypatch):
    where(monkeypatch, "vps")
    for result in (RuntimeError("KKTCarabam: liste sayfası alınamadı (engel ya da zaman aşımı olabilir)"),  # Cloudflare engeli
                   RuntimeError("liste sayfasında hiç ilan kartı bulunamadı"),
                   KkaStats(seen=0),  # hata fırlatmadan ama kart görülmeden
                   KkaStats(seen=12, blocked=True),  # engelli işaretli
                   None):  # toplayıcı sonuç döndürmedi
        repo = Collect()
        collect_world(monkeypatch, repo, result)
        cron_collect.main("kktcarabam")
        assert repo.writes == [] and VPS_BROWSER_KEY not in repo.state, result


def test_vps_does_not_write_the_heartbeat_when_there_is_no_kktcarabam_source(monkeypatch):
    where(monkeypatch, "vps")
    repo = Collect(sources=[])
    calls = collect_world(monkeypatch, repo, GOOD)
    cron_collect.main("kktcarabam")
    assert calls == [] and repo.writes == []


def test_vps_does_not_write_the_heartbeat_when_tracking_the_result_failed(monkeypatch):
    where(monkeypatch, "vps")
    repo = Collect()
    collect_world(monkeypatch, repo, GOOD)

    def boom(repo, name, error=None):
        if error is None:
            raise RuntimeError("sayaç")  # başarı sayacı yazılamadı: `run` bunu hata sayar (errors dolar)

    monkeypatch.setattr(cron_collect, "track_collect", boom)
    cron_collect.main("kktcarabam")
    assert repo.writes == []


@pytest.mark.parametrize("runner", ["github", "yerel"])
def test_the_heartbeat_is_never_written_off_the_vps(monkeypatch, runner):
    where(monkeypatch, runner)
    repo = Collect()
    collect_world(monkeypatch, repo, GOOD)
    cron_collect.main("kktcarabam")
    assert VPS_BROWSER_KEY not in repo.state  # VPS kalp atışını yalnız VPS yazar
    assert repo.writes == ([GH_BROWSER_KEY] if runner == "github" else [])  # GitHub yalnız kendi yedek notunu yazar


def test_other_jobs_never_write_the_browser_heartbeat(monkeypatch):
    where(monkeypatch, "vps")
    repo = Collect(sources=[{"id": 1, "name": "KKTCar", "url": "https://www.kktcar.com/", "platform": "web"}])
    calls = collect_world(monkeypatch, repo, GOOD)
    cron_collect.main("kktcar")
    assert calls == ["KKTCar"] and repo.writes == []


def test_github_skips_kktcarabam_while_the_vps_browser_heartbeat_is_fresh(monkeypatch, capsys):
    where(monkeypatch, "github")
    repo = Collect({VPS_BROWSER_KEY: real_ago(100)})
    calls = collect_world(monkeypatch, repo, GOOD)
    cron_collect.main("kktcarabam")
    assert calls == [] and repo.writes == [GH_BROWSER_KEY]  # toplama yok; yalnız yedeğin "buradayım" notu
    assert "VPS KKTCarabam'ı topluyor: GitHub toplaması atlandı" in capsys.readouterr().out


@pytest.mark.parametrize("heartbeat", [None, "bozuk", "bayat", "ileri"])
def test_github_collects_kktcarabam_when_the_browser_heartbeat_is_missing_stale_or_garbage(monkeypatch, heartbeat):
    where(monkeypatch, "github")
    value = {"bayat": real_ago(151), "ileri": real_ago(-60)}.get(heartbeat, heartbeat)
    repo = Collect({VPS_BROWSER_KEY: value} if value is not None else {})
    calls = collect_world(monkeypatch, repo, GOOD)
    cron_collect.main("kktcarabam")
    assert calls == ["KKTCarabam"] and repo.writes == [GH_BROWSER_KEY]  # GitHub VPS kalp atışını yazmaz, yalnız kendi yedek notunu


def test_github_collects_kktcarabam_when_the_heartbeat_cannot_be_read(monkeypatch):
    where(monkeypatch, "github")
    repo = Collect({VPS_BROWSER_KEY: datetime.now(timezone.utc).isoformat()}, fail_read={VPS_BROWSER_KEY})
    calls = collect_world(monkeypatch, repo, GOOD)
    cron_collect.main("kktcarabam")
    assert calls == ["KKTCarabam"]


def test_github_still_collects_other_jobs_even_with_fresh_browser_heartbeat(monkeypatch):
    where(monkeypatch, "github")
    repo = Collect({VPS_BROWSER_KEY: datetime.now(timezone.utc).isoformat()},
                   sources=[{"id": 1, "name": "KKTCar", "url": "https://www.kktcar.com/", "platform": "web"}])
    calls = collect_world(monkeypatch, repo, GOOD)
    cron_collect.main("kktcar")
    assert calls == ["KKTCar"] and repo.reads == []  # yalnız tarayıcılı iş kapıya bakar


def test_run_collects_outcomes_only_when_asked_and_keeps_its_old_signature(monkeypatch):
    where(monkeypatch, "yerel")
    repo = Collect()
    collect_world(monkeypatch, repo, GOOD)
    assert cron_collect.run("kktcarabam", repo) == []  # tick.run_batch'in eski çağrısı
    outcomes = []
    assert cron_collect.run("kktcarabam", repo, outcomes) == [] and outcomes == [GOOD]


def test_kktcarabam_worked_requires_cards_seen_and_not_blocked():
    assert cron_collect.kktcarabam_worked([KkaStats(seen=1)])
    assert not cron_collect.kktcarabam_worked([])
    assert not cron_collect.kktcarabam_worked([KkaStats(seen=0)])
    assert not cron_collect.kktcarabam_worked([KkaStats(seen=5, blocked=True)])
    assert not cron_collect.kktcarabam_worked(["KKTCarabam: tamam", None, 5])  # beklenmeyen sonuç türü
