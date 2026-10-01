ALTER TABLE listings ADD COLUMN brand_norm TEXT, ADD COLUMN model_norm TEXT;
CREATE INDEX idx_listings_norm ON listings (brand_norm, model_norm, year);

CREATE TABLE subscribers (
    chat_id     TEXT PRIMARY KEY,
    name        TEXT,
    status      TEXT NOT NULL DEFAULT 'bekliyor',  -- bekliyor / onayli / reddedildi / durduruldu
    is_owner    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE subscribers ENABLE ROW LEVEL SECURITY;

CREATE TABLE bot_state (key TEXT PRIMARY KEY, value TEXT);
ALTER TABLE bot_state ENABLE ROW LEVEL SECURITY;

ALTER TABLE alerts ADD COLUMN chat_id TEXT;
CREATE UNIQUE INDEX idx_alerts_unique ON alerts (listing_id, chat_id, tier);
