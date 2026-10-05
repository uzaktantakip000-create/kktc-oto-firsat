"""Bot anında dinleyici (entrypoints/bot_listen): kalp atışı, GitHub turunun yoklamayı atlaması, uzun yoklama ayarı, hata/409/bağlantı kopması,
temiz çıkış. Gerçek ağ/DB yok: sahte repo, sahte Telegram çağrısı, sahte saat."""
import os
import signal
from datetime import datetime, timedelta, timezone

import httpx
import psycopg
import pytest

from application import bot_poll, notify
from application.bot_poll import LISTENER_FRESH_SECONDS, LISTENER_STATE_KEY, listener_alive, listener_running
from entrypoints import bot_listen, cron_evaluate
from infrastructure.db.repository import Repository
from infrastructure.fx import frankfurter
from tests.conftest import safe_test_dsn
from tests.test_eval_guard import Repo as CronRepo
from tests.test_eval_guard import wire as wire_cron

NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)
OWNER = "1"


def ago(seconds: float) -> str:
    return (NOW - timedelta(seconds=seconds)).isoformat()


# --- kalp atışı: saf kural --------------------------------------------------------------------------------------------
def test_listener_alive_only_when_the_heartbeat_is_fresh():
    assert listener_alive(ago(0), NOW) and listener_alive(ago(60), NOW)
    assert listener_alive(ago(LISTENER_FRESH_SECONDS - 1), NOW)
    assert not listener_alive(ago(LISTENER_FRESH_SECONDS), NOW)  # sınır dahil değil
    assert not listener_alive(ago(3600), NOW)


def test_listener_alive_is_false_for_missing_or_unusable_values():
    for bad in (None, "", "bozuk değer", "2026-13-45", "12345", ago(10).replace("+00:00", "")):  # sonuncusu: saat dilimsiz
        assert not listener_alive(bad, NOW), bad
    assert not listener_alive(ago(10), NOW.replace(tzinfo=None))


def test_listener_alive_tolerates_small_clock_skew_but_not_a_far_future_heartbeat():
    assert listener_alive(ago(-30), NOW)  # VPS saati 30 sn ileride: yine ayakta
    assert not listener_alive(ago(-600), NOW)  # 10 dk ileri: güvenilmez, GitHub turu yoklar


def test_listener_running_reads_the_heartbeat_from_bot_state():
    class R:
        def __init__(self, value):
            self.value = value

        def get_state(self, key, default=None):
            assert key == LISTENER_STATE_KEY
            return self.value if self.value is not None else default

    assert listener_running(R(ago(5)), NOW) and not listener_running(R(ago(500)), NOW) and not listener_running(R(None), NOW)
    assert listener_running(R(datetime.now(timezone.utc).isoformat()))  # `now` verilmezse şimdiki an


# --- GitHub turu: dinleyici ayaktaysa yoklamayı atlar -----------------------------------------------------------------------
def _cron_world(monkeypatch):
    wire_cron(monkeypatch, lambda repo, settings, book=None, failures=None, quick=False, backlog=True: [])
    calls = {"poll": [], "menu": 0}
    monkeypatch.setattr(cron_evaluate, "poll_bot", lambda *a, **k: calls["poll"].append((a, k)))
    monkeypatch.setattr(cron_evaluate, "ensure_menu", lambda *a, **k: calls.__setitem__("menu", calls["menu"] + 1))
    return calls


def test_cron_skips_polling_while_the_listener_heartbeat_is_fresh(monkeypatch, capsys):
    calls = _cron_world(monkeypatch)
    repo = CronRepo()
    repo.state[LISTENER_STATE_KEY] = datetime.now(timezone.utc).isoformat()
    cron_evaluate.run(repo)
    assert calls["poll"] == [] and calls["menu"] == 1  # yoklama yok, komut menüsü yine kontrol edilir
    assert "bot: anında dinleyici çalışıyor, bu turda yoklama atlandı" in capsys.readouterr().out


@pytest.mark.parametrize("seen", [None, "", "bozuk", "stale"])
def test_cron_polls_as_before_when_the_heartbeat_is_stale_missing_or_garbage(monkeypatch, capsys, seen):
    calls = _cron_world(monkeypatch)
    repo = CronRepo()
    if seen == "stale":
        seen = (datetime.now(timezone.utc) - timedelta(seconds=LISTENER_FRESH_SECONDS + 60)).isoformat()
    if seen is not None:
        repo.state[LISTENER_STATE_KEY] = seen
    cron_evaluate.run(repo)
    assert calls["poll"] == [((repo, "t"), {"owner_chat_id": "1"})]  # eskisiyle aynı çağrı: timeout/ek parametre yok
    assert "yoklama atlandı" not in capsys.readouterr().out and calls["menu"] == 1


def test_cron_polls_when_the_heartbeat_cannot_be_read(monkeypatch):
    calls = _cron_world(monkeypatch)

    class Broken(CronRepo):
        def get_state(self, key, default=None):
            if key == LISTENER_STATE_KEY:
                raise psycopg.OperationalError("okunamadı")
            return super().get_state(key, default)

    cron_evaluate.run(Broken())
    assert len(calls["poll"]) == 1  # kalp atışı okunamadı: ayakta sayılmaz, eskisi gibi yoklanır


# --- poll_bot: uzun yoklama ayarı, cron yolu eskisi gibi ---------------------------------------------------------------------
class PollRepo:
    def __init__(self):
        self.state, self.conn = {}, type("C", (), {"execute": lambda *a, **k: None})()

    def get_state(self, key, default=None):
        return self.state.get(key, default)

    def set_state(self, key, value):
        self.state[key] = value


def _capture_api(monkeypatch, updates=()):
    calls = []

    def fake_api(token, method, **kw):
        calls.append((method, kw))
        return list(updates) if method == "getUpdates" else {}

    monkeypatch.setattr(bot_poll, "api", fake_api)
    return calls


def test_poll_bot_default_is_the_old_short_poll_without_extra_fields(monkeypatch):
    upserts = []
    monkeypatch.setattr(bot_poll, "_upsert_owner", lambda repo, chat: upserts.append(chat))
    calls = _capture_api(monkeypatch)
    assert bot_poll.poll_bot(PollRepo(), "t", OWNER) == 0
    assert calls == [("getUpdates", {"offset": 0, "timeout": 0, "allowed_updates": ["message", "callback_query"]})]  # http_timeout bile yok
    assert upserts == [OWNER]  # cron yolu sahip kaydını her yoklamada yapar


def test_poll_bot_passes_the_long_poll_timeout_and_a_larger_http_timeout(monkeypatch):
    upserts = []
    monkeypatch.setattr(bot_poll, "_upsert_owner", lambda repo, chat: upserts.append(chat))
    calls = _capture_api(monkeypatch)
    bot_poll.poll_bot(PollRepo(), "t", OWNER, timeout=50, upsert_owner=False)
    (method, kw), = calls
    assert method == "getUpdates" and kw["timeout"] == 50 and kw["http_timeout"] > 50  # HTTP okuma süresi Telegram'ın beklemesinden büyük
    assert upserts == []  # dinleyici yolu her yoklamada veritabanına yazmaz


def test_poll_bot_calls_on_update_after_each_update_and_saves_the_offset_first(monkeypatch):
    monkeypatch.setattr(bot_poll, "_upsert_owner", lambda repo, chat: None)
    monkeypatch.setattr(bot_poll, "_handle_message", lambda *a: None)
    _capture_api(monkeypatch, [{"update_id": 10, "message": {}}, {"update_id": 11, "message": {}}])
    repo, seen = PollRepo(), []
    assert bot_poll.poll_bot(repo, "t", OWNER, on_update=lambda: seen.append(repo.state["tg_offset"])) == 2
    assert seen == ["11", "12"]


def test_api_keeps_the_20_second_default_and_accepts_a_longer_http_timeout(monkeypatch):
    seen = []

    class Resp:
        status_code = 200

        def json(self):
            return {"result": []}

    monkeypatch.setattr(notify.httpx, "post", lambda url, json, timeout: seen.append((json, timeout)) or Resp())
    notify.api("t", "getMe")
    notify.api("t", "getUpdates", timeout=50, http_timeout=65)
    assert seen == [({}, 20), ({"timeout": 50}, 65)]  # http_timeout Telegram'a giden gövdeye karışmaz


# --- dinleyici döngüsü --------------------------------------------------------------------------------------------------------
class FakeConn:
    closed = False
    broken = False

    def close(self):
        self.closed = True


class ListenRepo:
    def __init__(self):
        self.state, self.conn, self.writes = {}, FakeConn(), []

    def get_state(self, key, default=None):
        return self.state.get(key, default)

    def set_state(self, key, value):
        self.writes.append((key, value))
        self.state[key] = value


class World:
    """Sahte dünya: saat, bekleme, repo açma, owner kaydı ve yoklama. `script`: her yoklamada sırayla dönen sonuç ya da fırlatılan hata."""

    def __init__(self, monkeypatch, script, stop=None):
        self.t, self.waits, self.repos, self.polls, self.owner_syncs = 0.0, [], [], [], []
        self.script, self.stop = list(script), stop or bot_listen.Stop()
        self.cleared = []
        monkeypatch.setattr(bot_poll, "sync_owner", lambda repo, owner: self.owner_syncs.append(repo))
        monkeypatch.setattr(frankfurter, "use_store", lambda repo: None)
        monkeypatch.setattr(frankfurter, "clear_cache", lambda: self.cleared.append(self.t))

    def open_repo(self):
        repo = ListenRepo()
        self.repos.append(repo)
        return repo

    def poll(self, repo, token, owner, timeout=0, upsert_owner=True, on_update=None):
        self.polls.append({"repo": repo, "token": token, "owner": owner, "timeout": timeout, "upsert_owner": upsert_owner, "on_update": on_update})
        self.t += 50  # her uzun yoklama ~50 sn sürer
        step = self.script.pop(0)
        if not self.script:
            self.stop.requested = True  # betiğin son adımından sonra döngü durur (o adımın sonucu yine de işlenir)
        if isinstance(step, Exception):
            raise step
        return step

    def stop_now(self):
        self.stop.requested = True
        return 0

    def wait(self, seconds):
        self.waits.append(seconds)
        self.t += seconds

    def run(self):
        bot_listen.listen(self.open_repo, "t", OWNER, self.stop, poll=self.poll, wait=self.wait, clock=lambda: self.t,
                          utcnow=lambda: NOW + timedelta(seconds=self.t))


def tg_error(status, text="x"):
    return notify.TelegramError("getUpdates", status, text)


def test_first_iteration_syncs_the_owner_polls_with_long_timeout_and_writes_a_heartbeat(monkeypatch):
    w = World(monkeypatch, [0])  # tek yoklama; sonra sahte dünya durdurur
    w.run()
    assert len(w.repos) == 1 and w.owner_syncs == [w.repos[0]]
    (p,) = w.polls
    assert p["timeout"] == bot_listen.POLL_TIMEOUT_S == 50 and p["upsert_owner"] is False and p["owner"] == OWNER and callable(p["on_update"])
    beats = [v for k, v in w.repos[0].writes if k == LISTENER_STATE_KEY and v]
    assert len(beats) == 1 and datetime.fromisoformat(beats[0]) == NOW + timedelta(seconds=50)  # UTC, yoklama başarılı bittikten sonra


def test_owner_is_synced_once_not_on_every_poll(monkeypatch):
    w = World(monkeypatch, [0, 0, 3, 0, 0])
    w.run()
    assert len(w.polls) == 5 and len(w.owner_syncs) == 1  # her yoklamada değil: yalnız başta
    assert all(p["upsert_owner"] is False for p in w.polls)


def test_heartbeat_is_written_at_most_once_per_interval(monkeypatch):
    w = World(monkeypatch, [0, 0, 0, 0])
    w.run()  # yoklamalar 50 sn aralıkla: her biri bir kalp atışı
    beats = [v for k, v in w.repos[0].writes if k == LISTENER_STATE_KEY and v]
    assert len(beats) == len(set(beats)) == 4

    # çok sık biten yoklamalarda (yoğun sohbet) aralık korunur
    w2 = World(monkeypatch, [0] * 12)

    def quick_poll(repo, token, owner, timeout=0, upsert_owner=True, on_update=None):
        w2.t += 5  # 5 sn'de biten yoklama
        if len(w2.polls) == 11:
            w2.stop.requested = True
        w2.polls.append(1)
        return 0

    w2.poll = quick_poll
    w2.run()
    seen = [v for k, v in w2.repos[0].writes if k == LISTENER_STATE_KEY and v]
    assert len(seen) == 2  # t=5 (ilk) ve t=50; aradaki yoklamalar yazmaz


def test_on_update_beat_keeps_the_heartbeat_fresh_during_a_long_batch(monkeypatch):
    w = World(monkeypatch, [])

    def long_batch(repo, token, owner, timeout=0, upsert_owner=True, on_update=None):
        for _ in range(3):  # üç uzun ilan kontrolü: her biri sonrası kalp atışı
            w.t += 100
            on_update()
        w.stop_now()
        return 3

    w.poll = long_batch
    w.run()
    beats = [v for k, v in w.repos[0].writes if k == LISTENER_STATE_KEY and v]
    assert len(beats) == 3  # 100, 200, 300. sn (poll sonrası beat aralık içinde olduğu için yazmaz)


def test_errors_back_off_5_10_30_60_then_stay_at_60_and_reset_after_a_success(monkeypatch, capsys):
    w = World(monkeypatch, [tg_error(0, "ConnectError")] * 5 + [2] + [tg_error(0, "ReadTimeout")] + [0])
    w.run()
    assert w.waits == [5, 10, 30, 60, 60, 5]  # 6. hata: başarıdan sonra yeniden 5 sn
    out = capsys.readouterr().out
    assert "bot: bağlantı düzeldi" in out and "TelegramError" in out and out.count("hatası") >= 6


def test_unexpected_exceptions_never_crash_the_loop(monkeypatch):
    w = World(monkeypatch, [ValueError("beklenmedik"), RuntimeError("x"), httpx.ConnectError("boom"), 0])
    w.run()  # fırlatmaz
    assert w.waits == [5, 10, 30] and len(w.repos) == 1  # veritabanı kopmadı: aynı bağlantı sürer


def test_error_text_is_redacted_in_the_log(monkeypatch, capsys):
    secret = "sk-or-GIZLI-DEGER-123456"
    monkeypatch.setenv("TEST_API_KEY", secret)
    w = World(monkeypatch, [RuntimeError("x" * 140 + secret), 0])
    w.run()
    out = capsys.readouterr().out
    assert secret not in out and secret[:8] not in out and "***" in out


def test_409_conflict_waits_30_seconds_and_logs_once_per_streak(monkeypatch, capsys):
    w = World(monkeypatch, [tg_error(409, "Conflict"), tg_error(409, "Conflict"), tg_error(409, "Conflict"), 0, tg_error(409, "Conflict"), 0])
    w.run()
    assert w.waits == [30, 30, 30, 30]  # üst üste hata gibi katlanmaz
    out = capsys.readouterr().out
    assert out.count("Telegram 409") == 2  # seri başına bir satır (başarıyla biten seri + yeni seri)


def test_409_and_other_errors_do_not_write_a_heartbeat(monkeypatch):
    """Ayakta ama dinleyemiyorsa "ayaktayım" yazılmaz: yoksa GitHub turu da yoklamayı bırakır ve kimse dinlemez."""
    w = World(monkeypatch, [tg_error(409), tg_error(0, "ConnectError"), tg_error(500, "x")])
    w.run()
    heartbeat_writes = [v for repo in w.repos for k, v in repo.writes if k == LISTENER_STATE_KEY]
    assert heartbeat_writes == [""]  # tek yazım: temiz çıkışta silme; hiçbir hata turunda "ayaktayım" yazılmadı


def test_database_loss_reopens_the_connection_and_syncs_the_owner_again(monkeypatch):
    w = World(monkeypatch, [psycopg.OperationalError("bağlantı koptu"), 0])
    w.run()
    assert len(w.repos) == 2 and w.repos[0].conn.closed  # eski bağlantı kapatıldı, yenisi açıldı
    assert w.owner_syncs == w.repos  # yeni bağlantıda sahip kaydı yeniden
    assert w.polls[1]["repo"] is w.repos[1] and w.waits == [5]


def test_a_closed_connection_is_also_replaced_even_for_other_error_types(monkeypatch):
    w = World(monkeypatch, [])

    def poll(repo, token, owner, timeout=0, upsert_owner=True, on_update=None):
        w.t += 1
        if len(w.polls) == 0:
            w.polls.append(repo)
            repo.conn.closed = True
            raise RuntimeError("cursor kapalı")
        w.polls.append(repo)
        return w.stop_now()

    w.poll = poll
    w.run()
    assert len(w.repos) == 2


def test_database_down_at_startup_is_retried_not_fatal(monkeypatch):
    w = World(monkeypatch, [0])
    attempts = []
    real_open = w.open_repo

    def flaky_open():
        attempts.append(1)
        if len(attempts) < 3:
            raise psycopg.OperationalError("açılamadı")
        return real_open()

    w.open_repo = flaky_open
    w.run()
    assert len(attempts) == 3 and w.waits == [5, 10] and len(w.repos) == 1


def test_fx_cache_is_cleared_hourly(monkeypatch):
    w = World(monkeypatch, [0] * 80)  # 80 × 50 sn = 4000 sn
    w.run()
    assert len(w.cleared) == 1 and w.cleared[0] >= bot_listen.FX_REFRESH_S


def test_clean_exit_clears_the_heartbeat_and_closes_the_connection(monkeypatch):
    w = World(monkeypatch, [0])
    w.run()
    repo = w.repos[0]
    assert repo.state[LISTENER_STATE_KEY] == "" and not bot_poll.listener_alive(repo.state[LISTENER_STATE_KEY], NOW)  # GitHub turu hemen devralır
    assert repo.conn.closed


def test_stop_flag_finishes_the_current_poll_then_exits_without_waiting(monkeypatch):
    stop = bot_listen.Stop()
    w = World(monkeypatch, [], stop=stop)

    def poll(repo, token, owner, timeout=0, upsert_owner=True, on_update=None):
        w.polls.append(1)
        stop.request()  # yoklama sürerken SIGTERM geldi
        return 2  # yoklama yine de biter (güncellemeler işlendi)

    w.poll = poll
    w.run()
    assert len(w.polls) == 1 and w.waits == []


# --- sinyaller ve bekleme ---------------------------------------------------------------------------------------------------------
def test_sigterm_and_sigint_set_the_stop_flag_instead_of_killing_the_process():
    stop = bot_listen.Stop()
    old = {s: signal.signal(s, stop.request) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        os.kill(os.getpid(), signal.SIGTERM)
        assert stop.requested
        with pytest.raises(KeyboardInterrupt):  # ikinci sinyal (ikinci Ctrl+C) beklemeden keser
            os.kill(os.getpid(), signal.SIGINT)
    finally:
        for s, h in old.items():
            signal.signal(s, h)


def test_pause_returns_early_when_a_stop_is_requested():
    stop, slept = bot_listen.Stop(), []
    t = [0.0]

    def sleep(seconds):
        slept.append(seconds)
        t[0] += seconds
        if len(slept) == 2:
            stop.requested = True

    bot_listen.pause(60, stop, sleep=sleep, clock=lambda: t[0])
    assert slept == [1.0, 1.0]  # 60 sn'lik bekleme 2 sn'de bitti
    slept.clear()
    t[0], stop.requested = 0.0, False
    bot_listen.pause(2.5, stop, sleep=lambda s: (slept.append(s), t.__setitem__(0, t[0] + s)), clock=lambda: t[0])
    assert slept == [1.0, 1.0, 0.5]  # durdurma yoksa tam süre beklenir


def test_main_requires_env_and_never_prints_secrets(monkeypatch, capsys):
    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(os.path.dirname(__file__))  # .env aranmaz
    with pytest.raises(SystemExit) as exit_info:
        bot_listen.main()
    assert exit_info.value.code == bot_listen.CONFIG_EXIT_CODE == 78  # systemd bu kodda yeniden başlatmaz
    assert "Ortam değişkeni eksik" in capsys.readouterr().out

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:SECRET-TOKEN-VALUE")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "987654321")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:pw-gizli@localhost/x")
    seen = {}
    monkeypatch.setattr(bot_listen, "listen", lambda open_repo, token, owner, stop, **k: seen.update(token=token, owner=owner))
    old = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        bot_listen.main()
    finally:
        for s, h in old.items():
            signal.signal(s, h)
    out = capsys.readouterr().out
    assert "bot dinleyicisi başladı: sürüm" in out and "bot dinleyicisi durdu" in out
    for secret in ("SECRET-TOKEN-VALUE", "987654321", "pw-gizli"):
        assert secret not in out
    assert seen == {"token": "123456:SECRET-TOKEN-VALUE", "owner": "987654321"}


def test_code_version_is_a_short_sha_or_question_mark(monkeypatch):
    assert bot_listen.code_version() == "?" or 4 <= len(bot_listen.code_version()) <= 12

    def boom(*a, **k):
        raise FileNotFoundError("git yok")

    monkeypatch.setattr(bot_listen.subprocess, "run", boom)
    assert bot_listen.code_version() == "?"


# --- gerçek PostgreSQL (yalnız TEST_DATABASE_URL varsa) -----------------------------------------------------------------------
@pytest.mark.db
def test_heartbeat_round_trip_through_the_real_bot_state_table(db, monkeypatch):
    """Dinleyicinin yazdığı kalp atışını GitHub turunun kodu (listener_running) gerçek tablodan okur; temiz çıkış silince yoklama geri döner."""
    monkeypatch.setattr(bot_poll, "sync_owner", lambda repo, owner: None)
    monkeypatch.setattr(frankfurter, "use_store", lambda repo: None)
    stop = bot_listen.Stop()
    seen = []

    def poll(repo, token, owner, timeout=0, upsert_owner=True, on_update=None):
        seen.append(listener_running(repo))  # ilk başarılı yoklamadan önce kalp atışı yok
        stop.requested = True
        return 0

    bot_listen.listen(lambda: Repository(safe_test_dsn()), "t", OWNER, stop, poll=poll, wait=lambda s: None)  # dinleyici KENDİ bağlantısını açar/kapatır
    assert seen == [False]
    assert db.get_state(LISTENER_STATE_KEY) is not None  # başarılı yoklamadan sonra yazıldı, temiz çıkışta silindi
    assert db.get_state(LISTENER_STATE_KEY) == "" and not listener_running(db)

    db.set_state(LISTENER_STATE_KEY, datetime.now(timezone.utc).isoformat())  # UTC ISO metni gerçek tabloda okunur
    assert listener_running(db)
