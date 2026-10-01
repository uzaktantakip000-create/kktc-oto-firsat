from datetime import datetime, timedelta, timezone

from application.evaluate import evaluate_new
from domain.profit import Tier


class FakeRepo:
    def __init__(self, listings, pool):
        self._listings, self._pool, self.saved = listings, pool, []

    def market_pool(self, days):
        return self._pool

    def unevaluated_active(self):
        return self._listings

    def save_evaluation(self, listing_id, ev):
        self.saved.append((listing_id, ev))


NOW = datetime.now(timezone.utc)


def car(i, price, **kw):
    base = dict(id=i, brand_norm="Toyota", model_norm="vitz", year=2015, km=80_000, steering="RHD",
                transmission="otomatik", fuel="benzin", price_gbp=price, currency_guess=False,
                first_seen_at=NOW, ref_date=NOW, is_active=True, duplicate_of=None, raw_text="", model="Vitz")
    return base | kw


POOL = [car(f"p{i}", p) for i, p in enumerate([8000, 8200, 8400, 8600, 8800, 9000, 9200, 9400])]  # 8 emsal = orta güven (3-7 emsalde 🟢 verilmez)


def test_cheap_known_steering_is_strong():
    repo = FakeRepo([car("t", 5000)], POOL)
    (ev,) = evaluate_new(repo)
    assert ev.profit.tier is Tier.STRONG


def test_unknown_steering_counts_as_right_hand_drive():
    # KKTC'de ilanların ~%99'u sağ direksiyon: yazmıyorsa RHD varsayılır, 🟢 engellenmez
    repo = FakeRepo([car("t", 5000, steering=None)], POOL)
    (ev,) = evaluate_new(repo)
    assert ev.profit.tier is Tier.STRONG


def test_explicit_left_hand_drive_is_compared_only_with_lhd():
    repo = FakeRepo([car("t", 5000, steering="LHD")], POOL)  # havuz RHD: emsal yok
    assert evaluate_new(repo) == []


def test_implausible_price_saved_as_none_without_alert():
    repo = FakeRepo([car("t", 15)], POOL)
    assert evaluate_new(repo) == []
    assert repo.saved[0][1]["tier"] == "yok" and "fiyat_gecersiz" in repo.saved[0][1]["red_flags"]


def test_missing_km_never_strong():
    repo = FakeRepo([car("t", 5000, km=None)], POOL)
    (ev,) = evaluate_new(repo)
    assert ev.profit.tier is Tier.NEGOTIABLE and "km_yok" in repo.saved[0][1]["red_flags"]


def test_guessed_currency_never_strong():
    repo = FakeRepo([car("t", 5000, currency_guess=True)], POOL)
    (ev,) = evaluate_new(repo)
    assert ev.profit.tier is Tier.NEGOTIABLE and "para_birimi_tahmin" in repo.saved[0][1]["red_flags"]


def test_missing_model_never_strong():
    repo = FakeRepo([car("t", 5000, model_norm=None)], [car(f"p{i}", 8000 + i, model_norm=None) for i in range(5)])
    (ev,) = evaluate_new(repo)
    assert ev.profit.tier is Tier.NEGOTIABLE


def test_km_far_above_comparables_never_strong():
    pool = [car(f"p{i}", 8000 + i * 100, km=60_000) for i in range(8)]  # hepsi 60 bin km
    repo = FakeRepo([car("t", 5000, km=98_000)], pool)  # aynı km bandı (50-100 bin) ama +%63
    (ev,) = evaluate_new(repo)
    assert ev.market.median_km == 60_000
    assert ev.profit.tier is Tier.NEGOTIABLE and "km_yuksek" in repo.saved[0][1]["red_flags"]


def test_similar_km_stays_strong():
    pool = [car(f"p{i}", 8000 + i * 100, km=60_000) for i in range(8)]
    repo = FakeRepo([car("t", 5000, km=70_000)], pool)
    (ev,) = evaluate_new(repo)
    assert ev.profit.tier is Tier.STRONG


def test_zero_km_counts_as_missing_and_flag_only_when_downgraded():
    repo = FakeRepo([car("t", 5000, km=0)], POOL)
    (ev,) = evaluate_new(repo)
    assert ev.profit.tier is Tier.NEGOTIABLE and "km_yok" in repo.saved[0][1]["red_flags"]
    repo = FakeRepo([car("t", 7000, km=None)], POOL)  # zaten kâr yetersiz (🟢 olmazdı): "bu yüzden 🟢 değil" yazılmaz
    evaluate_new(repo)
    assert "km_yok" not in repo.saved[0][1]["red_flags"]
