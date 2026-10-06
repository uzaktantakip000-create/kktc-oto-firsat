"""Öz-izleme 3: Telegram dinleyicisi bekçisi (application/selfwatch.py: listener_watch). VPS tick'inde çalışır; kayıp en az 20 dk KESİNTİSİZ sürmedikçe
uyarı yok (deploy yeniden başlatması tek bir kayıp okuma verir). Sahte repo + sahte Telegram; ortak düzen tests/test_selfwatch.py'de."""
from datetime import datetime, timedelta, timezone

import pytest

from application import bot_poll, selfwatch
from application.runner_gate import VPS_TICK_KEY
from entrypoints import tick
from tests.test_runner_gate import SECRET_DSN, Tick, real_ago, where
from tests.test_selfwatch import NOW, Repo, ago, tg  # noqa: F401  (tg: pytest fixture)
from tests.test_selfwatch_failover import Boom

LISTEN = bot_poll.LISTENER_STATE_KEY
MISS, DOWN = selfwatch.LISTEN_MISS_KEY, selfwatch.LISTEN_DOWN_KEY
DOWN_TEXT = "⚠️ Telegram'da anında cevap durdu (dinleyici sessiz, en az 30 dk). Komutlarına yine cevap gelir ama ~15 dk gecikmeyle: tarama turu bakıyor."


class Clock:
    """Tick okumalarını sırayla yapar: `at(dakika, kalp_atışı)` o ana gider; kalp atışı: None = hiç yok, "" = temiz çıkışta silinmiş, sayı = o kadar dk önce yazılmış."""

    def __init__(self, repo):
        self.repo = repo

    def at(self, minutes, heartbeat=None):
        now = NOW + timedelta(minutes=minutes)
        self.repo.now = now
        if heartbeat is None:
            self.repo.state.pop(LISTEN, None)
        elif heartbeat == "":
            self.repo.state[LISTEN] = ""
        else:
            self.repo.state[LISTEN] = ago(heartbeat, now)
        selfwatch.listener_watch(self.repo, now)


@pytest.fixture
def vps(monkeypatch):
    where(monkeypatch, "vps")


def test_the_listener_keys_are_short_and_fixed():
    assert MISS == "ls_miss" and DOWN == "ls_down" and LISTEN == "bot_listen_seen"


def test_the_first_missing_reading_only_takes_a_note_and_never_alerts(vps, tg):
    repo = Repo()
    Clock(repo).at(0)
    assert tg == [] and repo.state[MISS] == f"{NOW.isoformat()}|{NOW.isoformat()}" and DOWN not in repo.state


def test_alerts_once_after_20_continuous_minutes_and_not_again_within_12_hours(vps, tg):
    repo = Repo()
    clock = Clock(repo)
    clock.at(0)
    clock.at(15)
    assert tg == []  # 15 dk: henüz 20'ye varmadı
    clock.at(30)
    assert tg == [DOWN_TEXT] and repo.state[DOWN] == NOW.isoformat()  # olay açıldı, başı = ilk görülen an
    for minutes in range(45, 12 * 60, 15):  # her 15 dk'da bir tur, 12 saate kadar
        clock.at(minutes)
    assert len(tg) == 1
    clock.at(12 * 60 + 30)  # 12 saat sonra hatırlatma (süre güncel)
    assert len(tg) == 2 and "en az 12 saat" in tg[1] and repo.state[DOWN] == NOW.isoformat()


@pytest.mark.parametrize("gap,expect", [(19, False), (20, True), (21, True)])
def test_the_20_minute_threshold_is_inclusive(vps, tg, gap, expect):
    clock = Clock(Repo())
    clock.at(0)
    clock.at(gap)
    assert bool(tg) is expect


def test_a_single_missing_reading_then_a_fresh_one_never_alerts_and_clears_the_note(vps, tg):
    """Deploy: dinleyici yeniden başlarken kalp atışı silinir, ~1 dk sonra yeniden yazılır; sıradaki tur taze görür."""
    repo = Repo()
    clock = Clock(repo)
    clock.at(0, "")
    assert MISS in repo.state and repo.state[MISS] != ""
    clock.at(15, 0.5)
    assert tg == [] and repo.state[MISS] == "" and DOWN not in repo.state


@pytest.mark.parametrize("heartbeat", [None, "", 4, 60, 600])  # yok / temiz çıkışta silinmiş / 4 dk (180 sn'den eski) / bayat
def test_a_missing_empty_or_stale_heartbeat_counts_as_missing(vps, tg, heartbeat):
    repo = Repo()
    clock = Clock(repo)
    clock.at(0, heartbeat)
    clock.at(30, heartbeat if heartbeat in (None, "") else heartbeat + 30)
    assert len(tg) == 1


@pytest.mark.parametrize("value", ["bozuk", "2026-13-45", ago(1).replace("+00:00", ""), ago(-10)])  # bozuk / saat dilimsiz / çok ileri tarihli
def test_a_garbage_naive_or_far_future_heartbeat_counts_as_missing(vps, tg, value):
    repo = Repo({LISTEN: value})
    selfwatch.listener_watch(repo, NOW)
    assert MISS in repo.state


def test_a_fresh_heartbeat_inside_the_window_means_the_listener_is_alive_and_writes_nothing(vps, tg):
    for fresh in (ago(0), ago(2.9), ago(-0.5)):  # saat farkı payı içindeki hafif ileri tarih de canlı
        repo = Repo({LISTEN: fresh})
        selfwatch.listener_watch(repo, NOW)
        assert tg == [] and repo.writes == []  # sağlıklı turda hiçbir yazma yok


def test_recovery_says_so_once_and_clears_everything(vps, tg):
    repo = Repo()
    clock = Clock(repo)
    for minutes in (0, 15, 30, 45):
        clock.at(minutes)
    assert len(tg) == 1
    clock.at(60, 0.2)
    assert len(tg) == 2 and tg[1] == "✅ Telegram'da anında cevap yeniden çalışıyor (kesinti ~60 dk)."
    assert repo.state[MISS] == "" and repo.state[DOWN] == ""
    clock.at(75, 0.2)
    clock.at(90, 0.2)
    assert len(tg) == 2  # ikinci kez yazmaz


def test_no_check_mark_when_the_alert_never_went_out(vps, tg):
    repo = Repo()
    clock = Clock(repo)
    clock.at(0)
    clock.at(10)
    clock.at(25, 0.2)  # 20 dk dolmadan düzeldi: ne ⚠️ ne ✅
    assert tg == [] and DOWN not in repo.state
    # 12 saatlik sınır içinde ikinci bir uzun kayıp: ⚠️ susar, bu yüzden ✅ da susar
    first = Repo()
    clock = Clock(first)
    for minutes in (0, 15, 30):
        clock.at(minutes)
    clock.at(45, 0.2)  # ⚠️ gitti, ✅ gitti
    assert [t[:1] for t in tg] == ["⚠", "✅"]
    for minutes in (60, 75, 90):
        clock.at(minutes)  # yeni kesinti: ⚠️ 12 saat sınırı içinde, susar
    clock.at(105, 0.2)
    assert [t[:1] for t in tg] == ["⚠", "✅"] and first.state[DOWN] == ""


def test_flapping_listener_never_accumulates_into_an_alert(vps, tg):
    repo = Repo()
    clock = Clock(repo)
    for k in range(6):  # bir tur kayıp, bir tur taze: kesintisiz değil
        clock.at(30 * k)
        clock.at(30 * k + 15, 0.5)
    assert tg == []
    clock.at(200)
    clock.at(215)
    assert tg == []  # kesintisiz 15 dk: yine yok
    clock.at(230)
    assert len(tg) == 1  # kesintisiz 30 dk: uyarı


def test_a_stale_note_from_long_ago_does_not_count_as_continuous(vps, tg):
    repo = Repo({MISS: f"{ago(300)}|{ago(285)}"})  # 5 saat önceki kayıp sayacı (turlar o arada durmuştu)
    selfwatch.listener_watch(repo, NOW)
    assert tg == [] and repo.state[MISS] == f"{NOW.isoformat()}|{NOW.isoformat()}"  # sayaç yeniden başladı
    repo = Repo({MISS: f"{ago(40)}|{ago(15)}"})  # 15 dk önceki okuma: kesintisiz sürüyor
    selfwatch.listener_watch(repo, NOW)
    assert len(tg) == 1


@pytest.mark.parametrize("garbage", ["", "bozuk", "a|b", ago(5), f"{ago(5)}|{ago(1)}|x", f"{ago(1)}|{ago(5)}", f"{ago(5).replace('+00:00', '')}|{ago(1)}"])
def test_a_garbage_note_restarts_the_count_without_error(vps, tg, capsys, garbage):
    repo = Repo({MISS: garbage})
    selfwatch.listener_watch(repo, NOW)
    assert tg == [] and repo.state[MISS] == f"{NOW.isoformat()}|{NOW.isoformat()}" and capsys.readouterr().out == ""


def test_a_failed_alert_leaves_no_incident_and_is_retried_next_round(vps, monkeypatch, tg):
    from application import health

    repo = Repo()
    clock = Clock(repo)
    clock.at(0)
    clock.at(15)
    monkeypatch.setattr(health, "api", lambda *a, **k: (_ for _ in ()).throw(health.TelegramError("sendMessage", 500, None)))
    clock.at(30)
    assert DOWN not in repo.state and tg == []
    monkeypatch.setattr(health, "api", lambda token, method, **kw: tg.append(kw["text"]))
    clock.at(45)
    assert len(tg) == 1 and repo.state[DOWN] == NOW.isoformat()


def test_a_repeat_alert_never_moves_the_start_of_an_open_incident(vps, tg):
    repo = Repo({DOWN: ago(900), MISS: f"{ago(40)}|{ago(15)}"})
    selfwatch.listener_watch(repo, NOW)
    assert len(tg) == 1 and repo.state[DOWN] == ago(900)


# --- tick.py bağlantısı -------------------------------------------------------------------------------------------------------
def run_tick(monkeypatch, runner, repo, eval_ok=True):
    where(monkeypatch, runner)
    Tick(monkeypatch, repo, eval_ok=eval_ok)
    monkeypatch.setattr(tick, "due_jobs", lambda *a: [])
    tick.main()


def test_a_vps_tick_runs_the_watch_and_the_first_missing_reading_is_silent(monkeypatch, tg):
    repo = Repo()
    run_tick(monkeypatch, "vps", repo)
    assert tg == [] and MISS in repo.state and VPS_TICK_KEY in repo.state  # tur normal bitti


def test_a_vps_tick_alerts_when_the_loss_has_lasted_20_minutes(monkeypatch, tg):
    repo = Repo({MISS: f"{real_ago(30)}|{real_ago(15)}"}, now=datetime.now(timezone.utc))
    run_tick(monkeypatch, "vps", repo)
    assert len(tg) == 1 and tg[0].startswith("⚠️ Telegram'da anında cevap durdu")


def test_the_watch_also_runs_when_the_evaluation_failed_and_the_exit_code_is_unchanged(monkeypatch, tg):
    repo = Repo({MISS: f"{real_ago(30)}|{real_ago(15)}"}, now=datetime.now(timezone.utc))
    with pytest.raises(SystemExit) as e:
        run_tick(monkeypatch, "vps", repo, eval_ok=False)
    assert e.value.code == 1 and len(tg) == 1 and VPS_TICK_KEY not in repo.state


@pytest.mark.parametrize("runner", ["github", "yerel"])
def test_the_watch_never_runs_off_the_vps(monkeypatch, tg, runner):
    repo = Repo()
    run_tick(monkeypatch, runner, repo)
    assert MISS not in repo.state and LISTEN not in repo.reads and tg == []


def test_a_failing_watch_never_breaks_or_changes_the_vps_tick(monkeypatch, tg, capsys):
    def boom(*a, **k):
        raise RuntimeError(SECRET_DSN)

    monkeypatch.setattr(selfwatch, "listener_watch", boom)
    repo = Repo()
    run_tick(monkeypatch, "vps", repo)  # çıkış 0
    assert VPS_TICK_KEY in repo.state
    out = capsys.readouterr().out
    assert "öz-izleme (dinleyici bekçisi) başarısız: RuntimeError" in out and SECRET_DSN not in out


def test_every_listener_read_or_write_error_is_swallowed(vps, tg, capsys):
    selfwatch.after_tick(Boom(), True, NOW)
    selfwatch.after_tick(Repo(fail_read={LISTEN}), False, NOW)
    selfwatch.after_tick(Repo(fail_write={MISS}), False, NOW)
    selfwatch.after_tick(Repo({MISS: f"{ago(40)}|{ago(15)}"}, fail_write={DOWN}), False, NOW)  # uyarı gitti, olay yazılamadı
    out = capsys.readouterr().out
    assert out.count("öz-izleme") >= 4 and SECRET_DSN not in out and "kullanici" not in out
