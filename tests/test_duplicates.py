from datetime import date, datetime, timedelta, timezone

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


# --- 2.7: km, değerlendirmedeki gibi effective_km ile okunur (şüpheli km = bilinmiyor) ---
TODAY = date(2026, 10, 5)


def test_trusted_close_km_still_merges():
    assert same_car(car(1, km=80_000), car(2, km=80_300, price_gbp=5800.0), TODAY)  # aynı telefon + yakın km
    assert same_car(car(1, km=80_000, seller_phone=None), car(2, km=80_400, seller_phone=None, price_gbp=6500.0), TODAY)  # km + ±%15 fiyat
    assert not same_car(car(1, km=80_000), car(2, km=95_000), TODAY)  # güvenilir km uzak: farklı araç (telefon aynı olsa da)


def test_suspicious_km_on_both_is_not_evidence_of_same_car():
    # Eski kural: "1 km" ile "1 km" (ya da 107 ile 220 = 107.000 ile 220.000) yakın sayılıp telefonsuz iki farklı araç birleşiyordu
    assert not same_car(car(1, year=2010, km=1, seller_phone=None), car(2, year=2010, km=1, seller_phone=None), TODAY)
    assert not same_car(car(1, year=2013, km=107, seller_phone=None, price_gbp=6400.0),
                        car(2, year=2013, km=220, seller_phone=None, price_gbp=5790.0), TODAY)


def test_suspicious_km_falls_back_to_phone_and_price():
    # Canlıdaki örnek: aynı satıcı aynı Swift'i önce "1 km", sonra "218.000 km" yazarak yeniden ilana koydu
    first, fixed = car(1, year=2008, km=1, price_gbp=4100.0), car(2, year=2008, km=218_000, price_gbp=4100.0)
    assert same_car(first, fixed, TODAY)
    assert not same_car(first, {**fixed, "price_gbp": 5200.0}, TODAY)  # fiyat ±%15 dışında: birleşmez
    assert not same_car(first, {**fixed, "seller_phone": "905330000001"}, TODAY)  # farklı telefon: km kanıtı yok, birleşmez
    assert not same_car({**first, "seller_phone": None}, {**fixed, "seller_phone": None}, TODAY)  # telefonsuz: birleşmez


def test_one_suspicious_one_missing_km_needs_phone():
    a = car(1, year=2013, km=11_500, seller_phone=None)  # 13 yaşında 11.500 km: şüpheli (115.000 olabilir)
    assert not same_car(a, car(2, year=2013, km=115_000, seller_phone=None), TODAY)
    assert not same_car(a, car(2, year=2013, km=None, seller_phone=None), TODAY)
    assert same_car(car(1, year=2013, km=11_500), car(2, year=2013, km=None, price_gbp=6200.0), TODAY)  # aynı telefon + yakın fiyat


def test_equal_km_different_phone_depends_on_price():
    # Telefon farkı tek başına engel değil (aynı araç farklı sitede farklı numarayla olabilir); km + fiyat kuralı aynen geçerli
    a = car(1, km=80_250, seller_phone="905330000001")
    assert same_car(a, car(2, km=80_250, seller_phone="905330000002", price_gbp=6400.0), TODAY)
    assert not same_car(a, car(2, km=80_250, seller_phone="905330000002", price_gbp=7500.0), TODAY)
    r = car(1, km=150_000, seller_phone="905330000001")  # yuvarlak km: fiyat ±%3
    assert not same_car(r, car(2, km=150_000, seller_phone="905330000002", price_gbp=6300.0), TODAY)


def test_same_car_judges_km_on_the_given_day():
    a = car(1, year=2026, km=500, seller_phone=None, price_gbp=20_000.0)
    b = car(2, year=2026, km=500, seller_phone=None, price_gbp=20_500.0)
    assert same_car(a, b, date(2026, 10, 5))  # yeni araçta 500 km makul
    assert not same_car(a, b, date(2028, 6, 1))  # 2 yaşında 500 km: şüpheli → km kanıtı yok


def test_mark_duplicates_uses_the_round_time():
    def rows():
        return [car("a", 5, year=2026, km=500, seller_phone=None, price_gbp=20_000.0),
                car("b", 1, year=2026, km=500, seller_phone=None, price_gbp=20_500.0)]

    assert mark_duplicates(FakeRepo(rows()), now=datetime(2026, 10, 5, tzinfo=timezone.utc)) == 1
    assert mark_duplicates(FakeRepo(rows()), now=datetime(2028, 6, 1, tzinfo=timezone.utc)) == 0


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


def test_quick_round_asks_only_for_groups_with_new_listings_and_marks_the_same():
    class QuickRepo:
        def __init__(self, rows):
            self.rows, self.dups, self.new_hours = rows, {}, []

        def dedupe_candidates(self, days=120, new_hours=None):
            self.new_hours.append(new_hours)
            return list(self.rows)

        def set_duplicate(self, listing_id, canonical_id):
            self.dups[listing_id] = canonical_id

    repo = QuickRepo([car("a", 5), car("b", 1, price_gbp=5900.0)])
    assert mark_duplicates(repo, quick=True) == 1 and repo.dups == {"b": "a"}  # sonuç tam tarama ile aynı
    assert repo.new_hours == [3]
    mark_duplicates(repo)  # varsayılan: tam tarama
    assert repo.new_hours == [3, None]  # tam taramada daraltma yok
