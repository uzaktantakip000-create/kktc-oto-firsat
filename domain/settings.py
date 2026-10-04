from pydantic import BaseModel

RULES_VERSION = "2026-10-04c"  # değerleme kuralları değişince artır: son 7 günün (bildirimsiz) değerlendirmeleri yeniden yapılır


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
    gbp_only_min_comparables: int = 8  # £ hedefte TL emsalsiz havuz tek başına ≥ bu kadar emsal verirse TL'siz piyasa kullanılır (TL ilanlar ~%12-23 ucuz görünür)
    min_plausible_price_gbp: float = 500  # altı yanlış yazım/eksik rakam sayılır (arabanın fiyatı değil)
    max_plausible_price_gbp: float = 250_000
    small_pool_band: float = 0.5  # 8'den az emsalde medyanın %50'sinden az / 2 katından çok olanlar atılır
    km_high_ratio: float = 1.3  # ilanın km'si emsal medyanının bu katından fazlaysa 🟢 verilmez (ucuzluk yüksek kmdendir)
    km_high_margin: int = 10_000  # ...ve fark en az bu kadar km ise
    engine_tolerance_l: float = 0.3  # iki ilanın motor hacmi (litre) bundan fazla farklıysa emsal sayılmaz
    active_max_age_days: int = 60  # aktif ama 60 günden uzun süredir yayında duran ilan satılamamıştır: emsal sayılmaz
    min_distinct_sellers: int = 3  # emsaller en az bu kadar farklı satıcıdan (telefon) gelmeli; tek galerinin fiyatı piyasa olmaz
    max_comparables_per_seller: int = 2  # bir satıcının (telefon ya da KKTCar satıcı kimliği) piyasaya katacağı en fazla emsal; 0 = sınırsız
    social_max_age_hours: int = 48  # Instagram/Facebook gönderisi bundan eskiyse anlık 🟢 gitmez (satılmış olabilir)
    low_confidence_can_alert: bool = False  # 3-7 emsalli ilan 🟢 olmaz (en fazla 🟡): küçük havuzlarda sahte fırsat çok çıkıyor
    # --- kullanıcı kararları (Telegram komutlarıyla değişir, application/settings_store.py) ---
    max_buy_gbp: float | None = None  # bundan pahalı ilan için bildirim yok (/butce)
    blocked_brands: list[str] = []  # /istemiyorum <marka>
    muted_models: list[str] = []  # "Marka|model": 3 kez "pas" denen model sadece özete düşer
    blocked_phones: list[str] = []  # "kusurlu/sahte" denen ilanların satıcıları
    # --- değer tablosu ve 🟠 tahmini fırsat (domain/price_book.py) ---
    estimated_alerts: bool = True  # /tahmini ac|kapat
    est_z: float = 1.53  # alt sınır = değer·exp(−z·σ); geriye dönük testte %80 aralığın gerçek karşılığı
    est_min_discount_to_lower: float = 0.80  # 🟠: fiyat ≤ alt sınırın bu katı (≈ değerden %30+ ucuz)
    est_min_value_ratio: float = 0.40  # fiyat değerin bundan azıysa yazım hatası say
    est_a_agree_ratio: float = 0.85  # emsal varsa fiyat ≤ emsal medyanının bu katı (eğri emsalle çelişmesin)
    est_min_curve_sellers: int = 5
    est_max_sigma: float = 0.30  # eğri dağınıklığı (ln) bundan büyükse eğri yok
    est_brand_max_sigma: float = 0.40
    est_curve_min_rows: int = 8
    est_curve_min_years: int = 3
    ridge_lambda: float = 0.5
    owner_sale_weight: float = 3.0  # sahibin girdiği gerçek satış eğride kaç ilan sayılır
    book_max_per_seller: int = 2  # bir satıcı bir satıra en fazla bu kadar ilanla girer (galeri ağırlığı)
    book_settled_min_n: int = 5
    book_settled_min_sellers: int = 3
    book_change_limit: float = 0.15  # gece değişim bundan büyükse satır şüpheli olur
    book_confirm_tolerance: float = 0.07  # bekleyen değer ertesi gece bu kadar yakın kalırsa onay sayılır
    book_confirm_nights: int = 2
    book_self_check_max_error: float = 0.25  # öz-kontrolde model ortanca hatası bundan büyükse 🟠 kapanır
    est_burst_limit: int = 15  # tek turda bundan fazla 🟠 çıkarsa arıza say: tek özet mesaj
