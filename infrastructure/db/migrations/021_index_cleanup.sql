-- 021: indeks temizliği (sistem sağlık denetimi, 21.11.2026; sahip onayı aynı gün). Tekrar çalıştırılabilir. Veri değişmez.
-- 1) evaluations(listing_id) üzerinde aynı iki indeks vardı: idx_eval_listing (kayıtsız, elle açılmış) ve idx_evaluations_listing (migration 004).
--    Kopya yazma maliyeti getirir; migration 004'ün adı kalır, kopya düşer.
-- 2) listings.duplicate_of yabancı anahtarı indekssizdi (Supabase uyarısı unindexed_foreign_keys): mükerrer sorguları ve silmeler için indeks.
-- GERİ ALMA: CREATE INDEX idx_eval_listing ON evaluations (listing_id); DROP INDEX IF EXISTS idx_listings_duplicate_of;
DROP INDEX IF EXISTS idx_eval_listing;
CREATE INDEX IF NOT EXISTS idx_listings_duplicate_of ON listings (duplicate_of) WHERE duplicate_of IS NOT NULL;
