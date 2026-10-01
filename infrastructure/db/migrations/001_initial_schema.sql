CREATE TABLE sources (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform        TEXT NOT NULL,      -- instagram / facebook / telegram / web / marketplace
    name            TEXT,
    url             TEXT UNIQUE NOT NULL,
    kind            TEXT,               -- ilan_sayfasi / galeri / grup / kanal / ilan_sitesi
    region          TEXT,               -- KKTC / Guney / karisik
    status          TEXT NOT NULL,      -- aday / deneme / aktif / pasif / disari / erisim_reddediyor
    priority        INTEGER,
    discovered_by   TEXT,               -- seed / hashtag / arama / iz_surme
    last_checked_at TIMESTAMPTZ,
    last_post_at    TIMESTAMPTZ,
    listings_7d     INTEGER DEFAULT 0,
    unique_7d       INTEGER DEFAULT 0,  -- başka kaynakta olmayan ilan sayısı
    deals_30d       INTEGER DEFAULT 0,
    cursor          TEXT,               -- son görülen ID / zaman damgası
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE listings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id       UUID REFERENCES sources(id),
    source_item_id  TEXT NOT NULL,      -- post shortcode / ilan ID
    url             TEXT,
    posted_at       TIMESTAMPTZ,
    raw_text        TEXT,
    photo_urls      TEXT[],
    photo_hash      TEXT,
    brand           TEXT,
    model           TEXT,
    variant         TEXT,               -- "A200d AMG", "R-Line" vb.
    year            INTEGER,
    km              INTEGER,
    fuel            TEXT,
    transmission    TEXT,
    steering        TEXT,               -- RHD / LHD / bilinmiyor
    location        TEXT,
    price_raw       TEXT,
    price_amount    DECIMAL(12,2),
    currency        TEXT,               -- GBP / TRY / EUR / USD
    currency_guess  BOOLEAN DEFAULT FALSE, -- para birimi yazmıyordu, tahmin edildi
    price_gbp       DECIMAL(10,2),
    seller_type     TEXT,               -- bireysel / galeri / bilinmiyor
    seller_handle   TEXT,
    seller_phone    TEXT,               -- normalize: 90533xxxxxxx
    negotiable      BOOLEAN,
    urgency_signals TEXT[],
    extraction_by   TEXT,               -- parser / haiku
    first_seen_at   TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at    TIMESTAMPTZ DEFAULT NOW(),
    is_active       BOOLEAN DEFAULT TRUE,
    duplicate_of    UUID REFERENCES listings(id),
    UNIQUE (source_id, source_item_id)
);

CREATE TABLE listing_history (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id  UUID REFERENCES listings(id),
    changed_at  TIMESTAMPTZ DEFAULT NOW(),
    field       TEXT,
    old_value   TEXT,
    new_value   TEXT
);

CREATE TABLE evaluations (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id        UUID REFERENCES listings(id),
    evaluated_at      TIMESTAMPTZ DEFAULT NOW(),
    comparables_n     INTEGER,
    market_median_gbp DECIMAL(10,2),
    exit_price_gbp    DECIMAL(10,2),
    profit_gbp        DECIMAL(10,2),
    profit_pct        DECIMAL(5,2),
    confidence        TEXT,             -- yuksek / orta / dusuk
    tier              TEXT,             -- guclu / pazarlik / yok
    red_flags         TEXT[],
    sonnet_note       TEXT
);

CREATE TABLE alerts (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id   UUID REFERENCES listings(id),
    tier         TEXT,
    sent_at      TIMESTAMPTZ DEFAULT NOW(),
    telegram_msg_id TEXT
);

CREATE TABLE feedback (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id  UUID REFERENCES listings(id),
    action      TEXT,   -- ilgilendim / pas / aradim / gordum / aldim / sattim / sahte / yanlis_fiyat
    amount_gbp  DECIMAL(10,2),
    note        TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- RLS açık (anon erişimi kapalı); sistem service_role ile bağlanır
ALTER TABLE sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE listings ENABLE ROW LEVEL SECURITY;
ALTER TABLE listing_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE evaluations ENABLE ROW LEVEL SECURITY;
ALTER TABLE alerts ENABLE ROW LEVEL SECURITY;
ALTER TABLE feedback ENABLE ROW LEVEL SECURITY;
