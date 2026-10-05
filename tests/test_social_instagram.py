"""Instagram okuyucusu (infrastructure/collectors/instagram_instaloader.py). Ağ yok, gerçek instaloader/playwright gerekmez:
sahte modüller sys.modules'e konur. Saat sabit (NOW): tarihe bağlı test yok."""
import json
import os
import stat
import sys
import types
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from application.social_port import Cursor, DailyCap, SocialSource, SocialStop, SourceError, Unreachable
from domain.social_brake import Signal
from infrastructure.collectors import instagram_instaloader as ig

NOW = datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc)
PROXY = "http://kullanici:Gizli%40Sifre@proxy.example.net:8000"
ENV = {ig.PROXY_ENV: PROXY}
FIXTURE = Path(__file__).parent / "fixtures" / "social" / "instagram_timeline_page.json"
SOURCE = SocialSource(platform="instagram", key="ornek_galeri", url="https://www.instagram.com/ornek_galeri/", alias="ig1")
COOKIES = {"sessionid": "SESSION123456", "csrftoken": "CSRF123456", "ds_user_id": "424242", "mid": "MID1"}
READER = "okuyucu_hesap"


def at(**ago) -> int:
    return int((NOW - timedelta(**ago)).timestamp())


def node(code, ts, pinned=False, user="ornek_galeri", caption="ilan"):
    n = {"code": code, "taken_at": ts, "media_type": 1, "caption": {"text": caption},
         "image_versions2": {"candidates": [{"url": f"https://cdn.example/{code}.jpg"}]}, "user": {"username": user}}
    if pinned:
        n["timeline_pinned_user_ids"] = [1000]
    return n


def page(nodes, more=False):
    return {"data": {"xdt_api__v1__feed__user_timeline_graphql_connection": {
        "edges": [{"node": n} for n in nodes], "page_info": {"has_next_page": more, "end_cursor": "c" if more else None}}},
        "status": "ok"}


def codes(result):
    return [p.post_id for p in result.posts]


# --- durma kuralları (saf) ----------------------------------------------------------------------------------------------

def test_cursor_hit_stops_and_output_is_newest_first():
    nodes = [node("N3", at(hours=1)), node("N2", at(hours=2)), node("N1", at(hours=3)), node("C", at(hours=5)),
             node("O1", at(hours=6))]
    r = ig.scan_timeline(nodes, "ornek_galeri", Cursor("C", NOW - timedelta(hours=5)), 50, NOW)
    assert codes(r) == ["N3", "N2", "N1"] and r.seen == 4
    assert r.cursor == Cursor("N3", NOW - timedelta(hours=1))


def test_three_pinned_on_top_then_four_old_in_a_row_stops():
    cursor = Cursor("SILINDI", NOW - timedelta(days=1))  # imleç gönderisi silinmiş: tarihe göre durulur
    nodes = [node("P1", at(days=40), pinned=True), node("P2", at(days=30)), node("P3", at(days=20)),
             node("N1", at(hours=1)), node("N2", at(hours=2)),
             node("O1", at(days=2)), node("O2", at(days=3)), node("O3", at(days=4)), node("O4", at(days=5)), node("O5", at(days=6))]
    r = ig.scan_timeline(iter(nodes), "ornek_galeri", cursor, 50, NOW)
    assert codes(r) == ["N1", "N2"] and r.seen == 9  # O5 hiç istenmedi
    assert r.cursor.post_id == "N1"
    nothing_new = ig.scan_timeline(nodes[:3] + nodes[5:], "ornek_galeri", cursor, 50, NOW)
    assert codes(nothing_new) == [] and nothing_new.seen == 4 and nothing_new.cursor == cursor


def test_first_run_reads_only_last_three_days_and_skips_old_pinned():
    nodes = [e["node"] for e in json.loads(FIXTURE.read_text(encoding="utf-8"))["data"][
        "xdt_api__v1__feed__user_timeline_graphql_connection"]["edges"]]
    r = ig.scan_timeline(nodes, "ornek_galeri", Cursor(), 50, NOW)
    assert codes(r) == ["YENI3", "YENI2", "YENI1"]  # ESKI1 5 gün, sabit gönderi 40 gün önce
    assert r.cursor.post_id == "YENI3" and r.seen == 5


def test_first_run_without_recent_posts_still_sets_cursor():
    nodes = [node("P1", at(days=40), pinned=True), node("O1", at(days=4)), node("O2", at(days=5)), node("O3", at(days=6))]
    r = ig.scan_timeline(nodes, "ornek_galeri", Cursor(), 50, NOW)
    assert r.posts == [] and r.cursor.post_id == "O1"  # sonraki okuma yalnız bundan yenisini ister


def test_max_posts_caps_the_read():
    nodes = [node(f"N{i}", at(minutes=i + 1)) for i in range(10)]
    r = ig.scan_timeline(nodes, "ornek_galeri", Cursor("X", NOW - timedelta(days=1)), 3, NOW)
    assert codes(r) == ["N0", "N1", "N2"] and r.seen == 3 and r.cursor.post_id == "N0"


def test_cursor_ignores_pinned_and_unflagged_pinned_is_detected_by_order():
    cursor = Cursor("C", NOW - timedelta(days=1))
    nodes = [node("PYENI", at(minutes=5), pinned=True),  # yeni ve sabitlenmiş: döner ama imleç olmaz
             node("PSIRA", at(hours=5)),  # alanı yok ama altındakinden eski: sabit
             node("N1", at(hours=1)), node("N2", at(hours=3)), node("C", at(days=1))]
    r = ig.scan_timeline(nodes, "ornek_galeri", cursor, 50, NOW)
    assert codes(r) == ["PYENI", "N1", "N2", "PSIRA"]
    assert {p.post_id: p.pinned for p in r.posts} == {"PYENI": True, "N1": False, "N2": False, "PSIRA": True}
    assert r.cursor.post_id == "N1"


def test_cursor_post_pinned_on_top_does_not_hide_new_posts():
    cursor = Cursor("C", NOW - timedelta(days=1))
    nodes = [node("C", at(days=1), pinned=True), node("N1", at(hours=1)), node("N2", at(hours=2)),
             node("O1", at(days=2)), node("O2", at(days=3)), node("O3", at(days=4)), node("O4", at(days=5))]
    r = ig.scan_timeline(nodes, "ornek_galeri", cursor, 50, NOW)
    assert codes(r) == ["N1", "N2"] and r.seen == 7  # sabit imleç eski sayılır, 4 eski art arda ile durulur


def test_no_posts_keeps_cursor_and_unrecognized_nodes_are_a_source_error():
    cursor = Cursor("C", NOW - timedelta(days=1))
    empty = ig.scan_timeline([], "ornek_galeri", cursor, 50, NOW)
    assert empty.posts == [] and empty.seen == 0 and empty.cursor == cursor
    pulled = []

    def broken():
        for i in range(20):
            pulled.append(i)
            yield {"beklenmeyen": i}

    with pytest.raises(SourceError):
        ig.scan_timeline(broken(), "ornek_galeri", cursor, 50, NOW)
    assert len(pulled) == ig.OLD_RUN_STOP  # bozuk düğümde sayfa sayfa sonsuz okunmaz


def test_post_fields_from_timeline_node():
    nodes = {e["node"]["code"]: e["node"] for e in json.loads(FIXTURE.read_text(encoding="utf-8"))["data"][
        "xdt_api__v1__feed__user_timeline_graphql_connection"]["edges"]}
    carousel = ig.post_from_node(nodes["YENI3"], "ornek_galeri")
    assert carousel.url == "https://www.instagram.com/p/YENI3/" and carousel.platform == "instagram"
    assert carousel.image_url == "https://cdn.example/vitz_1080.jpg"  # üstte görsel yok: ilk karusel öğesi
    assert carousel.posted_at == NOW - timedelta(hours=2) and carousel.posted_at.tzinfo is not None
    assert carousel.owner == "ornek_galeri" and "FİYAT: 9.500 STG" in carousel.text and not carousel.pinned
    assert ig.post_from_node(nodes["YENI1"], "ornek_galeri").text == ""
    assert ig.post_from_node(nodes["PINESKI1"], "ornek_galeri").pinned
    assert ig.post_from_node({"code": "X", "taken_at": at(hours=1)}, "ornek_galeri").owner == "ornek_galeri"
    assert ig.post_from_node({"code": "X"}, "ornek_galeri") is None


def test_timeline_edges_validates_shape():
    assert len(ig.timeline_edges(json.loads(FIXTURE.read_text(encoding="utf-8")))["edges"]) == 5
    for bad in ({}, {"data": None}, {"data": {"xdt_api__v1__feed__user_timeline_graphql_connection": None}}):
        with pytest.raises(SourceError):
            ig.timeline_edges(bad)


# --- sahte instaloader --------------------------------------------------------------------------------------------------

class FakeSession:
    def __init__(self, cookies=None):
        self.proxies, self.trust_env, self.hooks = {}, True, {"response": []}
        self.headers, self.cookies, self.sent = {"User-Agent": "ua"}, dict(cookies or {}), []
        self.reply = lambda req: FakeHttp({"ip": "203.0.113.7"})

    def send(self, req, **kwargs):
        self.sent.append((req, kwargs))
        return self.reply(req)


@dataclass
class FakeHttp:
    data: dict
    status_code: int = 200

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"{self.status_code}")

    def json(self):
        return self.data


class FakeRateController:
    def __init__(self, context):
        self._context, self.base_calls, self.base_sleeps = context, [], []

    def sleep(self, secs):
        self.base_sleeps.append(secs)

    def wait_before_query(self, query_type):
        self.base_calls.append(query_type)

    def handle_429(self, query_type):
        self.sleep(666)  # gerçek sınıf burada uzun uyur


def fake_instaloader(script=None, version="4.15.3"):
    """instaloader 4.15.3'ün kullanılan yüzü: Instaloader, RateController, NodeIterator, instaloadercontext.copy_session."""
    script = script or {}
    mod = types.ModuleType("instaloader")
    mod.__version__ = version
    mod.loaders = []

    class Context:
        def __init__(self):
            self._session, self.username, self.queries = FakeSession(), None, []

        def get_anonymous_session(self):
            return FakeSession()

    class Instaloader:
        def __init__(self, **kwargs):
            self.kwargs, self.context, self.closed = kwargs, Context(), False
            self.context._rate_controller = kwargs["rate_controller"](self.context)
            mod.loaders.append(self)

        def load_session(self, username, data):  # gerçeği gibi YENİ oturum açar (proxy'siz)
            self.context._session, self.context.username = FakeSession(data), username

        def test_login(self):
            self.context._rate_controller.wait_before_query("d6f4427fbe92d846298cf93df0b937d3")
            return READER

        def close(self):
            self.closed = True

    class NodeIterator:  # gerçeği gibi: ilk sayfa kurulurken, sonraki sayfa yalnız gerekince istenir
        def __init__(self, context, query_hash, edge_extractor, node_wrapper, query_variables=None, query_referer=None,
                     first_data=None, is_first=None, doc_id=None):
            self.context, self.extract, self.wrap, self.doc_id = context, edge_extractor, node_wrapper, doc_id
            self.pages = list(script[query_variables["username"]])
            context.queries.append((doc_id, query_variables, query_referer))
            self.data, self.i = self._query(), 0

        def _query(self):
            self.context._rate_controller.wait_before_query(self.doc_id)
            reply = self.pages.pop(0)
            if isinstance(reply, BaseException):
                raise reply
            return self.extract(reply)

        def __iter__(self):
            return self

        def __next__(self):
            if self.i < len(self.data["edges"]):
                self.i += 1
                return self.wrap(self.data["edges"][self.i - 1]["node"])
            if self.data["page_info"].get("has_next_page"):
                self.data, self.i = self._query(), 0
                return self.__next__()
            raise StopIteration

    def copy_session(session, request_timeout=None):  # 4.15.3 gibi: yalnız çerez + başlık
        new = FakeSession(session.cookies)
        new.headers = dict(session.headers)
        return new

    mod.Instaloader, mod.RateController, mod.NodeIterator = Instaloader, FakeRateController, NodeIterator
    mod.instaloadercontext = types.SimpleNamespace(copy_session=copy_session)
    return mod


@pytest.fixture
def state(tmp_path):
    ig.write_session(tmp_path / ig.SESSION_FILE, ig.SavedSession(READER, dict(COOKIES), "Mozilla/5.0 Test"), NOW)
    return tmp_path


def make_fetcher(monkeypatch, state, script, sleeps=None, clock=None):
    mod = fake_instaloader(script)
    monkeypatch.setitem(sys.modules, "instaloader", mod)
    sleeps = [] if sleeps is None else sleeps
    fetcher = ig.build(ENV, state, now=lambda: NOW, sleep=sleeps.append, monotonic=clock or (lambda: 1000.0))
    return fetcher, mod.loaders[0]


# --- build: proxy, oturum, seçenekler -----------------------------------------------------------------------------------

def test_build_minimal_traffic_options_and_proxy_on_every_session(monkeypatch, state):
    fetcher, loader = make_fetcher(monkeypatch, state, {})
    k = loader.kwargs
    assert k["iphone_support"] is False and k["max_connection_attempts"] == 1 and k["quiet"] and k["sleep"]
    assert not any(k[o] for o in ("download_pictures", "download_videos", "download_video_thumbnails", "download_geotags",
                                  "download_comments", "save_metadata"))
    assert k["user_agent"] == "Mozilla/5.0 Test" and k["request_timeout"] == ig.REQUEST_TIMEOUT_S
    s = loader.context._session
    assert s.proxies == {"http": PROXY, "https": PROXY} and s.trust_env is False and len(s.hooks["response"]) == 1
    assert loader.context.username == READER and s.cookies["sessionid"] == COOKIES["sessionid"]
    copied = sys.modules["instaloader"].instaloadercontext.copy_session(s)  # GraphQL istekleri kopya oturumdan çıkar
    assert copied.proxies == s.proxies and copied.trust_env is False and copied.hooks == s.hooks
    anon = loader.context.get_anonymous_session()
    assert anon.proxies == s.proxies and anon.trust_env is False
    assert fetcher.platform == "instagram"


def test_build_fails_closed_without_proxy_before_importing_instaloader(monkeypatch, state):
    monkeypatch.setitem(sys.modules, "instaloader", None)  # import edilirse ImportError olurdu
    for env in ({}, {ig.PROXY_ENV: "  "}, {ig.PROXY_ENV: "socks5://k:s@proxy.example.net:1080"}, {ig.PROXY_ENV: "http://proxy.example.net"}):
        with pytest.raises(SocialStop) as e:
            ig.build(env, state)
        assert e.value.signal == Signal.IP_CHANGED


def test_build_requires_a_valid_session_file(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "instaloader", None)
    with pytest.raises(SocialStop) as e:
        ig.build(ENV, tmp_path)
    assert e.value.signal == Signal.LOGIN_REQUIRED
    (tmp_path / ig.SESSION_FILE).write_text("{bozuk", encoding="utf-8")
    with pytest.raises(SocialStop) as e:
        ig.build(ENV, tmp_path)
    assert e.value.signal == Signal.LOGIN_REQUIRED
    (tmp_path / ig.SESSION_FILE).write_text(json.dumps({"username": "x", "cookies": {"csrftoken": "c"}}), encoding="utf-8")
    with pytest.raises(SocialStop) as e:
        ig.build(ENV, tmp_path)
    assert e.value.signal == Signal.LOGIN_REQUIRED


def test_build_refuses_unverified_instaloader_version(monkeypatch, state):
    monkeypatch.setitem(sys.modules, "instaloader", fake_instaloader(version="4.16"))
    with pytest.raises(RuntimeError):
        ig.build(ENV, state)


# --- fetch_new uçtan uca (sahte instaloader) ----------------------------------------------------------------------------

def test_fetch_new_one_query_when_cursor_is_on_first_page(monkeypatch, state):
    first = [node("N1", at(hours=1)), node("C", at(hours=3))] + [node(f"O{i}", at(days=i + 1)) for i in range(10)]
    fetcher, loader = make_fetcher(monkeypatch, state, {"ornek_galeri": [page(first, more=True), page([node("Z", at(days=30))])]})
    r = fetcher.fetch_new(SOURCE, Cursor("C", NOW - timedelta(hours=3)), 20)
    assert codes(r) == ["N1"] and r.requests == 1  # imleç ilk 3 sırada (sabit olabilir): 4 eskiyle durur, yine tek sayfa
    assert r.seen == ig.OLD_RUN_STOP + 1
    doc_id, variables, referer = loader.context.queries[0]
    assert doc_id == ig.TIMELINE_DOC_ID and variables["username"] == "ornek_galeri"
    assert referer == "https://www.instagram.com/ornek_galeri/"
    assert json.loads((state / ig.BUDGET_FILE).read_text())["count"] == 1


def test_fetch_new_reads_second_page_only_when_needed(monkeypatch, state):
    first = [node(f"N{i}", at(minutes=i + 1)) for i in range(12)]
    second = [node("N12", at(minutes=20)), node("C", at(hours=2))]
    fetcher, _ = make_fetcher(monkeypatch, state, {"ornek_galeri": [page(first, more=True), page(second)]})
    r = fetcher.fetch_new(SOURCE, Cursor("C", NOW - timedelta(hours=2)), 50)
    assert len(r.posts) == 13 and r.requests == 2 and r.posts[0].post_id == "N0"


def test_daily_cap_stops_before_any_query(monkeypatch, state):
    (state / ig.BUDGET_FILE).write_text(json.dumps({"day": "2026-10-05", "count": ig.DAILY_QUERY_CAP}), encoding="utf-8")
    fetcher, loader = make_fetcher(monkeypatch, state, {"ornek_galeri": [page([node("N1", at(hours=1))])]})
    with pytest.raises(DailyCap):
        fetcher.fetch_new(SOURCE, Cursor(), 20)
    assert loader.context._rate_controller.base_calls == []


class InstaloaderException(Exception):
    pass


class ConnectionException(InstaloaderException):
    pass


class TooManyRequestsException(ConnectionException):
    pass


class QueryReturnedNotFoundException(ConnectionException):
    pass


class LoginRequiredException(InstaloaderException):
    pass


class ProfileNotExistsException(InstaloaderException):
    pass


class PrivateProfileNotFollowedException(InstaloaderException):
    pass


class QueryReturnedForbiddenException(InstaloaderException):
    pass


class AbortDownloadException(Exception):
    pass


class RequestException(OSError):
    pass


class ProxyError(RequestException):
    pass


class JSONDecodeError(RequestException, ValueError):
    pass


def wrapped(outer_cls, outer_msg, inner):
    try:
        try:
            raise inner
        except Exception as e:
            raise outer_cls(outer_msg) from e
    except Exception as e:
        return e


def test_429_stops_without_sleeping_or_retrying(monkeypatch, state):
    err = wrapped(ConnectionException, "JSON Query to graphql/query: 429 Too Many Requests when accessing "
                  "https://www.instagram.com/graphql/query", TooManyRequestsException("429 Too Many Requests"))
    sleeps = []
    fetcher, loader = make_fetcher(monkeypatch, state, {"ornek_galeri": [err]}, sleeps=sleeps)
    with pytest.raises(SocialStop) as e:
        fetcher.fetch_new(SOURCE, Cursor(), 20)
    assert e.value.signal == Signal.RATE_LIMITED and sleeps == []
    rc = loader.context._rate_controller
    with pytest.raises(SocialStop) as e:  # attempts>1 olsaydı Instaloader handle_429'u çağırırdı: yine beklemez
        rc.handle_429("doc")
    assert e.value.signal == Signal.RATE_LIMITED and rc.base_sleeps == [] and sleeps == []


def test_proxy_error_detail_is_masked(monkeypatch, state):
    err = wrapped(ConnectionException, "JSON Query to graphql/query: HTTPSConnectionPool: Tunnel connection failed: "
                  f"403 Forbidden via {PROXY} {COOKIES['sessionid']}", ProxyError("Unable to connect to proxy"))
    fetcher, _ = make_fetcher(monkeypatch, state, {"ornek_galeri": [err]})
    with pytest.raises(SourceError) as e:  # proxy'nin 403'ü Instagram'ın 403'ü sayılmaz
        fetcher.fetch_new(SOURCE, Cursor(), 20)
    text = str(e.value)
    assert "kullanici" not in text and "Gizli" not in text and "proxy.example.net" not in text
    assert COOKIES["sessionid"] not in text and e.value.__cause__ is None and e.value.__suppress_context__


# --- hata eşleme --------------------------------------------------------------------------------------------------------

URL = " when accessing https://www.instagram.com/graphql/query"


@pytest.mark.parametrize("exc, expected", [
    (LoginRequiredException("Redirected to login page. Use --login or --load-cookies."), Signal.LOGIN_REQUIRED),
    (AbortDownloadException("Redirected to login page. You've been logged out, please wait some time"), Signal.LOGIN_REQUIRED),
    (AbortDownloadException('400 Bad Request - "fail" status, message "checkpoint_required"' + URL), Signal.CHECKPOINT),
    (AbortDownloadException('400 Bad Request - "fail" status, message "challenge_required"' + URL), Signal.CHECKPOINT),
    (AbortDownloadException('400 Bad Request - "fail" status, message "feedback_required"' + URL), Signal.FEEDBACK_REQUIRED),
    (TooManyRequestsException("x"), Signal.RATE_LIMITED),
    (ConnectionException("JSON Query to graphql/query: 429 Too Many Requests" + URL), Signal.RATE_LIMITED),
    (ConnectionException('JSON Query to graphql/query: 401 Unauthorized - "fail" status, message "Please wait a few '
                         'minutes before you try again."' + URL), Signal.RATE_LIMITED),
    (ConnectionException("JSON Query to graphql/query: 401 Unauthorized" + URL), Signal.AUTH_ERROR),
    (ConnectionException("JSON Query to graphql/query: 403 Forbidden" + URL), Signal.AUTH_ERROR),
    (QueryReturnedForbiddenException("x"), Signal.AUTH_ERROR),
    (ConnectionException('JSON Query to graphql/query: 400 Bad Request - "fail" status, message "useragent mismatch"'
                         + URL), Signal.AUTH_ERROR),
    (wrapped(ConnectionException, "JSON Query to graphql/query: Expecting value: line 1 column 1", JSONDecodeError("x")),
     Signal.AUTH_ERROR),
])
def test_platform_errors_map_to_signals(exc, expected):
    mapped = ig.social_error(exc)
    assert isinstance(mapped, SocialStop) and mapped.signal == expected


@pytest.mark.parametrize("exc", [
    ProfileNotExistsException("Profile ornek_galeri does not exist."),
    PrivateProfileNotFollowedException("Private but not followed."),
    QueryReturnedNotFoundException("JSON Query to graphql/query: 404 Not Found" + URL),
    ConnectionException("JSON Query to x: 500 Internal Server Error when accessing https://www.instagram.com/challenge_galeri/"),
    wrapped(ConnectionException, "JSON Query to graphql/query: Read timed out.", RequestException("timeout")),
])
def test_source_errors_do_not_brake(exc):
    assert isinstance(ig.social_error(exc), SourceError)


def test_programming_errors_are_not_hidden():
    assert ig.social_error(ValueError("hata")) is None


# --- yanıt bekçisi ------------------------------------------------------------------------------------------------------

@dataclass
class FakeResponse:
    url: str
    status_code: int = 200
    headers: dict = field(default_factory=dict)
    closed: bool = False

    @property
    def is_redirect(self):
        return "location" in self.headers and self.status_code in (301, 302, 303, 307, 308)

    def close(self):
        self.closed = True


@pytest.mark.parametrize("resp, expected", [
    (FakeResponse("https://www.instagram.com/graphql/query", 429), Signal.RATE_LIMITED),
    (FakeResponse("https://www.instagram.com/graphql/query", 302, {"location": "/challenge/?next=/"}), Signal.CHECKPOINT),
    (FakeResponse("https://www.instagram.com/graphql/query", 302, {"location": "https://www.instagram.com/accounts/login/"}),
     Signal.LOGIN_REQUIRED),
    (FakeResponse("https://i.instagram.com/api/v1/x/", 302, {"location": "https://www.instagram.com/"}), Signal.AUTH_ERROR),
])
def test_response_guard_stops_on_429_and_redirects(resp, expected):
    with pytest.raises(SocialStop) as e:
        ig.response_guard(ig.mask)(resp)
    assert e.value.signal == expected and resp.closed


def test_response_guard_lets_normal_responses_through():
    hook = ig.response_guard(ig.mask)
    ok = FakeResponse("https://www.instagram.com/graphql/query")
    assert hook(ok) is ok
    assert hook(FakeResponse("https://www.instagram.com/graphql/query", 301, {"location": "/graphql/query/"})).status_code == 301
    assert hook(FakeResponse("https://api.ipify.org/?format=json", 429)).status_code == 429


# --- günlük tavan ve hız ------------------------------------------------------------------------------------------------

def test_rate_controller_counts_every_query_and_enforces_daily_cap(tmp_path):
    day = [NOW]
    budget = ig.QueryBudget(tmp_path / ig.BUDGET_FILE, cap=3, now=lambda: day[0])
    clock, sleeps = [100.0], []
    rc = ig.rate_controller_class(FakeRateController, budget, PROXY, sleeps.append, lambda: clock[0])(
        types.SimpleNamespace(_session=types.SimpleNamespace(proxies={"http": PROXY, "https": PROXY}, trust_env=False)))
    rc.wait_before_query("doc")
    clock[0] = 101.0
    rc.wait_before_query("other")
    assert sleeps == [ig.MIN_QUERY_GAP_S - 1.0]  # en az aralık; Instaloader'ın kendi aralığı da çağrıldı
    rc.wait_before_query("iphone")
    assert rc.base_calls == ["doc", "other", "iphone"] and budget.used == 3
    assert json.loads((tmp_path / ig.BUDGET_FILE).read_text()) == {"day": "2026-10-05", "count": 3}
    with pytest.raises(DailyCap):
        rc.wait_before_query("doc")
    assert rc.base_calls == ["doc", "other", "iphone"]
    day[0] = NOW + timedelta(days=1)
    rc.wait_before_query("doc")
    assert json.loads((tmp_path / ig.BUDGET_FILE).read_text()) == {"day": "2026-10-06", "count": 1}


def test_corrupt_budget_file_fails_closed_for_the_day(tmp_path):
    path = tmp_path / ig.BUDGET_FILE
    path.write_text("bozuk", encoding="utf-8")
    with pytest.raises(DailyCap):
        ig.QueryBudget(path, now=lambda: NOW).spend()
    ig.QueryBudget(path, now=lambda: NOW + timedelta(days=1)).spend()


def test_query_without_proxy_on_session_is_refused(tmp_path):
    budget = ig.QueryBudget(tmp_path / ig.BUDGET_FILE, now=lambda: NOW)
    rc = ig.rate_controller_class(FakeRateController, budget, PROXY)(
        types.SimpleNamespace(_session=types.SimpleNamespace(proxies={}, trust_env=True)))
    with pytest.raises(SocialStop) as e:
        rc.wait_before_query("doc")
    assert e.value.signal == Signal.IP_CHANGED and budget.used == 0 and rc.base_calls == []


# --- çıkış IP'si --------------------------------------------------------------------------------------------------------

class FakeRequest:
    def __init__(self, method, url, headers=None):
        self.method, self.url, self.headers = method, url, dict(headers or {})

    def prepare(self):
        return self


def test_check_egress_uses_the_same_proxied_session_without_cookies(monkeypatch, state):
    fetcher, loader = make_fetcher(monkeypatch, state, {})
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(Request=FakeRequest))
    assert fetcher.check_egress() == "203.0.113.7"
    (req, kwargs), = loader.context._session.sent
    assert req.url == ig.EGRESS_URL and "Cookie" not in req.headers and "X-CSRFToken" not in req.headers
    assert kwargs["allow_redirects"] is False


def test_check_egress_proxy_down_is_unreachable_and_missing_proxy_stops(monkeypatch, state):
    fetcher, loader = make_fetcher(monkeypatch, state, {})
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(Request=FakeRequest))

    def down(req):
        raise OSError(f"proxy yanıt vermedi: {PROXY}")

    loader.context._session.reply = down
    with pytest.raises(Unreachable) as e:  # geçici: fren yok, tur okumadan biter
        fetcher.check_egress()
    assert "Gizli" not in str(e.value) and "kullanici" not in str(e.value)
    fetcher.proxy = ""
    with pytest.raises(SocialStop) as e:
        fetcher.check_egress()
    assert e.value.signal == Signal.IP_CHANGED


# --- gizlilik, çerez → oturum, oturum dosyası ---------------------------------------------------------------------------

def test_mask_removes_proxy_credentials_and_session_values():
    secrets = (*ig.proxy_secrets(PROXY), *ig.SavedSession(READER, COOKIES).secrets())
    text = ig.mask(f"hata {PROXY} kullanici Gizli@Sifre {COOKIES['sessionid']} {READER} ftp://a:b@h/x", secrets)
    for leak in ("kullanici", "Gizli", "proxy.example.net", COOKIES["sessionid"], READER, "a:b@"):
        assert leak not in text
    assert ig.playwright_proxy(PROXY) == {"server": "http://proxy.example.net:8000", "username": "kullanici",
                                          "password": "Gizli@Sifre"}


def test_browser_cookies_convert_to_instaloader_session():
    cookies = [
        {"name": "sessionid", "value": "S1", "domain": ".instagram.com"},
        {"name": "csrftoken", "value": "C1", "domain": "www.instagram.com"},
        {"name": "ds_user_id", "value": "42", "domain": ".instagram.com"},
        {"name": "mid", "value": "", "domain": ".instagram.com"},
        {"name": "datr", "value": "FB", "domain": ".facebook.com"},
        {"name": "x", "value": "y", "domain": ".notinstagram.com"},
    ]
    assert ig.session_from_browser_cookies(cookies) == {"sessionid": "S1", "csrftoken": "C1", "ds_user_id": "42"}
    with pytest.raises(ValueError):
        ig.session_from_browser_cookies([c for c in cookies if c["name"] != "csrftoken"])


def test_session_file_is_private_and_round_trips(tmp_path):
    path = tmp_path / ig.SESSION_FILE
    ig.write_session(path, ig.SavedSession(READER, dict(COOKIES), "UA"), NOW)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert ig.read_session(path) == ig.SavedSession(READER, COOKIES, "UA")


# --- sahibin girişi -----------------------------------------------------------------------------------------------------

def test_wait_for_session_polls_until_logged_in_and_out_of_challenge():
    states = iter([([], "https://www.instagram.com/accounts/login/"),
                   ([{"name": "sessionid", "value": "S", "domain": ".instagram.com"},
                     {"name": "csrftoken", "value": "C", "domain": ".instagram.com"}], "https://www.instagram.com/challenge/x/"),
                   ([{"name": "sessionid", "value": "S", "domain": ".instagram.com"},
                     {"name": "csrftoken", "value": "C", "domain": ".instagram.com"}], "https://www.instagram.com/")])
    current = {}

    def cookies():
        if "c" not in current or current.get("advance"):
            current["c"], current["u"] = next(states)
            current["advance"] = False
        return current["c"]

    sleeps = []

    def sleep(s):
        sleeps.append(s)
        current["advance"] = s == ig.LOGIN_POLL_S

    got = ig.wait_for_session(cookies, lambda: current["u"], sleep=sleep, clock=lambda: 0.0)
    assert {c["name"] for c in got} == {"sessionid", "csrftoken"}
    assert sleeps == [ig.LOGIN_POLL_S, ig.LOGIN_POLL_S, ig.LOGIN_SETTLE_S]


def test_wait_for_session_times_out_and_detects_closed_browser():
    t = [0.0]

    def sleep(s):
        t[0] += s

    with pytest.raises(SocialStop) as e:
        ig.wait_for_session(list, lambda: "https://www.instagram.com/accounts/login/", sleep=sleep,
                            clock=lambda: t[0], timeout_s=10)
    assert e.value.signal == Signal.LOGIN_REQUIRED

    def closed():
        raise RuntimeError("Target closed")

    with pytest.raises(SocialStop) as e:
        ig.wait_for_session(closed, lambda: "", sleep=sleep, clock=lambda: t[0])
    assert e.value.signal == Signal.LOGIN_REQUIRED


def test_login_fails_closed_without_proxy_before_opening_a_browser(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    with pytest.raises(SocialStop) as e:
        ig.login({}, tmp_path)
    assert e.value.signal == Signal.IP_CHANGED


def fake_playwright(log, cookies):
    class Page:
        url = "about:blank"

        def goto(self, url):
            log["goto"].append(url)
            self.url = "https://www.instagram.com/" if "instagram" in url else url

        def inner_text(self, selector):
            return '{"ip":"203.0.113.7"}'

        def evaluate(self, script):
            return "Mozilla/5.0 (X11; Linux x86_64) Chrome/146.0.0.0"

    class Context:
        def __init__(self):
            self.pages = [Page()]

        def cookies(self, url):
            return cookies

        def close(self):
            log["closed"] = True

    class Chromium:
        def launch_persistent_context(self, user_data_dir, **kwargs):
            log["launch"] = (user_data_dir, kwargs)
            return Context()

    class Manager:
        def __enter__(self):
            return types.SimpleNamespace(chromium=Chromium())

        def __exit__(self, *exc):
            return False

    pkg = types.ModuleType("playwright")
    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = Manager
    return pkg, sync_api


@pytest.mark.parametrize("env, tz", [(ENV, "Europe/Istanbul"), ({**ENV, ig.TZ_ENV: "Europe/Nicosia"}, "Europe/Nicosia")])
def test_login_saves_private_session_via_proxied_browser(monkeypatch, tmp_path, capsys, env, tz):
    log = {"goto": []}
    browser_cookies = [{"name": k, "value": v, "domain": ".instagram.com"} for k, v in COOKIES.items()]
    pkg, sync_api = fake_playwright(log, browser_cookies)
    monkeypatch.setitem(sys.modules, "playwright", pkg)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    mod = fake_instaloader()
    monkeypatch.setitem(sys.modules, "instaloader", mod)
    ig.login(env, tmp_path, now=lambda: NOW, sleep=lambda s: None, clock=lambda: 0.0)

    user_data_dir, kw = log["launch"]
    assert kw["headless"] is False and kw["timezone_id"] == tz
    assert kw["proxy"] == {"server": "http://proxy.example.net:8000", "username": "kullanici", "password": "Gizli@Sifre"}
    assert f"--force-webrtc-ip-handling-policy={ig.WEBRTC_POLICY}" in kw["args"]
    assert f"--webrtc-ip-handling-policy={ig.WEBRTC_POLICY}" in kw["args"]  # tam Chromium yalnız bunu okur
    prefs = json.loads((Path(user_data_dir) / "Default" / "Preferences").read_text())
    assert prefs["webrtc"]["ip_handling_policy"] == ig.WEBRTC_POLICY
    assert log["goto"] == [ig.EGRESS_URL, ig.LOGIN_URL] and log["closed"]

    loader = mod.loaders[0]  # test_login proxy'li oturumdan, sayılarak
    assert loader.context._session.proxies == {"http": PROXY, "https": PROXY} and loader.closed
    assert json.loads((tmp_path / ig.BUDGET_FILE).read_text())["count"] == 1
    path = tmp_path / ig.SESSION_FILE
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    saved = ig.read_session(path)
    assert saved.username == READER and saved.cookies == COOKIES and "Chrome/146" in saved.user_agent
    out = capsys.readouterr().out
    assert "Gizli" not in out and "kullanici" not in out and COOKIES["sessionid"] not in out
