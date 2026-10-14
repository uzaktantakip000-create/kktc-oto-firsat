-- 020: blocked_sellers tablosunda satır güvenliği (RLS) açılır. Migration 010 bunu unutmuştu: tablo Supabase'in herkese açık REST
-- arayüzünden (anon anahtarıyla) okunabilir/yazılabilir görünüyordu (Supabase güvenlik e-postası "rls_disabled_in_public", 11.10.2026).
-- Diğer 11 tabloda RLS zaten açık. Politika YOK = anon/authenticated hiçbir satırı göremez; bot `postgres` rolüyle bağlanır (RLS'i aşar),
-- davranış değişmez. Tekrar çalıştırılabilir. GERİ ALMA: ALTER TABLE blocked_sellers DISABLE ROW LEVEL SECURITY;
ALTER TABLE blocked_sellers ENABLE ROW LEVEL SECURITY;
