"""Facebook tarayıcı okuyucusu (infrastructure/collectors/facebook_browser.py): saf ayrıştırma, durma kuralları, tespit ve
oturum/proxy kuralları. Tarayıcı YOK: sayfa sahte. tests/fixtures/social/facebook_feed_items.json, EXTRACT_JS'in sentetik
facebook_group_feed.html üzerinde (yerel Chromium, ağ kapalı) verdiği çıktıdır; görsel adresleri sahte CDN adresiyle değiştirildi."""
import ast
import copy
import json
import random
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from application.social_port import Cursor, SocialPost, SocialSource, SocialStop, SourceError, Unreachable
from domain.social_brake import Signal
from infrastructure.collectors import facebook_browser as fb
from infrastructure.collectors import forage_parser

FIX = Path(__file__).parent / "fixtures" / "social"
ITEMS = json.loads((FIX / "facebook_feed_items.json").read_text(encoding="utf-8"))
UTC = timezone.utc
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)  # İstanbul 15:00
GROUP = "1000000000000001"
SOURCE = SocialSource("facebook", GROUP, f"https://www.facebook.com/groups/{GROUP}", "grup-a", slug="kktc.oto.ornek")
CURSOR = Cursor("2000000000000005", datetime(2026, 10, 4, 6, 0, tzinfo=UTC))  # fixture'daki "Yesterday at 9:00 AM"
PROXY_URL = "http://kullanici:s3cr3t@proxy.example:8000"
EXPANDED = "Satilik 2015 Toyota Corolla\n120.000 km, otomatik\nFiyat 8.500 STG\nTel 0533 000 00 00"


def utc(*a):
    return datetime(*a, tzinfo=UTC)


# ---------------------------------------------------------------- sahte tarayıcı

class FakeMouse:
    def __init__(self, page):
        self.page, self.moves, self.wheels, self.clicks = page, [], [], []

    def move(self, x, y, steps=1):
        self.moves.append((x, y))
        if self.page.last_target and self.page.last_target[1] == "link":
            self.page.hovered.add(self.page.last_target[0])

    def wheel(self, dx, dy):
        self.wheels.append(dy)

    def click(self, x, y, delay=0):
        self.clicks.append((x, y))
        if self.page.last_target:
            self.page.clicked.add(self.page.last_target[0])
        if self.page.click_navigates:
            self.page.url = "https://www.facebook.com/groups/1000000000000001/posts/2000000000000010/"


class FakePage:
    def __init__(self, items=(), state=None, redirect=None, per_call=4, expanded=None, hover_href=None, rtc=(),
                 click_navigates=False, goto_error=None):
        self.items = [copy.deepcopy(i) for i in items]
        self.state = {"has_feed": True, "login_form": False, "dialogs": [], "body": ""} | (state or {})
        self.redirect, self.per_call, self.rtc = redirect, per_call, list(rtc)
        self.expanded, self.hover_href = expanded or {}, hover_href or {}
        self.click_navigates, self.goto_error = click_navigates, goto_error
        self.url, self.gotos, self.calls, self.backs = "about:blank", [], 0, 0
        self.last_target, self.clicked, self.hovered = None, set(), set()
        self.mouse = FakeMouse(self)

    def goto(self, url, **kw):
        self.gotos.append(url)
        if self.goto_error:
            raise self.goto_error
        self.url = self.redirect or url

    def go_back(self, **kw):
        self.backs += 1
        self.url = self.gotos[-1]

    def wait_for_selector(self, sel, **kw):
        if not self.state["has_feed"]:
            raise TimeoutError(sel)

    def close(self):
        pass

    def _item(self, i):
        it = copy.deepcopy(self.items[i])
        it["i"] = i
        if i in self.clicked and i in self.expanded:
            it["text"], it["see_more"] = self.expanded[i], -1
        if i in self.hovered and i in self.hover_href:
            it["times"][0]["href"] = self.hover_href[i]
        return it

    def evaluate(self, js, arg=None):
        if js is fb.STATE_JS:
            return dict(self.state)
        if js is fb.WEBRTC_JS:
            return list(self.rtc)
        if js is fb.TARGET_JS:
            self.last_target = (arg["i"], arg["kind"])
            return {"x": 100.0, "y": 300.0, "w": 60.0, "h": 17.0}
        if js is fb.EXTRACT_JS:
            if arg["only"] is not None:
                return [self._item(arg["only"])]
            self.calls += 1
            return [self._item(i) for i in range(min(len(self.items), self.calls * self.per_call))]
        raise AssertionError("bilinmeyen JS")


class FakeResponse:
    def __init__(self, status, payload, headers=None):
        self.status, self.ok, self.payload = status, 200 <= status < 300, payload
        self.headers = headers or {}

    def text(self):
        return self.payload

    def body(self):
        return self.payload


class FakeRequest:
    def __init__(self, responses):
        self.responses, self.urls = list(responses), []

    def get(self, url, timeout=None):
        self.urls.append(url)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class FakeContext:
    def __init__(self, page, cookies=None, responses=()):
        self.page = page
        self._cookies = [{"name": "c_user", "value": "1"}] if cookies is None else cookies
        self.request = FakeRequest(responses)
        self.closed = False

    def new_page(self):
        return self.page

    def cookies(self, urls=None):
        return list(self._cookies)

    def storage_state(self):
        return {"cookies": self._cookies, "origins": []}


def make(page, tmp_path, cookies=None, responses=(), sleeps=None):
    ctx = FakeContext(page, cookies, responses)
    sleeps = [] if sleeps is None else sleeps

    def close():
        ctx.closed = True
    f = fb.FacebookBrowserFetcher({"server": "http://proxy.example:8000"}, tmp_path / fb.STATE_FILE, headless=True,
                                  opener=lambda: fb.BrowserSession(ctx, close), sleep=sleeps.append,
                                  rng=random.Random(7), clock=lambda: NOW)
    return f, ctx


def post_of(i, **kw):
    item = ITEMS[i]
    _, pid = fb.post_identity(item)
    return replace_post(fb.to_post(item, SOURCE, pid, NOW), **kw)


def replace_post(p: SocialPost, **kw) -> SocialPost:
    from dataclasses import replace
    return replace(p, **kw)


# ---------------------------------------------------------------- zaman

@pytest.mark.parametrize("text,expected", [
    ("Just now", NOW),
    ("5m", utc(2026, 10, 5, 11, 55)),
    ("6h", utc(2026, 10, 5, 6, 0)),
    ("2d", utc(2026, 10, 3, 12, 0)),
    ("Yesterday at 3:15 PM", utc(2026, 10, 4, 12, 15)),  # İstanbul duvar saati (UTC+3)
    ("Yesterday at 3:15 PM", utc(2026, 10, 4, 12, 15)),
    ("October 2 at 10:00 AM", utc(2026, 10, 2, 7, 0)),
    ("September 28, 2025", utc(2025, 9, 27, 21, 0)),
    ("Thursday, October 2, 2025 at 10:00 AM", utc(2025, 10, 2, 7, 0)),
    ("December 28 at 3:45 PM", utc(2025, 12, 28, 12, 45)),  # yılsız ve gelecekte kalırsa geçen yıl
    ("h\n3\nS\nx9q", None),  # karışık harfli zaman bağlantısının ham metni
    ("KKTC Araba 2", None),
    ("", None),
])
def test_timestamp_parsing_to_aware_utc_in_browser_timezone(text, expected):
    assert fb.parse_fb_time(text, NOW, "Europe/Istanbul") == expected


def test_istanbul_differs_from_kktc_after_kktc_winter_time_starts():
    later = utc(2026, 10, 26, 10, 0)  # KKTC 25.10.2026'da UTC+2'ye geçti, Türkiye UTC+3 kaldı
    assert fb.parse_fb_time("Yesterday at 3:15 PM", later, "Europe/Istanbul") == utc(2026, 10, 25, 12, 15)
    assert fb.parse_fb_time("Yesterday at 3:15 PM", later, "Asia/Famagusta") == utc(2026, 10, 25, 13, 15)


def test_relative_time_is_real_duration_across_dst_boundary():
    now = utc(2026, 10, 25, 3, 0)  # Famagusta: 01:00 UTC'de yaz saati bitti (yerel 05:00)
    assert fb.parse_fb_time("6h", now, "Asia/Famagusta") == utc(2026, 10, 24, 21, 0)  # duvar saati çıkarmasıyla 20:00 olurdu
    spring = utc(2026, 3, 29, 12, 0)
    assert fb.parse_fb_time("Yesterday at 3:15 PM", spring, "Asia/Famagusta") == utc(2026, 3, 28, 13, 15)  # dün kış saati (+2)
    assert fb.parse_fb_time("2h", spring, "Asia/Famagusta") == utc(2026, 3, 29, 10, 0)


def test_wall_time_uses_offset_of_that_day():
    later = utc(2026, 10, 26, 10, 0)
    assert fb.parse_fb_time("October 24 at 3:15 PM", later, "Asia/Famagusta") == utc(2026, 10, 24, 12, 15)  # yaz saati (+3)


def test_vendored_relative_detection():
    assert forage_parser.is_relative("5m") and forage_parser.is_relative("Just now") and forage_parser.is_relative("Yesterday")
    assert not forage_parser.is_relative("Yesterday at 3:15 PM")
    assert not forage_parser.is_relative("October 2 at 10:00 AM")


def test_browser_timezone_default_override_and_invalid():
    assert fb.browser_tz({}) == fb.BROWSER_TZ == "Europe/Istanbul"
    assert fb.browser_tz({fb.TZ_ENV: "Asia/Famagusta"}) == "Asia/Famagusta"
    with pytest.raises(SocialStop) as e:
        fb.browser_tz({fb.TZ_ENV: "Mars/Olympus"})
    assert e.value.signal == Signal.IP_CHANGED
    assert fb.context_options("Europe/Istanbul") == {"locale": "en-US", "timezone_id": "Europe/Istanbul",
                                                     "viewport": {"width": 1440, "height": 900}}


# ---------------------------------------------------------------- bağlantılar

@pytest.mark.parametrize("href,expected", [
    (f"https://www.facebook.com/groups/{GROUP}/posts/2000000000000010/?__cft__[0]=AZx&__tn__=%2CO%2CP-R",
     fb.PostRef(GROUP, "2000000000000010")),
    ("/groups/kktc.oto.ornek/permalink/2000000000000011/", fb.PostRef("kktc.oto.ornek", "2000000000000011")),
    (f"/groups/{GROUP}/?multi_permalinks=2000000000000015", fb.PostRef(GROUP, "2000000000000015")),
    (f"/photo/?fbid=4000000000000012&set=gm.2000000000000012&idorvanity={GROUP}", fb.PostRef(GROUP, "2000000000000012")),
    ("/photo/?fbid=4000000000000010&set=pcb.2000000000000010", fb.PostRef(None, "2000000000000010")),
    ("/permalink.php?story_fbid=2000000000000016&id=1000000000000003", fb.PostRef(None, "2000000000000016")),
    (f"/groups/{GROUP}/posts/pfbid02AbCdEfGhIjKlMn/", fb.PostRef(GROUP, "pfbid02AbCdEfGhIjKlMn")),
    (f"/groups/{GROUP}/posts/2000000000000010/?comment_id=3000000000000001", None),  # yorum bağlantısı
    (f"/photo/?fbid=4000000000000010&set=g.{GROUP}", None),  # grup albümü, gönderi değil
    (f"/groups/{GROUP}/user/9000000000000001/", None),
    ("https://l.facebook.com/l.php?u=https%3A%2F%2Fexample.com", None),
    ("https://example.com/groups/1/posts/2000000000000010/", None),
    ("#", None),
    ("", None),
])
def test_post_url_parsing(href, expected):
    assert fb.parse_post_url(href) == expected


def test_group_from_url_and_canonical_permalink():
    assert fb.group_from_url(f"/groups/{GROUP}/user/9000000000000001/") == GROUP
    assert fb.group_from_url("/groups/feed/") is None
    assert fb.permalink(GROUP, "2000000000000010") == f"https://www.facebook.com/groups/{GROUP}/posts/2000000000000010/"
    assert fb.source_matches(SOURCE, "KKTC.OTO.ORNEK") and fb.source_matches(SOURCE, GROUP)
    assert not fb.source_matches(SOURCE, "1000000000000009")


def test_identity_from_fixture_items():
    assert fb.post_identity(ITEMS[1]) == (GROUP, "2000000000000010")
    assert fb.post_identity(ITEMS[2]) == ("kktc.oto.ornek", "2000000000000011")
    assert fb.post_identity(ITEMS[3]) == (GROUP, "2000000000000012")  # zaman bağlantısı "#": fotoğraf bağlantısından (set=gm.)


# ---------------------------------------------------------------- gönderi alanları

def test_post_fields_owner_always_none_and_canonical_url():
    p = post_of(1)
    assert p.platform == "facebook" and p.source_key == GROUP and p.owner is None
    assert p.url == f"https://www.facebook.com/groups/{GROUP}/posts/2000000000000010/"  # __cft__ izleme parametresi yok
    assert p.posted_at == utc(2026, 10, 5, 11, 55)
    slug = post_of(2)
    assert slug.url == f"https://www.facebook.com/groups/{GROUP}/posts/2000000000000011/"  # okunur addan kaynağın kimliğine
    assert post_of(0).pinned and not p.pinned
    for i in range(len(ITEMS)):
        assert post_of(i).owner is None
        assert "Ornek" not in post_of(i).text  # yazar adı metne girmez


def test_obfuscated_and_tooltip_timestamps():
    assert post_of(3).posted_at == utc(2026, 10, 5, 9, 0)  # ham metin karışık, görünen sıra "3h"
    assert post_of(4).posted_at == utc(2026, 10, 4, 12, 15)  # aria-labelledby ipucu "Yesterday at 3:15 PM"
    assert fb.item_time({"utime": "1790000000", "times": []}, NOW) == datetime.fromtimestamp(1790000000, UTC)


def test_image_is_first_cdn_photo_not_emoji_or_tiny():
    assert post_of(1).image_url.startswith("https://scontent-ist1-1.xx.fbcdn.net/")
    assert post_of(2).image_url is None
    assert fb.pick_image([{"src": "https://static.xx.fbcdn.net/images/emoji.php/v9/x.png", "w": 600, "h": 600},
                          {"src": "https://scontent.xx.fbcdn.net/v/avatar.jpg", "w": 40, "h": 40},
                          {"src": "data:image/gif;base64,AAAA", "w": 600, "h": 400},
                          {"src": "https://scontent.xx.fbcdn.net/v/photo.jpg", "w": 960, "h": 720}]) \
        == "https://scontent.xx.fbcdn.net/v/photo.jpg"
    assert fb.pick_image(None) is None


def test_text_cleanup():
    assert post_of(1).text == "Satilik 2015 Toyota Corolla\n120.000 km, otomatik\nFiyat 8.500 STG"  # "… See more" atıldı
    assert post_of(2).text == "2018 Honda Civic\n£11.750 – 0533 000 00 00"  # "See translation" atıldı
    assert post_of(3).text == "2012 Nissan Note\nTakas olur, fiyat 4.200 stg"  # yedek metin (gövde özniteliği yok)
    assert fb.clean_text("Ford Focus\n3h\n·\nLike\n5.500 stg", fallback=True) == "Ford Focus\n5.500 stg"
    assert fb.clean_text("Ford Focus\n3h", fallback=False) == "Ford Focus"  # Forage: tek başına \d+[hdwm] satırı


# ---------------------------------------------------------------- durma kuralları (saf)

def scan_all(cursor, max_posts=20, items=ITEMS):
    scan = fb.FeedScan(cursor, NOW, max_posts)
    visited = 0
    for i in range(len(items)):
        visited += 1
        scan.offer(post_of(i))
        if scan.stop:
            break
    return scan, visited


def test_stops_at_cursor_post_and_skips_pinned_old_post_at_top():
    scan, visited = scan_all(CURSOR)
    r = scan.result()
    assert scan.stop == "imlec" and visited == 6
    assert [p.post_id for p in r.posts] == ["2000000000000010", "2000000000000011", "2000000000000012", "2000000000000013"]
    assert r.cursor == Cursor("2000000000000010", utc(2026, 10, 5, 11, 55))
    assert all(p.owner is None for p in r.posts)


def test_five_old_in_a_row_stops_when_cursor_post_was_deleted():
    scan, visited = scan_all(Cursor("2000000000009999", CURSOR.posted_at))
    assert scan.stop == "eski" and visited == 11  # sabitlenmiş eski (1) + 4 yeni sayacı sıfırlar; sonra 5 eski
    ids = [p.post_id for p in scan.result().posts]
    assert ids[:4] == ["2000000000000010", "2000000000000011", "2000000000000012", "2000000000000013"]
    assert "2000000000000005" in ids  # imleçle aynı saat: tekrar olabilir, kaçırılmaz (upsert tekilleştirir)
    assert scan.result().cursor.post_id == "2000000000000010"


def test_first_read_takes_only_last_24_hours():
    scan, visited = scan_all(Cursor())
    r = scan.result()
    assert scan.stop == "eski" and visited == 10
    assert [p.post_id for p in r.posts] == ["2000000000000010", "2000000000000011", "2000000000000012", "2000000000000013"]
    assert all(p.posted_at >= NOW - timedelta(hours=24) for p in r.posts)
    assert r.cursor.post_id == "2000000000000010"


def test_first_read_with_nothing_new_still_sets_cursor_and_skips_undated():
    scan = fb.FeedScan(Cursor(), NOW, 20)
    assert not scan.offer(replace_post(post_of(1), post_id="2000000000000099", posted_at=None))  # tarihsiz: alınmaz
    for i in range(5, 11):
        scan.offer(post_of(i))
    r = scan.result()
    assert r.posts == [] and scan.stop == "eski"
    assert r.cursor == Cursor("2000000000000005", utc(2026, 10, 4, 6, 0))  # görülen en yeni (eski) gönderi


def test_max_posts_cap():
    scan, visited = scan_all(Cursor(), max_posts=2)
    assert scan.stop == "tavan" and visited == 3
    assert [p.post_id for p in scan.result().posts] == ["2000000000000010", "2000000000000011"]


def test_undated_post_above_cursor_is_taken_and_pinned_cursor_does_not_stop():
    scan = fb.FeedScan(CURSOR, NOW, 20)
    assert not scan.offer(post_of(5, pinned=True))  # imleç gönderisi sonradan sabitlenmiş: en üstte, durdurmaz
    assert scan.stop is None
    assert scan.offer(replace_post(post_of(1), posted_at=None))
    assert not scan.offer(post_of(5))  # asıl yerinde: durur
    assert scan.stop == "imlec"
    assert scan.result().cursor == Cursor("2000000000000010", CURSOR.posted_at)


def test_nothing_new_keeps_incoming_cursor():
    scan = fb.FeedScan(CURSOR, NOW, 20)
    scan.offer(post_of(5))
    r = scan.result()
    assert r.posts == [] and r.cursor == CURSOR and r.seen == 1


# ---------------------------------------------------------------- okuyucu (sahte sayfa)

def test_fetch_new_reads_slowly_and_expands_only_taken_posts(tmp_path):
    items = copy.deepcopy(ITEMS)
    items[7]["see_more"] = 0  # eski gönderi: "See more"a basılmamalı
    page = FakePage(items, expanded={1: EXPANDED})
    sleeps = []
    f, _ = make(page, tmp_path, sleeps=sleeps)
    r = f.fetch_new(SOURCE, CURSOR, 20)
    assert page.gotos == [f"https://www.facebook.com/groups/{GROUP}/?sorting_setting=CHRONOLOGICAL"]  # tek gönderi sayfası yok
    assert [p.post_id for p in r.posts] == ["2000000000000010", "2000000000000011", "2000000000000012", "2000000000000013"]
    assert r.posts[0].text == EXPANDED
    assert page.clicked == {1} and len(page.mouse.clicks) == 1
    assert r.requests == 1 and r.seen == 6
    assert r.cursor.post_id == "2000000000000010"
    assert page.mouse.wheels and all(80 <= dy <= 140 for dy in page.mouse.wheels)  # tekerlek, küçük adım
    assert any(1.5 <= s <= 4.0 for s in sleeps) and max(sleeps) <= 15
    assert f.last_stats["durma:imlec"] == 1 and f.last_stats["genisletildi"] == 1


def test_see_more_that_navigates_goes_back_and_is_disabled(tmp_path):
    page = FakePage(ITEMS, click_navigates=True)
    f, _ = make(page, tmp_path)
    r = f.fetch_new(SOURCE, CURSOR, 20)
    assert page.backs == 1 and len(page.mouse.clicks) == 1
    assert r.posts[0].text.startswith("Satilik 2015")
    assert r.requests == 2


def test_hover_reveals_permalink_when_href_is_hash(tmp_path):
    items = copy.deepcopy(ITEMS[:6])
    items[3]["links"] = [h for h in items[3]["links"] if "set=gm" not in h]
    items[3]["times"] = [t for t in items[3]["times"] if t["href"] == "#"]
    page = FakePage(items, hover_href={3: f"/groups/{GROUP}/posts/2000000000000012/"})
    f, _ = make(page, tmp_path)
    r = f.fetch_new(SOURCE, CURSOR, 20)
    assert "2000000000000012" in [p.post_id for p in r.posts]
    assert 3 in page.hovered and f.last_stats["kimliksiz"] == 0


def test_pinned_copy_of_cursor_post_on_top_does_not_stop_the_page_walk(tmp_path):
    items = [dict(copy.deepcopy(ITEMS[5]), pinned=True)] + copy.deepcopy(ITEMS[1:])
    f, _ = make(FakePage(items), tmp_path)
    r = f.fetch_new(SOURCE, CURSOR, 20)
    assert [p.post_id for p in r.posts] == ["2000000000000010", "2000000000000011", "2000000000000012", "2000000000000013"]
    assert f.last_stats["durma:imlec"] == 1


def test_unknown_vanity_name_on_own_group_page_is_still_this_group(tmp_path):
    bare = SocialSource("facebook", GROUP, SOURCE.url, "grup-a")  # kaynak listesinde slug yok
    f, _ = make(FakePage(ITEMS), tmp_path)
    r = f.fetch_new(bare, CURSOR, 20)
    assert "2000000000000011" in [p.post_id for p in r.posts] and all(p.source_key == GROUP for p in r.posts)
    assert f.last_stats["grup_adi_farkli"] == 1


def test_post_without_any_id_is_counted_not_returned(tmp_path):
    items = copy.deepcopy(ITEMS[:6])
    items[3]["links"] = []
    items[3]["times"] = [t for t in items[3]["times"] if t["href"] == "#"]
    f, _ = make(FakePage(items), tmp_path)
    r = f.fetch_new(SOURCE, CURSOR, 20)
    assert f.last_stats["kimliksiz"] == 1 and len(r.posts) == 3 and r.seen == 6


@pytest.mark.parametrize("kw,signal", [
    ({"redirect": "https://www.facebook.com/checkpoint/?next=x"}, Signal.CHECKPOINT),
    ({"redirect": "https://www.facebook.com/login/?next=https%3A%2F%2Fwww.facebook.com%2Fgroups%2F"}, Signal.LOGIN_REQUIRED),
    ({"state": {"login_form": True}}, Signal.LOGIN_REQUIRED),
    ({"state": {"dialogs": ["You’re Temporarily Blocked\nIt looks like you were misusing this feature by going too fast."]}},
     Signal.TEMP_BLOCKED),
    ({"state": {"dialogs": ["You can’t use this feature right now"]}}, Signal.TEMP_BLOCKED),
    ({"state": {"dialogs": ["You're going too fast. Slow down."]}}, Signal.RATE_LIMITED),
    ({"state": {"has_feed": False, "body": "Confirm your identity to continue"}}, Signal.CHECKPOINT),
])
def test_page_detection_raises_platform_stop(tmp_path, kw, signal):
    f, ctx = make(FakePage(ITEMS, **kw), tmp_path)
    with pytest.raises(SocialStop) as e:
        f.fetch_new(SOURCE, CURSOR, 20)
    assert e.value.signal == signal
    f.close()
    assert ctx.closed and not (tmp_path / fb.STATE_FILE).exists()  # düşmüş/engelli oturum kaydedilmez


def test_missing_c_user_cookie_is_login_required(tmp_path):
    f, _ = make(FakePage(ITEMS), tmp_path, cookies=[])
    with pytest.raises(SocialStop) as e:
        f.fetch_new(SOURCE, CURSOR, 20)
    assert e.value.signal == Signal.LOGIN_REQUIRED


def test_group_not_found_is_source_error_with_alias_only(tmp_path):
    page = FakePage(ITEMS, state={"has_feed": False, "body": "This content isn’t available right now"})
    f, _ = make(page, tmp_path)
    with pytest.raises(SourceError) as e:
        f.fetch_new(SOURCE, CURSOR, 20)
    assert "grup-a" in str(e.value) and GROUP not in str(e.value)
    page.state["body"] = "Something unexpected"
    with pytest.raises(SourceError, match="akış bulunamadı"):
        f.fetch_new(SOURCE, CURSOR, 20)


def test_empty_group_returns_nothing(tmp_path):
    f, _ = make(FakePage([], state={"has_feed": False, "body": "No posts yet"}), tmp_path)
    r = f.fetch_new(SOURCE, CURSOR, 20)
    assert r.posts == [] and r.seen == 0 and r.cursor == CURSOR and r.requests == 1


def test_classify_page_pure():
    ok = {"has_feed": True, "login_form": False, "dialogs": [], "body": ""}
    assert fb.classify_page("https://www.facebook.com/groups/1/", ok, True) == "ok"
    with pytest.raises(SocialStop):
        fb.classify_page("https://www.facebook.com/groups/1/", ok, False)
    with pytest.raises(SourceError):
        fb.classify_page("https://www.facebook.com/groups/1/", ok | {"has_feed": False, "body": "Join this group to see"}, True)
    # akış varken gövdedeki "bulunamadı" sözü kaynağı düşürmez (gönderi metni taranmaz)
    assert fb.classify_page("https://www.facebook.com/groups/1/", ok | {"body": "this content isn't available"}, True) == "ok"


def test_close_saves_fresh_session_with_private_permissions(tmp_path):
    f, ctx = make(FakePage(ITEMS), tmp_path)
    f.fetch_new(SOURCE, CURSOR, 20)
    f.close()
    path = tmp_path / fb.STATE_FILE
    assert ctx.closed and json.loads(path.read_text())["cookies"][0]["name"] == "c_user"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_one_browser_per_cycle_and_gap_between_page_loads(tmp_path):
    opened = []
    page = FakePage(ITEMS)
    ctx = FakeContext(page)
    sleeps = []

    def opener():
        opened.append(1)
        return fb.BrowserSession(ctx, lambda: None)
    f = fb.FacebookBrowserFetcher({"server": "http://proxy.example:8000"}, tmp_path / fb.STATE_FILE, opener=opener,
                                  sleep=sleeps.append, rng=random.Random(1), clock=lambda: NOW)
    other = SocialSource("facebook", "1000000000000002", "https://www.facebook.com/groups/1000000000000002", "grup-b")
    f.fetch_new(SOURCE, CURSOR, 20)
    page.calls = 0
    f.fetch_new(other, Cursor(), 20)
    assert opened == [1] and len(page.gotos) == 2
    assert any(fb.PAGE_GAP_S[0] <= s <= fb.PAGE_GAP_S[1] for s in sleeps)


# ---------------------------------------------------------------- birleşik akış

def group_item(group, pid, when, text="Arac ilani"):
    href = f"/groups/{group}/posts/{pid}/"
    return {"i": 0, "top": 0, "vh": 900, "links": [f"/groups/{group}/", href], "times": [
        {"j": 1, "href": href, "label": "", "text": when, "lb": "", "visual": when}], "utime": None, "text": text,
        "body": True, "see_more": -1, "pinned": False, "images": []}


def test_combined_feed_splits_posts_by_group(tmp_path):
    other = SocialSource("facebook", "1000000000000002", "https://www.facebook.com/groups/1000000000000002", "grup-b")
    items = [ITEMS[1], group_item("1000000000000002", "2000000000000020", "1h"),
             group_item("1000000000000009", "2000000000000090", "2h"),  # izlenmeyen grup
             ITEMS[2], ITEMS[5]] + [group_item("1000000000000002", f"200000000000003{k}", f"{k + 2}d") for k in range(5)]
    page = FakePage(items)
    f, _ = make(page, tmp_path)
    res = f.fetch_combined([SOURCE, other], {GROUP: CURSOR}, 20)
    assert page.gotos == [fb.COMBINED_FEED_URL]
    a, b = res[GROUP], res["1000000000000002"]
    assert [p.post_id for p in a.posts] == ["2000000000000010", "2000000000000011"]  # okunur adlı bağlantı da A'ya
    assert all(p.source_key == GROUP and p.owner is None for p in a.posts)
    assert [p.post_id for p in b.posts] == ["2000000000000020"] and b.posts[0].source_key == "1000000000000002"
    assert a.requests == 1 and b.requests == 0  # tek sayfa yüklemesi, toplam doğru
    assert a.seen == 3 and b.seen == 5  # A'nın imleci + 4 eski B: akış iki grubun da eşiğinin gerisinde (birleşik durma)
    assert a.cursor.post_id == "2000000000000010" and b.cursor.post_id == "2000000000000020"
    assert f.last_stats["izlenmeyen"] == 1


def test_combined_feed_accepts_worker_call_form(tmp_path):
    """application/social_run.compare_feeds: fetch_combined(kaynaklar, toplam_en_çok) — imleçsiz ilk okuma."""
    page = FakePage(ITEMS)
    f, _ = make(page, tmp_path)
    res = f.fetch_combined([SOURCE], 40)
    assert [p.post_id for p in res[GROUP].posts][:2] == ["2000000000000010", "2000000000000011"]
    assert f.fetch_combined([], 40) == {}


def test_combined_scan_stops_when_feed_is_older_than_every_group():
    scans = {"a": fb.FeedScan(CURSOR, NOW, 20), "b": fb.FeedScan(Cursor(), NOW, 20)}
    multi = fb.CombinedScan(scans)
    for k in range(4):
        multi.note(utc(2026, 9, 20 - k))
        assert not multi.stopped
    multi.note(utc(2026, 9, 15))
    assert multi.stopped


# ---------------------------------------------------------------- proxy, IP, WebRTC

def test_missing_or_bad_proxy_fails_closed(tmp_path):
    for env in ({}, {fb.PROXY_ENV: ""}, {fb.PROXY_ENV: "gizlikullanici:gizlisifre@proxy.example"},
                {fb.PROXY_ENV: "socks5://gizlikullanici:gizlisifre@proxy.example:1080"},
                {fb.PROXY_ENV: "http://gizlikullanici:gizlisifre@proxy.example:notaport"}):
        with pytest.raises(SocialStop) as e:
            fb.build(env, tmp_path)
        assert e.value.signal == Signal.IP_CHANGED
        assert "gizlisifre" not in str(e.value) and "gizlikullanici" not in str(e.value)


def test_proxy_parsing_and_masking():
    assert fb.parse_proxy(PROXY_URL) == {"server": "http://proxy.example:8000", "username": "kullanici", "password": "s3cr3t"}
    assert fb.parse_proxy("http://proxy.example:8000") == {"server": "http://proxy.example:8000"}
    assert fb.parse_proxy("http://k%40x:p%3Aw@proxy.example:8000")["password"] == "p:w"
    masked = fb.mask_proxy(PROXY_URL)
    assert masked == "http://***:***@proxy.example:8000" and "s3cr3t" not in masked and "kullanici" not in masked
    assert fb.mask_proxy(None) == "(yok)"


def test_launch_args_keep_webrtc_and_dns_inside_proxy():
    args = fb.launch_args(fb.parse_proxy(PROXY_URL))
    assert "--webrtc-ip-handling-policy=disable_non_proxied_udp" in args
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in args
    assert "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE proxy.example" in args
    assert not any("s3cr3t" in a or "kullanici" in a for a in args)


def test_webrtc_leak_stops_before_any_page_load(tmp_path):
    page = FakePage(ITEMS, rtc=["udp"])
    f, _ = make(page, tmp_path)
    with pytest.raises(SocialStop) as e:
        f.check_egress()
    assert e.value.signal == Signal.IP_CHANGED and page.gotos == []


def test_check_egress_through_same_context(tmp_path):
    f, ctx = make(FakePage(ITEMS), tmp_path, responses=[FakeResponse(200, '{"ip":"203.0.113.7"}')])
    assert f.check_egress() == "203.0.113.7"
    assert ctx.request.urls == [fb.EGRESS_URLS[0]]
    f2, _ = make(FakePage(ITEMS), tmp_path, responses=[RuntimeError("tünel"), FakeResponse(200, "203.0.113.8\n")])
    assert f2.check_egress() == "203.0.113.8"
    assert fb.parse_ip('{"ip": "2001:db8::1"}') == "2001:db8::1" and fb.parse_ip("yok") is None


def test_unreadable_egress_is_unreachable_not_a_brake(tmp_path):
    f, ctx = make(FakePage(ITEMS), tmp_path, responses=[FakeResponse(502, ""), FakeResponse(200, "<html>")])
    with pytest.raises(Unreachable):
        f.check_egress()
    assert ctx.request.urls == list(fb.EGRESS_URLS)
    f.close()
    assert (tmp_path / fb.STATE_FILE).exists()  # oturum düşmedi: kayıt korunur


def test_fetcher_does_not_judge_ip_mismatch(tmp_path):
    """Beklenen IP ile karşılaştırma işçinin işi: okuyucu ne görürse onu döndürür, ortamdaki beklenen IP'ye bakmaz."""
    f, _ = make(FakePage(ITEMS), tmp_path, responses=[FakeResponse(200, '{"ip":"203.0.113.7"}'),
                                                       FakeResponse(200, '{"ip":"203.0.113.99"}')])
    assert f.check_egress() == "203.0.113.7"
    assert f.check_egress() == "203.0.113.99"
    assert "EXPECTED_IP" not in Path(fb.__file__).read_text(encoding="utf-8")


def test_network_error_on_page_load_is_unreachable_without_group_id(tmp_path):
    err = RuntimeError(f"Page.goto: net::ERR_TUNNEL_CONNECTION_FAILED at https://www.facebook.com/groups/{GROUP}/")
    f, _ = make(FakePage(ITEMS, goto_error=err), tmp_path)
    with pytest.raises(Unreachable) as e:
        f.fetch_new(SOURCE, CURSOR, 20)
    assert "ERR_TUNNEL_CONNECTION_FAILED" in str(e.value) and GROUP not in str(e.value)
    f2, _ = make(FakePage(ITEMS, goto_error=ValueError("başka")), tmp_path)
    with pytest.raises(SourceError):
        f2.fetch_new(SOURCE, CURSOR, 20)


def test_fetch_image_through_same_context_with_limits(tmp_path):
    url = ITEMS[1]["images"][0]["src"]
    jpeg = FakeResponse(200, b"\xff\xd8jpeg", {"content-type": "image/jpeg"})
    f, ctx = make(FakePage(ITEMS), tmp_path, responses=[jpeg, FakeResponse(200, b"<html>", {"content-type": "text/html"}),
                                                         FakeResponse(200, b"x", {"content-type": "image/png",
                                                                                  "content-length": "9000000"})])
    assert f.fetch_image(url) == (b"\xff\xd8jpeg", "image/jpeg")
    assert f.fetch_image(url) is None and f.fetch_image(url) is None
    assert f.fetch_image("https://example.com/x.jpg") is None  # CDN dışı adres hiç istenmez
    assert ctx.request.urls == [url, url, url]


def test_build_needs_saved_session_and_tightens_permissions(tmp_path):
    env = {fb.PROXY_ENV: PROXY_URL, fb.HEADLESS_ENV: "1"}
    with pytest.raises(SocialStop) as e:
        fb.build(env, tmp_path)
    assert e.value.signal == Signal.LOGIN_REQUIRED
    path = tmp_path / fb.STATE_FILE
    path.write_text("{}")
    path.chmod(0o644)
    f = fb.build(env, tmp_path)
    assert isinstance(f, fb.FacebookBrowserFetcher) and f.headless and f.tz == "Europe/Istanbul" and f.platform == "facebook"
    assert f.proxy["password"] == "s3cr3t" and stat.S_IMODE(path.stat().st_mode) == 0o600
    assert f._session is None  # tarayıcı ilk kullanımda açılır


# ---------------------------------------------------------------- giriş yardımcısı

class LoginContext(FakeContext):
    def __init__(self, page, ready_after):
        super().__init__(page, cookies=[])
        self.polls, self.ready_after = 0, ready_after

    def cookies(self, urls=None):
        self.polls += 1
        if self.polls <= self.ready_after:
            return []
        self.page.url = "https://www.facebook.com/"  # giriş bitti, ana sayfa
        self._cookies = [{"name": "c_user", "value": "1"}]
        return list(self._cookies)


def test_login_waits_for_owner_and_saves_session(tmp_path):
    page = FakePage()
    ctx = LoginContext(page, ready_after=2)
    out, ticks = [], iter(range(10_000))
    fb.login({fb.PROXY_ENV: PROXY_URL}, tmp_path, opener=lambda: fb.BrowserSession(ctx, lambda: setattr(ctx, "closed", True)),
             sleep=lambda s: None, monotonic=lambda: next(ticks), out=out.append)
    path = tmp_path / fb.STATE_FILE
    assert page.gotos == [fb.LOGIN_URL] and ctx.closed
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and json.loads(path.read_text())["cookies"][0]["name"] == "c_user"
    shown = "\n".join(out)
    assert "s3cr3t" not in shown and "kullanici" not in shown and "***:***@proxy.example:8000" in shown
    assert not page.mouse.clicks  # kod tıklamaz, yazmaz


def test_login_timeout_saves_nothing(tmp_path):
    ctx = LoginContext(FakePage(), ready_after=10**9)
    clock = iter(range(0, 10**7, 60))
    with pytest.raises(SocialStop) as e:
        fb.login({fb.PROXY_ENV: PROXY_URL}, tmp_path, opener=lambda: fb.BrowserSession(ctx, lambda: None),
                 sleep=lambda s: None, monotonic=lambda: next(clock), out=lambda s: None)
    assert e.value.signal == Signal.LOGIN_REQUIRED and not (tmp_path / fb.STATE_FILE).exists()


# ---------------------------------------------------------------- yapı

def test_playwright_is_imported_only_inside_functions():
    tree = ast.parse(Path(fb.__file__).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Import):
            assert not any(a.name.startswith("playwright") for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("playwright")


def test_vendored_forage_file_keeps_mpl_notice_and_source_commit():
    head = Path(forage_parser.__file__).read_text(encoding="utf-8")[:900]
    assert "Mozilla Public" in head and "http://mozilla.org/MPL/2.0/" in head
    assert "f28c7c5a7df76bf81a8c954a2bede26c62cda9e1" in head
