from domain.profit import Confidence, Tier, evaluate_profit
from domain.settings import Settings

NO_COST = Settings(fixed_cost_gbp=0)  # eski testler masrafsız hesabı doğrular


def test_strong_deal():
    # medyan 10.000 -> satilabilir 9.500; alis 7.000 -> kar %35.7
    r = evaluate_profit(7000, 10000, 25, NO_COST)
    assert r.tier is Tier.STRONG
    assert r.confidence is Confidence.HIGH
    assert round(r.profit_gbp) == 2500


def test_negotiable_band():
    r = evaluate_profit(8300, 10000, 25, NO_COST)  # 9500/8300 -> %14.5
    assert r.tier is Tier.NEGOTIABLE


def test_no_deal():
    assert evaluate_profit(9300, 10000, 25).tier is Tier.NONE


def test_too_few_comparables_no_alert():
    assert evaluate_profit(5000, 10000, 2).tier is Tier.NONE


def test_low_confidence_needs_30_percent():
    assert evaluate_profit(7500, 10000, 5).tier is Tier.NONE  # %26.7
    assert evaluate_profit(6500, 10000, 5).tier is Tier.STRONG  # %46


def test_absurd_price_not_green():
    # medyanin %50'sinden dusuk -> yanlis fiyat suphesi
    assert evaluate_profit(4000, 10000, 25).tier is Tier.NEGOTIABLE


def test_fixed_cost_is_deducted():
    r = evaluate_profit(7000, 10000, 25)  # 9.500 - 7.000 - 300 masraf
    assert round(r.profit_gbp) == 2200


def test_cost_can_push_deal_below_threshold():
    # masrafsız %20.4 (🟢), £300 masrafla %16.0 (🟡)
    assert evaluate_profit(7890, 10000, 25, NO_COST).tier is Tier.STRONG
    assert evaluate_profit(7890, 10000, 25).tier is Tier.NEGOTIABLE


def test_small_car_needs_minimum_net_profit():
    # £3.000 araç: %30 kâr = £900 brüt ama masraftan sonra £600 < £750 -> 🟢 değil, 🟡
    r = evaluate_profit(3000, 4200, 25)
    assert round(r.profit_gbp) == 690 and r.tier is Tier.NEGOTIABLE
    # £6.000 araç: yeterli net kâr -> 🟢
    assert evaluate_profit(6000, 9000, 25).tier is Tier.STRONG
