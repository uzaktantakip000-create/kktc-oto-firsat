"""⚡ Facebook hızlı şerit (entrypoints/fb_fast, 11.10.2026): okuyucu gönderiyi yazınca yalnız yeni Facebook ilanları hemen değerlendirilir.
Sahte veritabanı ve sahte adımlar; ağ yok."""
from datetime import datetime, timezone

import pytest

from application import social_import as si
from application.social_import import ImportReport
from entrypoints import cron_evaluate, fb_fast
from tests.test_social_import import FakeRepo, _file, _line, _rows

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)


class Repo:
    def __init__(self, held=()):
        self.held, self.state, self.log = set(held), {}, []

    def acquire_lock(self, name, minutes=16):
        if name in self.held:
            return None
        self.held.add(name)
        self.log.append(("al", name))
        return f"t-{name}"

    def release_lock(self, name, token):
        assert token == f"t-{name}"
        self.held.discard(name)
        self.log.append(("bırak", name))

    def get_state(self, k, default=None):
        return self.state.get(k, default)

    def set_state(self, k, v):
        self.state[k] = v


@pytest.fixture
def world(monkeypatch):
    calls = []
    w = type("W", (), {})()
    w.calls, w.report, w.boom = calls, ImportReport(new_listings=2), None

    def imp(repo, d, log):
        calls.append(("aktar", d))
        return w.report

    def locked(repo, d, **kw):
        token = repo.acquire_lock(si.IMPORT_LOCK, minutes=si.IMPORT_LOCK_MIN)
        if not token:
            return None
        try:
            return imp(repo, d, kw.get("log"))
        finally:
            repo.release_lock(si.IMPORT_LOCK, token)

    monkeypatch.setattr(fb_fast.social_import, "import_facebook_locked", locked)
    monkeypatch.setattr(fb_fast, "load_settings", lambda repo: cron_evaluate.Settings())
    monkeypatch.setattr(fb_fast, "mark_duplicates", lambda repo, now, since: calls.append(("kopya", since)) or 1)

    def evaluate(repo, s, book, since, platform):
        calls.append(("değerlendir", since, platform))
        if w.boom:
            raise w.boom
        return ["ev"]

    monkeypatch.setattr(fb_fast, "evaluate_new", evaluate)
    monkeypatch.setattr(fb_fast.cron_evaluate, "deliver",
                        lambda repo, token, owner, s, evaluated, book, platform: calls.append(("gönder", evaluated, platform)) or (1, 1, 0))
    monkeypatch.setattr(fb_fast.ask_price, "run", lambda repo, send, s, since: calls.append(("foto", since)) or 0)
    monkeypatch.setattr(fb_fast.hunt, "run", lambda repo, owner, send, s: calls.append(("av",)) or 0)
    return w


def test_new_facebook_listings_are_deduped_evaluated_and_sent_at_once(world):
    repo, out = Repo(), []
    assert fb_fast.run_round(repo, "/devir", "t", "1", log=out.append) is True
    names = [c[0] for c in world.calls]
    assert names == ["aktar", "kopya", "değerlendir", "gönder", "foto", "av"]  # kopya taraması değerlendirmeden önce
    since = world.calls[1][1]
    assert world.calls[2] == ("değerlendir", since, "facebook") and world.calls[3] == ("gönder", ["ev"], "facebook")
    assert world.calls[4] == ("foto", since)  # okumalar yalnız az önce aktarılanlarla daralır
    assert repo.log == [("al", "sosyal_devir"), ("bırak", "sosyal_devir"), ("al", "evaluate"), ("bırak", "evaluate")] and not repo.held
    assert out[-1].startswith("hızlı şerit: yeni=2 kopya=1 değerlendirilen=1 güçlü=1 bildirilen=1")


def test_nothing_new_means_no_evaluation_lock_and_no_reads(world):
    world.report = ImportReport(lines=5, posts=5)  # araç olmayan gönderiler
    repo = Repo()
    assert fb_fast.run_round(repo, "/devir", "t", "1", log=lambda _: None) is True
    assert [c[0] for c in world.calls] == ["aktar"] and ("al", "evaluate") not in repo.log


def test_tick_importing_or_evaluating_leaves_the_rows_to_the_tick(world):
    out = []
    assert fb_fast.run_round(Repo(held={"sosyal_devir"}), "/devir", "t", "1", log=out.append) is False
    assert world.calls == [] and "tur şu an aktarıyor" in out[0]
    repo = Repo(held={"evaluate"})
    assert fb_fast.run_round(repo, "/devir", "t", "1", wait=lambda repo, name: None, log=out.append) is False
    assert [c[0] for c in world.calls] == ["aktar"] and "15 dakikalık tur işleyecek" in out[-1]
    assert repo.held == {"evaluate"}  # turun kilidine dokunulmaz


def test_a_crash_releases_the_evaluation_lock(world):
    world.boom = RuntimeError("çöktü")
    repo = Repo()
    with pytest.raises(RuntimeError):
        fb_fast.run_round(repo, "/devir", "t", "1", log=lambda _: None)
    assert not repo.held  # tur 16 dk kilitli kalmaz


def test_wait_lock_polls_until_the_other_side_finishes_or_gives_up():
    """Ortak bekleme: hızlı şerit turun değerlendirmesini (en çok 4 dk), tur hızlı şeridi (en çok 90 sn) bekler; tur artık atlanmaz."""
    class R:
        def __init__(self, free_after):
            self.n, self.free_after = 0, free_after

        def acquire_lock(self, name, minutes=16):
            self.n += 1
            return "t" if self.n > self.free_after else None

    t = [0.0]
    sleep = lambda s: t.__setitem__(0, t[0] + s)  # noqa: E731
    assert cron_evaluate.wait_lock(R(2), "evaluate", wait_s=60, poll_s=10, sleep=sleep, clock=lambda: t[0]) == "t" and t[0] == 20
    t[0] = 0.0
    assert cron_evaluate.wait_lock(R(99), "evaluate", wait_s=60, poll_s=10, sleep=sleep, clock=lambda: t[0]) is None and t[0] == 60
    assert cron_evaluate.LOCK_WAIT_S <= 90 and fb_fast.LOCK_WAIT_S <= 240  # turun 13 dakikalık bütçesine sığar


def test_daily_cap_counts_runs_per_kktc_day(monkeypatch):
    monkeypatch.setattr(fb_fast, "MAX_RUNS_PER_DAY", 2)
    repo = Repo()
    assert fb_fast.take_run(repo, NOW) and fb_fast.take_run(repo, NOW) and not fb_fast.take_run(repo, NOW)
    assert repo.state == {"hizli:gun:2026-10-14": "2"}
    assert fb_fast.take_run(repo, datetime(2026, 10, 14, 21, 30, tzinfo=timezone.utc))  # KKTC'de 15.10 00:30: yeni gün


def test_main_settles_first_and_loops_only_while_new_lines_arrive(world, monkeypatch, capsys):
    repo, slept, more = Repo(), [], [True, False]
    monkeypatch.setattr(fb_fast, "load_env", lambda: None)
    monkeypatch.setattr(fb_fast, "require", lambda name: "x")
    monkeypatch.setattr(fb_fast, "Repository", lambda url: repo)
    monkeypatch.setattr(fb_fast.frankfurter, "use_store", lambda repo: None)
    monkeypatch.setattr(fb_fast.social_import, "has_new_lines", lambda repo, d: more.pop(0))
    fb_fast.main(devir_dir="/devir", sleep=slept.append)
    assert slept == [fb_fast.SETTLE_S, fb_fast.ROUND_GAP_S]  # önce birleşme beklemesi; ikinci tur yalnız yeni satır gelince
    assert [c[0] for c in world.calls].count("aktar") == 2 and more == []
    assert "hızlı şerit:" in capsys.readouterr().out


def test_has_new_lines_sees_only_complete_lines_after_the_cursor(tmp_path):
    repo = FakeRepo(_rows())
    assert si.has_new_lines(repo, tmp_path) is False
    _file(tmp_path, NOW, _line("1000000001"))
    assert si.has_new_lines(repo, tmp_path) is True
    si.import_facebook(repo, tmp_path)
    assert si.has_new_lines(repo, tmp_path) is False
    with open(next(tmp_path.glob("facebook-*.jsonl")), "a") as f:
        f.write('{"yarım": ')  # okuyucu hâlâ yazıyor
    assert si.has_new_lines(repo, tmp_path) is False


def test_import_lock_is_shared_and_released(tmp_path):
    class R(FakeRepo):
        def __init__(self, rows, held=()):
            super().__init__(rows)
            self.held = set(held)

        def acquire_lock(self, name, minutes=16):
            if name in self.held:
                return None
            self.held.add(name)
            return "t"

        def release_lock(self, name, token):
            self.held.discard(name)

    _file(tmp_path, NOW, _line("1000000001"))
    assert si.import_facebook_locked(R(_rows(), held={"sosyal_devir"}), tmp_path) is None
    repo = R(_rows())
    rep = si.import_facebook_locked(repo, tmp_path)
    assert rep.lines == 1 and not repo.held


def test_two_failures_in_a_row_alert_the_owner_once_and_a_success_resets(world, monkeypatch):
    repo, sent = Repo(), []
    monkeypatch.setattr(fb_fast, "load_env", lambda: None)
    monkeypatch.setattr(fb_fast, "require", lambda name: "x")
    monkeypatch.setattr(fb_fast, "Repository", lambda url: repo)
    monkeypatch.setattr(fb_fast.frankfurter, "use_store", lambda repo: None)
    monkeypatch.setattr(fb_fast, "notify_owner", lambda repo, key, text, **kw: sent.append((key, text)))
    world.boom = RuntimeError("veritabanı")
    with pytest.raises(SystemExit):
        fb_fast.main(devir_dir="/devir", sleep=lambda s: None)
    assert sent == [] and repo.state[fb_fast.FAIL_KEY] == "1"  # tek hıçkırık: uyarı yok
    with pytest.raises(SystemExit):
        fb_fast.main(devir_dir="/devir", sleep=lambda s: None)
    assert [k for k, _ in sent] == ["hizli_serit_hata"] and "RuntimeError" in sent[0][1] and "15 dakikalık tur" in sent[0][1]
    world.boom = None
    fb_fast.main(devir_dir="/devir", sleep=lambda s: None)
    assert repo.state[fb_fast.FAIL_KEY] == "0"
