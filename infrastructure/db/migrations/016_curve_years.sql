-- Eğrinin yıl başına satır sayısı ("2012:5,2013:3"): veri boşluğu olan yıllarda tahmin üretilmesin (domain/price_book.py dense_at)
ALTER TABLE price_curves ADD COLUMN IF NOT EXISTS year_counts TEXT;
