"""Sosyal medya duraklatma: varsayılan kapalı, 403 → süreli duraklatma, tek sahip mesajı, alarm/sağlık/durum susar."""
from datetime import datetime, timedelta, timezone

import pytest

from application import feed_switch, health, source_alarm, status
from entrypoints import cron_collect


@pytest.fixture(autouse=True)
def _legacy_apify(monkeypatch):
    monkeypatch.setattr(feed_switch, "LEGACY_APIFY", True)  # bu dosya eski Apify yolunun davranışını da sınar (üretimde emekli, 07.10.2026)

NOW = datetime(2026, 10, 2, 9, 5, tzinfo=timezone.utc)


class FakeRepo:
    def __init__(self, state=None, sources=None, stale=None):
        self.state = dict(state or {})
        self.sources_rows, self.stale_rows = sources or [], stale or []
        self.marked, self.conn = [], self

    def get_state(self, k, default=None):
        return self.state.get(k, default)

    def set_state(self, k, v):
        self.state[k] = v

    def state_with_prefix(self, prefix):
        return {k[len(prefix):]: v for k, v in self.state.items() if k.startswith(prefix)}

    def alert_recent(self, key, hours):
        return key in self.marked

    def mark_alerted(self, key):
        self.marked.append(key)

    def alarm_sources(self):
        return self.sources_rows

    def stale_sources(self):
        return self.stale_rows

    def sources(self, platform, statuses):
        return []

    def execute(self, sql, params=None):
        class R:
            def fetchone(_):
                return {"old": True}
        return R()


ON = {"feed:instagram": "on", "feed:facebook": "on"}


def test_default_is_off_for_both_platforms():
    assert feed_switch.paused_platforms(FakeRepo(), NOW) == {"instagram": "kapali", "facebook": "kapali"}


def test_on_means_running_and_other_values_mean_off():
    assert feed_switch.paused_platforms(FakeRepo(ON), NOW) == {}
    assert feed_switch.paused_platforms(FakeRepo({"feed:instagram": "on"}), NOW) == {"facebook": "kapali"}
    assert feed_switch.paused_platforms(FakeRepo({"feed:instagram": "ON ", "feed:facebook": "evet"}), NOW) == {"facebook": "kapali"}


def test_provider_pause_expires_by_itself():
    until = (NOW + timedelta(hours=2)).isoformat()
    repo = FakeRepo(ON | {"feed:paused_until:apify": until})
    assert feed_switch.paused_platforms(repo, NOW) == {"instagram": "limit", "facebook": "limit"}
    assert feed_switch.paused_platforms(repo, NOW + timedelta(hours=3)) == {}  # kendiliğinden döner


def test_garbage_pause_value_is_ignored():
    assert feed_switch.paused_platforms(FakeRepo(ON | {"feed:paused_until:apify": "bozuk"}), NOW) == {}


def test_pause_provider_writes_state():
    repo = FakeRepo(ON)
    until = feed_switch.pause_provider(repo, "instagram", NOW)
    assert until == NOW + timedelta(hours=feed_switch.PAUSE_HOURS)
    assert feed_switch.paused_platforms(repo, NOW)["facebook"] == "limit"  # aynı sağlayıcı: ikisi de durur


def test_only_403_is_a_provider_limit():
    class Api(Exception):
        def __init__(self, code):
            self.status_code = code

    assert feed_switch.is_provider_limit(Api(403))
    assert not feed_switch.is_provider_limit(Api(500))
    assert not feed_switch.is_provider_limit(RuntimeError("403 yazıyor ama durum kodu yok"))


def test_pause_text_names_paused_platforms():
    both = feed_switch.pause_text({"instagram": "kapali", "facebook": "kapali"})
    assert both == "📴 Instagram/Facebook duraklatıldı. Siteler çalışıyor; ilanı bota iletebilirsin."
    assert feed_switch.pause_text({"facebook": "limit"}).startswith("📴 Facebook duraklatıldı.")


def test_owner_gets_one_message_not_one_per_tick():
    sent = []

    def notify(repo, key, text, repeat_hours=12):
        if key in repo.marked:
            return False
        repo.marked.append(key)
        sent.append((key, text, repeat_hours))
        return True

    repo = FakeRepo()
    assert feed_switch.announce_pause(repo, notify, NOW) is True
    assert feed_switch.announce_pause(repo, notify, NOW) is False
    assert len(sent) == 1 and sent[0][1].startswith("📴 Instagram/Facebook duraklatıldı")
    assert sent[0][2] >= 24 * 365  # kapalı anahtar: yılda bir kez bile tekrarlamaz


def test_no_message_when_nothing_is_paused():
    assert feed_switch.announce_pause(FakeRepo(ON), lambda *a, **k: pytest.fail("mesaj gitmemeliydi"), NOW) is False


def test_limit_pause_message_may_repeat_later_but_not_every_tick():
    sent = []
    repo = FakeRepo(ON | {"feed:paused_until:apify": (NOW + timedelta(hours=1)).isoformat()})
    feed_switch.announce_pause(repo, lambda r, key, text, repeat_hours=12: sent.append((key, repeat_hours)) or True, NOW)
    assert sent[0][0].startswith("feed_paused:limit") and sent[0][1] == 24 * 14


# --- alarm / sağlık / durum susar ---

def src(name, platform, hours, sid="1"):
    return dict(id=sid, name=name, platform=platform, url=f"https://x/{name}", hours_since_check=hours)


def test_paused_social_gives_no_source_alarm(monkeypatch):
    out = []
    monkeypatch.setattr(source_alarm, "notify_owner", lambda repo, key, text, repeat_hours=12: out.append(text) or True)
    repo = FakeRepo({"fail:Instagram (toplu)": "9", "fail:Facebook grupları": "5"},
                    sources=[src("a", "instagram", 200), src("g", "facebook", 90)])
    assert source_alarm.check_source_alarms(repo) == 0 and out == []


def test_paused_social_does_not_send_recovery_message(monkeypatch):
    out = []
    monkeypatch.setattr(source_alarm, "notify_owner", lambda repo, key, text, repeat_hours=12: out.append(text) or True)
    repo = FakeRepo({"srcalarm:Instagram (toplu)": "1", "srcalarm:a": "1"}, sources=[src("a", "instagram", 0.1)])
    assert source_alarm.check_source_alarms(repo) == 0 and out == []  # kapalıyken "tekrar çalışıyor" denmez


def test_open_social_still_alarms_and_sites_still_alarm(monkeypatch):
    out = []
    monkeypatch.setattr(source_alarm, "notify_owner", lambda repo, key, text, repeat_hours=12: out.append(text) or True)
    repo = FakeRepo({"feed:facebook": "on", "fail:Facebook grupları": "4", "fail:KibrisCars": "3"},
                    sources=[src("KibrisCars", "web", 0.2, "2")])
    assert source_alarm.check_source_alarms(repo) == 2
    assert any("Facebook grupları" in t for t in out) and any("KibrisCars" in t for t in out)


def test_paused_social_not_in_health_problems_but_sites_are():
    stale = [dict(id="1", name="ig", platform="instagram", url="https://instagram.com/x", created_at=None, listings_7d=0, hours_since_check=300),
             dict(id="2", name="kktcar", platform="web", url="https://kktcar.com/x", created_at=None, listings_7d=3, hours_since_check=30)]
    probs = health.source_problems(FakeRepo(stale=stale))
    assert [k for k, _ in probs] == ["stale:2"]
    assert [k for k, _ in health.source_problems(FakeRepo(ON, stale=stale))] == ["stale:1", "stale:2"]


def test_status_shows_pause_not_delay():
    rows = [dict(name="KKTCar", platform="web", url="https://kktcar.com/x", status="aktif", alert_level="yesil", hours_since_check=0.1, new_24h=4, fresh_n=10),
            dict(name="IG", platform="instagram", url="https://instagram.com/a", status="aktif", alert_level="yesil", hours_since_check=200, new_24h=0, fresh_n=5)]

    class Rows:
        def __init__(self, r):
            self.r = r

        def fetchall(self):
            return self.r

        def fetchone(self):
            return self.r[0]

    class StatusRepo(FakeRepo):
        def execute(self, sql, params=None):
            if "FROM sources s" in sql:
                return Rows(rows)
            if "FROM alerts" in sql:
                return Rows([{"strong": 0}])
            if "LATERAL" in sql:
                return Rows([{"total": 10, "ok": 4}])
            return Rows([{"n": 0}])

    text = status.build_status(StatusRepo({"tick:last": "2026-10-02T09:00:00+00:00"}), NOW)
    assert "✅ Sistem çalışıyor" in text and "gecikme" not in text.lower()  # sessizlik arıza gibi görünmez
    assert "📴 Instagram/Facebook duraklatıldı. Siteler çalışıyor; ilanı bota iletebilirsin." in text
    assert "📴 DURAKLATILANLAR" in text and "Instagram (1 kaynak)" in text and "IG ·" not in text  # kaynaklar tek satırda
    assert "SANA HABER VEREN YERLER (1)" in text  # duraklatılmış olan "haber verenler"e sayılmaz
    assert "Apify'a harcanan" not in text  # ikisi de kapalıyken harcama satırı yok


# --- toplama işi ---

def test_run_skips_paused_social_job_without_token_or_error(monkeypatch, capsys):
    monkeypatch.delenv("APIFY_TOKEN", raising=False)  # kapalıyken anahtar bile aranmaz
    monkeypatch.setattr(cron_collect, "collect_sources", lambda *a, **k: pytest.fail("çalışmamalıydı"))
    repo = FakeRepo()
    assert cron_collect.run("instagram", repo) == [] and cron_collect.run("facebook", repo) == []
    assert "duraklatılmış, atlandı" in capsys.readouterr().out


class ApifyForbidden(Exception):
    status_code = 403


def _boot(monkeypatch, collect):
    monkeypatch.setenv("APIFY_TOKEN", "t")
    monkeypatch.setattr(cron_collect.llm_reader, "from_env", lambda repo: None)
    monkeypatch.setattr(cron_collect, "collect_sources", collect)
    monkeypatch.setattr(cron_collect, "collect_facebook_groups", collect)


def test_apify_403_pauses_instead_of_failing(monkeypatch, capsys):
    def boom(*a, **k):
        raise ApifyForbidden("Monthly usage hard limit exceeded")

    _boot(monkeypatch, boom)
    repo = FakeRepo(ON)
    assert cron_collect.run("instagram", repo) == []  # hata listesine girmez
    assert "fail:Instagram (toplu)" not in repo.state  # arıza sayacı artmaz → alarm yok
    assert feed_switch.paused_platforms(repo)["instagram"] == "limit"
    assert cron_collect.run("facebook", repo) == []  # aynı sağlayıcı: Facebook da atlanır
    assert "duraklatılmış, atlandı" in capsys.readouterr().out


def test_other_apify_errors_are_still_failures(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("Apify süresi doldu")

    _boot(monkeypatch, boom)
    repo = FakeRepo(ON)
    errors = cron_collect.run("instagram", repo)
    assert errors and errors[0][0] == "Instagram (toplu)"
    assert repo.state["fail:Instagram (toplu)"] == "1"
    assert feed_switch.paused_platforms(repo) == {}


# --- duraklama bitince bekleme payı: ilk toplamadan önce sahte "taranamıyor" alarmı çıkmaz ---

def _resumed_repo(**extra):
    """Anahtar açıldı (ya da limit süresi doldu) ama ilk toplama henüz çalışmadı: kaynakların son taraması günler önce."""
    repo = FakeRepo(ON | {"feed:grace:instagram": "1", "feed:grace:facebook": "1"} | extra,
                    sources=[src("a", "instagram", 200), src("g", "facebook", 90)])
    return repo


def test_tick_marks_paused_platforms_with_grace():
    repo = FakeRepo()
    feed_switch.note_pauses(repo, feed_switch.paused_platforms(repo, NOW))
    assert repo.state["feed:grace:instagram"] == "1" and repo.state["feed:grace:facebook"] == "1"


def test_resumed_but_not_yet_collected_platform_is_quiet():
    repo = _resumed_repo()
    assert feed_switch.paused_platforms(repo, NOW) == {}  # toplama çalışabilir
    assert feed_switch.quiet_platforms(repo, NOW) == {"instagram", "facebook"}  # ama uyarılar susar


def test_no_false_stale_alarm_right_after_resume(monkeypatch):
    out = []
    monkeypatch.setattr(source_alarm, "notify_owner", lambda repo, key, text, repeat_hours=12: out.append(text) or True)
    assert source_alarm.check_source_alarms(_resumed_repo()) == 0 and out == []
    assert health.source_problems(FakeRepo(ON | {"feed:grace:instagram": "1"},
                                           stale=[dict(id="1", name="ig", platform="instagram", url="https://x", created_at=None,
                                                       listings_7d=0, hours_since_check=300)])) == []


def test_real_failures_after_resume_still_alarm(monkeypatch):
    """Bekleme payı yalnız "eski tarama zamanı"nı susturur; toplama gerçekten 3 tur hata verirse alarm gelir."""
    out = []
    monkeypatch.setattr(source_alarm, "notify_owner", lambda repo, key, text, repeat_hours=12: out.append(text) or True)
    repo = _resumed_repo(**{"fail:Instagram (toplu)": "3", "failmsg:Instagram (toplu)": "RuntimeError: x"})
    assert source_alarm.check_source_alarms(repo) == 1 and "Instagram (toplu) 3 turdur okunamıyor" in out[0]


def test_successful_collect_clears_grace_and_alarms_return(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "t")
    monkeypatch.setattr(cron_collect.llm_reader, "from_env", lambda repo: None)
    monkeypatch.setattr(cron_collect, "collect_sources", lambda *a, **k: {})
    repo = _resumed_repo()
    assert cron_collect.run("instagram", repo) == []
    assert repo.state["feed:grace:instagram"] == "0" and repo.state["feed:grace:facebook"] == "1"  # yalnız o platform
    assert feed_switch.quiet_platforms(repo, NOW) == {"facebook"}


def test_failed_collect_keeps_grace(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "t")
    monkeypatch.setattr(cron_collect.llm_reader, "from_env", lambda repo: None)

    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(cron_collect, "collect_sources", boom)
    repo = _resumed_repo()
    cron_collect.run("instagram", repo)
    assert repo.state["feed:grace:instagram"] == "1"


def test_status_does_not_call_just_resumed_source_late():
    rows = [dict(name="IG", platform="instagram", url="https://instagram.com/a", status="aktif", alert_level="yesil", hours_since_check=200, new_24h=0, fresh_n=5)]

    class Rows:
        def __init__(self, r):
            self.r = r

        def fetchall(self):
            return self.r

        def fetchone(self):
            return self.r[0]

    class StatusRepo(FakeRepo):
        def execute(self, sql, params=None):
            if "FROM sources s" in sql:
                return Rows(rows)
            if "FROM alerts" in sql:
                return Rows([{"strong": 0}])
            if "LATERAL" in sql:
                return Rows([{"total": 10, "ok": 4}])
            return Rows([{"n": 0}])

    text = status.build_status(StatusRepo(ON | {"feed:grace:instagram": "1", "tick:last": "2026-10-02T09:00:00+00:00"}), NOW)
    assert "GECİKMİŞ" not in text and "gecikme" not in text.lower()
    assert "Sistem çalışıyor" in text
