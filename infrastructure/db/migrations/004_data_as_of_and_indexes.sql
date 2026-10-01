-- İlanın verisinin gerçek tarihi (yayın tarihi yoksa sitemap lastmod). Emsal yaşı bununla ölçülür.
ALTER TABLE listings ADD COLUMN IF NOT EXISTS data_as_of TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_evaluations_listing ON evaluations (listing_id);
CREATE INDEX IF NOT EXISTS idx_listings_first_seen ON listings (first_seen_at);
CREATE INDEX IF NOT EXISTS idx_listings_phone ON listings (seller_phone) WHERE seller_phone IS NOT NULL;
