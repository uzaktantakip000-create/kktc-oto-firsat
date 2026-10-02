"""browserlike istemcisi: Scrapling yerine sahte oturum (ağ yok)."""
import sys
import types
from types import SimpleNamespace

import httpx
import pytest

from infrastructure.collectors import kibriscars, mezunum, sahibindenarabakibris
from infrastructure.http import browserlike
from infrastructure.http.browserlike import BrowserlikeClient, BrowserlikeResponse, new_browserlike_client


class FakeCurlTimeout(Exception):
    pass


class FakeCurlError(Exception):
    pass


class FakeSession:
    """scrapling.fetchers.FetcherSession yerine: with ile girilir, .get(url, **kw) -> Response benzeri."""
    routes: dict = {}
    calls: list = []

    def __init__(self, **kw):
        self.kw = kw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def get(self, url, **kw):
        FakeSession.calls.append((url, kw))
        r = FakeSession.routes[url]
        if isinstance(r, Exception):
            raise r
        status, final, body = r
        return SimpleNamespace(status=status, url=final or url, headers={"content-type": "text/html"}, body=body, encoding="UTF-8")


@pytest.fixture
def fake_scrapling(monkeypatch):
    FakeSession.routes, FakeSession.calls = {}, []
    mod = types.ModuleType("scrapling.fetchers")
    mod.FetcherSession = FakeSession
    monkeypatch.setitem(sys.modules, "scrapling", types.ModuleType("scrapling"))
    monkeypatch.setitem(sys.modules, "scrapling.fetchers", mod)
    return FakeSession


def test_response_text_decodes_body_and_raise_for_status():
    r = BrowserlikeResponse(200, "https://x.test/a", {"Content-Type": "text/html"}, "Şişli £5".encode(), "UTF-8")
    assert r.text == "Şişli £5" and r.content.startswith(b"\xc5") and r.headers["content-type"] == "text/html"
    assert r.raise_for_status() is r
    with pytest.raises(httpx.HTTPStatusError) as ei:
        BrowserlikeResponse(403, "https://x.test/a", {}, b"", None).raise_for_status()
    assert ei.value.response.status_code == 403
    assert BrowserlikeResponse(200, "u", {}, b"\xff", "bogus-enc").text  # bilinmeyen kodlama çökertmez


def test_client_get_maps_response_and_params(fake_scrapling):
    fake_scrapling.routes = {"https://x.test/l": (200, "https://x.test/l?page=2", "ç".encode())}
    with BrowserlikeClient() as c:
        r = c.get("https://x.test/l", params={"page": 2}, timeout=60)
    assert (r.status_code, r.url, r.text) == (200, "https://x.test/l?page=2", "ç")
    assert fake_scrapling.calls == [("https://x.test/l", {"params": {"page": 2}, "timeout": 60})]


def test_client_network_errors_become_httpx_errors(fake_scrapling):
    fake_scrapling.routes = {"https://x.test/t": FakeCurlTimeout("curl: (28) secret-ish"), "https://x.test/e": FakeCurlError("boom")}
    c = BrowserlikeClient()
    with pytest.raises(httpx.TimeoutException):
        c.get("https://x.test/t")
    with pytest.raises(httpx.TransportError) as ei:
        c.get("https://x.test/e")
    assert "boom" not in str(ei.value)
    c.close()


def test_fallback_to_httpx_when_scrapling_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "scrapling.fetchers", None)  # import ImportError verir
    c = new_browserlike_client("UA-test")
    assert isinstance(c, httpx.Client) and c.headers["user-agent"] == "UA-test"
    c.close()


@pytest.mark.parametrize("mod", [kibriscars, mezunum, sahibindenarabakibris])
def test_collectors_new_client_uses_browserlike(mod, fake_scrapling):
    with mod.new_client() as c:
        assert isinstance(c, BrowserlikeClient)


@pytest.mark.parametrize("mod,base", [(kibriscars, "https://kibriscars.com/araba-ilani/"), (sahibindenarabakibris, "https://sahibindenarabakibris.com/vehicle/")])
def test_fetch_detail_gone_semantics_with_browserlike_response(mod, base, fake_scrapling):
    e = mod.Entry(base + "abc/", "abc", None)
    cases = {404: mod.GONE["is_active"], 410: mod.GONE["is_active"]}
    for st, want in cases.items():
        fake_scrapling.routes = {e.url: (st, e.url, b"")}
        with mod.new_client() as c:
            assert mod.fetch_detail(c, e)["is_active"] is want
    fake_scrapling.routes = {e.url: (200, "https://" + e.url.split("/")[2] + "/", b"<html></html>")}  # ana sayfaya yönlendi
    with mod.new_client() as c:
        assert mod.fetch_detail(c, e)["is_active"] is False
    fake_scrapling.routes = {e.url: (429, e.url, b"")}
    with mod.new_client() as c:
        assert mod.fetch_detail(c, e) is None  # geçici: tekrar denenir
