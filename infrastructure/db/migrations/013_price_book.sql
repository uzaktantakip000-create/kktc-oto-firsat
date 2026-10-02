-- Değer tablosu: her gece kurulur (application/price_book_job.py); /fiyat ve 🟠 tahmini fırsat bunu okur.
CREATE TABLE IF NOT EXISTS price_book (
    brand_norm        TEXT NOT NULL,
    model_norm        TEXT NOT NULL,
    variant           TEXT NOT NULL DEFAULT '',   -- '' = birleşik satır; '180', '320', '1.6' ...
    year              INT  NOT NULL,
    value_gbp         DOUBLE PRECISION NOT NULL,  -- kabul edilmiş değer (ref_km için)
    low_gbp           DOUBLE PRECISION,
    high_gbp          DOUBLE PRECISION,
    ref_km            INT,
    n                 INT NOT NULL,
    sellers           INT NOT NULL,
    method            TEXT NOT NULL,              -- A emsal, B model eğrisi, C marka eğrisi
    status            TEXT NOT NULL,              -- oturmus / ince / supheli
    cand_value        DOUBLE PRECISION,           -- şüpheliyken bekleyen yeni değer
    cand_nights       INT NOT NULL DEFAULT 0,
    sales_n           INT NOT NULL DEFAULT 0,
    sales_median_gbp  DOUBLE PRECISION,
    built_at          TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (brand_norm, model_norm, variant, year)
);

-- Model (B) ve marka (C, model_norm='*') değer eğrileri: ln(fiyat) = a + b_age·(yaş−mean_age) + b_km·(km10−mean_km10)
CREATE TABLE IF NOT EXISTS price_curves (
    brand_norm   TEXT NOT NULL,
    model_norm   TEXT NOT NULL,
    a            DOUBLE PRECISION NOT NULL,
    b_age        DOUBLE PRECISION NOT NULL,
    b_km         DOUBLE PRECISION NOT NULL,
    sigma        DOUBLE PRECISION NOT NULL,
    n            INT NOT NULL,
    years        INT NOT NULL,
    sellers      INT NOT NULL,
    min_year     INT NOT NULL,
    max_year     INT NOT NULL,
    max_km       INT NOT NULL,
    ref_year     INT NOT NULL,
    mean_age     DOUBLE PRECISION NOT NULL DEFAULT 0,
    mean_km10    DOUBLE PRECISION NOT NULL DEFAULT 0,
    km_per_year  DOUBLE PRECISION NOT NULL DEFAULT 15000,
    built_at     TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (brand_norm, model_norm)
);

-- Sahibin /satti ile girdiği gerçek satışlar: eğride ağırlıklı (x3) kullanılır, emsal medyanına girmez
CREATE TABLE IF NOT EXISTS owner_sales (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brand_norm    TEXT NOT NULL,
    model_norm    TEXT NOT NULL,
    brand         TEXT,
    model         TEXT,
    year          INT NOT NULL,
    km            INT,
    price_amount  NUMERIC,
    currency      TEXT,
    price_gbp     DOUBLE PRECISION NOT NULL,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS owner_sales_model_idx ON owner_sales (brand_norm, model_norm);

ALTER TABLE price_book ENABLE ROW LEVEL SECURITY;
ALTER TABLE price_curves ENABLE ROW LEVEL SECURITY;
ALTER TABLE owner_sales ENABLE ROW LEVEL SECURITY;

-- Değerlendirmenin hangi yöntemle yapıldığı: A (benzer ilanlar) / B (değer eğrisi)
ALTER TABLE evaluations ADD COLUMN IF NOT EXISTS method TEXT;
