"""Emsal seçimi ve piyasa medyanı (DEGER_MOTORU.md bölüm 2)."""
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from domain.normalize import canon_fuel, canon_transmission
from domain.settings import Settings

KM_BANDS = [(0, 50_000), (50_000, 100_000), (100_000, 150_000), (150_000, 10**9)]


def effective_km(row: dict, today: date | None = None) -> int | None:
    """İlandaki km makul mü? Eski araçta 1.000 km altı çoğunlukla 'bin' yazılmış/eksik rakam (370 = 370.000): bilinmiyor say.
    `today` verilirse (karar `now`'ı) sonuç saatten bağımsızdır; yıl dönümünde altın dosya/testler kaymasın."""
    km, year = row.get("km"), row.get("year")
    if km is not None and km < 1000 and year is not None and year <= (today or date.today()).year - 2:
        return None
    return km


def km_band(km: int | None) -> int | None:
    if km is None:
        return None
    return next(i for i, (lo, hi) in enumerate(KM_BANDS) if lo <= km < hi)


@dataclass(frozen=True)
class Market:
    n: int
    median_gbp: float
    low_gbp: float
    high_gbp: float
    year_span: int
    archived_share: float  # emsalin ne kadarı arşiv/satıldı sayfalarından (daha az güvenilir)
    median_km: int | None = None  # km'si bilinen emsallerin medyanı (en az 3 emsalde)
    p25_gbp: float | None = None  # emsal fiyatlarının alt çeyreği: 🟢 için ilan bunun altında olmalı


def _is_comparable(target: dict, row: dict, year_span: int, widen_km: bool, now: datetime, s: Settings) -> bool:
    if row["id"] == target["id"] or row.get("duplicate_of"):
        return False
    if row["price_gbp"] is None or row.get("currency_guess"):
        return False
    # Piyasa fiyatı = satışa en yakın veri: aktif ilan taze olmalı, pasif ilan ancak "satıldı" ise sayılır.
    # (Arşivde duran ve satıldığı belli olmayan ilanın "son fiyatı" medyanı yukarı çeker.)
    if not row.get("is_active", True) and "satildi" not in (row.get("urgency_signals") or []):
        return False
    if not s.min_plausible_price_gbp <= row["price_gbp"] <= s.max_plausible_price_gbp:
        return False
    if row["brand_norm"] != target["brand_norm"] or row["model_norm"] != target["model_norm"]:
        return False
    if target["year"] is None or row["year"] is None or abs(row["year"] - target["year"]) > year_span:
        return False
    # RHD ve LHD asla karışmaz; bilinmeyen direksiyon sağ sayılır (KKTC'de çoğunluk)
    t_st, r_st = target.get("steering") or "RHD", row.get("steering") or "RHD"
    if t_st != r_st:
        return False
    if (target.get("transmission") and row.get("transmission")
            and canon_transmission(target["transmission"]) != canon_transmission(row["transmission"])):
        return False  # "düz" ile "manuel" aynı vites (kaynaklar farklı yazıyor)
    if target.get("fuel") and row.get("fuel") and canon_fuel(target["fuel"]) != canon_fuel(row["fuel"]):
        return False  # "elektrik" ile "elektrikli" aynı yakıt
    te, re_ = target.get("engine_l"), row.get("engine_l")
    if te is not None and re_ is not None and round(abs(float(te) - float(re_)), 1) > s.engine_tolerance_l:
        return False  # 316i ile 340i gibi farklı motorlar aynı havuzda karışmaz (bilinmeyen motor elenmez)
    today = now.date()
    tb, rb = km_band(effective_km(target, today)), km_band(effective_km(row, today))
    if tb is not None and rb is not None and abs(tb - rb) > (1 if widen_km else 0):
        return False
    # İlanın gerçek tarihi (yayın/arşiv); yoksa sisteme giriş tarihi. Geçmiş doldurma "bugün görüldü" sayılmaz.
    ref = row.get("ref_date") or row.get("first_seen_at")
    if ref and ref < now - timedelta(days=s.comparable_window_days):
        return False
    if row.get("is_active", True) and ref and ref < now - timedelta(days=s.active_max_age_days):
        return False  # 60+ gündür satılamayan ilan: istenen fiyat piyasa fiyatı değil
    return True


def seller_key(row: dict) -> str:
    """Aynı satıcıyı tanımak için telefon; telefon yoksa ilan kendi başına bir satıcı sayılır."""
    return row.get("seller_phone") or f"id:{row['id']}"


def _drop_outliers(prices: list[float], band: float = 0.5) -> list[float]:
    if len(prices) < 8:
        # IQR için az veri var: medyanın yarısından azı / iki katından fazlası (yanlış yazım) atılır
        med = statistics.median(prices)
        return [p for p in prices if band * med <= p <= med / band]
    q = statistics.quantiles(prices, n=4)
    iqr = q[2] - q[0]
    lo, hi = q[0] - 1.5 * iqr, q[2] + 1.5 * iqr
    return [p for p in prices if lo <= p <= hi]


def find_market(target: dict, pool: list[dict], settings: Settings | None = None, now: datetime | None = None) -> Market | None:
    """Önce ±1 yıl ve aynı km bandı; emsal azsa yıl ±2 ve komşu bant. Hiç emsal yoksa None."""
    s = settings or Settings()
    now = now or datetime.now(timezone.utc)
    for span, widen in ((1, False), (2, True)):
        rows = [r for r in pool if _is_comparable(target, r, span, widen, now, s)]
        if len(rows) >= s.min_comparables_alert:
            prices = _drop_outliers(sorted(r["price_gbp"] for r in rows), s.small_pool_band)
            if len(prices) < s.min_comparables_alert:
                continue
            kept = set(prices)
            used = [r for r in rows if r["price_gbp"] in kept]
            if len({seller_key(r) for r in used}) < s.min_distinct_sellers:
                continue  # emsallerin çoğu tek satıcıdan: piyasa fiyatı sayılmaz, havuzu genişlet
            archived = sum(1 for r in used if not r.get("is_active", True)) / len(used)
            kms = [k for k in (effective_km(r, now.date()) for r in used) if k]
            median_km = int(statistics.median(kms)) if len(kms) >= 3 else None
            p25 = statistics.quantiles(prices, n=4, method="inclusive")[0] if len(prices) >= 2 else min(prices)
            return Market(len(prices), statistics.median(prices), min(prices), max(prices), span, archived, median_km, p25)
    return None


def nearest_comparables(target: dict, pool: list[dict], market: Market, k: int = 3,
                        settings: Settings | None = None, now: datetime | None = None) -> list[dict]:
    """Mesajda gösterilecek en yakın k emsal (yıl farkı + km farkı + piyasa aralığı içinde fiyat)."""
    s = settings or Settings()
    now = now or datetime.now(timezone.utc)
    widen = market.year_span >= 2
    rows = [r for r in pool if _is_comparable(target, r, market.year_span, widen, now, s)
            and market.low_gbp <= r["price_gbp"] <= market.high_gbp]

    def distance(r: dict) -> float:
        d = abs((r["year"] or 0) - (target["year"] or 0))
        tk, rk = effective_km(target, now.date()), effective_km(r, now.date())
        if tk and rk:
            d += abs(rk - tk) / 30_000
        else:
            d += 1.0  # km bilinmiyorsa kesin yakın sayılmaz
        return d + abs(r["price_gbp"] - market.median_gbp) / max(market.median_gbp, 1) * 0.1

    return sorted(rows, key=distance)[:k]
