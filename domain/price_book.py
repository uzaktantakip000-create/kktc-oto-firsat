"""Değer tablosu (price book): her marka + model (+ varyant) + yıl için oturmuş piyasa değeri ve model değer eğrisi.
Saf mantık (ağ/DB yok, yalnızca standart kütüphane). Her gece application/price_book_job.py kurar; yeni ilan
application/evaluate.assess_listing içinde estimate_from_book ile karşılaştırılır (🟠 tahmini fırsat).

Yöntemler:
  A = doğrudan emsal: aynı model, yıl ±1, fiyatlar curve ile aynı yıl/km'ye çekilip medyan (galeri başına ≤2 ilan)
  B = model eğrisi: ln(fiyat) = a + b_age·yaş + b_km·km/10000 (ridge, saf Python), ≥8 ilan, ≥3 farklı yıl
  C = marka eğrisi: yalnızca /fiyat'ta "yaklaşık" bilgi; asla bildirim üretmez
"""
import math
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime

from domain.comparables import _drop_outliers, _is_comparable, effective_km
from domain.quality import YEAR_RANGE
from domain.settings import Settings

STATUS_SETTLED = "oturmus"
STATUS_THIN = "ince"
STATUS_SUSPECT = "supheli"

DEFAULT_B_AGE = -0.08  # eğri yoksa: yaş başına ~%8 değer kaybı
DEFAULT_B_KM = -0.03  # eğri yoksa: 10.000 km başına ~%3
MAX_B_AGE = 0.02  # eğri bundan fazla "yaşlandıkça değer kazanıyor" derse mantıksız: reddedilir
VARIANT_MIN_ROWS = 5
VARIANT_MIN_SELLERS = 3
MAX_PLAUSIBLE_KM = 600_000  # üstü yazım hatası (veri bakımı da bunu karantinaya alır)
ROBUST_K = 2.5  # artık > K·1.4826·MAD ise aykırı


@dataclass(frozen=True)
class Curve:
    brand_norm: str
    model_norm: str  # marka eğrisi (C) için "*"
    a: float  # ref_year'daki yaşta, ortalama km'de ln(fiyat) sabiti (merkezleme fit_curve içinde)
    b_age: float  # yaş başına ln(fiyat) değişimi (negatif)
    b_km: float  # 10.000 km başına ln(fiyat) değişimi (negatif)
    sigma: float  # artıkların standart sapması (ln ölçeğinde)
    n: int
    years: int  # farklı yıl sayısı
    sellers: int  # farklı satıcı sayısı
    min_year: int
    max_year: int
    max_km: int
    ref_year: int  # yaşın hesaplandığı yıl (kurulum yılı)
    mean_age: float = 0.0  # merkezleme için
    mean_km10: float = 0.0  # merkezleme için (km/10000)
    km_per_year: float = 15_000.0  # bu modelde yıl başına ortalama km (B satırlarının ref_km'si için)

    def predict_ln(self, year: int, km: int) -> float:
        """ln(fiyat) tahmini."""
        return self.a + self.b_age * ((self.ref_year - year) - self.mean_age) + self.b_km * (km / 10_000 - self.mean_km10)


@dataclass(frozen=True)
class BookRow:
    brand_norm: str
    model_norm: str
    variant: str  # "" = birleşik satır; "180", "320", "1.6" gibi
    year: int
    value_gbp: float  # kabul edilmiş değer (ref_km için)
    low_gbp: float  # p25 (A) ya da alt sınır (B)
    high_gbp: float  # p75 (A) ya da üst sınır (B)
    ref_km: int | None
    n: int
    sellers: int
    method: str  # "A" | "B" | "C"
    status: str  # STATUS_*
    cand_value: float | None = None  # şüpheli iken bekleyen yeni değer
    cand_nights: int = 0
    sales_n: int = 0  # sahibin girdiği gerçek satış sayısı (bu satıra düşen)
    sales_median_gbp: float | None = None


@dataclass
class PriceBook:
    rows: dict[tuple[str, str, str, int], BookRow] = field(default_factory=dict)
    curves: dict[tuple[str, str], Curve] = field(default_factory=dict)
    disabled_models: set[tuple[str, str]] = field(default_factory=set)  # öz-kontrol/geri bildirimle 🟠'su kapalı modeller

    def row(self, brand_norm: str, model_norm: str, year: int, variant: str = "") -> BookRow | None:
        return self.rows.get((brand_norm, model_norm, variant, year))

    def curve(self, brand_norm: str, model_norm: str) -> Curve | None:
        return self.curves.get((brand_norm, model_norm))


@dataclass(frozen=True)
class Estimate:
    value_gbp: float  # eğrinin nokta tahmini
    lower_gbp: float  # value · exp(−z·σ): "en kötü ihtimalle"
    method: str  # "B"
    n: int
    sellers: int
    sigma: float
    row: BookRow | None = None  # aynı model-yılın tablo satırı (mesajda gösterilir)


# --- varyant ---
_THREE_DIGIT = re.compile(r"(?<!\d)(\d{3})(?!\d)")


def variant_of(row: dict) -> str:
    """Ham model metninden/motor hacminden varyant: 'C Serisi C 180' -> '180', '320i' -> '320', diğer: '1.6'; yoksa ''."""
    if row.get("brand_norm") in ("Mercedes-Benz", "BMW"):
        m = _THREE_DIGIT.search(row.get("model") or "")
        return m.group(1) if m else ""
    eng = row.get("engine_l")
    if eng is None or not 0.5 <= float(eng) <= 8.0:
        return ""
    return f"{int(float(eng) / 0.2 + 0.5 + 1e-9) * 0.2:.1f}"


# --- eğri ---
def _skey(r: dict, i: int) -> str:
    return r.get("seller_phone") or f"id:{r.get('id', i)}"


def _usable_fit_rows(rows: list[dict], weights: list[float] | None) -> list[tuple[dict, float, float, float, float]]:
    """(satır, ağırlık, yaş-yıl, km/10k, ln fiyat): fiyat/yıl/km bilinen, sol direksiyon olmayan satırlar."""
    out = []
    for i, r in enumerate(rows):
        km, price, year = effective_km(r), r.get("price_gbp"), r.get("year")
        if km is None or not price or price <= 0 or year is None or r.get("steering") == "LHD":
            continue
        out.append((r, 1.0 if weights is None else weights[i], year, km / 10_000, math.log(price)))
    return out


def _ridge(pts: list[tuple], ref_year: int, lam: float):
    """Merkezlenmiş 2x2 ridge. pts: (satır, w, yıl, km10, y). Dönen: (ma, mk, my, b_age, b_km) ya da None."""
    sw = sum(p[1] for p in pts)
    if sw <= 0:
        return None
    ma = sum(p[1] * (ref_year - p[2]) for p in pts) / sw
    mk = sum(p[1] * p[3] for p in pts) / sw
    my = sum(p[1] * p[4] for p in pts) / sw
    s11 = s22 = s12 = t1 = t2 = 0.0
    for _, w, yr, k, y in pts:
        a, k, y = (ref_year - yr) - ma, k - mk, y - my
        s11 += w * a * a
        s22 += w * k * k
        s12 += w * a * k
        t1 += w * a * y
        t2 += w * k * y
    s11 += lam
    s22 += lam
    det = s11 * s22 - s12 * s12
    if det <= 1e-9:
        return None
    return ma, mk, my, (t1 * s22 - t2 * s12) / det, (s11 * t2 - s12 * t1) / det


def _residuals(pts: list[tuple], fit: tuple, ref_year: int) -> list[float]:
    ma, mk, my, b1, b2 = fit
    return [p[4] - my - b1 * ((ref_year - p[2]) - ma) - b2 * (p[3] - mk) for p in pts]


def fit_curve(rows: list[dict], weights: list[float] | None, brand_norm: str, model_norm: str, ref_year: int,
              s: Settings, max_sigma: float | None = None) -> Curve | None:
    """Ridge ile ln(fiyat) ~ yaş + km/10k. Koşullar sağlanmazsa None (≥8 km'li ilan, ≥3 yıl, σ ≤ max_sigma)."""
    max_sigma = s.est_max_sigma if max_sigma is None else max_sigma
    pts = _usable_fit_rows(rows, weights)

    def enough(p: list[tuple]) -> bool:
        return len(p) >= s.est_curve_min_rows and len({x[2] for x in p}) >= s.est_curve_min_years

    if not enough(pts):
        return None
    fit = _ridge(pts, ref_year, s.ridge_lambda)
    if fit is None:
        return None
    res = _residuals(pts, fit, ref_year)  # tek sağlam geçiş: MAD'e göre aykırıları at, yeniden uydur
    mad = statistics.median(abs(r - statistics.median(res)) for r in res)
    if mad > 0:
        keep = [p for p, r in zip(pts, res) if abs(r - statistics.median(res)) <= ROBUST_K * 1.4826 * mad]
        if len(keep) < len(pts) and enough(keep):
            refit = _ridge(keep, ref_year, s.ridge_lambda)
            if refit is not None:
                pts, fit = keep, refit
                res = _residuals(pts, fit, ref_year)
    ma, mk, my, b_age, b_km = fit
    sw = sum(p[1] for p in pts)
    sigma = math.sqrt(sum(p[1] * r * r for p, r in zip(pts, res)) / max(sw - 3, 1.0))
    if sigma > max_sigma or b_age > MAX_B_AGE:
        return None
    ages = [max(ref_year - p[2], 1) for p in pts]
    return Curve(
        brand_norm, model_norm, my, b_age, b_km, sigma, len(pts), len({p[2] for p in pts}),
        len({_skey(p[0], i) for i, p in enumerate(pts)}), min(p[2] for p in pts), max(p[2] for p in pts),
        int(max(p[3] for p in pts) * 10_000), ref_year, ma, mk,
        statistics.median(p[3] * 10_000 / a for p, a in zip(pts, ages)))


# --- durum makinesi ---
def next_status(old: BookRow | None, new_value: float, n: int, sellers: int, s: Settings) -> tuple[float, str, float | None, int]:
    """(kabul edilen değer, durum, bekleyen değer, bekleme gecesi). %15 kuralı + 2 gece kuralı."""
    thin = n < s.book_settled_min_n or sellers < s.book_settled_min_sellers
    base = STATUS_THIN if thin else STATUS_SETTLED
    if old is None or old.value_gbp <= 0:
        return new_value, base, None, 0
    if abs(new_value - old.value_gbp) / old.value_gbp <= s.book_change_limit:
        return new_value, base, None, 0  # küçük değişim: kabul (şüphe varsa kalkar)
    cand = old.cand_value if old.status == STATUS_SUSPECT else None
    if cand and abs(new_value - cand) / cand <= s.book_confirm_tolerance:
        nights = old.cand_nights + 1
        if nights >= s.book_confirm_nights:
            return new_value, base, None, 0  # yeni değer yeterince gece tutarlı: kabul
        return old.value_gbp, STATUS_SUSPECT, new_value, nights
    return old.value_gbp, STATUS_SUSPECT, new_value, 1  # büyük sıçrama (ya da aday oynadı): eski değer kalır, sayaç baştan


# --- tablo kurulumu ---
def _ts(r: dict) -> float:
    d = r.get("ref_date") or r.get("first_seen_at")
    return d.timestamp() if d else 0.0


def _cap_per_seller(rows: list[dict], cap: int) -> list[dict]:
    """Bir satıcının satıra en fazla 'cap' ilanla girmesi (en yeniler kalır): galeri ortalamayı tek başına belirlemesin."""
    by: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by[_skey(r, 0)].append(r)
    return [r for v in by.values() for r in sorted(v, key=_ts, reverse=True)[:cap]]


def _seller_weights(rows: list[dict], cap: int) -> list[float]:
    """Eğri için: bir satıcının m ilanı varsa her biri min(1, cap/m) ağırlık alır (bilgi kaybı olmadan etki sınırı)."""
    cnt = Counter(_skey(r, i) for i, r in enumerate(rows))
    return [min(1.0, cap / cnt[_skey(r, i)]) for i, r in enumerate(rows)]


def _quantiles(prices: list[float]) -> tuple[float, float]:
    if len(prices) < 2:
        return prices[0], prices[0]
    q = statistics.quantiles(prices, n=4, method="inclusive")
    return q[0], q[2]


def _adjust_params(curve: Curve | None) -> tuple[float, float]:
    """Yıl ve km düzeltmesi katsayıları (ln ölçeği); değer kazandıran (pozitif) katsayı kullanılmaz."""
    if curve is None:
        return DEFAULT_B_AGE, DEFAULT_B_KM
    return min(curve.b_age, 0.0), min(curve.b_km, 0.0)


def _row_from_comps(comps: list[dict], year: int, curve: Curve | None, s: Settings):
    """Emsallerden (A) (değer, alt, üst, ref_km, n, satıcı) ya da None. Fiyatlar hedef yıla ve ref_km'ye çevrilir."""
    comps = _cap_per_seller(comps, s.book_max_per_seller)
    kms = [k for k in (effective_km(r) for r in comps) if k]
    ref_km = int(statistics.median(kms)) if len(kms) >= 3 else None
    b_age, b_km = _adjust_params(curve)
    adj = []
    for r in comps:
        ln = math.log(r["price_gbp"]) + b_age * (r["year"] - year)
        k = effective_km(r)
        if ref_km is not None and k:
            ln += b_km * (ref_km - k) / 10_000
        adj.append((math.exp(ln), r))
    kept = set(_drop_outliers(sorted(p for p, _ in adj), s.small_pool_band))
    used = [(p, r) for p, r in adj if p in kept]
    if len(used) < s.min_comparables_alert:
        return None
    prices = [p for p, _ in used]
    lo, hi = _quantiles(prices)
    return statistics.median(prices), lo, hi, ref_km, len(prices), len({_skey(r, 0) for _, r in used})


def _sales_info(sales_by_model: list[dict], year: int) -> tuple[int, float | None]:
    near = [x["price_gbp"] for x in sales_by_model if abs(x["year"] - year) <= 1]
    return (len(near), statistics.median(near)) if near else (0, None)


def _synthetic_target(brand: str, model: str, year: int) -> dict:
    return {"id": None, "brand_norm": brand, "model_norm": model, "year": year, "steering": "RHD", "km": None,
            "transmission": None, "fuel": None, "engine_l": None}


def _fit_group(rows: list[dict], sale_rows: list[dict], brand: str, model: str, ref_year: int, s: Settings,
               max_sigma: float | None) -> Curve | None:
    allr = rows + sale_rows
    w = _seller_weights(rows, s.book_max_per_seller) + [s.owner_sale_weight] * len(sale_rows)
    return fit_curve(allr, w, brand, model, ref_year, s, max_sigma)


def build_book(pool: list[dict], sales: list[dict], now: datetime, prev: PriceBook | None, s: Settings) -> PriceBook:
    """Gece kurulumu: önce eğriler, sonra satırlar (A, yoksa eğri aralığında B, ikisi de yoksa C)."""
    ref_year = now.year
    book = PriceBook(disabled_models=set(prev.disabled_models) if prev else set())
    # emsal kuralları _is_comparable ile aynı: hedef yılı kendi yılı (span 0) olan RHD sentetik hedefe karşı elenir
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in pool:
        if r.get("brand_norm") and r.get("model_norm") and r.get("price_gbp") is not None and r.get("year") is not None \
                and YEAR_RANGE[0] <= r["year"] <= ref_year + 1 and (r.get("km") or 0) <= MAX_PLAUSIBLE_KM \
                and _is_comparable(_synthetic_target(r["brand_norm"], r["model_norm"], r["year"]), r, 0, False, now, s):
            groups[(r["brand_norm"], r["model_norm"])].append(r)
    sale_rows = defaultdict(list)  # (marka, model) -> temiz satışlar
    for i, x in enumerate(sales):
        if x.get("brand_norm") and x.get("model_norm") and x.get("year") and x.get("price_gbp") \
                and s.min_plausible_price_gbp <= x["price_gbp"] <= s.max_plausible_price_gbp:
            sale_rows[(x["brand_norm"], x["model_norm"])].append(
                {**x, "id": f"sale:{i}", "steering": "RHD", "ref_date": x.get("created_at")})

    # eğriler: model (B) ve marka (C)
    for (b, m), rows in groups.items():
        c = _fit_group(rows, sale_rows.get((b, m), []), b, m, ref_year, s, None)
        if c:
            book.curves[(b, m)] = c
    by_brand: dict[str, list[dict]] = defaultdict(list)
    brand_sales: dict[str, list[dict]] = defaultdict(list)
    for (b, m), rows in groups.items():
        by_brand[b] += rows
        brand_sales[b] += sale_rows.get((b, m), [])
    for b, rows in by_brand.items():
        c = _fit_group(rows, brand_sales[b], b, "*", ref_year, s, s.est_brand_max_sigma)
        if c:
            book.curves[(b, "*")] = c

    def finish(row: BookRow, sales_m: list[dict]) -> BookRow:
        sn, sm = _sales_info(sales_m, row.year)
        return replace(row, sales_n=sn, sales_median_gbp=sm)

    def put(b, m, v, y, value, lo, hi, ref_km, n, sellers, method, sales_m):
        old = prev.row(b, m, y, v) if prev else None
        val, status, cand, nights = next_status(old, value, n, sellers, s)
        row = BookRow(b, m, v, y, val, lo, hi, ref_km, n, sellers, method, status, cand, nights)
        book.rows[(b, m, v, y)] = finish(row, sales_m)

    for (b, m), rows in groups.items():
        curve, sales_m = book.curves.get((b, m)), sale_rows.get((b, m), [])
        for y in sorted({r["year"] for r in rows}):
            comps = [r for r in rows if abs(r["year"] - y) <= 1]
            res = _row_from_comps(comps, y, curve, s)
            if res:
                put(b, m, "", y, *res, "A", sales_m)
            cnt = Counter(variant_of(r) for r in comps)
            for v, c_n in cnt.items():
                if not v or c_n < VARIANT_MIN_ROWS:
                    continue
                vc = [r for r in comps if variant_of(r) == v]
                if len({_skey(r, 0) for r in vc}) < VARIANT_MIN_SELLERS:
                    continue
                vres = _row_from_comps(vc, y, curve, s)
                if vres:
                    put(b, m, v, y, *vres, "A", sales_m)
        if curve:  # B: eğri aralığında, oturmuş/şüpheli A satırı olmayan yıllar (ince A'nın yerine eğri değeri geçer)
            for y in range(curve.min_year, curve.max_year + 1):
                a_row = book.row(b, m, y)
                if a_row and a_row.status != STATUS_THIN:
                    continue
                ref_km = int(min(curve.km_per_year * max(ref_year - y, 0.5), curve.max_km))
                value = math.exp(curve.predict_ln(y, ref_km))
                put(b, m, "", y, value, value * math.exp(-s.est_z * curve.sigma), value * math.exp(s.est_z * curve.sigma),
                    ref_km, curve.n, curve.sellers, "B", sales_m)
                row = book.rows[(b, m, "", y)]
                if row.status == STATUS_SETTLED and curve.sellers < s.est_min_curve_sellers:
                    book.rows[(b, m, "", y)] = replace(row, status=STATUS_THIN)
    # C: ne A ne B satırı olan modeller için marka eğrisi (yalnızca bilgi)
    has_rows = {(k[0], k[1]) for k in book.rows}
    for (b, m), rows in groups.items():
        cb = book.curves.get((b, "*"))
        if cb is None or (b, m) in has_rows:
            continue
        for y in sorted({r["year"] for r in rows}):
            ref_km = int(min(cb.km_per_year * max(ref_year - y, 0.5), cb.max_km))
            value = math.exp(cb.predict_ln(y, ref_km))
            old = prev.row(b, m, y) if prev else None
            row = BookRow(b, m, "", y, value, value * math.exp(-s.est_z * cb.sigma), value * math.exp(s.est_z * cb.sigma),
                          ref_km, cb.n, cb.sellers, "C", STATUS_THIN)
            book.rows[(b, m, "", y)] = finish(row, sale_rows.get((b, m), []))
    return book


# --- yeni ilan için tahmin ve öz-kontrol ---
def estimate_from_book(listing: dict, book: PriceBook, s: Settings) -> Estimate | None:
    """İlan için B tahmini (yalnızca 🟠 için). Korkuluklar: LHD değil, km ve yıl biliniyor ve eğri aralığında,
    eğride ≥ est_min_curve_sellers satıcı, model kapalı değil. Uymazsa None."""
    b, m, year = listing.get("brand_norm"), listing.get("model_norm"), listing.get("year")
    c = book.curve(b, m) if b and m else None
    if c is None or (b, m) in book.disabled_models or listing.get("steering") == "LHD" or year is None:
        return None
    km = effective_km(listing)
    if km is None or not c.min_year - 1 <= year <= c.max_year + 1 or km > c.max_km * 1.1 or c.sellers < s.est_min_curve_sellers:
        return None
    value = math.exp(c.predict_ln(year, km))
    return Estimate(value, value * math.exp(-s.est_z * c.sigma), "B", c.n, c.sellers, c.sigma, book.row(b, m, year))


def self_check(book: PriceBook, recent: list[dict], s: Settings) -> tuple[float | None, set[tuple[str, str]]]:
    """Gece öz-kontrolü: son 30 günün yeni ilanlarını eğriyle karşılaştır. (genel ortanca hata, güvenilmez modeller)."""
    errs: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in recent:
        c = book.curve(r.get("brand_norm"), r.get("model_norm"))
        km, price = effective_km(r), r.get("price_gbp")
        if c is None or km is None or not price or price <= 0 or r.get("year") is None or r.get("steering") == "LHD":
            continue
        errs[(r["brand_norm"], r["model_norm"])].append(abs(math.log(price) - c.predict_ln(r["year"], km)))
    allerr = [e for v in errs.values() for e in v]
    overall = math.exp(statistics.median(allerr)) - 1 if allerr else None
    bad = {k for k, v in errs.items() if len(v) >= 5 and math.exp(statistics.median(v)) - 1 > s.book_self_check_max_error}
    return overall, bad
