"""km 'bin' eksik kuralı (05.10.2026, sahip onaylı): ≥2 yaşında araçta 1-999 km yazıyorsa (çoğu zaman binler: "214" = 214.000) ve ×1000
okunursa km emsal medyanından AÇIKÇA yüksekse (`km_yuksek` ile aynı koşul) yeni eksik `km_bin_eksik_yuksek` eklenir: 🟢 en fazla 🟡 olur.
Gerçek vaka: 2014 Mazda Demio £4.500, "214 km", emsal km medyanı ~129.000: 05.10'da 🟢 gitmişti. ×1000 okuma açıkça olumsuz DEĞİLSE ilan
eskisi gibi ele alınır (km bilinmiyor, engel yok): sahibin 04.10 kuralı (yanlış km tek başına fırsatı engellemez) bozulmaz."""
from datetime import datetime, timezone

from application.evaluate import evaluate_new
from domain.comparables import Market, effective_km
from domain.data_gate import GAP_LABELS, KM_UNKNOWN_WARNING, data_gaps
from domain.profit import Tier
from domain.settings import Settings
from tests.test_evaluate import FakeRepo, car

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
GAP = "km_bin_eksik_yuksek"


def market(median_km):
    return Market(n=9, median_gbp=8700.0, low_gbp=8000.0, high_gbp=9400.0, year_span=1, archived_share=0.0, median_km=median_km)


def gaps(km, year=2014, median_km=129_000, **kw):
    listing = {"km": km, "year": year, "model_norm": "demio", "currency": "GBP", **kw}
    return data_gaps(listing, market(median_km), Settings(), NOW)


def test_demio_214_km_on_2014_with_median_129k_gets_the_gap():
    assert gaps(214) == [GAP]  # km_yuksek değil: effective_km 214'ü zaten "bilinmiyor" sayıyor; yalnız yeni eksik
    assert GAP_LABELS[GAP].startswith("km çok düşük yazıyor")  # düz Türkçe etiket (ilanı-ilet mesajı ve haftalık rapor bunu okur)


def test_214_km_with_median_230k_has_no_gap_because_times_1000_is_not_high():
    assert gaps(214, median_km=230_000) == []  # 214.000 < 230.000·1,3: ×1000 okuması olumsuz değil: eskisi gibi (km bilinmiyor, engel yok)


def test_5_km_has_no_gap_because_times_1000_is_not_high():
    assert gaps(5) == []  # 5.000 km emsal medyanı 129.000'den düşük
    assert gaps(5, median_km=2_000) == []  # medyan çok küçük olsa da: 5.000 > 2.600 ama fark 3.000 < 10.000 (km_high_margin)


def test_thresholds_are_the_same_as_km_yuksek():
    assert gaps(168) == [GAP] and gaps(167) == []  # 168.000 > 129.000·1,3 = 167.700 > 167.000
    assert gaps(16, median_km=6_000) == [GAP]  # fark tam 10.000 = km_high_margin: yeter (km_yuksek'teki >= ile aynı)
    assert gaps(15, median_km=6_000) == []  # fark 9.000 < 10.000
    assert gaps(214, median_km=None) == []  # emsal km medyanı yoksa kıyas yok


def test_km_999_on_a_young_car_is_unaffected():
    assert effective_km({"km": 999, "year": 2025}, NOW.date()) == 999  # 1 yaşında: km makul, "bilinmiyor" değil
    assert effective_km({"km": 999, "year": 2026}, NOW.date()) == 999
    assert gaps(999, year=2025, median_km=20_000) == []  # ×1000 okunsaydı çok yüksek olurdu; ama km zaten makul: kural işlemez
    assert gaps(999, year=2026, median_km=20_000) == []
    assert gaps(214, year=2025) == [] and gaps(214, year=2024) == [GAP]  # sınır: 2 yaş (effective_km'in "bilinmiyor" eşiğiyle aynı)


def test_km_null_or_year_null_or_normal_km_has_no_gap():
    assert gaps(None) == []
    assert gaps(214, year=None) == []  # yıl yoksa effective_km km'yi olduğu gibi okur: kural işlemez
    assert gaps(98_000) == []  # normal km
    assert gaps(0) == []  # 0 km: 1-999 aralığında değil (×1000 de 0)
    assert gaps(1000) == []  # 1.000 km aralığın dışında (sahip kararı: 1-999)


def test_normal_high_km_is_still_plain_km_yuksek_not_the_new_gap():
    assert gaps(214_000) == ["km_yuksek"]


def test_gap_does_not_remove_the_other_gaps():
    assert gaps(214, currency="TRY") == ["tl_fiyat", GAP]


# --- karar düzeyi: 🟢 → 🟡 ---

def pool(km):
    return [car(f"p{i}", p, year=2014, km=km) for i, p in enumerate([8000, 8200, 8400, 8600, 8800, 9000, 9200, 9400])]


def test_decision_demio_like_green_is_capped_at_yellow():
    repo = FakeRepo([car("t", 5000, year=2014, km=214)], pool(129_000))
    (ev,) = evaluate_new(repo, Settings(), book=None)
    assert ev.market.median_km == 129_000
    saved = repo.saved[0][1]
    assert ev.profit.tier is Tier.NEGOTIABLE and saved["tier"] == "pazarlik" and GAP in saved["red_flags"] and saved["nedenler"] == [GAP]
    assert KM_UNKNOWN_WARNING in ev.warnings  # km hâlâ "bilinmiyor": uyarı da gider


def test_decision_owner_rule_intact_when_times_1000_is_not_adverse():
    repo = FakeRepo([car("t", 5000, year=2014, km=214)], pool(230_000))
    (ev,) = evaluate_new(repo, Settings(), book=None)
    assert ev.profit.tier is Tier.STRONG and GAP not in repo.saved[0][1]["red_flags"] and KM_UNKNOWN_WARNING in ev.warnings  # eskisi gibi: 🟢, km uyarısıyla
