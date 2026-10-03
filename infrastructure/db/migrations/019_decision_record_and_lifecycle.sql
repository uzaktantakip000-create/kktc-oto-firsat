-- 019: Karar kaydı + ilan yaşam döngüsü. YALNIZ EKLEME: boş (NULL) bırakılabilen sütunlar. Silme, kısıt, varsayılan değer, tetikleyici,
-- indeks YOK. Eski satırlar NULL kalır; bu sütunları okuyan/yazan kod (Adım 5b/5c) bu migration uygulandıktan SONRA yayına girer.
-- Tekrar çalıştırılabilir (IF NOT EXISTS). Bilgi: bu tablolarda satır güvenliği (RLS) açık; yeni sütunlar da onu devralır.

-- Değerlendirme: kararın neden öyle çıktığını sonradan açıklayabilmek için (kural sürümü, kanıt özeti).
ALTER TABLE evaluations
    ADD COLUMN IF NOT EXISTS rules_version    TEXT,            -- kararı veren kural sürümü (domain/settings.py RULES_VERSION)
    ADD COLUMN IF NOT EXISTS saticilar_n      INTEGER,         -- emsallerdeki farklı satıcı sayısı
    ADD COLUMN IF NOT EXISTS alt_ceyrek_gbp   DECIMAL(10,2),   -- emsal fiyatlarının alt çeyreği
    ADD COLUMN IF NOT EXISTS tablo_degeri_gbp DECIMAL(10,2),   -- değer tablosunun bu ilan için değeri (varsa)
    ADD COLUMN IF NOT EXISTS nedenler         TEXT[],          -- 🟢 olmamasının/düşürülmesinin nedenleri (gaps)
    ADD COLUMN IF NOT EXISTS evidence         JSONB;           -- kalan kanıt ayrıntıları (medyan km, yöntem, tahmin alt sınırı ...)

-- Bildirim: hangi kararın sonucu gitti ve ne fiyatla gitti ("fiyat düştü" bildirimi için, 019b'de indeks buna bağlanır).
ALTER TABLE alerts
    ADD COLUMN IF NOT EXISTS evaluation_id    UUID,            -- bildirimi doğuran değerlendirme (bağlantı kısıtı bilerek YOK)
    ADD COLUMN IF NOT EXISTS kind             TEXT,            -- NULL = ilk bildirim; 'fiyat_dustu' (019b'den sonra)
    ADD COLUMN IF NOT EXISTS fiyat_gonderimde DECIMAL(10,2);   -- gönderildiği andaki fiyat (GBP)

-- İlan: gerçek satış/kaybolma zamanı (aylık "ilanlar kaç günde kayboluyor" ölçümü ve emsal yaşı için).
ALTER TABLE listings
    ADD COLUMN IF NOT EXISTS sold_at         TIMESTAMPTZ,      -- kaynağın bildirdiği satıldı/arşive alınma zamanı (güvenilir olduğu yerde)
    ADD COLUMN IF NOT EXISTS inactive_at     TIMESTAMPTZ,      -- sistemin ilanı pasifleştirdiği an
    ADD COLUMN IF NOT EXISTS inactive_reason TEXT,             -- 'satildi' | 'kaldirildi' | 'belirsiz'
    ADD COLUMN IF NOT EXISTS last_alive_at   TIMESTAMPTZ;      -- kaynakta canlı görüldüğü son an

-- GERİ ALMA (gerekirse, yalnız bu sütunlardaki veri gider):
--   ALTER TABLE evaluations DROP COLUMN IF EXISTS rules_version, DROP COLUMN IF EXISTS saticilar_n, DROP COLUMN IF EXISTS alt_ceyrek_gbp,
--       DROP COLUMN IF EXISTS tablo_degeri_gbp, DROP COLUMN IF EXISTS nedenler, DROP COLUMN IF EXISTS evidence;
--   ALTER TABLE alerts DROP COLUMN IF EXISTS evaluation_id, DROP COLUMN IF EXISTS kind, DROP COLUMN IF EXISTS fiyat_gonderimde;
--   ALTER TABLE listings DROP COLUMN IF EXISTS sold_at, DROP COLUMN IF EXISTS inactive_at, DROP COLUMN IF EXISTS inactive_reason,
--       DROP COLUMN IF EXISTS last_alive_at;
