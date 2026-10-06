import fcntl
import json
import os
from datetime import datetime, timedelta, timezone
from random import Random
from types import SimpleNamespace

import pytest
from pathlib import Path

from application import collect_facebook as cf
from application.social_port import Cursor, FetchResult, SocialPost, SocialStop
from domain.social_brake import Signal
from entrypoints import social_worker as w
from infrastructure.fx import frankfurter
from infrastructure.social_files import FileStateStore

NOW = datetime(2026, 10, 5, 9, tzinfo=timezone.utc)  # 12:00 KKTC
IP = "203.0.113.7"
CAR = "Satılık 2013 Toyota Auris 1.6 benzin otomatik 98.000 km 7.250 STG"
CSV = ("platform,key,url,alias,slug,priority,default_steering,active\n"
       "facebook,901,https://www.facebook.com/groups/901/,fb-1,gizli-grup-1,1,,1\n"
       "facebook,902,https://www.facebook.com/groups/902/,fb-2,,2,LHD,1\n"
       "instagram,galeri_a,https://www.instagram.com/galeri_a/,ig-a,,1,,1\n")


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.setattr(cf, "gbp_rate", lambda c: 1.0)
    monkeypatch.setattr(frankfurter, "_store", None)  # işçinin use_store çağrısı diğer testlere sızmasın


class Fetcher:
    platform = "facebook"

    def __init__(self, stop=None):
        self.stop, self.calls, self.closed = stop, [], 0

    def check_egress(self):
        return IP

    def fetch_new(self, source, cursor, max_posts):
        self.calls.append(source.key)
        if self.stop:
            raise self.stop
        p = SocialPost("facebook", source.key, f"{source.key}-1", f"https://www.facebook.com/groups/{source.key}/posts/1/", NOW, CAR)
        return FetchResult([p], Cursor(p.post_id, NOW), seen=4, requests=1)

    def fetch_combined(self, sources, max_posts):
        return [SocialPost("facebook", "", "901-1", "u", NOW, CAR), SocialPost("facebook", "", "777-1", "u", NOW, CAR)]

    def close(self):
        self.closed += 1


class Loader:
    def __init__(self, fetcher=None):
        self.fetcher, self.imported, self.built, self.logins, self.browses = fetcher or Fetcher(), [], 0, 0, 0

    def __call__(self, name):
        self.imported.append(name)
        return SimpleNamespace(build=self.build, login=self.login, browse=self.browse)

    def build(self, env, state_dir):
        self.built += 1
        return self.fetcher

    def login(self, env, state_dir):
        self.logins += 1

    def browse(self, env, state_dir):
        self.browses += 1


@pytest.fixture
def env(tmp_path):
    (tmp_path / "sources.csv").write_text(CSV)
    return {"SOCIAL_MODE": "trial", "SOCIAL_STATE_DIR": str(tmp_path / "state"), "SOCIAL_SOURCES_CSV": str(tmp_path / "sources.csv"),
            "SOCIAL_EXPECTED_IP_FACEBOOK": IP}


def main(argv, env, loader=None, now=NOW):
    lines = []
    code = w.main(argv, env, clock=lambda: now, sleep=lambda s: None, rng=Random(1), import_module=loader or Loader(), log=lines.append)
    return code, lines


def store(env, platform="facebook"):
    return FileStateStore(os.path.join(env["SOCIAL_STATE_DIR"], f"{platform}.state.json"))


def test_mode_guard_refuses_anything_but_trial(env):
    for mode in (None, "", "db", "live"):
        e = {k: v for k, v in env.items() if k != "SOCIAL_MODE"} | ({"SOCIAL_MODE": mode} if mode is not None else {})
        loader = Loader()
        code, lines = main(["run", "facebook"], e, loader)
        assert code == 1 and "2. aşamada" in lines[0] and loader.imported == []
        assert main(["compare", "facebook"], e, loader)[0] == 1


def test_run_happy_path_writes_trial_file_and_schedules(env, tmp_path):
    loader = Loader()
    code, lines = main(["run", "facebook"], env, loader)
    assert code == 0 and loader.imported == ["infrastructure.collectors.facebook_browser"] and loader.fetcher.closed == 1
    rows = [json.loads(x) for x in (tmp_path / "state" / "trial" / "facebook-20261005.jsonl").read_text().splitlines()]
    assert sorted(r["alias"] for r in rows) == ["fb-1", "fb-2"] and {r["outcome"] for r in rows} == {"ilan"}
    st = store(env)
    assert st.get_state("social:next_after:facebook") and st.get_state("social:started_at:facebook") == NOW.isoformat()
    text = "\n".join(lines)
    assert "tur bitti" in text and "901" not in text and "gizli-grup" not in text
    oct_mode = os.stat(tmp_path / "state" / "facebook.state.json").st_mode & 0o777
    assert oct_mode == 0o600


def test_run_publishes_status_file_and_skips_when_folder_missing(env, tmp_path):
    main(["run", "facebook"], env)  # varsayılan/ayarsız klasör yok: sessizce atlanır, tur bozulmaz
    folder = tmp_path / "durum"
    folder.mkdir()
    env = env | {"SOCIAL_STATUS_DIR": str(folder)}
    store(env).set_state("social:next_after:facebook", (NOW - timedelta(minutes=1)).isoformat())
    code, _ = main(["run", "facebook"], env)
    doc = json.loads((folder / "durum.json").read_text())
    fb = doc["platformlar"]["facebook"]
    assert code == 0 and doc["surum"] == 1 and fb["sonuc"] == "tamam" and fb["son_tur_utc"] == NOW.isoformat()
    assert "instagram" not in doc["platformlar"] and "901" not in json.dumps(doc)
    code, _ = main(["run", "facebook"], env)  # sıra gelmedi: yine yazılır (sonraki tur güncel kalır)
    assert json.loads((folder / "durum.json").read_text())["platformlar"]["facebook"]["sonraki_tur_utc"]


def test_not_due_builds_no_fetcher(env):
    loader = Loader()
    store(env).set_state("social:next_after:facebook", (NOW + timedelta(hours=1)).isoformat())
    code, lines = main(["run", "facebook"], env, loader)
    assert code == 0 and loader.imported == [] and loader.built == 0 and "sıra gelmedi" in lines[-1]
    night = datetime(2026, 10, 5, 22, tzinfo=timezone.utc)  # 01:00 KKTC
    code, lines = main(["run", "instagram"], env, loader, now=night)
    assert code == 0 and loader.imported == [] and "pencere" in lines[-1]


def test_force_skips_window_but_never_brake(env):
    loader = Loader()
    store(env).set_state("social:next_after:facebook", (NOW + timedelta(hours=1)).isoformat())
    assert main(["run", "facebook", "--force"], env, loader)[0] == 0 and loader.built == 1
    store(env).set_state("social:brake:facebook", json.dumps({"signal": "checkpoint", "reason": "doğrulama", "at": NOW.isoformat()}))
    loader2 = Loader()
    code, lines = main(["run", "facebook", "--force"], env, loader2)
    assert code == 2 and loader2.imported == [] and "fren" in lines[-1]
    pause = Loader()
    store(env, "instagram").set_state("social:paused_until:instagram", (NOW + timedelta(hours=3)).isoformat())
    code, lines = main(["run", "instagram", "--force"], env, pause)
    assert code == 0 and pause.imported == [] and "duraklama" in lines[-1]  # SOFT duraklama: atlandı, fren kodu değil


def test_lock_held_means_already_running(env, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    fd = os.open(state / "facebook.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        loader = Loader()
        code, lines = main(["run", "facebook"], env, loader)
        assert code == 0 and loader.imported == [] and "sürüyor" in lines[0]
        assert main(["resume", "facebook", "--yes"], env)[0] == 1
        other = Loader()
        assert main(["run", "instagram"], env | {"SOCIAL_EXPECTED_IP_INSTAGRAM": IP}, other)[0] == 0 and other.built == 1  # ayrı kilit
    finally:
        os.close(fd)


def test_brake_this_cycle_exits_2_then_resume(env):
    loader = Loader(Fetcher(stop=SocialStop(Signal.CHECKPOINT, "doğrulama")))
    code, lines = main(["run", "facebook"], env, loader)
    assert code == 2 and "FREN" in "\n".join(lines) and loader.fetcher.closed == 1
    assert main(["run", "facebook", "--force"], env, Loader())[0] == 2  # hâlâ çekili
    code, lines = main(["resume", "facebook"], env)
    assert code == 1 and "--yes" in lines[0]
    code, lines = main(["resume", "facebook", "--yes"], env)
    assert code == 0 and "kaldırıldı" in lines[0]
    again = Loader()
    assert main(["run", "facebook", "--force"], env, again)[0] == 0 and again.built == 1


def test_status_shows_aliases_only(env):
    main(["run", "facebook"], env, Loader())
    store(env).set_state("social:source_errors:facebook:902", json.dumps({"count": 2, "last_at": NOW.isoformat(), "detail": "bulunamadı"}))
    code, lines = main(["status"], env)
    text = "\n".join(lines)
    assert code == 0 and "fren yok" in text and "fb-2: üst üste 2 kez" in text and "instagram: henüz hiç çalışmadı" in text
    assert "902" not in text and "gizli-grup" not in text


def test_login_uses_lazy_module(env):
    loader = Loader()
    code, lines = main(["login", "instagram"], env, loader)
    assert code == 0 and loader.logins == 1 and loader.imported == ["infrastructure.collectors.instagram_instaloader"]


def test_browse_is_facebook_only_and_respects_platform_lock(env, tmp_path):
    loader = Loader()
    code, _ = main(["browse", "facebook"], env, loader)
    assert code == 0 and loader.browses == 1 and loader.imported == ["infrastructure.collectors.facebook_browser"]
    code, lines = main(["browse", "instagram"], env, loader)
    assert code == 1 and loader.browses == 1
    with w.platform_lock(Path(env["SOCIAL_STATE_DIR"]), "facebook") as got:  # okuma turu sürüyor
        assert got
        code, lines = main(["browse", "facebook"], env, loader)
    assert code == 1 and loader.browses == 1 and "çalışma sürüyor" in lines[-1]


def test_missing_fetcher_module_is_clear_error(env):
    def missing(name):
        raise ModuleNotFoundError(name=name)

    out = []
    code = w.main(["run", "facebook"], env, clock=lambda: NOW, sleep=lambda s: None, rng=Random(1), import_module=missing, log=out.append)
    assert code == 1 and "okuyucu modülü yüklenemedi" in out[-1]


def test_bad_csv_and_bad_command_exit_1(env, tmp_path):
    (tmp_path / "sources.csv").write_text("platform,key\n")
    code, lines = main(["run", "facebook"], env)
    assert code == 1 and "sütun eksik" in lines[-1]
    assert main(["run", "tiktok"], env)[0] == 1  # argparse'ın 2'si fren koduyla karışmaz
    assert main(["compare", "instagram"], env)[0] == 1


def test_compare_writes_coverage_json(env, tmp_path):
    loader = Loader()
    code, lines = main(["compare", "facebook", "--force"], env, loader)
    assert code == 0 and loader.fetcher.closed == 1
    files = list((tmp_path / "state" / "trial").glob("compare-facebook-*.json"))
    out = json.loads(files[0].read_text())
    g = out["gruplar"]
    assert g["fb-1"]["kapsama"] == 1.0 and g["fb-2"]["kapsama"] == 0.0 and g["fb-2"]["yalniz_grup_ici"] == ["902-1"]
    assert out["birlesik_kaynak_disi"] == 1 and set(g) == {"fb-1", "fb-2"}
    assert store(env).get_state("social:next_after:facebook")  # karşılaştırma da okuma sayılır: sıradaki tur ertelenir
    assert store(env).get_state("social:cursor:facebook:901") is None  # imleç ilerlemez


def test_compare_without_combined_feed(env):
    class Plain(Fetcher):
        fetch_combined = None

    loader = Loader(Plain())
    code, lines = main(["compare", "facebook", "--force"], env, loader)
    assert code == 1 and loader.fetcher.closed == 1 and "fetch_combined" in lines[-1]


def test_rate_proxy_routes_fx_through_platform_proxy_and_restores(monkeypatch):
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.setenv("HTTP_PROXY", "http://eski:1")
    with w.rate_proxy({"SOCIAL_PROXY_FACEBOOK": "http://u:p@192.0.2.10:8000"}, "facebook"):
        assert os.environ["HTTPS_PROXY"] == os.environ["HTTP_PROXY"] == "http://u:p@192.0.2.10:8000"
    assert "HTTPS_PROXY" not in os.environ and os.environ["HTTP_PROXY"] == "http://eski:1"
    with w.rate_proxy({}, "instagram"):  # proxy yoksa hiçbir şey değişmez (okuyucu zaten kurulamaz)
        assert "HTTPS_PROXY" not in os.environ
