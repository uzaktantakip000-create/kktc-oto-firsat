-- Veri bakımı: şüpheli ilan silinmez, karantinaya alınır (emsalden ve bildirimden çıkar). Neden metin olarak yazılır.
ALTER TABLE listings ADD COLUMN IF NOT EXISTS karantina_nedeni TEXT;
CREATE INDEX IF NOT EXISTS listings_karantina_idx ON listings (karantina_nedeni) WHERE karantina_nedeni IS NOT NULL;
