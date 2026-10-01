-- Gönderilemeyen bildirimin sonradan aynı içerikle yeniden denenebilmesi için emsal ayrıntıları saklanır
ALTER TABLE evaluations
    ADD COLUMN IF NOT EXISTS market_low_gbp  DECIMAL(10,2),
    ADD COLUMN IF NOT EXISTS market_high_gbp DECIMAL(10,2),
    ADD COLUMN IF NOT EXISTS year_span       INTEGER,
    ADD COLUMN IF NOT EXISTS archived_share  DECIMAL(4,3);
