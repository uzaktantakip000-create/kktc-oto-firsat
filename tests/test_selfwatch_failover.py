"""Öz-izleme 2: sunucu turları durdu → GitHub devraldı → yeniden çalışıyor (application/selfwatch.py: github_failover, vps_recovery, after_tick,
after_browser). Sahte repo + sahte Telegram; ortak düzen tests/test_selfwatch.py'de."""
from datetime import datetime, timedelta, timezone

import pytest

from application import health, selfwatch
from application.runner_gate import VPS_TICK_KEY, mark_vps
from entrypoints import cron_collect, tick
from tests.test_runner_gate import GOOD, SECRET_DSN, Collect, Tick, collect_world, real_ago, where
from tests.test_selfwatch import NOW, Repo, ago, tg  # noqa: F401  (tg: pytest fixture)

DOWN_52 = "⚠️ Sunucu turları durdu (son tur 52 dk önce); GitHub devraldı, tarama sürüyor."
FO_TICK, FO_BROWSER = selfwatch.FO_TICK_KEY, selfwatch.FO_BROWSER_KEY


class Boom:
    """Her çağrısı hata veren repo (veritabanı çöktü)."""

    def __getattr__(self, name):
        def fail(*a, **k):
            raise RuntimeError(SECRET_DSN)
        return fail


def telegram_error(*a, **k):
    raise health.TelegramError("sendMessage", 502, "Bad Gateway")


def test_the_failover_keys_are_short_and_fixed():
    assert FO_TICK == "fo_tick" and FO_BROWSER == "fo_browser"


def test_github_alerts_once_when_the_vps_heartbeat_exists_but_is_stale_and_remembers_the_incident_start(monkeypatch, tg):
    where(monkeypatch, "github")
    repo = Repo({VPS_TICK_KEY: ago(52)})
    selfwatch.after_tick(repo, False, NOW)
    assert tg == [DOWN_52]
    assert repo.state[FO_TICK] == ago(52)  # olayın başı: VPS'in son iyi turu


def test_the_alert_is_not_repeated_within_12_hours_and_repeats_after_without_moving_the_incident_start(monkeypatch, tg):
    where(monkeypatch, "github")
    repo = Repo({VPS_TICK_KEY: ago(52)})
    selfwatch.after_tick(repo, False, NOW)
    for minutes in (15, 30, 60, 6 * 60, 12 * 60 - 1):  # GitHub her 15 dk'da bir tarar; hepsi susar
        repo.now = NOW + timedelta(minutes=minutes)
        selfwatch.after_tick(repo, False, repo.now)
    assert len(tg) == 1
    repo.now = NOW + timedelta(hours=12, minutes=1)
    selfwatch.after_tick(repo, False, repo.now)
    assert len(tg) == 2 and "son tur 12 saat önce" in tg[1]  # 12 saat sonra hatırlatma (süre güncel)
    assert repo.state[FO_TICK] == ago(52)  # başlangıç değişmedi


def test_a_repeat_alert_never_moves_the_start_of_an_already_open_incident(monkeypatch, tg):
    where(monkeypatch, "github")
    repo = Repo({VPS_TICK_KEY: ago(52), FO_TICK: ago(500)})  # açık olay (eski başlangıç); kalp atışı bu arada değişmiş
    selfwatch.after_tick(repo, False, NOW)
    assert len(tg) == 1 and repo.state[FO_TICK] == ago(500)


def test_a_recovery_is_never_held_back_by_the_repeat_limit_because_an_open_incident_means_an_alert_went_out(monkeypatch, tg):
    where(monkeypatch, "vps")
    repo = Repo({FO_TICK: ago(60)})
    selfwatch.after_tick(repo, True, NOW)
    repo.now = NOW + timedelta(minutes=1)
    repo.state[FO_TICK] = ago(30, repo.now)  # başka bir olay (ör. el ile açıldı) hemen ardından
    selfwatch.after_tick(repo, True, repo.now)
    assert [t[:1] for t in tg] == ["✅", "✅"]


@pytest.mark.parametrize("age_min,expect", [(34, False), (35, True), (36, True)])
def test_the_alert_threshold_is_the_same_as_the_githubs_takeover_threshold(monkeypatch, tg, age_min, expect):
    where(monkeypatch, "github")
    selfwatch.after_tick(Repo({VPS_TICK_KEY: ago(age_min)}), False, NOW)
    assert bool(tg) is expect


@pytest.mark.parametrize("value", [None, "", "bozuk", "2026-13-45", ago(10).replace("+00:00", ""), ago(-60), ago(-6)])
def test_no_alert_when_the_vps_heartbeat_never_existed_or_is_garbage_naive_or_in_the_future(monkeypatch, tg, capsys, value):
    where(monkeypatch, "github")
    repo = Repo({VPS_TICK_KEY: value} if value is not None else {})
    selfwatch.after_tick(repo, False, NOW)
    assert tg == [] and repo.writes == [] and capsys.readouterr().out == ""  # sessiz: ne uyarı ne hata satırı


@pytest.mark.parametrize("runner", ["vps", "yerel"])
def test_no_failover_alert_off_github(monkeypatch, tg, runner):
    where(monkeypatch, runner)
    repo = Repo({VPS_TICK_KEY: ago(300)})
    selfwatch.after_tick(repo, False, NOW)
    assert tg == [] and repo.reads == [] and repo.writes == []


def test_no_incident_is_recorded_when_the_alert_could_not_be_sent(monkeypatch):
    where(monkeypatch, "github")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)  # sahibe yazılamıyor
    repo = Repo({VPS_TICK_KEY: ago(52)})
    selfwatch.after_tick(repo, False, NOW)
    assert FO_TICK not in repo.state and repo.alerted == {}
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(health, "api", telegram_error)
    selfwatch.after_tick(repo, False, NOW)
    assert FO_TICK not in repo.state and repo.alerted == {}  # Telegram hatası: olay açılmaz, sonraki turda yeniden denenir


def test_recovery_says_so_once_with_the_outage_length_and_closes_the_incident(monkeypatch, tg):
    where(monkeypatch, "vps")
    repo = Repo({FO_TICK: ago(70)})
    selfwatch.after_tick(repo, True, NOW)
    assert tg == ["✅ Sunucu turları yeniden çalışıyor (kesinti ~70 dk; bu arada GitHub taradı)."]
    assert repo.state[FO_TICK] == ""
    selfwatch.after_tick(repo, True, NOW + timedelta(minutes=15))
    selfwatch.after_tick(repo, True, NOW + timedelta(minutes=30))
    assert len(tg) == 1  # ikinci kez yazmaz


def test_recovery_needs_a_written_heartbeat_an_open_incident_and_the_vps(monkeypatch, tg):
    where(monkeypatch, "vps")
    open_ = Repo({FO_TICK: ago(70)})
    selfwatch.after_tick(open_, False, NOW)  # tur başarısız (kalp atışı yazılmadı): henüz düzelmedi
    assert tg == [] and open_.state[FO_TICK] == ago(70)
    selfwatch.after_tick(Repo(), True, NOW)  # olay yok: sessiz
    assert tg == []
    where(monkeypatch, "yerel")
    selfwatch.after_tick(open_, True, NOW)
    assert tg == [] and open_.state[FO_TICK] == ago(70)


def test_recovery_message_failure_keeps_the_incident_open_and_it_is_retried(monkeypatch, tg):
    where(monkeypatch, "vps")
    repo = Repo({FO_TICK: ago(40)})
    monkeypatch.setattr(health, "api", telegram_error)
    selfwatch.after_tick(repo, True, NOW)
    assert repo.state[FO_TICK] == ago(40) and tg == []
    monkeypatch.setattr(health, "api", lambda token, method, **kw: tg.append(kw["text"]))
    selfwatch.after_tick(repo, True, NOW + timedelta(minutes=15))  # Telegram düzeldi
    assert len(tg) == 1 and "~55 dk" in tg[0] and repo.state[FO_TICK] == ""


@pytest.mark.parametrize("garbage", ["bozuk", ago(-600)])
def test_a_garbage_incident_record_is_closed_silently(monkeypatch, tg, garbage):
    where(monkeypatch, "vps")
    repo = Repo({FO_TICK: garbage})
    selfwatch.after_tick(repo, True, NOW)
    assert tg == [] and repo.state[FO_TICK] == ""


def test_a_brief_flap_inside_the_repeat_window_gives_one_pair_not_a_second_orphan_check_mark(monkeypatch, tg):
    where(monkeypatch, "github")
    repo = Repo({VPS_TICK_KEY: ago(40)})
    selfwatch.after_tick(repo, False, NOW)  # 1. kesinti: ⚠️
    where(monkeypatch, "vps")
    repo.now = NOW + timedelta(minutes=20)
    selfwatch.after_tick(repo, True, repo.now)  # düzeldi: ✅
    where(monkeypatch, "github")
    repo.now = NOW + timedelta(hours=3)
    repo.state[VPS_TICK_KEY] = ago(40, repo.now)  # 3 saat sonra yeniden kesinti (12 saatlik sınır içinde)
    selfwatch.after_tick(repo, False, repo.now)  # ⚠️ susar: olay açılmaz
    assert FO_TICK in repo.state and repo.state[FO_TICK] == ""
    where(monkeypatch, "vps")
    repo.now = NOW + timedelta(hours=3, minutes=30)
    selfwatch.after_tick(repo, True, repo.now)  # ✅ da susar (öncesinde ⚠️ gitmedi)
    assert len(tg) == 2 and tg[0].startswith("⚠️ Sunucu turları durdu") and tg[1].startswith("✅ Sunucu turları yeniden")


def test_a_new_outage_after_the_repeat_window_alerts_and_recovers_again(monkeypatch, tg):
    where(monkeypatch, "github")
    repo = Repo({VPS_TICK_KEY: ago(40)})
    selfwatch.after_tick(repo, False, NOW)
    where(monkeypatch, "vps")
    selfwatch.after_tick(repo, True, NOW + timedelta(minutes=20))
    later = NOW + timedelta(hours=13)
    repo.now = later
    where(monkeypatch, "github")
    repo.state[VPS_TICK_KEY] = ago(45, later)
    selfwatch.after_tick(repo, False, later)
    where(monkeypatch, "vps")
    selfwatch.after_tick(repo, True, later + timedelta(minutes=20))
    assert [t[:1] for t in tg] == ["⚠", "✅", "⚠", "✅"]


def test_durations_are_short_and_plain():
    assert selfwatch._span(timedelta(seconds=10)) == "1 dk" and selfwatch._span(timedelta(minutes=52)) == "52 dk"
    assert selfwatch._span(timedelta(minutes=119)) == "119 dk" and selfwatch._span(timedelta(minutes=120)) == "2 saat"
    assert selfwatch._span(timedelta(hours=47)) == "47 saat" and selfwatch._span(timedelta(hours=72)) == "3 gün"


def test_every_failover_check_swallows_errors_and_logs_only_the_error_type(monkeypatch, tg, capsys):
    for runner, beat in (("github", False), ("vps", True)):
        where(monkeypatch, runner)
        selfwatch.after_tick(Boom(), beat, NOW)  # fırlatmaz
        selfwatch.after_browser(Boom(), beat, NOW)
        selfwatch.after_tick(Repo({VPS_TICK_KEY: ago(52), FO_TICK: ago(60)}, fail_read={VPS_TICK_KEY, FO_TICK}), beat, NOW)
        selfwatch.after_tick(Repo({VPS_TICK_KEY: ago(52), FO_TICK: ago(60)}, fail_write={FO_TICK}), beat, NOW)  # mesaj gitti, olay yazılamadı/kapanamadı
    out = capsys.readouterr().out
    assert "RuntimeError" in out and "OperationalError" in out
    assert SECRET_DSN not in out and "GIZLI-PAROLA" not in out and "kullanici" not in out


# --- tick.py ve cron_collect.py bağlantısı ------------------------------------------------------------------------------------
def test_a_github_tick_sends_the_alert_after_scanning_and_does_not_change_the_scan(monkeypatch, tg):
    where(monkeypatch, "github")
    repo = Repo({VPS_TICK_KEY: real_ago(60)}, now=datetime.now(timezone.utc))
    world = Tick(monkeypatch, repo)
    monkeypatch.setattr(tick, "due_jobs", lambda *a: [])
    tick.main()
    assert world.eval_calls == [1] and "tick:last" in repo.state  # tarama aynen yapıldı
    assert len(tg) == 1 and tg[0].startswith("⚠️ Sunucu turları durdu (son tur 60 dk önce)")
    assert FO_TICK in repo.state


def test_a_github_tick_that_skips_sends_no_failover_alert(monkeypatch, tg):
    where(monkeypatch, "github")
    repo = Repo({VPS_TICK_KEY: real_ago(5)})
    Tick(monkeypatch, repo)
    tick.main()
    assert tg == []


def test_a_failing_selfwatch_never_breaks_or_changes_the_tick(monkeypatch, tg, capsys):
    def boom(*a, **k):
        raise RuntimeError(SECRET_DSN)

    monkeypatch.setattr(selfwatch, "github_failover", boom)
    monkeypatch.setattr(selfwatch, "vps_recovery", boom)
    for runner in ("github", "vps"):
        where(monkeypatch, runner)
        repo = Repo({VPS_TICK_KEY: real_ago(90), FO_TICK: real_ago(100)})
        world = Tick(monkeypatch, repo)
        monkeypatch.setattr(tick, "due_jobs", lambda *a: [])
        tick.main()  # çıkış 0
        assert world.eval_calls == [1] and "tick:last" in repo.state
    out = capsys.readouterr().out
    assert out.count("öz-izleme") == 2 and SECRET_DSN not in out


def test_the_vps_tick_announces_the_recovery_after_the_heartbeat_is_written(monkeypatch, tg):
    where(monkeypatch, "vps")
    repo = Repo({FO_TICK: real_ago(75)}, now=datetime.now(timezone.utc))
    Tick(monkeypatch, repo)
    monkeypatch.setattr(tick, "due_jobs", lambda *a: [])
    tick.main()
    assert len(tg) == 1 and tg[0].startswith("✅ Sunucu turları yeniden çalışıyor (kesinti ~75 dk")
    assert repo.writes.index(VPS_TICK_KEY) < repo.writes.index(FO_TICK) and repo.state[FO_TICK] == ""


def test_a_failed_evaluation_on_the_vps_gives_no_recovery_but_keeps_the_old_exit_behavior(monkeypatch, tg):
    where(monkeypatch, "vps")
    repo = Repo({FO_TICK: real_ago(75)})
    Tick(monkeypatch, repo, eval_ok=False)
    monkeypatch.setattr(tick, "due_jobs", lambda *a: [])
    with pytest.raises(SystemExit) as e:
        tick.main()
    assert e.value.code == 1 and tg == [] and VPS_TICK_KEY not in repo.state


def test_a_failed_evaluation_on_github_still_sends_the_failover_alert_then_exits_with_error(monkeypatch, tg):
    where(monkeypatch, "github")
    repo = Repo({VPS_TICK_KEY: real_ago(60)}, now=datetime.now(timezone.utc))
    Tick(monkeypatch, repo, eval_ok=False)
    monkeypatch.setattr(tick, "due_jobs", lambda *a: [])
    with pytest.raises(SystemExit) as e:
        tick.main()
    assert e.value.code == 1 and len(tg) == 1


# --- aynısı KKTCarabam tarayıcı işi için (eşik 6 saat, tekrar 24 saat) ---------------------------------------------------------
def test_browser_failover_waits_for_three_missed_vps_runs_before_alerting(monkeypatch, tg):
    where(monkeypatch, "github")
    for minutes in (151, 5 * 60 + 59):  # GitHub devraldı ama tek/iki aksayan tur uyarı sebebi değil
        selfwatch.after_browser(Repo({"vps_browser_seen": ago(minutes)}), False, NOW)
    selfwatch.after_browser(Repo(), False, NOW)  # kalp atışı hiç yok: uyarı yok
    assert tg == []
    repo = Repo({"vps_browser_seen": ago(7 * 60)})
    selfwatch.after_browser(repo, False, NOW)
    assert tg == ["⚠️ Sunucu KKTCarabam'ı okuyamıyor (son başarı 7 saat önce); GitHub devraldı, tarama sürüyor."]
    assert repo.state[FO_BROWSER] == ago(7 * 60) and FO_TICK not in repo.state


def test_browser_failover_repeats_daily_and_recovers_once(monkeypatch, tg):
    where(monkeypatch, "github")
    repo = Repo({"vps_browser_seen": ago(7 * 60)})
    selfwatch.after_browser(repo, False, NOW)
    for hours in (2, 12, 23):
        repo.now = NOW + timedelta(hours=hours)
        selfwatch.after_browser(repo, False, repo.now)
    assert len(tg) == 1
    repo.now = NOW + timedelta(hours=24, minutes=1)
    selfwatch.after_browser(repo, False, repo.now)
    assert len(tg) == 2
    where(monkeypatch, "vps")
    selfwatch.after_browser(repo, False, NOW + timedelta(hours=30))  # VPS bu turda da okuyamadı: sessiz
    assert len(tg) == 2
    selfwatch.after_browser(repo, True, NOW + timedelta(hours=31))
    selfwatch.after_browser(repo, True, NOW + timedelta(hours=33))
    assert len(tg) == 3 and tg[2] == "✅ Sunucu KKTCarabam'ı yeniden okuyor (kesinti ~38 saat; bu arada GitHub okudu)."


def test_a_github_browser_run_alerts_after_collecting_and_a_vps_success_announces_the_recovery(monkeypatch, tg):
    where(monkeypatch, "github")
    repo = Collect({"vps_browser_seen": real_ago(8 * 60)})
    repo.alert_recent = lambda key, hours: False
    repo.mark_alerted = lambda key: None
    calls = collect_world(monkeypatch, repo, GOOD)
    cron_collect.main("kktcarabam")
    assert calls == ["KKTCarabam"] and len(tg) == 1 and tg[0].startswith("⚠️ Sunucu KKTCarabam'ı okuyamıyor (son başarı 8 saat önce)")
    where(monkeypatch, "vps")
    collect_world(monkeypatch, repo, GOOD)
    cron_collect.main("kktcarabam")
    assert len(tg) == 2 and tg[1].startswith("✅ Sunucu KKTCarabam'ı yeniden okuyor") and repo.state[FO_BROWSER] == ""
    assert repo.writes.index("vps_browser_seen") < repo.writes.index(FO_BROWSER, repo.writes.index("vps_browser_seen"))  # önce kalp atışı, sonra olay kapandı


def test_a_vps_browser_run_that_could_not_read_the_site_announces_nothing(monkeypatch, tg):
    where(monkeypatch, "vps")
    started = real_ago(500)
    repo = Collect({FO_BROWSER: started})
    collect_world(monkeypatch, repo, RuntimeError("liste sayfası alınamadı"))  # Cloudflare engeli
    cron_collect.main("kktcarabam")
    assert tg == [] and repo.state[FO_BROWSER] == started and "vps_browser_seen" not in repo.state


def test_a_failing_selfwatch_never_breaks_the_browser_collection(monkeypatch, tg):
    def boom(*a, **k):
        raise RuntimeError(SECRET_DSN)

    monkeypatch.setattr(selfwatch, "github_failover", boom)
    where(monkeypatch, "github")
    repo = Collect({"vps_browser_seen": real_ago(500)})
    calls = collect_world(monkeypatch, repo, GOOD)
    cron_collect.main("kktcarabam")  # fırlatmaz
    assert calls == ["KKTCarabam"]


def test_mark_vps_reports_whether_the_heartbeat_was_written(monkeypatch):
    where(monkeypatch, "vps")
    assert mark_vps(Repo(), VPS_TICK_KEY, NOW) is True
    assert mark_vps(Repo(fail_write={VPS_TICK_KEY}), VPS_TICK_KEY, NOW) is False
    where(monkeypatch, "github")
    assert mark_vps(Repo(), VPS_TICK_KEY, NOW) is False
