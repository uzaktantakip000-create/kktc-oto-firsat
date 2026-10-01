from dataclasses import dataclass
from enum import StrEnum

from domain.settings import Settings


class Tier(StrEnum):
    STRONG = "guclu"
    NEGOTIABLE = "pazarlik"
    NONE = "yok"


class Confidence(StrEnum):
    HIGH = "yuksek"
    MEDIUM = "orta"
    LOW = "dusuk"
    NONE = "yok"


@dataclass(frozen=True)
class ProfitResult:
    exit_price_gbp: float
    profit_gbp: float
    profit_pct: float
    confidence: Confidence
    tier: Tier


def confidence_for(comparables_n: int) -> Confidence:
    if comparables_n >= 20:
        return Confidence.HIGH
    if comparables_n >= 8:
        return Confidence.MEDIUM
    if comparables_n >= 3:
        return Confidence.LOW
    return Confidence.NONE


def evaluate_profit(
    buy_price_gbp: float,
    market_median_gbp: float,
    comparables_n: int,
    settings: Settings | None = None,
) -> ProfitResult:
    s = settings or Settings()
    exit_price = market_median_gbp * s.quick_sale_factor
    profit = exit_price - buy_price_gbp - s.fixed_cost_gbp
    pct = profit / buy_price_gbp
    confidence = confidence_for(comparables_n)

    if confidence is Confidence.NONE or comparables_n < s.min_comparables_alert:
        tier = Tier.NONE
    elif pct >= s.strong_threshold:
        tier = Tier.STRONG
    elif pct >= s.negotiable_threshold:
        tier = Tier.NEGOTIABLE
    else:
        tier = Tier.NONE

    # Düşük güvende sadece kâr >= %30 ise bildirim
    if confidence is Confidence.LOW and pct < s.low_confidence_min_profit:
        tier = Tier.NONE
    # Küçük araçlarda %20 az para eder: asgari net kâr yoksa 🟢 verilmez (🟡 olarak kalır)
    if tier is Tier.STRONG and profit < s.min_strong_profit_gbp:
        tier = Tier.NEGOTIABLE
    # Emsal medyanının %50'sinden düşük fiyat: muhtemelen yanlış yazım, 🟢 verilmez
    if buy_price_gbp < market_median_gbp * s.absurd_price_ratio and tier is Tier.STRONG:
        tier = Tier.NEGOTIABLE

    return ProfitResult(exit_price, profit, pct, confidence, tier)
