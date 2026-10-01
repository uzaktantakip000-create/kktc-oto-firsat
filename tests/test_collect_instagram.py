from datetime import datetime, timezone

from application import collect_instagram as ci
from infrastructure.collectors.instagram_apify import RawPost

CAP = "Marka: Toyota\nModel: Vitz\nYıl: 2015\nFiyat: 6.900 STG"


class FakeRepo:
    def __init__(self, spent="0"):
        self.known, self.checked, self.state = set(), {}, {}
        self.spent = spent

    def get_state(self, key, default=None):
        return self.state.get(key, self.spent)

    def set_state(self, key, value):
        self.state[key] = value

    def upsert_listing(self, source_id, item_id, data):
        new = (source_id, item_id) not in self.known
        self.known.add((source_id, item_id))
        return new

    def mark_checked(self, source_id, cursor, last_post_at, listings_7d):
        self.checked[source_id] = cursor

    def count_recent(self, source_id):
        return 1


def test_single_apify_run_for_all_sources_and_posts_routed_by_owner(monkeypatch):
    calls = []

    def fake_fetch(token, usernames, newer_than, limit=20):
        calls.append((usernames, newer_than))
        t = datetime(2026, 10, 1, tzinfo=timezone.utc)
        return [RawPost("a1", "u", t, CAP, None, "acc_a"), RawPost("b1", "u", t, CAP, None, "acc_b"),
                RawPost("zz", "u", t, CAP, None, "renamed_account")]

    monkeypatch.setattr(ci, "fetch_posts", fake_fetch)
    monkeypatch.setattr(ci, "gbp_rate", lambda c: 1.0)
    sources = [dict(id="A", name="A", url="https://www.instagram.com/Acc_A/", cursor="2026-09-30T10:00:00"),
               dict(id="B", name="B", url="https://www.instagram.com/acc_b/", cursor=None)]
    repo = FakeRepo()
    res = ci.collect_sources(repo, "tok", sources)
    # imleci olan hesap ana çağrıda (son imleçten itibaren), hiç taranmamış hesap ayrı tek seferlik çağrıda (3 güne kadar)
    assert [c[0] for c in calls] == [["Acc_A"], ["acc_b"]]
    assert calls[0][1] == "2026-09-30T10:00:00" and calls[1][1] < calls[0][1]
    assert (res["A"].new, res["B"].new) == (1, 1)  # yeniden adlandırılmış hesabın gönderisi hiçbir kaynağa karışmaz
    assert set(repo.checked) == {"A", "B"}


def test_next_run_asks_only_for_posts_after_previous_run_not_oldest_cursor(monkeypatch):
    calls = []
    monkeypatch.setattr(ci, "fetch_posts", lambda token, usernames, newer_than, limit=20: calls.append((usernames, newer_than)) or [])
    monkeypatch.setattr(ci, "gbp_rate", lambda c: 1.0)
    repo = FakeRepo()
    now = datetime.now(timezone.utc)
    repo.state["ig_watermark"] = now.strftime("%Y-%m-%dT%H:%M:%S")  # az önce tarandı
    old = dict(id="S", name="S", url="https://www.instagram.com/slow/", cursor="2026-09-30T10:00:00")  # yavaş hesap: eski imleç
    ci.collect_sources(repo, "tok", [old])
    assert calls[0][1] >= (now.replace(microsecond=0) - __import__("datetime").timedelta(minutes=25)).strftime("%Y-%m-%dT%H:%M:%S")


def test_account_without_posts_gets_a_cursor_so_it_joins_the_main_group(monkeypatch):
    monkeypatch.setattr(ci, "fetch_posts", lambda *a, **k: [])
    repo = FakeRepo()
    ci.collect_sources(repo, "tok", [dict(id="N", name="N", url="https://www.instagram.com/new/", cursor=None)])
    assert repo.checked["N"] is not None


def test_monthly_budget_blocks_collection():
    import pytest
    with pytest.raises(RuntimeError, match="tavan"):
        ci.collect_sources(FakeRepo(spent="8.0"), "tok", [dict(id="A", name="A", url="https://www.instagram.com/a/", cursor=None)])
