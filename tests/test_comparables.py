import math
import random
from datetime import datetime, timedelta, timezone

from domain.comparables import find_market, km_adjusted_price, nearest_comparables, seller_key

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


def test_km_adjusted_price_moves_the_comparable_to_the_target_km():
    """10.000 km başına %1,1 (ln ölçeğinde, iki yönde aynı): az km'li emsal ucuzlar, çok km'li emsal pahalanır; km'lerden biri yoksa dokunulmaz."""
    assert abs(km_adjusted_price(10_000, 120_000, 100_000, 0.011) - 10_000 * math.exp(-0.022)) < 1e-6  # emsal 20bin az km'li: ~£9.782
    assert abs(km_adjusted_price(10_000, 100_000, 120_000, 0.011) - 10_000 * math.exp(0.022)) < 1e-6  # emsal 20bin çok km'li: ~£10.222
    assert km_adjusted_price(10_000, None, 120_000, 0.011) == 10_000 and km_adjusted_price(10_000, 120_000, None, 0.011) == 10_000
    assert km_adjusted_price(10_000, 120_000, 100_000, 0.0) == 10_000


def test_no_km_band_cliff_at_150k():
    """Eski bantta 149.999 km ile 150.000 km farklı emsal kümesi seçiyordu (BMW 3 2007 £5.400: +%7 ↔ +%24). Bant yok: aynı emsaller, aynı medyan."""
    pool = [row(i, 5500 + i * 250, year=2007, km=116_000 + i * 8_000) for i in range(16)]  # 116.000 … 236.000 km
    a = find_market(row("t", 5400, year=2007, km=149_999), pool, now=NOW)
    b = find_market(row("t", 5400, year=2007, km=150_000), pool, now=NOW)
    assert a.comparable_ids == b.comparable_ids and a.n == b.n == 16
    assert abs(a.median_gbp - b.median_gbp) < 0.01 * a.median_gbp / 100  # 1 km farkı: medyan %0,01'den az oynar


def test_market_uses_km_adjusted_prices_and_unknown_km_is_untouched():
    pool = [row(i, 10_000, km=100_000) for i in range(3)] + [row(f"u{i}", 10_000, km=None) for i in range(2)]
    m = find_market(row("t", 8000, km=120_000), pool, now=NOW)  # 3 emsal 20bin az km'li → £9.782; 2 km'siz emsal £10.000 (dokunulmaz)
    assert m.n == 5 and abs(m.median_gbp - 10_000 * math.exp(-0.022)) < 0.01 and m.high_gbp == 10_000
    km_less = find_market(row("t", 8000, km=None), pool, now=NOW)  # km'si yazmayan ilan: hiç düzeltme yok (bugünkü gibi)
    assert km_less.median_gbp == 10_000 and km_less.near_n is None


def test_far_km_comparables_enter_the_median_but_not_the_near_count():
    """±50.000 km dışındaki emsal fiyatı düzeltilerek medyana girer, ama 'yakın emsal' sayısına girmez (km'si yazmayan emsal yakın sayılır)."""
    near = [row(f"n{i}", 7000 + i * 100, km=70_000 + i * 5_000) for i in range(5)]  # 70-90bin: hedefe (80bin) yakın
    far = [row(f"f{i}", 6000 + i * 100, km=200_000 + i * 5_000) for i in range(3)]  # 200-210bin: 120bin+ uzak
    unknown = [row(f"u{i}", 7500, km=None) for i in range(2)]
    m = find_market(TARGET, near + far + unknown, now=NOW)
    assert m.n == 10 and m.near_n == 7  # 5 yakın + 2 km'siz
    assert set(m.comparable_ids) >= {"f0", "f1", "f2"}


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


def test_tl_exclusion_alone_never_widens_but_a_thin_first_market_does_under_the_8_rule():
    """Golf düzeneği: ±1'de 2 £ + 1 TL emsal (3 emsal). 7c'den önce ±1'de DURULURDU; şimdi ilk geçerli piyasa 8'den azsa ±2 denenir
    ve 8'e ulaşıyorsa seçilir (medyan, dar ve geniş piyasanın KÜÇÜĞÜ: geniş piyasa pahalı yıllara kaymışsa fırsat şişmesin)."""
    pool = [row(1, 6000, year=2014, currency="GBP"), row(2, 6200, year=2016, currency="GBP"), row(3, 5800, year=2015, currency="TRY")]
    pool += [row(f"w{i}", 9000 + i * 100, year=2013 if i % 2 else 2017, currency="GBP") for i in range(8)]
    m = find_market(TARGET, pool, now=NOW)
    assert m.year_span == 2 and m.n >= 8
    assert m.median_gbp <= 6000  # dar piyasanın medyanı (6000) geniş piyasanınkinden (≈9000) küçük: o kullanılır


def test_widening_stops_at_the_first_step_that_reaches_eight():
    narrow = [row(f"n{i}", 7000 + i * 100, year=2015) for i in range(9)]
    wide = [row(f"w{i}", 9000 + i * 100, year=2013) for i in range(9)]
    m = find_market(TARGET, narrow + wide, now=NOW)
    assert m.year_span == 1 and m.n == 9  # ±1 zaten 8'e ulaştı: ±2'ye hiç bakılmaz


def test_widening_keeps_the_first_valid_market_when_no_step_reaches_eight():
    pool = [row(f"n{i}", 7000 + i * 100, year=2015) for i in range(4)] + [row(f"w{i}", 7500 + i * 100, year=2013) for i in range(3)]
    m = find_market(TARGET, pool, now=NOW)  # ±1: 4 emsal, ±2: 7 emsal: hiçbiri 8 değil → eski davranış (ilk geçerli = ±1)
    assert m.year_span == 1 and m.n == 4


def test_widening_reaches_eight_with_the_conservative_merge_of_the_two_markets():
    narrow = [row(f"n{i}", 7000 + i * 100, year=2015) for i in range(5)]  # medyan 7200
    wide = [row(f"w{i}", 9000 + i * 100, year=2013) for i in range(5)]
    m = find_market(TARGET, narrow + wide, now=NOW)
    assert m.year_span == 2 and m.n == 10 and m.median_gbp == 7200  # geniş piyasanın medyanı ~8.100, dar 7.200: küçüğü


def test_a_market_of_eight_or_more_always_has_at_least_four_sellers_property():
    rnd = random.Random(21)
    for _ in range(300):
        pool = [row(f"r{i}", rnd.randint(5000, 9000), year=rnd.randint(2013, 2017), seller_phone=f"90{rnd.randint(1, 6)}" if rnd.random() < 0.7 else None)
                for i in range(rnd.randint(8, 30))]
        m = find_market(TARGET, pool, now=NOW)
        if m is not None and m.n >= 8:
            assert m.sellers_n >= 4  # satıcı başına ≤2 emsal kuralının doğrudan sonucu


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
    from domain.settings import Settings
    one = [row(f"a{i}", 7000 + i, seller_phone="901") for i in range(6)]
    assert find_market(TARGET, one, now=NOW) is None  # tek satıcı: sınırdan sonra 2 emsal, 1 satıcı < 2
    two = [row(f"a{i}", 7000, seller_phone="901") for i in range(5)] + [row(f"b{i}", 7100, seller_phone="902") for i in range(5)]
    m = find_market(TARGET, two, now=NOW)
    assert m.n == 4 and m.sellers_n == 2  # iki satıcı yeter (sahip kararı: 3→2), ama emsal 8'den az
    assert find_market(TARGET, two, Settings(min_distinct_sellers=3), NOW) is None


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


def test_conservative_merge_takes_the_smaller_median_p25_and_km_and_records_both_medians():
    """Opus (04.10.2026): dar piyasanın medyanı ile geniş piyasanın p25/km'sinin karışması 'en ucuz çeyrek' kapısını gevşetirdi."""
    narrow = [row(f"n{i}", 7000 + i * 100, year=2015, km=60_000 + i * 1000) for i in range(5)]  # medyan 7200 (62.000 km), km ~62.000
    wide = [row(f"w{i}", 9000 + i * 100, year=2013, km=90_000 + i * 1000) for i in range(5)]
    m = find_market(TARGET, narrow + wide, now=NOW)
    narrow_median = 7200 * math.exp(-0.011 * 1.8)  # medyan emsal 62.000 km, hedef 80.000 km: 18bin km farkı kadar ucuzlatılır (~£7.059)
    assert m.year_span == 2 and abs(m.median_gbp - narrow_median) < 0.01 and m.narrow_median_gbp == m.median_gbp and m.wide_median_gbp > 8000
    assert m.p25_gbp is not None and m.p25_gbp <= m.median_gbp  # p25 ≤ medyan: tutarsız çift yok
    assert m.median_km is not None and m.median_km <= 80_000


def test_unmerged_markets_carry_no_narrow_wide_fields():
    m = find_market(TARGET, [row(i, 6000 + i * 100) for i in range(9)], now=NOW)
    assert m.narrow_median_gbp is None and m.wide_median_gbp is None


def test_p25_never_exceeds_the_median_property():
    rnd = random.Random(33)
    for _ in range(300):
        pool = [row(f"r{i}", rnd.randint(4000, 12000), year=rnd.randint(2012, 2018), km=rnd.randint(10_000, 200_000)) for i in range(rnd.randint(3, 30))]
        m = find_market(TARGET, pool, now=NOW)
        if m is not None and m.p25_gbp is not None:
            assert m.p25_gbp <= m.median_gbp + 1e-9


def test_inconsistent_thresholds_are_refused():
    import pytest
    from domain.settings import Settings
    with pytest.raises(ValueError):
        Settings(gbp_only_min_comparables=5, widen_until_comparables=8)  # £-yalnız piyasa genişleme eşiğinden önce geçerli olurdu: 6c tersine döner


def test_old_cars_with_implausibly_low_km_are_treated_as_unknown_km():
    """Adım 8: 10+ yaşında araçta 15.000 km altı (2013 model 11.500 km = 115.000 yazılmış olabilir) şüpheli; 10 yaşından genç araçta makul."""
    from datetime import date

    from domain.comparables import effective_km
    today = date(2026, 10, 4)
    assert effective_km({"km": 11_500, "year": 2013}, today) is None  # 13 yaşında
    assert effective_km({"km": 14_999, "year": 2016}, today) is None  # tam 10 yaşında
    assert effective_km({"km": 15_000, "year": 2013}, today) == 15_000  # sınır: makul
    assert effective_km({"km": 11_500, "year": 2017}, today) == 11_500  # 9 yaşında: makul
    assert effective_km({"km": 1000, "year": 2006}, today) is None  # (eski kural 1000'i kaçırıyordu: <1000)
    assert effective_km({"km": 11_500, "year": None}, today) == 11_500  # yıl bilinmiyorsa dokunulmaz


def test_old_low_km_listing_is_compared_without_a_km_band():
    pool = [row(f"p{i}", 6000 + i * 100, year=2013, km=120_000 + i * 1000) for i in range(8)]  # 100-150 bin km bandı
    target = row("t", 4800, year=2013, km=11_500)  # 115.000 yazılacakken 11.500: band 0'a düşüp emsalsiz kalmasın
    m = find_market(target, pool, now=NOW)
    assert m is not None and m.n == 8
