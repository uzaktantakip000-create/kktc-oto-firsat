import math
from datetime import datetime, timedelta, timezone

from domain import price_book as pb
from domain.price_book import BookRow, Curve, PriceBook, build_book, estimate_from_book, fit_curve, next_status, self_check, variant_of
from domain.settings import Settings

NOW = datetime(2026, 10, 2, 3, 0, tzinfo=timezone.utc)
S = Settings()
REF = 2026


def car(i, year, km, price, phone=None, brand="Toyota", model="vitz", raw=None, **kw):
    d = NOW - timedelta(days=5)
    return {"id": f"c{i}", "brand_norm": brand, "model_norm": model, "model": raw, "year": year, "km": km, "price_gbp": float(price),
            "steering": "RHD", "seller_phone": phone or f"90533{i:07d}", "is_active": True, "duplicate_of": None,
            "currency_guess": False, "urgency_signals": [], "ref_date": d, "first_seen_at": d, **kw}


def synth(n=60, b_age=-0.09, b_km=-0.03, noise=0.0, years=range(2012, 2021)):
    """ln fiyat = 9.5 + b_age·yaş + b_km·km10; km yaştan bağımsız oynar ki iki katsayı ayrılabilsin."""
    ys = list(years)
    out = []
    for i in range(n):
        year = ys[i % len(ys)]
        km = (REF - year) * 10_000 + ((i * 7) % 11 - 5) * 6_000 + 40_000
        eps = noise * (1 if i % 2 else -1)
        out.append(car(i, year, km, math.exp(9.5 + b_age * (REF - year) + b_km * km / 10_000 + eps)))
    return out


def fit(rows, weights=None, **kw):
    return fit_curve(rows, weights, "Toyota", "vitz", REF, S, **kw)


# --- fit_curve ---
def test_fit_recovers_coefficients():
    c = fit(synth())
    assert c is not None
    assert abs(c.b_age - (-0.09)) < 0.01 and abs(c.b_km - (-0.03)) < 0.01 and c.sigma < 0.02
    assert c.n == 60 and c.years == 9 and c.min_year == 2012 and c.max_year == 2020 and c.sellers == 60
    assert abs(math.exp(c.predict_ln(2016, 100_000)) - math.exp(9.5 - 0.09 * 10 - 0.03 * 10)) < 60


def test_fit_rejections():
    assert fit(synth(n=7)) is None  # <8 ilan
    assert fit(synth(n=30, years=[2015, 2016])) is None  # <3 yıl
    assert fit(synth(noise=0.8)) is None  # σ > 0.30
    assert fit(synth(b_age=+0.10)) is None  # yaşlandıkça değer kazanan eğri mantıksız
    assert fit(synth(), max_sigma=0.0) is None


def test_fit_ignores_lhd_and_unknown_km():
    rows = synth(n=10)
    assert fit(rows) is not None
    for r in rows[:3]:
        r["steering"] = "LHD"
    rows[3]["km"] = None
    assert fit(rows) is None  # 6 satır kaldı


def test_owner_sale_weight_shifts_fit():
    rows = synth(n=30, noise=0.1)
    sale = car(99, 2016, 100_000, math.exp(9.5 - 0.09 * 10 - 0.03 * 10) * 0.75, phone="sale")
    c1 = fit(rows + [sale], [1.0] * 30 + [1.0])
    c3 = fit(rows + [sale], [1.0] * 30 + [3.0])
    assert c3.a < c1.a  # ucuz satış 3 kat ağırlıkla eğriyi aşağı çeker
    assert c1.n == c3.n == 31


# --- varyant ---
def test_variant_of():
    m = lambda model, brand="Mercedes-Benz", **kw: variant_of({"brand_norm": brand, "model": model, **kw})
    assert m("C Serisi C 180") == "180" and m("E 220 d") == "220" and m("C180") == "180" and m("CLA 200") == "200"
    assert m("3 Serisi 320i", "BMW") == "320" and m("116d", "BMW") == "116" and m("M3", "BMW") == ""
    assert m("Corolla", "Toyota", engine_l=1.6) == "1.6" and m("Corolla", "Toyota", engine_l=1.58) == "1.6"
    assert m("Fit", "Honda", engine_l=1.4) == "1.4" and m("Fit", "Honda", engine_l=1.2) == "1.2"
    assert m("Corolla", "Toyota") == "" and m("Corolla", "Toyota", engine_l=None) == ""


# --- satır kurulumu ---
def test_year_adjustment_without_curve():
    comps = [car(i, 2014, None, 10_000) for i in range(3)]
    value, lo, hi, ref_km, n, sellers = pb._row_from_comps(comps, 2015, None, S)
    assert abs(value - 10_000 * math.exp(0.08)) < 1 and ref_km is None and n == 3 and sellers == 3


def test_km_adjustment_uses_ref_km():
    comps = [car(0, 2015, 50_000, 10_000), car(1, 2015, 100_000, 10_000), car(2, 2015, 150_000, 10_000)]
    value, _lo, _hi, ref_km, n, sellers = pb._row_from_comps(comps, 2015, None, S)
    assert ref_km == 100_000
    # 50bin km'lik araç 100bin'e çevrilince ucuzlar, 150bin'lik pahalılaşır; medyan 100bin'liktir
    assert abs(value - 10_000) < 1


def test_per_seller_cap():
    pool = [car(i, 2015, None, 20_000, phone="905330000001") for i in range(6)]
    pool += [car(10 + i, 2015, None, 10_000) for i in range(3)]
    book = build_book(pool, [], NOW, None, S)
    row = book.row("Toyota", "vitz", 2015)
    assert abs(row.value_gbp - 10_000) < 0.01 and row.sellers == 4 and row.n == 5  # galeri 2 ilanla girer


def test_variant_split_vs_merge():
    pool = [car(i, 2015, None, 15_000 + i * 100, brand="Mercedes-Benz", model="c", raw="C Serisi C 180") for i in range(6)]
    pool += [car(20 + i, 2015, None, 21_000, brand="Mercedes-Benz", model="c", raw="C 200") for i in range(2)]
    book = build_book(pool, [], NOW, None, S)
    assert book.row("Mercedes-Benz", "c", 2015, "") is not None and book.row("Mercedes-Benz", "c", 2015, "180") is not None
    assert book.row("Mercedes-Benz", "c", 2015, "200") is None  # 2 ilan: ayrı satır yok, birleşikte
    assert book.row("Mercedes-Benz", "c", 2015, "").n == 8
    assert book.row("Mercedes-Benz", "c", 2015, "180").n == 6


def test_thin_row_and_min_rows():
    pool = [car(i, 2015, None, 10_000) for i in range(3)]
    row = build_book(pool, [], NOW, None, S).row("Toyota", "vitz", 2015)
    assert row.status == "ince" and row.method == "A"
    assert build_book(pool[:2], [], NOW, None, S).row("Toyota", "vitz", 2015) is None  # 3'ten az emsal: satır yok


def test_book_builds_curve_rows_and_owner_sales():
    pool = synth(n=60)
    sales = [{"brand_norm": "Toyota", "model_norm": "vitz", "year": 2016, "km": 90_000, "price_gbp": 6_000.0, "created_at": NOW}]
    book = build_book(pool, sales, NOW, None, S)
    assert book.curve("Toyota", "vitz") is not None
    row = book.row("Toyota", "vitz", 2016)
    assert row.sales_n == 1 and row.sales_median_gbp == 6_000.0
    # eğri aralığındaki her yıl için bir satır vardır
    assert all(book.row("Toyota", "vitz", y) for y in range(2012, 2021))


def test_brand_curve_rows_only_for_models_without_other_rows():
    pool = synth(n=60)  # vitz: eğri + satırlar
    pool += [car(500 + i, 2012 + i, 100_000, 5_000, model="tiny") for i in range(2)]  # 2 ilan: A yok
    book = build_book(pool, [], NOW, None, S)
    assert book.curve("Toyota", "*") is not None
    c_rows = [r for r in book.rows.values() if r.method == "C"]
    assert c_rows and all(r.model_norm == "tiny" and r.status == "ince" for r in c_rows)


# --- next_status ---
def old(value=10_000.0, status="oturmus", cand=None, nights=0):
    return BookRow("Toyota", "vitz", "", 2015, value, value, value, None, 10, 5, "A", status, cand, nights)


def test_next_status_sequences():
    assert next_status(None, 9_000, 10, 5, S) == (9_000, "oturmus", None, 0)  # ilk kurulum
    assert next_status(None, 9_000, 3, 3, S) == (9_000, "ince", None, 0)  # az veri
    assert next_status(old(), 10_800, 10, 5, S) == (10_800, "oturmus", None, 0)  # küçük değişim kabul
    assert next_status(old(), 13_000, 10, 5, S) == (10_000, "supheli", 13_000, 1)  # büyük sıçrama: eski kalır
    # ertesi gece aynı yerde: 2. gece, kabul
    assert next_status(old(status="supheli", cand=13_000, nights=1), 13_200, 10, 5, S) == (13_200, "oturmus", None, 0)
    # aday oynarsa sayaç baştan
    assert next_status(old(status="supheli", cand=13_000, nights=1), 15_000, 10, 5, S) == (10_000, "supheli", 15_000, 1)
    # değer eskiye dönerse şüphe kalkar
    assert next_status(old(status="supheli", cand=13_000, nights=1), 10_300, 10, 5, S) == (10_300, "oturmus", None, 0)
    # 3 gece şartı
    s3 = Settings(book_confirm_nights=3)
    assert next_status(old(status="supheli", cand=13_000, nights=1), 13_100, 10, 5, s3)[1:] == ("supheli", 13_100, 2)


# --- estimate_from_book ---
def curve(**kw):
    base = dict(brand_norm="Toyota", model_norm="vitz", a=9.0, b_age=-0.09, b_km=-0.03, sigma=0.15, n=40, years=9, sellers=30,
                min_year=2010, max_year=2020, max_km=200_000, ref_year=REF, mean_age=10.0, mean_km10=10.0)
    return Curve(**{**base, **kw})


def book_with(c=None, disabled=()):
    b = PriceBook()
    c = c or curve()
    b.curves[(c.brand_norm, c.model_norm)] = c
    b.rows[("Toyota", "vitz", "", 2016)] = old()
    b.disabled_models = set(disabled)
    return b


def lst(**kw):
    return {"brand_norm": "Toyota", "model_norm": "vitz", "year": 2016, "km": 100_000, "steering": "RHD", **kw}


def test_estimate_basic():
    e = estimate_from_book(lst(), book_with(), S)
    assert e is not None and e.method == "B" and e.n == 40 and e.sellers == 30 and e.row is not None
    assert abs(e.value_gbp - math.exp(9.0)) < 1  # ortalama yaş ve km'de değer = exp(a)
    assert abs(e.lower_gbp - e.value_gbp * math.exp(-S.est_z * 0.15)) < 1e-6


def test_estimate_guards():
    b = book_with()
    assert estimate_from_book(lst(steering="LHD"), b, S) is None
    assert estimate_from_book(lst(year=None), b, S) is None
    assert estimate_from_book(lst(year=2008), b, S) is None and estimate_from_book(lst(year=2009), b, S) is not None
    assert estimate_from_book(lst(year=2022), b, S) is None and estimate_from_book(lst(year=2021), b, S) is not None
    assert estimate_from_book(lst(km=None), b, S) is None
    assert estimate_from_book(lst(km=300_000), b, S) is None  # eğrinin km aralığı dışı
    assert estimate_from_book(lst(km=215_000), b, S) is not None  # %10 pay
    assert estimate_from_book(lst(), book_with(curve(sellers=4)), S) is None
    assert estimate_from_book(lst(), book_with(disabled=[("Toyota", "vitz")]), S) is None
    assert estimate_from_book(lst(model_norm="fit"), b, S) is None
    assert estimate_from_book(lst(km=500), b, S) is None  # 2015 araç, 500 km: eksik rakam sayılır


# --- self_check ---
def test_self_check_flags_bad_model():
    b = book_with()
    b.curves[("Opel", "astra")] = curve(brand_norm="Opel", model_norm="astra")
    good = [lst(price_gbp=math.exp(9.0) * 1.05) for _ in range(6)]  # tahmine yakın
    bad = [{**lst(price_gbp=math.exp(9.0) * 2.0), "brand_norm": "Opel", "model_norm": "astra"} for _ in range(6)]
    overall, unreliable = self_check(b, good + bad, S)
    assert unreliable == {("Opel", "astra")} and overall is not None
    overall2, unreliable2 = self_check(b, good, S)
    assert unreliable2 == set() and 0.04 < overall2 < 0.06
    assert self_check(b, bad[:4], S)[1] == set()  # 5'ten az ilan: karar yok
    assert self_check(b, [], S) == (None, set())
