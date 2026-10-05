"""km bandı yerine km düzeltmesi + 'yakın emsal' kapısı (kmbant2 çalışması, 05.10.2026): karar düzeyinde davranış.
- Emsal fiyatları ilanın km'sine çekilir (10.000 km başına %1,1); bant yok, 149.999 / 150.000 km uçurumu yok.
- 🟢 için emsallerin en az 8'i ilanın km'sine ±50.000 km yakın olmalı (km'si yazmayan emsal sayılır); değilse en fazla 🟡 ('km_yakin_emsal_az').
- km'si yazmayan ilan: düzeltme yok, kapı yok (sahip kararı: km eksikliği tek başına fırsatı engellemez).
- ilandaki km yanlış yazılmışsa (190.000 → 19.000) uzak km'li emsaller yukarı düzeltilip sahte 🟢 doğmaz (kapı tutar)."""
from datetime import datetime, timedelta, timezone

from domain.decision import decide
from domain.profit import Tier
from domain.settings import Settings

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
S = Settings()


def car(i, price, **kw):
    base = dict(id=i, brand_norm="BMW", model_norm="3", year=2012, km=150_000, steering="RHD", transmission="otomatik", fuel="benzin",
                price_gbp=price, currency_guess=False, first_seen_at=NOW - timedelta(days=2), ref_date=NOW - timedelta(days=2),
                is_active=True, duplicate_of=None, raw_text="", model="320i")
    return base | kw


def comps(n, start_km, step_km, price=9000, step_price=100, prefix="c"):
    return [car(f"{prefix}{i}", price + i * step_price, km=start_km + i * step_km) for i in range(n)]


def test_strong_needs_eight_comparables_with_nearby_km():
    near = comps(7, 140_000, 3_000)  # 140-158bin: ilana (150bin) yakın
    far = comps(3, 30_000, 5_000, prefix="f")  # 30-40bin: 110bin+ uzak (düzeltilerek medyana girer)
    d = decide(car("t", 6000), near + far, S, now=NOW)
    assert d.market.n == 10 and d.market.near_n == 7
    assert d.profit.profit_pct >= S.strong_threshold and d.profit.tier is Tier.NEGOTIABLE and "km_yakin_emsal_az" in d.gaps
    d8 = decide(car("t", 6000), near + far + [car("c7", 9700, km=165_000)], S, now=NOW)
    assert d8.market.near_n == 8 and d8.profit.tier is Tier.STRONG and not d8.gaps


def test_unknown_km_listing_gets_no_adjustment_and_no_near_rule():
    pool = comps(7, 140_000, 3_000) + comps(3, 30_000, 5_000, prefix="f")
    d = decide(car("t", 6000, km=None), pool, S, now=NOW)
    assert d.market.near_n is None and d.profit.tier is Tier.STRONG  # km yok: engel değil, uyarıyla gider
    raw = sorted(r["price_gbp"] for r in pool)
    assert d.market.median_gbp == (raw[4] + raw[5]) / 2  # fiyatlara dokunulmadı


def test_mistyped_low_km_does_not_create_a_fake_green():
    """2012 BMW 190.000 km iken '19.000' yazılmış: emsaller 150-220bin. Düzeltme emsalleri yukarı çeker (ilan ucuz görünür) ama
    ±50bin içinde emsal yok: 🟢 değil. Aynı ilan doğru km ile (190bin) 🟢 değil (gerçekte ucuz değil)."""
    pool = comps(12, 150_000, 6_000, price=7000, step_price=50)  # 150-216bin, £7.000-7.550
    typo = decide(car("t", 6100, km=19_000), pool, S, now=NOW)
    assert typo.market.near_n == 0 and typo.profit.tier is not Tier.STRONG
    true_km = decide(car("t", 6100, km=190_000), pool, S, now=NOW)
    assert true_km.profit.tier is not Tier.STRONG


def test_no_cliff_between_149_999_and_150_000_km():
    pool = comps(16, 116_000, 8_000, price=5500, step_price=250)
    a = decide(car("t", 5400, km=149_999), pool, S, now=NOW)
    b = decide(car("t", 5400, km=150_000), pool, S, now=NOW)
    assert a.profit.tier == b.profit.tier and abs(a.profit.profit_pct - b.profit.profit_pct) < 0.001


def test_high_km_listing_far_from_all_comparables_is_capped_by_the_near_rule():
    """İlan 200bin, emsaller 100-127bin: hiçbiri ±50bin yakın değil → 🟢 yok ('km_yakin_emsal_az'). 07.10.2026'dan beri düzeltilmiş
    piyasada 'km_yuksek' kapısı YOK (yüksek km fiyattan zaten düşüldü; ikinci ceza olmaz): uzak km'yi bu kapı tutar."""
    pool = comps(10, 100_000, 3_000)  # 100-127bin
    d = decide(car("t", 5000, km=200_000), pool, S, now=NOW)
    assert d.profit.tier is not Tier.STRONG and d.gaps == ["km_yakin_emsal_az"]


def test_high_km_listing_with_nearby_comparables_is_judged_on_adjusted_prices():
    """2012 Mercedes 240bin gibi: emsallerin km medyanı ilandan çok düşük (130bin, 1,3 katı aşılır) ama ±50bin içinde ≥8 emsal var.
    Fiyatlar ilanın km'sine çekildikten sonra hâlâ %20+ ucuzsa 🟢 (eskiden 'km_yuksek' ile 🟡'ya düşerdi: yüksek km iki kez cezalanıyordu)."""
    low = comps(9, 100_000, 5_000, price=12_000, prefix="l")  # 100-140bin
    near = comps(8, 195_000, 5_000, price=10_500, prefix="n")  # 195-230bin: ilana (240bin) ±50bin yakın
    d = decide(car("t", 7000, km=240_000), low + near, S, now=NOW)
    assert d.market.median_km < 240_000 / S.km_high_ratio and d.market.near_n == 8
    assert d.profit.tier is Tier.STRONG and "km_yuksek" not in d.gaps
    assert 10_000 < d.market.median_gbp < 12_000  # ham medyan £12.000 (17 emsalin 9.su); düzeltilmiş ≈ £10.720: az km'li emsaller ucuzlatıldı
