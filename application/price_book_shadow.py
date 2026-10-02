"""🟠 kuru deneme (gölge): son N günde ilk görülen ilanlara 🟠 kuralı bugünkü tabloyla uygulanır; hiçbir şey yazılmaz,
hiçbir şey gönderilmez. Amaç: eşiği açmadan önce "kaç 🟠 çıkardı, saçma mı?" sorusuna cevap.

Yaklaşım (belgelenmiş yaklaşıklık): tablo bir kez bellekte kurulur (build_book, tüm havuzla). Değerlendirilen ilan kendi
modelinin havuzunda da bulunduğu için, o modelin eğrisi ilan HARİÇ yeniden uydurulur (leave-one-out, fit_curve; ağırlık ve
galeri sınırı uygulanmaz, ham ilanlar). Başka modellerin eğrileri ve tablo satırları aynen kalır. Fiyat, ilanın bugünkü
fiyatıdır (sonradan düştüyse gerçekte ilk bildirilen fiyattan farklı olabilir)."""
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

from application.evaluate import assess_listing
from domain.comparables import find_market
from domain.normalize import is_car_brand
from domain.price_book import PriceBook
from domain.profit import Tier
from domain.settings import Settings


@dataclass
class ShadowRow:
    day: str
    label: str  # "2014 Toyota Corolla"
    km: int | None
    price: float
    value: float
    lower: float
    discount: float  # değere göre ucuzluk (0-1)
    curve_n: int
    curve_sellers: int
    a_median: float | None  # benzer ilanlardan (yöntem A) medyan, varsa
    a_confirmed: bool  # A medyanı var ve fiyat ≤ est_a_agree_ratio·medyan
    url: str | None
    active: bool


def _loo_book(book: PriceBook, listing: dict, pool: list[dict], s: Settings, now: datetime, fit) -> PriceBook:
    """Aynı tablo; yalnızca ilanın modelinin eğrisi ilan hariç yeniden uydurulur."""
    key = (listing["brand_norm"], listing["model_norm"])
    old = book.curve(*key)
    if old is None:
        return book
    rows = [r for r in pool if (r["brand_norm"], r["model_norm"]) == key and r["id"] != listing["id"]
            and r.get("year") and r.get("km") and r.get("price_gbp")]
    curve = fit(rows, None, key[0], key[1], old.ref_year or now.year, s, s.est_max_sigma)
    curves = {k: v for k, v in book.curves.items() if k != key}
    if curve is not None:
        curves[key] = curve
    return PriceBook(rows=book.rows, curves=curves, disabled_models=book.disabled_models)


def shadow_candidates(candidates: list[dict], pool: list[dict], book: PriceBook, s: Settings, fit,
                      now: datetime | None = None) -> list[ShadowRow]:
    now = now or datetime.now(timezone.utc)
    s = s.model_copy(update={"estimated_alerts": True})
    out = []
    for l in candidates:
        if not is_car_brand(l.get("brand_norm")) or not s.min_plausible_price_gbp <= float(l["price_gbp"]) <= s.max_plausible_price_gbp:
            continue
        a = assess_listing(l, pool, s, _loo_book(book, l, pool, s, now, fit))
        if a is None or a.profit.tier is not Tier.ESTIMATED:
            continue
        price = float(l["price_gbp"])
        mk = find_market(l, pool, s)  # yöntem A (varsa): aynı ilanlara bakan bağımsız kontrol
        a_med = mk.median_gbp if mk else None
        out.append(ShadowRow(
            f"{l['first_seen_at']:%Y-%m-%d}", f"{l['year']} {l.get('brand') or l['brand_norm']} {l.get('model') or l['model_norm'] or ''}".strip(),
            l.get("km"), price, a.market.median_gbp, a.market.low_gbp, 1 - price / a.market.median_gbp,
            a.estimate.n if a.estimate else 0, a.estimate.sellers if a.estimate else 0,
            a_med, bool(a_med and price <= s.est_a_agree_ratio * a_med), l.get("url"), bool(l.get("is_active", True))))
    return out


def _n(x, unit: str) -> str:
    return "?" if not x else f"{unit}{x:,.0f}".replace(",", ".")


def format_report(rows: list[ShadowRow], days: int, total: int) -> str:
    lines = [f"🟠 kuru deneme: son {days} günde ilk görülen {total} ilandan {len(rows)} tanesi 🟠 çıkardı."]
    per_day = Counter(r.day for r in rows)
    lines += ["", "Günlük adet:"] + [f"  {d}: {n}" for d, n in sorted(per_day.items())]
    confirmed = sum(r.a_confirmed for r in rows)
    lines.append(f"\nBenzer ilanlarla da doğrulanan (A medyanı var, fiyat ≤ %85'i): {confirmed} / {len(rows)}")
    lines += ["", "tarih | araç | km | fiyat | değer | alt sınır | ucuzluk | eğri ilan/satıcı | A medyan | akt | link"]
    for r in sorted(rows, key=lambda r: (r.day, -r.discount)):
        lines.append(f"{r.day} | {r.label} | {_n(r.km, '')} | {_n(r.price, '£')} | {_n(r.value, '£')} | {_n(r.lower, '£')} | "
                     f"%{r.discount * 100:.0f} | {r.curve_n}/{r.curve_sellers} | {_n(r.a_median, '£')} | "
                     f"{'E' if r.active else 'H'} | {r.url or '-'}")
    return "\n".join(lines)


def run_shadow(repo, days: int = 14, s: Settings | None = None) -> str:
    """Salt okunur: tabloyu bellekte kurar, adayları değerlendirir, rapor metni döndürür."""
    from domain.price_book import build_book, fit_curve  # A modülü: gevşek bağlantı
    s = s or Settings()
    now = datetime.now(timezone.utc)
    pool = [r for r in repo.market_pool(days=s.comparable_window_days + 30) if is_car_brand(r.get("brand_norm"))]
    book = build_book(pool, [], now, None, s)
    candidates = repo.first_seen_since(days)
    rows = shadow_candidates(candidates, pool, book, s, fit_curve, now)
    return format_report(rows, days, len(candidates))
