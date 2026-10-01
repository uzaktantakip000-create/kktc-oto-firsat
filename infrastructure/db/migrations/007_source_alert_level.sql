-- Yeni kaynak kalite kapısı: golge = bildirim yok (sadece toplanır/ölçülür), sari = sadece günlük özet (🟡), yesil = tam yetki
ALTER TABLE sources ADD COLUMN IF NOT EXISTS alert_level TEXT NOT NULL DEFAULT 'yesil';
-- kibrisarabaal.com: robots.txt izinli (Crawl-delay 5), ilan sayfalarında yapılandırılmış veri var. Tohum kaydı varsa güncellenir.
UPDATE sources SET url='https://kibrisarabaal.com/', name='KibrisArabaAl', status='aktif', priority=1, alert_level='golge'
 WHERE url='https://www.kibrisarabaal.com';
INSERT INTO sources (platform,name,url,kind,region,status,priority,discovered_by,alert_level)
SELECT 'web','KibrisArabaAl','https://kibrisarabaal.com/','ilan_sitesi','KKTC','aktif',1,'arama','golge'
 WHERE NOT EXISTS (SELECT 1 FROM sources WHERE url='https://kibrisarabaal.com/');
