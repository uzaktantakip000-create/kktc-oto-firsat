-- 014: yeni ilan siteleri + araba markası altındaki motosiklet/kamyon/tekne temizliği.
-- Siteler: robots.txt ve kullanım şartları otomatik okumayı yasaklamıyor (kibriscars.com: robots.txt boş; pazarkibris.com: yalnızca /admin /users /api /login... kapalı;
-- sahibindenarabakibris.com: Disallow boş). Sahibin kararı: yeni kaynaklar ilk günden anlık bildirir (🆕 etiketiyle).
INSERT INTO sources (platform,name,url,kind,region,status,priority,discovered_by,alert_level) VALUES
('web','KibrisCars','https://kibriscars.com/','ilan_sitesi','KKTC','aktif',2,'arama','yesil'),
('web','PazarKibris','https://pazarkibris.com/','ilan_sitesi','KKTC','aday',3,'arama','yesil')  -- deneme: 80 ilandan yalnız 4'ünde fiyat; şimdilik taranmaz
ON CONFLICT (url) DO NOTHING;
-- Sahibinden Araba Kibris tohum kaydı 002'de 'aday' olarak var: etkinleştir.
UPDATE sources SET status='aktif', alert_level='yesil', name='SahibindenArabaKibris'
 WHERE url='https://sahibindenarabakibris.com' AND status='aday';
INSERT INTO sources (platform,name,url,kind,region,status,priority,discovered_by,alert_level)
SELECT 'web','SahibindenArabaKibris','https://sahibindenarabakibris.com','ilan_sitesi','KKTC','aktif',2,'arama','yesil'
 WHERE NOT EXISTS (SELECT 1 FROM sources WHERE url LIKE '%sahibindenarabakibris.com%');

-- Motosiklet / kamyon / tekne: yalnızca brand_norm düzelir (ham 'brand' sütunu aynen kalır, geri alınabilir).
-- Liste, canlı veritabanındaki gerçek (brand_norm, model_norm) çiftlerinden çıkarıldı; domain/normalize.py reclassify_non_car ile aynı kural.
UPDATE listings SET brand_norm='Motosiklet' WHERE (brand_norm, model_norm) IN (
 ('BMW','c'),('BMW','f'),('BMW','f750'),('BMW','g'),('BMW','r'),
 ('Honda','400x'),('Honda','cb'),('Honda','cbr'),('Honda','crf1100l'),('Honda','forza'),('Honda','joker'),('Honda','nc'),
 ('Honda','nx500'),('Honda','pcx125'),('Honda','spacy'),('Honda','today'),('Honda','transalp'),('Honda','transalp750'),('Honda','xl'),
 ('Peugeot','speedfight'),('Suzuki','adress'),('Suzuki','burgman'),('Suzuki','v'));
-- BMW "M 1000 RR" motosiklettir; "M Serisi M4" araba (ikisinin model_norm'u de 'm')
UPDATE listings SET brand_norm='Motosiklet' WHERE brand_norm='BMW' AND model_norm='m' AND model ~* '^m ?1000';
UPDATE listings SET brand_norm='Kamyon & Kamyonet' WHERE (brand_norm, model_norm) IN (
 ('Isuzu','elf'),('Isuzu','n75.190'),('Mercedes-Benz','actros'),('Mercedes-Benz','atego'),('Mitsubishi','canter'),('Mitsubishi','fuso'));
UPDATE listings SET brand_norm='Balikci Teknesi' WHERE brand_norm='Honda' AND model_norm='tekne';
-- Yer tutucu fiyat (£1): gece bakımı karantinaya alır (domain/quality.py 'fiyat_yer_tutucu'); burada ek işlem gerekmez.
