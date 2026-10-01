"""Geriye dönük doğruluk testi: her ilanı, kendisi hariç emsallerin medyanıyla karşılaştırır.
Gerçek satış fiyatını bilmiyoruz; bu test ilan fiyatlarının emsallerden ne kadar saptığını ve motorun kaç ilanı
fırsat saydığını gösterir (çok yüksek 'güçlü' oranı = sahte alarm riski)."""
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from domain.comparables import find_market
from domain.profit import Confidence, Tier, evaluate_profit
from domain.settings import Settings


@dataclass
class BacktestRow:
    n: int = 0
    abs_errors: list = None
    strong: int = 0
    negotiable: int = 0

    def summary(self) -> dict:
        err = statistics.median(self.abs_errors) * 100 if self.abs_errors else 0.0
        return {"ilan": self.n, "medyan_sapma_pct": round(err, 1),
                "guclu_pct": round(100 * self.strong / self.n, 1) if self.n else 0.0,
                "pazarlik_pct": round(100 * self.negotiable / self.n, 1) if self.n else 0.0}


def run_backtest(pool: list[dict], settings: Settings | None = None, now: datetime | None = None) -> dict[str, dict]:
    s = settings or Settings()
    by_conf: dict[str, BacktestRow] = defaultdict(lambda: BacktestRow(abs_errors=[]))
    for target in pool:
        price = target.get("price_gbp")
        if (not price or target.get("currency_guess") or target.get("duplicate_of")
                or not s.min_plausible_price_gbp <= price <= s.max_plausible_price_gbp):
            continue
        market = find_market(target, pool, s, now)
        if market is None:
            continue
        p = evaluate_profit(price, market.median_gbp, market.n, s)
        row = by_conf[p.confidence.value]
        row.n += 1
        row.abs_errors.append(abs(price - market.median_gbp) / market.median_gbp)
        row.strong += p.tier is Tier.STRONG
        row.negotiable += p.tier is Tier.NEGOTIABLE
    order = [c.value for c in (Confidence.HIGH, Confidence.MEDIUM, Confidence.LOW)]
    return {c: by_conf[c].summary() for c in order if c in by_conf}
