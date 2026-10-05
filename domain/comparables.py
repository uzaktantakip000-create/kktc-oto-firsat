"""Emsal seçimi ve piyasa medyanı (DEGER_MOTORU.md bölüm 2)."""
import math
import statistics
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone

from domain.normalize import canon_fuel, canon_transmission
from domain.settings import Settings

LOW_KM_OLD_AGE = 10  # bu yaştan eski araçta...
LOW_KM_OLD_LIMIT = 15_000  # ...bu kadar kmden azı (ör. 2013 model 11.500 km = 115.000 yazılmış olabilir) şüpheli: bilinmiyor sayılır


def effective_km(row: dict, today: date | None = None) -> int | None:
    """İlandaki km makul mü? Eski araçta 1.000 km altı çoğunlukla 'bin' yazılmış/eksik rakam (370 = 370.000): bilinmiyor say.
    10+ yaşındaki araçta 15.000 km altı da şüpheli (düşük-km emsallerle kıyaslanıp sahte ucuz görünmesin; sahip kararı: km yanlışı fırsatı
    ENGELLEMEZ, yalnız uyarı olur). `today` verilirse (karar `now`'ı) sonuç saatten bağımsızdır; yıl dönümünde altın dosya/testler kaymasın."""
    km, year = row.get("km"), row.get("year")
    if km is not None and year is not None:
        age = (today or date.today()).year - year
        if (km < 1000 and age >= 2) or (km < LOW_KM_OLD_LIMIT and age >= LOW_KM_OLD_AGE):
            return None
    return km


def km_adjusted_price(price: float, target_km: int | None, row_km: int | None, per_10k: float) -> float:
    """Emsal fiyatını ilanın km'sine çeker: aradaki her 10.000 km için %`per_10k` (ln ölçeğinde; iki yönde aynı katsayı).
    Emsal ilandan AZ km'liyse fiyatı düşer, ÇOK km'liyse yükselir. İki km'den biri bilinmiyorsa fiyata dokunulmaz (km'siz ilan = bugünkü gibi)."""
    if target_km is None or row_km is None:
        return price
    return price * math.exp(-per_10k * (target_km - row_km) / 10_000)


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
    gbp_only: bool = False  # piyasa TL fiyatlı emsal OLMADAN kuruldu (mesajdaki emsaller de aynı havuzdan seçilir)
    sellers_n: int = 0  # emsallerdeki farklı satıcı sayısı (seller_key)
    median_year: float | None = None  # emsal yıllarının medyanı (±2 genişlemede hedefe göre kayıklığı ölçmek için)
    comparable_ids: tuple = ()  # piyasayı KURAN emsal ilanlarının kimlikleri (mesajdaki emsaller yalnız bunlardan seçilir)
    narrow_median_gbp: float | None = None  # genişleme birleşiminde (7c): dar (ilk geçerli) piyasanın medyanı; birleşme olmadıysa None
    wide_median_gbp: float | None = None  # ...ve geniş piyasanın kendi medyanı (median_gbp ikisinin küçüğüdür)
    near_n: int | None = None  # km'si ilana ±km_near_limit yakın (ya da km'si yazmayan) emsal sayısı; ilanın km'si bilinmiyorsa None


def _is_comparable(target: dict, row: dict, year_span: int, now: datetime, s: Settings, gbp_only: bool = False) -> bool:
    """Emsal kuralları (km HARİÇ: km bandı yok, fiyat _build_market'te ilanın km'sine çekilir)."""
    if row["id"] == target["id"] or row.get("duplicate_of"):
        return False
    if gbp_only and row.get("currency") == "TRY":
        return False  # TL ilanlar GBP komşularından ~%12-23 ucuz görünür (satıcı eski kurla fiyatlar / eski tarihli ilan bugünkü kurla çevrilir)
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
    # İlanın gerçek tarihi (yayın/arşiv); yoksa sisteme giriş tarihi. Geçmiş doldurma "bugün görüldü" sayılmaz.
    ref = row.get("ref_date") or row.get("first_seen_at")
    if ref and ref < now - timedelta(days=s.comparable_window_days):
        return False
    if row.get("is_active", True) and ref and ref < now - timedelta(days=s.active_max_age_days):
        return False  # 60+ gündür satılamayan ilan: istenen fiyat piyasa fiyatı değil
    return True


KKTCAR_HANDLE_PREFIX = "kktcar:"


def seller_key(row: dict, fallback=None) -> str:
    """Aynı satıcıyı tanımak için: telefon → KKTCar satıcı kimliği ("kktcar:" önekli) → ilan kendi başına bir satıcı sayılır.
    Önek şart: KibrisArabaAl yazar adları ve Instagram hesap adları da seller_handle'a yazılıyor, onlar tek başına satıcı anahtarı OLMAZ.
    TEK tanım: find_market, değer tablosu (price_book) ve altın dosya aynı fonksiyonu kullanır (satıcı sayısı her yerde aynı olsun)."""
    handle = row.get("seller_handle")
    if not row.get("seller_phone") and isinstance(handle, str) and handle.startswith(KKTCAR_HANDLE_PREFIX) and len(handle) > len(KKTCAR_HANDLE_PREFIX):
        return handle
    return row.get("seller_phone") or f"id:{row.get('id', fallback)}"


def _cap_per_seller(rows: list[dict], target: dict, cap: int, today: date) -> list[dict]:
    """Bir satıcı (galeri) piyasayı tek başına belirlemesin: aynı satıcının en fazla `cap` emsali kalır, hedefe yıl+km'ce en yakın olanlar.
    Sınır aykırı değer atılmadan ÖNCE uygulanır. Kimliksiz satıcılar (her ilan ayrı satıcı sayılır) etkilenmez. cap<=0: kapalı."""
    if cap <= 0:
        return rows
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(seller_key(r), []).append(r)
    if all(len(g) <= cap for g in groups.values()):
        return rows
    tk = effective_km(target, today)

    def closeness(r: dict) -> tuple:
        d = abs((r["year"] or 0) - (target["year"] or 0))
        rk = effective_km(r, today)
        d += abs(rk - tk) / 30_000 if tk and rk else 1.0  # km bilinmiyorsa kesin yakın sayılmaz (nearest_comparables ile aynı ölçü)
        return (d, str(r["id"]))  # eşitlikte kimlik: sonuç havuz sırasından bağımsız

    keep = {r["id"] for g in groups.values() for r in sorted(g, key=closeness)[:cap]}
    return [r for r in rows if r["id"] in keep]


def _drop_outliers(prices: list[float], band: float = 0.5) -> list[float]:
    if len(prices) < 8:
        # IQR için az veri var: medyanın yarısından azı / iki katından fazlası (yanlış yazım) atılır
        med = statistics.median(prices)
        return [p for p in prices if band * med <= p <= med / band]
    q = statistics.quantiles(prices, n=4)
    iqr = q[2] - q[0]
    lo, hi = q[0] - 1.5 * iqr, q[2] + 1.5 * iqr
    return [p for p in prices if lo <= p <= hi]


def _build_market(target: dict, pool: list[dict], span: int, gbp_only: bool, s: Settings, now: datetime) -> Market | None:
    """Tek bir genişleme adımı (yıl aralığı + TL ayrımı) için piyasa; geçerli değilse None.
    km bandı yok: her emsalin fiyatı ilanın km'sine çekilir (km_adjusted_price); aykırı atma, medyan, alt çeyrek ve aralık DÜZELTİLMİŞ
    fiyatlarla. km'si bilinmeyen ilanda (ya da emsalde) düzeltme yok."""
    today = now.date()
    rows = _cap_per_seller([r for r in pool if _is_comparable(target, r, span, now, s, gbp_only)],
                           target, s.max_comparables_per_seller, today)
    if len(rows) < s.min_comparables_alert:
        return None
    tk = effective_km(target, today)
    adj = {r["id"]: km_adjusted_price(r["price_gbp"], tk, effective_km(r, today), s.km_adjust_per_10k) for r in rows}
    prices = _drop_outliers(sorted(adj.values()), s.small_pool_band)
    if len(prices) < s.min_comparables_alert or (gbp_only and len(prices) < s.gbp_only_min_comparables):
        return None
    kept = set(prices)
    used = [r for r in rows if adj[r["id"]] in kept]
    sellers_n = len({seller_key(r) for r in used})
    if sellers_n < s.min_distinct_sellers:
        return None  # emsallerin çoğu tek satıcıdan: piyasa fiyatı sayılmaz, havuzu genişlet
    archived = sum(1 for r in used if not r.get("is_active", True)) / len(used)
    row_kms = [effective_km(r, today) for r in used]
    kms = [k for k in row_kms if k]
    median_km = int(statistics.median(kms)) if len(kms) >= 3 else None
    near_n = None if tk is None else sum(1 for k in row_kms if k is None or abs(k - tk) <= s.km_near_limit)
    p25 = statistics.quantiles(prices, n=4, method="inclusive")[0] if len(prices) >= 2 else min(prices)
    years = [r["year"] for r in used if r.get("year") is not None]
    return Market(len(prices), statistics.median(prices), min(prices), max(prices), span, archived, median_km, p25, gbp_only,
                  sellers_n, statistics.median(years) if years else None, tuple(r["id"] for r in used), near_n=near_n)


def _merge_conservative(first: Market, wide: Market) -> Market:
    """Dar (ilk geçerli, <8 emsal) ve geniş (≥8 emsal) piyasa birleşimi: n/aralık/satıcı/kimlikler geniş piyasadan; fiyat ölçüleri (medyan,
    alt çeyrek) ve km medyanı ikisinin KÜÇÜĞÜ (muhafazakâr: tutarsız p25-medyan çiftiyle 'en ucuz çeyrek' kapısı gevşemesin)."""
    p25s = [x for x in (first.p25_gbp, wide.p25_gbp) if x is not None]
    kms = [x for x in (first.median_km, wide.median_km) if x is not None]
    return replace(wide, median_gbp=min(first.median_gbp, wide.median_gbp), p25_gbp=min(p25s) if p25s else None,
                   median_km=min(kms) if kms else None, narrow_median_gbp=first.median_gbp, wide_median_gbp=wide.median_gbp)


def find_market(target: dict, pool: list[dict], settings: Settings | None = None, now: datetime | None = None) -> Market | None:
    """Önce ±1 yıl; emsal azsa yıl ±2. Hiç emsal yoksa None. km bandı yok: emsal fiyatları ilanın km'sine çekilir (_build_market).
    £ hedefte, her yıl aralığında önce TL fiyatlı emsal OLMADAN denenir (yalnız o havuz tek başına ≥ gbp_only_min_comparables emsal
    verirse kullanılır); olmazsa eskisi gibi TL dahil. Adım sırası: ±1 £ → ±1 hepsi → ±2 £ → ±2 hepsi.
    Genişleme (Adım 7c): ilk GEÇERLİ adım `widen_until_comparables`'a (8) ulaşıyorsa o seçilir; ulaşmıyorsa sıradaki adımlar denenir ve
    İLK ULAŞAN seçilir (ilk geçerli adım da varsa medyan, iki medyanın KÜÇÜĞÜdür: dar ve geniş piyasa aynı fikirde olmadıkça fırsat şişmesin).
    Hiçbir adım ulaşmazsa ilk geçerli adım (eski davranış). TL hedefte TL ayrımı yok."""
    s = settings or Settings()
    now = now or datetime.now(timezone.utc)
    gbp_first = target.get("currency") != "TRY" and any(r.get("currency") == "TRY" for r in pool)
    first: Market | None = None
    for span in (1, 2):
        for gbp_only in ((True, False) if gbp_first else (False,)):
            m = _build_market(target, pool, span, gbp_only, s, now)
            if m is None:
                continue
            if m.n >= s.widen_until_comparables:
                return m if first is None else _merge_conservative(first, m)
            if first is None:
                first = m
    return first


def nearest_comparables(target: dict, pool: list[dict], market: Market, k: int = 3,
                        settings: Settings | None = None, now: datetime | None = None) -> list[dict]:
    """Mesajda gösterilecek en yakın k emsal (yıl farkı + km farkı + piyasa aralığı içinde fiyat)."""
    s = settings or Settings()
    now = now or datetime.now(timezone.utc)
    if market.comparable_ids:  # piyasayı kuran emsaller (satıcı sınırı/aykırı atma sonrası): mesajda GÖSTERİLEN = SAYILAN
        ids = set(market.comparable_ids)
        rows = [r for r in pool if r["id"] in ids]
    else:  # piyasa kayıtlı özetten yeniden kurulmuş (kimlik yok): eski süzme
        rows = [r for r in pool if _is_comparable(target, r, market.year_span, now, s, market.gbp_only)
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
