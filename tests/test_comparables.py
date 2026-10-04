from datetime import datetime, timedelta, timezone

import random

from domain.comparables import find_market, km_band, nearest_comparables, seller_key

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def row(i, price, **kw):
    base = dict(id=i, brand_norm="Toyota", model_norm="vitz", year=2015, km=80_000, steering="RHD",
                transmission="otomatik", fuel="benzin", price_gbp=price, currency_guess=False,
                first_seen_at=NOW - timedelta(days=10), is_active=True, duplicate_of=None)
    return base | kw


TARGET = row("t", 5000)


def test_median_of_comparables():
    pool = [row(i, p) for i, p in enumerate([6000, 6200, 6400, 6600, 6800])]
    m = find_market(TARGET, pool, now=NOW)
    assert m.n == 5 and m.median_gbp == 6400


def test_lhd_never_mixed():
    pool = [row(i, 9000, steering="LHD") for i in range(5)]
    assert find_market(TARGET, pool, now=NOW) is None


def test_too_old_and_guessed_currency_excluded():
    pool = [row(1, 6000, first_seen_at=NOW - timedelta(days=200)), row(2, 6000, currency_guess=True),
            row(3, 6000), row(4, 6100)]
    assert find_market(TARGET, pool, now=NOW) is None  # sadece 2 geçerli emsal


def test_widens_year_when_few():
    pool = [row(1, 6000, year=2013), row(2, 6200, year=2017), row(3, 6400, year=2013)]
    m = find_market(TARGET, pool, now=NOW)
    assert m.n == 3 and m.year_span == 2


def test_outlier_removed():
    pool = [row(i, p) for i, p in enumerate([6000, 6100, 6200, 6300, 6400, 6500, 6600, 6700, 60000])]
    assert find_market(TARGET, pool, now=NOW).high_gbp == 6700


def test_km_band():
    assert km_band(49_999) == 0 and km_band(50_000) == 1 and km_band(None) is None


def test_age_uses_ref_date_not_first_seen():
    # Sisteme yeni girmiş ama ilanın kendi tarihi eski: emsal sayılmaz
    old = NOW - timedelta(days=200)
    pool = [row(i, 6000 + i * 100, ref_date=old) for i in range(5)]
    assert find_market(TARGET, pool, now=NOW) is None
    fresh = [row(i, 6000 + i * 100, ref_date=NOW - timedelta(days=5)) for i in range(5)]
    assert find_market(TARGET, fresh, now=NOW).n == 5


def test_small_pool_typo_outlier_dropped():
    pool = [row(i, p) for i, p in enumerate([600, 6000, 6200, 6400, 6600])]  # 600 = eksik rakam
    m = find_market(TARGET, pool, now=NOW)
    assert m.n == 4 and m.median_gbp == 6300


def test_implausible_prices_never_comparable():
    pool = [row(i, 15) for i in range(6)]
    assert find_market(TARGET, pool, now=NOW) is None


def test_nearest_comparables_picks_closest_year_and_km():
    from domain.comparables import nearest_comparables
    pool = [row(1, 6000, year=2015, km=79_000), row(2, 6200, year=2016, km=70_000), row(3, 6400, year=2014, km=90_000),
            row(4, 6600, year=2015, km=60_000), row(5, 6800, year=2015, km=85_000)]
    m = find_market(TARGET, pool, now=NOW)
    near = nearest_comparables(TARGET, pool, m, k=2, now=NOW)
    assert [r["id"] for r in near] == [1, 5]
    assert m.median_km == 79_000


def test_different_engine_sizes_not_mixed_but_unknown_engine_allowed():
    from domain.engine import engine_liters
    target = row("t", 5000, engine_l=1.6)
    pool = [row(1, 14000, engine_l=3.0), row(2, 15000, engine_l=3.0), row(3, 16000, engine_l=3.0)]
    assert find_market(target, pool, now=NOW) is None  # 340i'ler 316i'nın emsali değil
    pool += [row(4, 6000, engine_l=1.6), row(5, 6100), row(6, 6200, engine_l=1.5)]
    m = find_market(target, pool, now=NOW)
    assert m.n == 3 and m.median_gbp == 6100
    assert engine_liters("2.5 cc") == 2.5 and engine_liters("1600 cc") == 1.6 and engine_liters("1.5 L") == 1.5
    assert engine_liters("Yok") is None and engine_liters(None) is None


def test_engine_tolerance_is_inclusive_despite_float_rounding():
    pool = [row(i, 6000 + i, engine_l=1.3) for i in range(3)]
    assert find_market(row("t", 5000, engine_l=1.6), pool, now=NOW) is not None  # fark tam 0,3 L: emsal
    assert find_market(row("t", 5000, engine_l=1.7), pool, now=NOW) is None  # 0,4 L: emsal değil


def test_effective_km_depends_on_the_given_date_not_the_clock():
    """Karar saatten bağımsız olmalı: 2025 model araçta 500 km, 2027'de 'eksik rakam' sayılır (≥2 yaş), 2026'da gerçek km."""
    from datetime import date

    from domain.comparables import effective_km

    car = {"km": 500, "year": 2025}
    assert effective_km(car, date(2026, 6, 1)) == 500
    assert effective_km(car, date(2027, 1, 1)) is None
    assert effective_km({"km": 500, "year": 2015}, date(2026, 6, 1)) is None  # eski araçta <1000 km: eksik rakam
    assert effective_km({"km": None, "year": 2015}, date(2026, 6, 1)) is None


# --- TL fiyatlı emsal (Adım 6c): £ hedefte TL'siz piyasa, yalnız tek başına yeterliyse; yıl aralığı asla genişlemez ---

def gbp_rows(n, start=8000, step=100, **kw):
    return [row(f"g{i}", start + i * step, currency="GBP", **kw) for i in range(n)]


def tl_rows(n, start=6000, step=50, **kw):
    return [row(f"t{i}", start + i * step, currency="TRY", **kw) for i in range(n)]


def test_tl_comparables_excluded_when_gbp_alone_is_enough():
    m = find_market(TARGET, gbp_rows(8) + tl_rows(4), now=NOW)
    assert m.gbp_only and m.n == 8 and m.median_gbp == 8350


def test_tl_kept_when_gbp_alone_is_not_enough():
    m = find_market(TARGET, gbp_rows(5) + tl_rows(4), now=NOW)  # 5 £ emsal < 8: eski davranış (TL dahil)
    assert not m.gbp_only and m.n == 9


def test_tl_target_and_rows_without_currency_behave_as_before():
    tl_target = row("t", 5000, currency="TRY")
    assert not find_market(tl_target, gbp_rows(8) + tl_rows(4), now=NOW).gbp_only
    no_cur = [row(i, 8000 + i * 100) for i in range(8)] + [row(f"x{i}", 6000 + i) for i in range(4)]
    assert not find_market(TARGET, no_cur, now=NOW).gbp_only  # currency anahtarı yok: hepsi sayılır


def test_year_span_not_widened_by_tl_exclusion():
    """Golf düzeneği: ±1'de 2 £ + 1 TL emsal; £ yetmeyince aynı aralıkta TL de sayılır, ±2'ye ATLANMAZ."""
    pool = [row(1, 6000, year=2014, currency="GBP"), row(2, 6200, year=2016, currency="GBP"), row(3, 5800, year=2015, currency="TRY")]
    pool += [row(f"w{i}", 9000 + i * 100, year=2013 if i % 2 else 2017, currency="GBP") for i in range(8)]
    m = find_market(TARGET, pool, now=NOW)
    assert m.year_span == 1 and m.n == 3 and not m.gbp_only


def test_year_span_never_wider_than_before_property():
    rnd = random.Random(7)
    for _ in range(400):
        pool = [row(i, rnd.randint(4000, 12000), year=rnd.randint(2012, 2018), currency=rnd.choice(["GBP", "GBP", "TRY"]))
                for i in range(rnd.randint(3, 25))]
        old = find_market(TARGET, [{**r, "currency": None} for r in pool], now=NOW)  # eski davranış: TL ayrımı yok
        new = find_market(TARGET, pool, now=NOW)
        if old is not None:
            assert new is not None and new.year_span <= old.year_span


def test_nearest_comparables_follow_the_same_pool_as_the_market():
    pool = gbp_rows(8) + tl_rows(3, start=8150, step=100)  # TL fiyatları £ aralığının İÇİNDE: dışlanmazsa mesajda görünürdü
    m = find_market(TARGET, pool, now=NOW)
    near = nearest_comparables(TARGET, pool, m, 8, now=NOW)
    assert m.gbp_only and near and all(r["currency"] == "GBP" for r in near)


# --- Adım 7 hazırlığı: piyasa özeti satıcı sayısı / medyan yıl / emsal kimliklerini taşır; tek seller_key ---

def test_market_summary_carries_sellers_median_year_and_comparable_ids():
    pool = [row(i, 6000 + i * 100, year=2014 + (i % 3), seller_phone=f"90{i % 4}") for i in range(8)]  # 4 telefon = 4 satıcı
    m = find_market(TARGET, pool, now=NOW)
    assert m.sellers_n == 4 and m.n == 8 and set(m.comparable_ids) == set(range(8))
    import statistics
    assert m.median_year == statistics.median(r["year"] for r in pool)


def test_nearest_comparables_come_only_from_the_comparables_that_built_the_market():
    pool = gbp_rows(8) + tl_rows(3, start=8150, step=100)
    m = find_market(TARGET, pool, now=NOW)
    # piyasa kimlikleri dışındaki satır (aynı özellikte ama sayılmayan) mesajda ASLA görünmez
    extra = row("x", 8400, currency="GBP")
    near = nearest_comparables(TARGET, pool + [extra], m, 8, now=NOW)
    assert {r["id"] for r in near} <= set(m.comparable_ids) and "x" not in {r["id"] for r in near}


def test_seller_key_is_the_single_definition_used_by_the_price_book():
    from domain import price_book
    for r in (row(1, 6000, seller_phone="905"), row(2, 6000), {"seller_phone": None}):
        assert price_book._skey(r, 7) == seller_key(r, 7)
    assert seller_key(row(3, 6000)) == "id:3" and seller_key({"seller_phone": None}, 9) == "id:9"


# --- Adım 7 (2): KKTCar satıcı kimliği seller_key'e girer; bir satıcıdan en fazla 2 emsal (hedefe yıl+km'ce en yakınlar) ---

def test_seller_key_order_phone_then_kktcar_handle_then_listing_id():
    assert seller_key(row(1, 6000, seller_phone="905", seller_handle="kktcar:abc")) == "905"
    assert seller_key(row(2, 6000, seller_handle="kktcar:abc")) == "kktcar:abc"
    assert seller_key(row(3, 6000, seller_handle="Ahmet Galeri")) == "id:3"  # KibrisArabaAl yazar adı / Instagram hesabı satıcı anahtarı OLMAZ
    assert seller_key(row(4, 6000, seller_handle="kktcar:")) == "id:4"  # boş kimlik anahtar sayılmaz


def test_one_seller_contributes_at_most_two_comparables_the_closest_ones():
    gallery = [row(f"gal{i}", 9000 + i, seller_phone="999", year=2015 - (i % 3), km=80_000 + i * 2_000) for i in range(6)]
    others = [row(f"o{i}", 6000 + i * 100, seller_phone=f"90{i}") for i in range(6)]
    m = find_market(TARGET, gallery + others, now=NOW)
    gal_used = [i for i in m.comparable_ids if str(i).startswith("gal")]
    assert len(gal_used) == 2 and m.n == 8 and m.sellers_n == 7
    assert set(gal_used) == {"gal0", "gal3"}  # (yıl 2015, km 80.000) ve (2015, 86.000): hedefe (2015, 80.000) en yakın ikisi


def test_kktcar_handle_sellers_are_capped_but_unidentified_rows_are_not():
    capped = [row(f"k{i}", 6400, seller_handle="kktcar:g1") for i in range(5)]
    loose = [row(f"u{i}", 6000 + i * 100) for i in range(8)]  # kimliksiz: her biri ayrı satıcı, sınır uygulanmaz
    m = find_market(TARGET, capped + loose, now=NOW)
    assert m.n == 10 and sum(1 for i in m.comparable_ids if str(i).startswith("k")) == 2


def test_cap_applies_before_outlier_removal_and_zero_disables_it():
    from domain.settings import Settings
    gallery = [row(f"gal{i}", 12_000, seller_phone="999") for i in range(5)]
    others = [row(f"o{i}", 6000 + i * 100, seller_phone=f"90{i}") for i in range(7)]
    capped = find_market(TARGET, gallery + others, now=NOW)
    assert capped.n == 9 and sum(1 for i in capped.comparable_ids if str(i).startswith("gal")) == 2
    off = find_market(TARGET, gallery + others, Settings(max_comparables_per_seller=0), NOW)
    assert off.n == 12 and off.median_gbp > capped.median_gbp  # sınırsız: galerinin 5 ilanı medyanı yukarı çekiyor


def test_market_needs_distinct_sellers_after_the_cap():
    pool = [row(f"a{i}", 7000, seller_phone="901") for i in range(5)] + [row(f"b{i}", 7100, seller_phone="902") for i in range(5)]
    assert find_market(TARGET, pool, now=NOW) is None  # 10 ilan ama iki satıcı: sınırdan sonra 4 emsal, 2 satıcı < 3


def test_cap_choice_does_not_depend_on_pool_order_property():
    rnd = random.Random(11)
    for _ in range(200):
        pool = [row(f"r{i}", rnd.randint(5000, 9000), year=rnd.randint(2013, 2017), km=rnd.randint(20_000, 140_000),
                    seller_phone=f"90{rnd.randint(1, 4)}") for i in range(rnd.randint(6, 20))]
        a = find_market(TARGET, pool, now=NOW)
        shuffled = pool[:]
        rnd.shuffle(shuffled)
        b = find_market(TARGET, shuffled, now=NOW)
        assert (a is None) == (b is None)
        if a is not None:
            assert set(a.comparable_ids) == set(b.comparable_ids) and a.median_gbp == b.median_gbp
