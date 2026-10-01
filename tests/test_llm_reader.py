from datetime import datetime, timezone

import pytest

from application import collect_facebook as cf
from application import llm_reader as lr
from application.evaluate import Evaluated
from infrastructure.collectors.facebook_groups import RawGroupPost

TEXT = "2016 Honda Fit 1.3 otomatik\n53.000 km\nFiyat: 7.500£ nakit"
GOOD = {"arac_ilani_mi": True, "marka": "Honda", "model": "Fit", "yil": 2016, "yil_alinti": "2016 Honda Fit",
        "km": 53000, "km_alinti": "53.000 km", "fiyat": 7500, "fiyat_alinti": "Fiyat: 7.500£",
        "direksiyon": None, "pesinat_veya_kredi_devri": False, "satildi": False}


class FakeRepo:
    def __init__(self):
        self.state, self.downgraded, self.rows = {}, [], {}

    def get_state(self, k, default=None):
        return self.state.get(k, default)

    def set_state(self, k, v):
        self.state[k] = v

    def downgrade_evaluation(self, listing_id, flags):
        self.downgraded.append((listing_id, flags))

    def known_item_ids(self, source_id):
        return set()

    def upsert_listing(self, source_id, item_id, data):
        self.rows[(source_id, item_id)] = data
        return True

    def mark_checked(self, *a, **k):
        pass

    def count_recent(self, source_id):
        return 0


def reader(repo, data=GOOD, err=None, cost=0.0005):
    calls = []

    def call(key, model, text):
        calls.append(text)
        return data, err, cost

    r = lr.LlmReader(repo, "k", "m", call=call, now=datetime(2026, 10, 2, tzinfo=timezone.utc))
    r.seen = calls
    return r


def ev(platform="instagram", **over):
    listing = {"id": "L1", "platform": platform, "raw_text": TEXT, "price_amount": 7500, "currency": "GBP", "year": 2016,
               "km": 53000, "brand": "Honda", "steering": None} | over
    return Evaluated(listing, None, None, [], [], [])


def test_clean_candidate_is_kept_with_check_line_and_cached():
    repo = FakeRepo()
    r = reader(repo)
    out = lr.verify_candidates(repo, r, [ev()])
    assert len(out) == 1 and out[0].checks and not repo.downgraded
    lr.verify_candidates(repo, r, [ev()])
    assert len(r.seen) == 1  # ikinci turda önbellekten: tekrar ödenmez


def test_mismatch_downgrades_and_drops_candidate():
    repo = FakeRepo()
    out = lr.verify_candidates(repo, reader(repo), [ev(price_amount=4500)])
    assert out == [] and repo.downgraded == [("L1", ["okuma_fiyat"])]


def test_llm_failure_keeps_candidate_with_unchecked_note():
    repo = FakeRepo()
    out = lr.verify_candidates(repo, reader(repo, data=None, err="http 500"), [ev()])
    assert len(out) == 1 and lr.UNCHECKED in out[0].warnings and not repo.downgraded


def test_unreachable_price_is_flagged_not_dropped():
    repo = FakeRepo()
    out = lr.verify_candidates(repo, reader(repo, data={**GOOD, "fiyat": None, "fiyat_alinti": None}), [ev()])
    assert len(out) == 1 and lr.UNCONFIRMED in out[0].warnings


def test_sites_and_missing_reader_are_untouched():
    repo = FakeRepo()
    r = reader(repo)
    assert len(lr.verify_candidates(repo, r, [ev("web")])) == 1 and r.seen == []
    assert len(lr.verify_candidates(repo, None, [ev()])) == 1


def test_daily_budget_stops_reading():
    repo = FakeRepo()
    r = reader(repo, cost=lr.DAILY_BUDGET_USD)
    assert r.read(TEXT) is not None
    assert r.read(TEXT) is None and "bütçe" in r.last_error and len(r.seen) == 1


def test_listing_fields_requires_brand_year_price_and_rejects_sold_or_credit(monkeypatch):
    monkeypatch.setattr(lr, "gbp_rate", lambda c: 1.0)
    from domain.llm_read import parse_llm_read
    ok = lr.listing_fields(parse_llm_read(GOOD, TEXT))
    assert ok["extraction_by"] == "llm" and ok["price_gbp"] == 7500 and ok["brand"] == "Honda"
    assert lr.listing_fields(parse_llm_read({**GOOD, "satildi": True}, TEXT)) is None
    assert lr.listing_fields(parse_llm_read({**GOOD, "pesinat_veya_kredi_devri": True}, TEXT)) is None
    assert lr.listing_fields(parse_llm_read({**GOOD, "fiyat": None, "fiyat_alinti": None}, TEXT)) is None
    assert lr.listing_fields(None) is None


def test_facebook_post_with_two_prices_is_read_by_llm_and_marked(monkeypatch):
    monkeypatch.setattr(lr, "gbp_rate", lambda c: 1.0)
    monkeypatch.setattr(cf, "gbp_rate", lambda c: 1.0)
    repo = FakeRepo()
    text = "2016 Honda Fit otomatik 53.000 km\n7.500£\n8.200£ (takaslı)\nGirne"
    assert cf.listing_data(RawGroupPost("1", "u", None, text, "g"), {"name": "x"}) is None  # kural okuyamıyor
    good = {**GOOD, "yil_alinti": "2016 Honda Fit", "fiyat_alinti": "7.500£"}
    now = datetime(2026, 10, 2, 9, tzinfo=timezone.utc)
    src = [dict(id="G1", name="G", url="https://www.facebook.com/groups/1/", last_checked_at=None)]
    fetch = lambda token, urls, hours, n: ([RawGroupPost("1", "u1", now, text, "https://www.facebook.com/groups/1")], 0.01, 1)
    res = cf.collect_facebook_groups(repo, "t", src, fetch=fetch, now=now, reader=reader(repo, data=good))
    row = repo.rows[("G1", "1")]
    assert res["G"].llm_read == 1 and row["extraction_by"] == "llm" and row["price_gbp"] == 7500


def test_free_text_site_listing_is_verified_but_structured_site_is_not():
    repo = FakeRepo()
    r = reader(repo)
    structured = ev("web", extraction_by="parser")
    assert len(lr.verify_candidates(repo, r, [structured])) == 1 and r.seen == []        # JSON-LD'li site: gerek yok
    free = ev("web", extraction_by="parser_serbest", price_amount=4500)                  # serbest metin: okutulur, fiyat uyuşmuyor
    assert lr.verify_candidates(repo, r, [free]) == [] and len(r.seen) == 1
