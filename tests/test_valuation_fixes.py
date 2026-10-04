"""Değerleme düzeltmeleri (02.10.2026 denetimi): km eğimi, veri boşluğu, TL kapısı, saçma fiyat, mükerrer, kilit, kural sürümü."""
import math
from contextlib import contextmanager

import pytest

from application import evaluate
from domain import decision
from application.evaluate import assess_listing, evaluate_new
from domain.data_gate import GAP_LABELS, data_gaps
from domain.price_book import B_KM_PRIOR, B_KM_WEAK, Curve, Estimate, PriceBook, build_book, estimate_from_book
from domain.profit import Tier, evaluate_profit
from domain.settings import RULES_VERSION, Settings
from entrypoints import cron_evaluate
from infrastructure.db.price_book_store import _pack_years, _unpack_years
from infrastructure.db.repository import Repository
from tests.test_evaluate import FakeRepo, POOL, car as ecar
from tests.test_price_book import NOW, REF, S, car, curve, fit, lst, synth


# --- 1. km eğimi ---
def test_positive_km_slope_is_clamped_to_prior():
    rows = synth(b_km=+0.04)  # veri "km arttıkça pahalı" diyor: mantıksız
    c = fit(rows)
    assert c is not None and c.b_km == B_KM_PRIOR < B_KM_WEAK
    assert c.b_age < 0 and c.sigma < 0.3


def test_sane_km_slope_is_untouched():
    c = fit(synth(b_km=-0.03))
    assert abs(c.b_km - (-0.03)) < 0.01


def test_high_km_car_not_inflated_by_bad_slope():
    """BMW 3 2011, 234k km: eğim pozitif öğrenildiği için değer şişiyordu; artık düşük km'li aynı yıldan ucuz."""
    c = fit(synth(b_km=+0.04))
    old = Curve(**{**c.__dict__, "b_km": +0.044})  # eski veritabanındaki yanıltıcı eğri (güvenlik ağı)
    e_hi = estimate_from_book(lst(year=2012, km=190_000), PriceBook(curves={("Toyota", "vitz"): c}), S)
    e_lo = estimate_from_book(lst(year=2012, km=60_000), PriceBook(curves={("Toyota", "vitz"): c}), S)
    assert e_hi.value_gbp < e_lo.value_gbp
    # güvenlik ağı: b_km pozitif saklanmış olsa da tahmin eğimi pozitif kullanmaz
    assert old.predict_ln(2012, 230_000) < old.predict_ln(2012, 50_000)


def test_weak_km_slope_below_refit_threshold_is_clamped():
    assert fit(synth(b_km=-0.006)).b_km == B_KM_PRIOR  # -0.010'dan zayıf: veri km'yi yaştan ayıramamış
    assert fit(synth(b_km=-0.015)).b_km < -0.012  # makul eğim korunur


# --- 2. veri boşluğu ---
def test_fit_stores_year_counts():
    c = fit(synth())
    assert sum(c.year_counts.values()) == c.n and set(c.year_counts) == set(range(2012, 2021))


def test_estimate_requires_rows_near_target_year():
    c = curve(min_year=2012, max_year=2021, year_counts={2012: 5, 2013: 4, 2019: 4, 2020: 4, 2021: 1})
    b = PriceBook(curves={("Toyota", "vitz"): c})
    assert estimate_from_book(lst(year=2013), b, S) is not None
    assert estimate_from_book(lst(year=2020), b, S) is not None
    assert estimate_from_book(lst(year=2016), b, S) is None  # ±2 yılda hiç satır yok
    assert estimate_from_book(lst(year=2015), b, S) is not None  # 2013-2017 penceresinde 4 satır var


def test_density_boundaries():
    c = curve(year_counts={2012: 2, 2013: 1, 2020: 1})
    assert c.dense_at(2012) and c.dense_at(2014) and not c.dense_at(2015)  # 2012-2014: 3 satır yeter; 2015: yalnız 1
    assert curve().dense_at(2016)  # year_counts bilinmiyorsa (eski kayıt) kapı uygulanmaz


def test_book_b_rows_skip_gap_years():
    pool = [car(i, y, (REF - y) * 12_000, 9000 * math.exp(-0.09 * (REF - y)) * 2.5, brand="Toyota", model="vitz")
            for i, y in enumerate([2012] * 6 + [2013] * 6 + [2019] * 6 + [2020] * 6)]
    book = build_book(pool, [], NOW, None, S)
    cur = book.curve("Toyota", "vitz")
    assert cur is not None and cur.year_counts == {2012: 6, 2013: 6, 2019: 6, 2020: 6}
    assert book.row("Toyota", "vitz", 2016) is None  # ±2 yılda satır yok: B satırı yazılmaz
    assert book.row("Toyota", "vitz", 2012) is not None and book.row("Toyota", "vitz", 2020) is not None


def test_year_counts_roundtrip():
    assert _unpack_years(_pack_years({2013: 4, 2012: 5})) == {2012: 5, 2013: 4}
    assert _unpack_years(None) == {} and _pack_years({}) == ""


# --- 3. TL kapısı ---
def test_tl_gap_label_and_gate():
    assert "tl_fiyat" in GAP_LABELS and "TL" in GAP_LABELS["tl_fiyat"]
    from domain.comparables import find_market
    l = ecar("t", 5000, currency="TRY")
    assert "tl_fiyat" in data_gaps(l, find_market(l, POOL, S), S)
    l = ecar("t", 5000, currency="GBP")
    assert "tl_fiyat" not in data_gaps(l, find_market(l, POOL, S), S)


def test_tl_listing_is_at_most_yellow():
    (ev,) = evaluate_new(FakeRepo([ecar("t", 5000, currency="TRY")], POOL))
    assert ev.profit.tier is Tier.NEGOTIABLE
    (ev,) = evaluate_new(FakeRepo([ecar("t", 5000, currency="GBP")], POOL))
    assert ev.profit.tier is Tier.STRONG


def test_tl_listing_never_estimated(monkeypatch):
    est = Estimate(10_000, 8_000, "B", 30, 12, 0.15)
    monkeypatch.setattr(decision, "estimate_from_book", lambda l, b, s, now=None: est)
    b = PriceBook()
    assert assess_listing(ecar("t", 6000, currency="GBP"), [], S, b).profit.tier is Tier.ESTIMATED
    assert assess_listing(ecar("t", 6000, currency="TRY"), [], S, b) is None


# --- 4. saçma fiyat ---
def test_absurd_price_few_comparables_is_none():
    assert evaluate_profit(1750, 18_000, 3).tier is Tier.NONE  # Mazda 3 2020 örneği (+%854 🟡 çıkıyordu)
    assert evaluate_profit(4000, 10_000, 5).tier is Tier.NONE
    assert evaluate_profit(4000, 10_000, 25).tier is Tier.NEGOTIABLE  # bol emsalde eski davranış


def test_absurd_price_stored_as_yok_with_red_flag():
    repo = FakeRepo([ecar("t", 1600)], POOL[:3])  # 3 emsal ~£8.000
    (ev,) = evaluate_new(repo)
    _, saved = repo.saved[0]
    assert saved["tier"] == "yok" and "fiyat_asiri_dusuk" in saved["red_flags"] and "fiyat_asiri_dusuk" in GAP_LABELS
    assert ev.profit.tier is Tier.NONE


def test_absurd_price_not_estimated(monkeypatch):
    est = Estimate(10_000, 8_000, "B", 30, 12, 0.15)
    monkeypatch.setattr(decision, "estimate_from_book", lambda l, b, s, now=None: est)
    a = assess_listing(ecar("t", 3900), POOL[:3], S, PriceBook())  # medyan ~8.000: yarısından düşük
    assert a is not None and a.profit.tier is Tier.NONE


# --- 5. mükerrer, 6. kilit ---
class Conn:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self

    def fetchall(self):
        return []

    def fetchone(self):
        return None

    rowcount = 3


def repo_with(conn):
    r = object.__new__(Repository)
    r.conn = conn
    return r


def test_pending_strong_excludes_duplicates():
    conn = Conn()
    repo_with(conn).pending_strong(36, "tahmini")
    assert "l.duplicate_of IS NULL" in conn.calls[0][0]


def test_lock_expires_in_16_minutes():
    conn = Conn()
    repo_with(conn).acquire_lock("evaluate")
    assert conn.calls[0][1] == ("lock:evaluate", 16)


# --- 7. kural sürümü ---
class VRepo:
    def __init__(self, state=None):
        self.state, self.reset_days = state or {}, []

    def get_state(self, k, default=None):
        return self.state.get(k, default)

    def set_state(self, k, v):
        self.state[k] = v

    def reset_evaluations(self, days):
        self.reset_days.append(days)
        return 5


def test_rules_version_change_resets_once():
    repo = VRepo({"rules_version": "eski"})
    assert cron_evaluate.apply_rules_version(repo) == 5 and repo.reset_days == [7]
    assert repo.state["rules_version"] == RULES_VERSION == "2026-10-04c"
    assert repo.state["eval:full"] == ""  # silinen değerlendirmeler saatlik tam turu beklemez: sonraki tur TAM tur olur
    assert cron_evaluate.apply_rules_version(repo) == 0 and repo.reset_days == [7]  # aynı sürüm: silme yok
    assert cron_evaluate.apply_rules_version(VRepo()) == 5  # hiç yazılmamışsa da bir kez


def test_reset_evaluations_never_touches_alerted_or_inactive():
    conn = Conn()
    assert repo_with(conn).reset_evaluations(7) == 3
    sql, params = conn.calls[0]
    assert "NOT EXISTS (SELECT 1 FROM alerts" in sql and "l.is_active" in sql and params == (7,)
