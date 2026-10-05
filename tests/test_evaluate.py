import math
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from application.evaluate import PCT_LIMIT, EvaluationFailure, clamp_pct, evaluate_new
from domain.data_gate import KM_UNKNOWN_WARNING
from domain.profit import Tier


class FakeRepo:
    def __init__(self, listings, pool):
        self._listings, self._pool, self.saved = listings, pool, []
        self.pool_calls, self.recent_args, self.uneval_args = [], [], []

    def market_pool(self, days, keys=None):
        self.pool_calls.append(keys)  # keys: [(brand_norm, model_norm), ...] ya da None (tüm havuz)
        return [r for r in self._pool if keys is None or (r["brand_norm"], r["model_norm"]) in keys]

    def unevaluated_active(self, recheck_days=3, recent_hours=None, rules_version=None, unevaluated_hours=None):
        self.recent_args.append(recent_hours)
        self.uneval_args.append((recent_hours, rules_version, unevaluated_hours))
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


def test_missing_km_does_not_block_strong_but_warns():
    """Sahip kararı (04.10.2026): km eksik/yanlışsa diğer her şey uygunsa fırsat engellenmez; mesajda uyarı olur."""
    repo = FakeRepo([car("t", 5000, km=None)], POOL)
    (ev,) = evaluate_new(repo)
    assert ev.profit.tier is Tier.STRONG and KM_UNKNOWN_WARNING in ev.warnings
    assert "km_yok" not in repo.saved[0][1]["red_flags"]


def test_guessed_currency_never_strong():
    repo = FakeRepo([car("t", 5000, currency_guess=True)], POOL)
    (ev,) = evaluate_new(repo)
    assert ev.profit.tier is Tier.NEGOTIABLE and "para_birimi_tahmin" in repo.saved[0][1]["red_flags"]


def test_missing_model_never_strong():
    repo = FakeRepo([car("t", 5000, model_norm=None)], [car(f"p{i}", 8000 + i, model_norm=None) for i in range(5)])
    (ev,) = evaluate_new(repo)
    assert ev.profit.tier is Tier.NEGOTIABLE


def test_km_far_above_comparables_is_judged_on_km_adjusted_prices():
    """07.10.2026: emsal fiyatları ilanın km'sine çekilir (10.000 km başına %1,1); düzeltilmiş piyasada 'km_yuksek' kapısı yok (yüksek km fiyattan
    zaten düşüldü). 98bin km'lik ilan, 60bin km'lik emsallerin düzeltilmiş medyanının hâlâ %20+ altındaysa 🟢; emsaller ±50bin yakın (8 ≥ 8)."""
    pool = [car(f"p{i}", 8000 + i * 100, km=60_000) for i in range(8)]  # hepsi 60 bin km
    repo = FakeRepo([car("t", 5000, km=98_000)], pool)
    (ev,) = evaluate_new(repo)
    assert ev.market.median_km == 60_000 and ev.market.near_n == 8
    assert abs(ev.market.median_gbp - 8350 * math.exp(-0.011 * 3.8)) < 0.01  # medyan £8.350, 38bin km farkıyla ≈ £8.008
    assert ev.profit.tier is Tier.STRONG and "km_yuksek" not in (repo.saved[0][1]["red_flags"] or [])


def test_similar_km_stays_strong():
    pool = [car(f"p{i}", 8000 + i * 100, km=60_000) for i in range(8)]
    repo = FakeRepo([car("t", 5000, km=70_000)], pool)
    (ev,) = evaluate_new(repo)
    assert ev.profit.tier is Tier.STRONG


def test_zero_or_suspicious_km_counts_as_unknown_and_warns():
    repo = FakeRepo([car("t", 5000, km=0)], POOL)
    (ev,) = evaluate_new(repo)
    assert ev.profit.tier is Tier.STRONG and KM_UNKNOWN_WARNING in ev.warnings
    high_km_pool = [car(f"p{i}", 8000 + i * 200, km=200_000) for i in range(8)]  # emsal km medyanı 200.000: 215 km'nin ×1000 okuması (215.000) olumsuz DEĞİL
    repo = FakeRepo([car("t", 5000, km=215)], high_km_pool)  # eski araçta 215 km: "215 bin" yazılmış olabilir
    (ev,) = evaluate_new(repo)
    assert ev.profit.tier is Tier.STRONG and KM_UNKNOWN_WARNING in ev.warnings and "km_bin_eksik_yuksek" not in repo.saved[0][1]["red_flags"]
    repo = FakeRepo([car("t", 5000, km=215)], POOL)  # emsal medyanı 80.000: ×1000 (215.000) açıkça yüksek -> 🟡 (05.10 kuralı, tests/test_km_thousand.py)
    (ev,) = evaluate_new(repo)
    assert ev.profit.tier is Tier.NEGOTIABLE and "km_bin_eksik_yuksek" in repo.saved[0][1]["red_flags"]
    repo = FakeRepo([car("t", 7000, km=None)], POOL)  # kâr <%20: 🟡; "bu yüzden 🟢 değil" (km_yok) yazılmaz ama km uyarısı görünür
    (ev,) = evaluate_new(repo)
    assert ev.profit.tier is Tier.NEGOTIABLE and "km_yok" not in repo.saved[0][1]["red_flags"] and KM_UNKNOWN_WARNING in ev.warnings


def test_unknown_km_uses_the_same_20_percent_rule():
    """Sahip kararı (04.10.2026): km bilinmiyorsa kâr şartı DEĞİŞMEZ (%20); yalnız mesajda uyarı olur."""
    repo = FakeRepo([car("t", 6500, km=None)], POOL)  # kâr ~%22
    (ev,) = evaluate_new(repo)
    assert ev.profit.tier is Tier.STRONG and KM_UNKNOWN_WARNING in ev.warnings and "km_yok" not in repo.saved[0][1]["red_flags"]
    (ev,) = evaluate_new(FakeRepo([car("t", 7500, km=None)], POOL))  # kâr ~%8: yine 🟢 değil
    assert ev.profit.tier is not Tier.STRONG


def test_known_km_gets_no_km_warning():
    pool = [car(f"p{i}", 8000 + i * 100, km=60_000) for i in range(8)]
    (ev,) = evaluate_new(FakeRepo([car("t", 5000, km=70_000)], pool))
    assert ev.profit.tier is Tier.STRONG and KM_UNKNOWN_WARNING not in ev.warnings


# --- Adım 1: taşma ve ilan başına hata sınırı ---

class FailingRepo(FakeRepo):
    """Belirli ilanların kaydı patlar (tek bozuk ilan tüm turu düşürmesin)."""
    def __init__(self, listings, pool, fail_ids=(), exc=None):
        super().__init__(listings, pool)
        self.fail_ids, self.exc = set(fail_ids), exc or ValueError("boom")

    def save_evaluation(self, listing_id, ev):
        if listing_id in self.fail_ids:
            raise self.exc
        super().save_evaluation(listing_id, ev)


def test_clamp_pct_keeps_values_inside_the_column_range():
    assert PCT_LIMIT == 999.99  # evaluations.profit_pct DECIMAL(5,2)
    assert clamp_pct(0.25) == 25.0 and clamp_pct(-0.4) == -40.0
    assert clamp_pct(63.5) == 999.99 and clamp_pct(-50.0) == -999.99


def test_absurdly_cheap_listing_is_saved_without_overflow_and_is_never_green():
    pool = [car(f"p{i}", 40_000 + i * 100) for i in range(8)]  # KAA "38000 TRY" Hilux örneği: £600 ilan, £40 bin medyan
    repo = FakeRepo([car("t", 600)], pool)
    (ev,) = evaluate_new(repo)
    assert repo.saved[0][1]["profit_pct"] == 999.99  # taşmadan (ham değer ~%6290) kaydedildi
    assert ev.profit.tier is not Tier.STRONG


def test_one_bad_listing_does_not_stop_the_others():
    repo = FailingRepo([car("bad", 5000), car("good", 5000)], POOL, fail_ids={"bad"})
    failures = []
    evs = evaluate_new(repo, failures=failures)
    assert [e.listing["id"] for e in evs] == ["good"] and failures == [("bad", "ValueError")]


def test_database_outage_is_not_swallowed():
    repo = FailingRepo([car("t", 5000)], POOL, fail_ids={"t"}, exc=psycopg.OperationalError("sunucu yok"))
    with pytest.raises(psycopg.OperationalError):
        evaluate_new(repo)


def test_mass_failure_raises_but_a_few_failures_do_not():
    many = [car(f"x{i}", 5000) for i in range(10)]
    with pytest.raises(EvaluationFailure):
        evaluate_new(FailingRepo(many, POOL, fail_ids={l["id"] for l in many}))  # 10/10 patladı
    some = FailingRepo(many, POOL, fail_ids={f"x{i}" for i in range(4)})  # 4/10 = %40: tur sürer
    assert len(evaluate_new(some)) == 6
    few = [car(f"y{i}", 5000) for i in range(9)]  # 10'dan az deneme: oran güvenilmez, tur hata vermez
    assert evaluate_new(FailingRepo(few, POOL, fail_ids={l["id"] for l in few})) == []


# --- Adım 2h: veritabanı okuma hacmi (hızlı tur + yalnız ilgili modellerin emsal havuzu) ---

def test_quick_round_asks_only_for_recent_listings_and_full_round_for_everything():
    repo = FakeRepo([car("t", 5000)], POOL)
    evaluate_new(repo, quick=True)
    evaluate_new(repo)
    assert repo.recent_args == [3, None]


def test_hourly_full_round_leaves_the_old_unevaluated_backlog_to_the_daily_round():
    """Adım 2h devamı: tam turda backlog=False ise hiç değerlendirilmemiş ilanlardan yalnız son 72 saatte görülen/fiyatı değişenler istenir;
    backlog=True (varsayılan, cron'da günde bir) bugünkü tam turun aynısı; hızlı tur değişmedi."""
    from application.evaluate import BACKLOG_AFTER_HOURS
    from domain.settings import RULES_VERSION

    repo = FakeRepo([car("t", 5000)], POOL)
    evaluate_new(repo, quick=True)
    evaluate_new(repo, quick=True, backlog=False)
    evaluate_new(repo, backlog=False)
    evaluate_new(repo)
    assert repo.uneval_args == [(3, None, None), (3, None, None), (None, RULES_VERSION, BACKLOG_AFTER_HOURS), (None, RULES_VERSION, None)]
    assert BACKLOG_AFTER_HOURS == 72


def test_backlog_window_covers_every_listing_that_can_still_alert():
    """Saatlik turun 72 saatlik penceresi tazelik kuralını (notify.is_fresh: ilk görülme ya da fiyat değişikliği ≤36 saat, yayın ≤4 gün,
    sosyal ≤48 saat) tamamen kapsar: is_fresh True olan HER ilanın ilk görülmesi ya da son fiyat değişikliği pencere içindedir (SQL: '>' NOW()-72h).
    Tazelik kuralı bir gün gevşetilir de pencereyi aşarsa bu test kırılır (saatlik turda olmayan ilan bildirim kaçırmasın)."""
    import inspect

    from application.evaluate import BACKLOG_AFTER_HOURS
    from application.notify import is_fresh

    assert BACKLOG_AFTER_HOURS >= 2 * inspect.signature(is_fresh).parameters["fresh_hours"].default  # en az iki kat güvenlik payı
    ages = [0, 1, 12, 35, 36, 37, 47, 48, 49, 71, 72, 73, 96, 200, 2000]
    checked = 0
    for fs in ages:
        for pc in [None, *ages]:
            for posted in [None, *ages]:
                for platform in ("web", "instagram", "facebook"):
                    fresh = is_fresh(NOW - timedelta(hours=fs), None if posted is None else NOW - timedelta(hours=posted), now=NOW,
                                     price_changed_at=None if pc is None else NOW - timedelta(hours=pc), platform=platform)
                    if fresh:
                        checked += 1
                        assert fs < BACKLOG_AFTER_HOURS or (pc is not None and pc < BACKLOG_AFTER_HOURS), (fs, pc, posted, platform)
    assert checked > 100


def test_pool_is_limited_to_the_models_being_evaluated_without_changing_results():
    other = [car(f"o{i}", 2000 + i, brand_norm="Honda", model_norm="fit", model="Fit") for i in range(8)]
    narrow = FakeRepo([car("t", 5000)], POOL + other)
    full = FakeRepo([car("t", 5000)], POOL + other)
    full.market_pool = lambda days, keys=None: full._pool  # eski davranış: tüm havuz
    evaluate_new(narrow)
    evaluate_new(full)
    assert narrow.pool_calls == [[("Toyota", "vitz")]]  # yalnız değerlendirilen ilanın (marka, model) çifti istendi
    assert narrow.saved == full.saved  # kayıtlı sonuç birebir aynı


def test_nothing_to_evaluate_does_not_read_the_market_pool_at_all():
    repo = FakeRepo([], POOL)
    assert evaluate_new(repo, quick=True) == [] and repo.pool_calls == []
    repo = FakeRepo([car("m", 3000, brand_norm="Yamaha")], POOL)  # motosiklet: otomobil sistemi dışı
    assert evaluate_new(repo) == [] and repo.pool_calls == []


def test_strong_is_held_back_when_the_widened_comparables_are_newer_than_the_car():
    """Adım 7c koruması: ±2 yıl genişlemede emsal medyan yılı hedeften büyükse (yeni modeller pahalı) 🟢 yok (en fazla 🟡)."""
    narrow = [car(f"n{i}", 8000 + i * 100, year=2015) for i in range(3)]
    newer = [car(f"w{i}", 8200 + i * 100, year=2017) for i in range(8)]  # ±2'de çoğunluk 2017 (hedef 2015)
    repo = FakeRepo([car("t", 5000, year=2015)], narrow + newer)
    (ev,) = evaluate_new(repo)
    assert ev.market.year_span == 2 and ev.profit.tier is Tier.NEGOTIABLE
    assert "emsal_yili_yeni" in repo.saved[0][1]["red_flags"]
    older = [car(f"o{i}", 8200 + i * 100, year=2013) for i in range(8)]  # ±2'de çoğunluk 2013 (hedeften ESKİ): kıyas muhafazakâr, 🟢 olabilir
    repo = FakeRepo([car("t", 5000, year=2015)], narrow + older)
    (ev,) = evaluate_new(repo)
    assert ev.market.year_span == 2 and ev.profit.tier is Tier.STRONG


def test_strong_is_held_back_when_even_the_plus_minus_one_comparables_skew_a_year_newer():
    """Adım 7e (Opus 04.10.2026): ±1'de de medyan yıl hedef+1 ise (emsallerin çoğu bir yıl yeni) hedef ucuz görünür: 🟢 yok."""
    newer = [car(f"n{i}", 8000 + i * 100, year=2016) for i in range(8)]  # hedef 2015, emsallerin hepsi 2016 (±1 içinde)
    repo = FakeRepo([car("t", 5000, year=2015)], newer)
    (ev,) = evaluate_new(repo)
    assert ev.market.year_span == 1 and ev.profit.tier is Tier.NEGOTIABLE and "emsal_yili_yeni" in repo.saved[0][1]["red_flags"]
    mixed = [car(f"m{i}", 8000 + i * 100, year=2014 if i % 2 else 2016) for i in range(8)]  # medyan yıl 2015 = hedef: kayma yok
    repo = FakeRepo([car("t", 5000, year=2015)], mixed)
    (ev,) = evaluate_new(repo)
    assert ev.profit.tier is Tier.STRONG
