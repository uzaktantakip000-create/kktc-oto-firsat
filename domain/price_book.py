"""Değer tablosu (price book): her marka + model (+ varyant) + yıl için oturmuş piyasa değeri ve model değer eğrisi.
Saf mantık (ağ/DB yok, yalnızca standart kütüphane). Her gece application/price_book_job.py kurar; yeni ilan
application/evaluate.assess_listing içinde estimate_from_book ile karşılaştırılır (🟠 tahmini fırsat).

Yöntemler:
  A = doğrudan emsal: aynı model, yıl ±1, fiyatlar curve ile aynı yıl/km'ye çekilip medyan (galeri başına ≤2 ilan)
  B = model eğrisi: ln(fiyat) = a + b_age·yaş + b_km·km/10000 (ridge, saf Python), ≥8 ilan, ≥3 farklı yıl
  C = marka eğrisi: yalnızca /fiyat'ta "yaklaşık" bilgi; asla bildirim üretmez
"""
from dataclasses import dataclass, field
from datetime import datetime

from domain.settings import Settings

STATUS_SETTLED = "oturmus"
STATUS_THIN = "ince"
STATUS_SUSPECT = "supheli"


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
        raise NotImplementedError


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


def variant_of(row: dict) -> str:
    """Ham model metninden/motor hacminden varyant: 'C Serisi C 180' -> '180', '320i' -> '320', diğer: '1.6'; yoksa ''."""
    raise NotImplementedError


def fit_curve(rows: list[dict], weights: list[float] | None, brand_norm: str, model_norm: str, ref_year: int,
              s: Settings, max_sigma: float | None = None) -> Curve | None:
    """Ridge ile ln(fiyat) ~ yaş + km/10k. Koşullar sağlanmazsa None (≥8 km'li ilan, ≥3 yıl, σ ≤ max_sigma)."""
    raise NotImplementedError


def next_status(old: BookRow | None, new_value: float, n: int, sellers: int, s: Settings) -> tuple[float, str, float | None, int]:
    """(kabul edilen değer, durum, bekleyen değer, bekleme gecesi). %15 kuralı + 2 gece kuralı."""
    raise NotImplementedError


def build_book(pool: list[dict], sales: list[dict], now: datetime, prev: PriceBook | None, s: Settings) -> PriceBook:
    """Gece kurulumu: önce eğriler, sonra satırlar (A, yoksa eğri aralığında B, ikisi de yoksa C)."""
    raise NotImplementedError


def estimate_from_book(listing: dict, book: PriceBook, s: Settings) -> Estimate | None:
    """İlan için B tahmini (yalnızca 🟠 için). Korkuluklar: LHD değil, km ve yıl biliniyor ve eğri aralığında,
    eğride ≥ est_min_curve_sellers satıcı, model kapalı değil. Uymazsa None."""
    raise NotImplementedError


def self_check(book: PriceBook, recent: list[dict], s: Settings) -> tuple[float | None, set[tuple[str, str]]]:
    """Gece öz-kontrolü: son 30 günün yeni ilanlarını eğriyle karşılaştır. (genel ortanca hata, güvenilmez modeller)."""
    raise NotImplementedError
