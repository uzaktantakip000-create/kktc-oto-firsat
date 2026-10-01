from datetime import datetime, timedelta, timezone

from application.dedupe import mark_duplicates
from domain.duplicates import same_car

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def car(i, hours_ago=0, **kw):
    base = dict(id=i, brand_norm="Toyota", model_norm="vitz", year=2015, km=80_000, seller_phone="905330000009",
                price_gbp=6000.0, is_active=True, first_seen_at=NOW - timedelta(hours=hours_ago), duplicate_of=None)
    return base | kw


def test_repost_same_phone_km_price_is_duplicate():
    assert same_car(car(1), car(2, price_gbp=5800.0))


def test_same_dealer_two_different_cars_not_merged():
    assert not same_car(car(1), car(2, km=120_000))  # km çok farklı
    assert not same_car(car(1, km=None), car(2, km=None, price_gbp=9000.0))  # km yok, fiyat uzak


def test_cross_source_same_km_close_price_without_phone():
    assert same_car(car(1, km=80_250, seller_phone=None), car(2, km=80_250, seller_phone=None, price_gbp=6100.0))


def test_round_km_without_phone_needs_nearly_same_price():
    # İki farklı araç da "150.000 km" yazmış olabilir
    assert not same_car(car(1, km=150_000, seller_phone=None), car(2, km=150_000, seller_phone=None, price_gbp=6500.0))
    assert same_car(car(1, km=150_000, seller_phone=None), car(2, km=150_000, seller_phone=None, price_gbp=5950.0))


def test_same_km_but_far_price_and_no_phone_not_merged():
    assert not same_car(car(1, seller_phone=None), car(2, seller_phone=None, price_gbp=9000.0))


def test_no_km_requires_phone_and_price():
    assert same_car(car(1, km=None), car(2, km=None, price_gbp=6100.0))
    assert not same_car(car(1, km=None, seller_phone=None), car(2, km=None, seller_phone=None))


class FakeRepo:
    def __init__(self, rows):
        self.rows, self.dups = rows, {}

    def dedupe_candidates(self):
        return self.rows

    def set_duplicate(self, i, canon):
        self.dups[i] = canon


def test_oldest_is_canonical_and_chain_points_to_it():
    repo = FakeRepo([car("new", 1), car("old", 100), car("mid", 50, price_gbp=5900.0), car("other", 10, km=200_000)])
    assert mark_duplicates(repo) == 2
    assert repo.dups == {"mid": "old", "new": "old"}


def test_relisted_car_is_not_duplicate_of_old_inactive_listing():
    repo = FakeRepo([car("old", 100, is_active=False), car("relisted", 1, price_gbp=5000.0)])
    repo.rows[1]["km"] = 80_000
    assert mark_duplicates(repo) == 0  # eski ilan satılmış/arşivde; yeni ilan yeni fırsat olabilir


def test_inactive_old_duplicates_still_collapse():
    repo = FakeRepo([car("a", 100, is_active=False), car("b", 50, is_active=False)])
    assert mark_duplicates(repo) == 1 and repo.dups == {"b": "a"}
