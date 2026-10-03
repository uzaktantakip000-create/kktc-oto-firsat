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


# --- Adım 2i: döviz servisi uzun süre yanıt vermezse sahibe haber ---

from datetime import datetime, timedelta, timezone  # noqa: E402


def test_fallback_start_is_recorded_once_and_cleared_when_the_service_recovers(monkeypatch):
    frankfurter._cache.clear()
    store = Store()
    store.set_state("fx:TRY", "0.02")
    frankfurter.use_store(store)
    monkeypatch.setattr(frankfurter.httpx, "get", lambda *a, **kw: (_ for _ in ()).throw(httpx.ConnectError("down")))
    assert frankfurter.gbp_rate("TRY") == 0.02
    first = store.d["fx:fallback:TRY"]
    assert first  # yedek kullanımının BAŞLANGIÇ zamanı
    frankfurter._cache.clear()
    assert frankfurter.gbp_rate("TRY") == 0.02 and store.d["fx:fallback:TRY"] == first  # tekrar yazılmaz (başlangıç korunur)

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"rates": {"GBP": 0.021}}

    frankfurter._cache.clear()
    monkeypatch.setattr(frankfurter.httpx, "get", lambda *a, **kw: Resp())
    assert frankfurter.gbp_rate("TRY") == 0.021 and store.d["fx:fallback:TRY"] == ""  # servis döndü: işaret silindi
    frankfurter._cache.clear()
    frankfurter.use_store(None)


class FxRepo(FakeRepo):
    def __init__(self, fallbacks):
        super().__init__()
        self.fallbacks = fallbacks

    def state_with_prefix(self, prefix):
        return dict(self.fallbacks) if prefix == "fx:fallback:" else {}


def test_check_fx_alerts_only_after_24_hours_of_fallback(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    sent = []
    monkeypatch.setattr(health, "api", lambda *a, **kw: sent.append(kw["text"]))
    now = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
    assert health.check_fx(FxRepo({"TRY": (now - timedelta(hours=5)).isoformat(), "EUR": ""}), now) == 0 and sent == []  # kısa süre / düzelmiş
    assert health.check_fx(FxRepo({"TRY": (now - timedelta(hours=30)).isoformat()}), now) == 1
    assert "30 saattir" in sent[0] and "TRY" in sent[0]
    assert health.check_fx(FxRepo({"TRY": "bozuk değer"}), now) == 0  # okunamayan kayıt hata vermez
