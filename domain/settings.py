from pydantic import BaseModel


class Settings(BaseModel):
    strong_threshold: float = 0.20
    negotiable_threshold: float = 0.12
    quick_sale_factor: float = 0.95
    fixed_cost_gbp: float = 300.0  # her araçtan düşülen sabit masraf (bakım, evrak, ilan); mesajda yazılır
    min_strong_profit_gbp: float = 750.0  # 🟢 için masraf düşüldükten sonra asgari net kâr
    min_comparables_alert: int = 3
    comparable_window_days: int = 90
    absurd_price_ratio: float = 0.50
    low_confidence_min_profit: float = 0.30
    min_plausible_price_gbp: float = 500  # altı yanlış yazım/eksik rakam sayılır (arabanın fiyatı değil)
    max_plausible_price_gbp: float = 250_000
    small_pool_band: float = 0.5  # 8'den az emsalde medyanın %50'sinden az / 2 katından çok olanlar atılır
    km_high_ratio: float = 1.3  # ilanın km'si emsal medyanının bu katından fazlaysa 🟢 verilmez (ucuzluk yüksek kmdendir)
    km_high_margin: int = 10_000  # ...ve fark en az bu kadar km ise
    engine_tolerance_l: float = 0.3  # iki ilanın motor hacmi (litre) bundan fazla farklıysa emsal sayılmaz
    active_max_age_days: int = 60  # aktif ama 60 günden uzun süredir yayında duran ilan satılamamıştır: emsal sayılmaz
    min_distinct_sellers: int = 3  # emsaller en az bu kadar farklı satıcıdan (telefon) gelmeli; tek galerinin fiyatı piyasa olmaz
    social_max_age_hours: int = 48  # Instagram/Facebook gönderisi bundan eskiyse anlık 🟢 gitmez (satılmış olabilir)
    low_confidence_can_alert: bool = False  # 3-7 emsalli ilan 🟢 olmaz (en fazla 🟡): küçük havuzlarda sahte fırsat çok çıkıyor
