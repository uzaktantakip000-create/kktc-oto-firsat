from dataclasses import dataclass, field

from domain.comparables import Market, find_market
from domain.data_gate import below_cheap_quartile, data_gaps
from domain.profit import Confidence, ProfitResult, Tier, evaluate_profit
from domain.normalize import is_car_brand
from domain.red_flags import blocking_flags, plate_flags, urgency_signals, warning_flags
from domain.settings import Settings
from infrastructure.db.repository import Repository


@dataclass
class Evaluated:
    listing: dict
    market: Market
    profit: ProfitResult
    blocking: list[str]
    warnings: list[str]
    urgency: list[str]
    checks: list[str] = field(default_factory=list)  # ✅ ile gösterilen doğrulama satırları


@dataclass
class Assessment:
    market: Market
    profit: ProfitResult  # tier: kuralların hepsinden geçtikten sonraki son seviye
    blocking: list[str]
    warnings: list[str]
    gaps: list[str]  # 🟢 iken düşürülmesine yol açan eksikler (düşürülmediyse boş)
    text: str


def _apply_user_decisions(listing: dict, s: Settings, price: float, tier: Tier, gaps: list[str]) -> tuple[Tier, list[str]]:
    """Kullanıcının Telegram'dan verdiği kararlar: istenmeyen marka / bütçe üstü / kara listedeki satıcı -> bildirim yok;
    3 kez 'pas' denen model -> en fazla 🟡."""
    brand, model = listing.get("brand_norm"), listing.get("model_norm")
    if brand in s.blocked_brands or (s.max_buy_gbp and price > s.max_buy_gbp):
        return Tier.NONE, gaps
    if listing.get("seller_phone") and listing["seller_phone"] in s.blocked_phones:
        return Tier.NONE, gaps
    if tier is Tier.STRONG and f"{brand}|{model}" in s.muted_models:
        gaps = gaps + ["sessiz_model"]
    return tier, gaps


def assess_listing(listing: dict, pool: list[dict], s: Settings) -> Assessment | None:
    """Tek ilanın piyasa değerlendirmesi (toplayıcı ilanları ve kullanıcının ilettiği ilanlar aynı kuralları kullanır).
    Emsal yoksa None."""
    price = float(listing["price_gbp"])
    market = find_market(listing, pool, s)
    if market is None:
        return None
    profit = evaluate_profit(price, market.median_gbp, market.n, s)
    text = (listing.get("raw_text") or "") + " " + (listing.get("model") or "")
    blocking, warnings = blocking_flags(text), warning_flags(text)
    gaps = data_gaps(listing, market, s)
    tier = Tier.NONE if blocking else profit.tier
    if tier is Tier.STRONG and not below_cheap_quartile(price, market):
        gaps = gaps + ["ucuz_ceyrek_degil"]  # medyandan %20 ucuz ama benzerlerin en ucuz çeyreğinde değil: sıradan fiyat
    if tier is Tier.STRONG and plate_flags(text):
        gaps = gaps + ["plaka_uyari"]
    tier, gaps = _apply_user_decisions(listing, s, price, tier, gaps)
    downgraded = tier is Tier.STRONG and bool(gaps)
    if downgraded:  # eksik/şüpheli veriyle 🟢 yok: en fazla 🟡
        tier = Tier.NEGOTIABLE
    final = ProfitResult(profit.exit_price_gbp, profit.profit_gbp, profit.profit_pct, profit.confidence, tier)
    return Assessment(market, final, blocking, warnings, gaps if downgraded else [], text)


def evaluate_new(repo: Repository, settings: Settings | None = None) -> list[Evaluated]:
    """Henüz değerlendirilmemiş aktif ilanları değerlendirir. Emsali olmayanlar bir sonraki turda tekrar denenir."""
    s = settings or Settings()
    pool = [r for r in repo.market_pool(days=s.comparable_window_days + 30) if is_car_brand(r.get("brand_norm"))]
    results = []
    for listing in repo.unevaluated_active():
        if not is_car_brand(listing.get("brand_norm")):
            continue  # motosiklet/tekne/karavan/ticari: bu sistem otomobil içindir
        price = float(listing["price_gbp"])
        if not s.min_plausible_price_gbp <= price <= s.max_plausible_price_gbp:
            # Eksik rakam/yanlış yazım olasılığı: değerlendirme kaydı atılır ama bildirim üretilmez
            repo.save_evaluation(listing["id"], {"comparables_n": 0, "confidence": Confidence.NONE.value,
                                                 "tier": Tier.NONE.value, "red_flags": ["fiyat_gecersiz"]})
            continue
        a = assess_listing(listing, pool, s)
        if a is None:
            continue
        market, profit = a.market, a.profit
        repo.save_evaluation(
            listing["id"],
            {
                "comparables_n": market.n,
                "market_median_gbp": round(market.median_gbp, 2),
                "market_low_gbp": round(market.low_gbp, 2),
                "market_high_gbp": round(market.high_gbp, 2),
                "year_span": market.year_span,
                "archived_share": round(market.archived_share, 3),
                "exit_price_gbp": round(profit.exit_price_gbp, 2),
                "profit_gbp": round(profit.profit_gbp, 2),
                "profit_pct": round(profit.profit_pct * 100, 2),
                "confidence": profit.confidence.value,
                "tier": profit.tier.value,
                "red_flags": a.blocking + a.warnings + a.gaps,  # "bu yüzden 🟢 değil" sadece gerçekten düşürüldüyse
            },
        )
        results.append(Evaluated(listing, market, profit, a.blocking, a.warnings, urgency_signals(a.text)))
    return results


def pending_alerts(repo: Repository, hours: int = 36) -> list[Evaluated]:
    """Gönderilmesi gereken 🟢 fırsatlar: yeni değerlendirilenler + daha önce gönderilemeyenler (hata, hız sınırı, yeni abone)."""
    out = []
    for r in repo.pending_strong(hours):
        text = (r["raw_text"] or "") + " " + (r["model"] or "")
        med = r["market_median_gbp"]
        market = Market(r["comparables_n"], med, r["market_low_gbp"] or med, r["market_high_gbp"] or med,
                        r["year_span"] or 1, r["archived_share"] or 0.0)
        profit = ProfitResult(r["exit_price_gbp"], r["profit_gbp"], r["profit_pct"] / 100,
                              Confidence(r["confidence"]), Tier.STRONG)
        out.append(Evaluated(r, market, profit, [], list(r["red_flags"] or []), urgency_signals(text)))
    return out


def confidence_label(c: Confidence) -> str:
    return {"yuksek": "YÜKSEK", "orta": "ORTA", "dusuk": "DÜŞÜK — kontrol et", "yok": "YOK"}[c.value]
