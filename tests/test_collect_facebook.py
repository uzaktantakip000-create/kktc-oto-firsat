import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from application import collect_facebook as cf
from infrastructure.collectors.facebook_groups import RawGroupPost, parse_item

TEXTS = {p["id"]: p["text"] for p in json.loads((Path(__file__).parent / "fixtures/facebook_group_posts.json").read_text())}
GROUP = "https://www.facebook.com/groups/1"


class FakeRepo:
    def __init__(self, spent="0"):
        self.state, self.rows, self.checked = {"fb_spend:2026-10": spent}, {}, {}

    def get_state(self, key, default=None):
        return self.state.get(key, default)

    def set_state(self, key, value):
        self.state[key] = value

    def upsert_listing(self, source_id, item_id, data):
        new = (source_id, item_id) not in self.rows
        self.rows.setdefault((source_id, item_id), data)
        return new

    def mark_checked(self, source_id, cursor, last_post_at, listings_7d):
        self.checked[source_id] = last_post_at

    def count_recent(self, source_id):
        return 0


NOW = datetime(2026, 10, 1, 20, tzinfo=timezone.utc)
SOURCES = [dict(id="G1", name="Kktc Sol Direksiyon Araba Pazari", url=GROUP + "/", last_checked_at=None)]


def fetch_stub(posts, spent=0.05):
    calls = []

    def fetch(token, urls, hours, max_items):
        calls.append((urls, hours, max_items))
        return posts, spent, len(posts)

    fetch.calls = calls
    return fetch


def post(i, text):
    return RawGroupPost(i, f"https://www.facebook.com/groups/1/permalink/{i}/", NOW, text, GROUP)


def test_only_real_listings_are_stored_and_no_personal_fields(monkeypatch):
    monkeypatch.setattr(cf, "gbp_rate", lambda c: 1.0)
    repo = FakeRepo()
    fetch = fetch_stub([post("1", TEXTS["x1"]), post("2", TEXTS["3124656827729983"]), post("3", TEXTS["x2"])])
    res = cf.collect_facebook_groups(repo, "tok", SOURCES, fetch=fetch, now=NOW)
    st = res["Kktc Sol Direksiyon Araba Pazari"]
    assert (st.fetched, st.new, st.skipped) == (3, 1, 2)
    row = repo.rows[("G1", "1")]
    assert row["price_gbp"] == 7250 and row["seller_phone"] == "905330000009" and row["extraction_by"] == "parser_serbest"
    assert not {"seller_handle", "author", "user"} & set(row)  # yazar/profil bilgisi hiç saklanmaz
    assert fetch.calls[0][1] == cf.MAX_HOURS  # ilk çalıştırma: en geniş pencere


def test_second_run_does_not_duplicate_and_window_shrinks(monkeypatch):
    monkeypatch.setattr(cf, "gbp_rate", lambda c: 1.0)
    repo = FakeRepo()
    src = [{**SOURCES[0], "last_checked_at": datetime(2026, 10, 1, 16, tzinfo=timezone.utc)}]
    fetch = fetch_stub([post("1", TEXTS["x1"])])
    cf.collect_facebook_groups(repo, "tok", src, fetch=fetch, now=NOW)
    res = cf.collect_facebook_groups(repo, "tok", src, fetch=fetch, now=NOW)
    assert res["Kktc Sol Direksiyon Araba Pazari"].new == 0 and fetch.calls[0][1] == 5


def test_monthly_budget_stops_collection():
    repo = FakeRepo(spent="15.0")
    with pytest.raises(RuntimeError, match="tavan"):
        cf.collect_facebook_groups(repo, "tok", SOURCES, fetch=fetch_stub([]), now=NOW)


def test_spend_is_recorded(monkeypatch):
    monkeypatch.setattr(cf, "gbp_rate", lambda c: 1.0)
    repo = FakeRepo(spent="1.0")
    cf.collect_facebook_groups(repo, "tok", SOURCES, fetch=fetch_stub([], spent=0.25), now=NOW)
    assert float(repo.state["fb_spend:2026-10"]) == pytest.approx(1.25)


def test_apify_item_keeps_only_listing_fields():
    item = {"legacyId": "123", "url": "https://www.facebook.com/groups/1/permalink/123/", "text": "2015 Kia Ceed 5.000 STG",
            "time": "2026-10-01T15:53:37.000Z", "inputUrl": GROUP + "/", "user.name": "X", "user": {"name": "X", "id": "9"}}
    p = parse_item(item)
    assert (p.post_id, p.group_url) == ("123", GROUP) and not hasattr(p, "user")
    assert parse_item({**item, "text": ""}) is None  # metinsiz gönderi
