import httpx
import pytest

from application import health
from infrastructure.fx import frankfurter


class FakeRepo:
    def __init__(self, sources=(), recent=False):
        self._sources, self._recent, self.marked, self.conn = list(sources), recent, [], self
        self.state = {"feed:instagram": "on", "feed:facebook": "on"}  # sosyal anahtar açık (varsayılan kapalı)

    def get_state(self, key, default=None):
        return self.state.get(key, default)

    def stale_sources(self):
        return self._sources

    def alert_recent(self, key, hours):
        return self._recent

    def mark_alerted(self, key):
        self.marked.append(key)

    def execute(self, sql, params):
        class R:
            def fetchone(_):
                return {"old": True}
        return R()


def src(name="s", hours=1.0, n7=5, url="https://instagram.com/x", platform="instagram"):
    return dict(id="1", name=name, url=url, platform=platform, created_at=None, listings_7d=n7, hours_since_check=hours)


def test_stale_and_silent_sources_flagged():
    probs = health.source_problems(FakeRepo([src("a", hours=30), src("b", hours=1, n7=0), src("c", hours=1, n7=4)]))
    assert [k.split(":")[0] for k, _ in probs] == ["stale", "silent"]


def test_kktcarabam_has_longer_limit():
    ok = health.source_problems(FakeRepo([src("k", hours=12, url="https://www.kktcarabam.com/x", platform="web")]))
    bad = health.source_problems(FakeRepo([src("k", hours=7, url="https://kktcar.com/x", platform="web")]))
    assert ok == [] and len(bad) == 1


def test_alert_not_repeated(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    sent = []
    monkeypatch.setattr(health, "api", lambda *a, **kw: sent.append(kw["text"]))
    repo = FakeRepo(recent=True)
    assert health.notify_owner(repo, "k", "x") is False and sent == []
    repo = FakeRepo(recent=False)
    assert health.notify_owner(repo, "k", "x") is True and repo.marked == ["k"]


def test_alert_text_hides_secrets(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:SECRETSECRET")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    sent = []
    monkeypatch.setattr(health, "api", lambda *a, **kw: sent.append(kw["text"]))
    health.notify_owner(FakeRepo(), "k", "hata url: https://api.telegram.org/bot123456:SECRETSECRET/x")
    assert "SECRETSECRET" not in sent[0]


class Store:
    def __init__(self):
        self.d = {}

    def get_state(self, k, default=None):
        return self.d.get(k, default)

    def set_state(self, k, v):
        self.d[k] = v


def test_fx_falls_back_to_last_known_rate(monkeypatch):
    frankfurter._cache.clear()
    store = Store()
    store.set_state("fx:TRY", "0.02")
    frankfurter.use_store(store)

    def boom(*a, **kw):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(frankfurter.httpx, "get", boom)
    assert frankfurter.gbp_rate("TRY") == 0.02
    frankfurter._cache.clear()
    frankfurter.use_store(None)
    with pytest.raises(httpx.ConnectError):
        frankfurter.gbp_rate("TRY")  # yedek yoksa hata yükselir (sessizce yanlış kur kullanılmaz)
