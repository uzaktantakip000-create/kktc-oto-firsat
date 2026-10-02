-- 018: Bi'Arabacık (biarabacik.com) kaynak listesine eklendi (kullanıcı verdi, 02.10.2026).
-- 'aday' = toplayıcı yazılana kadar taranmaz. Sitemap'te ~960 araç ilanı var (adres sonu numara); ilan sayfasında Marka/Seri/Yıl/Km/Vites/Yakıt/Motor/Kimden alanları var.
INSERT INTO sources (platform, name, url, kind, region, status, priority, discovered_by, alert_level)
SELECT 'web', 'BiArabacik', 'https://biarabacik.com/', 'ilan_sitesi', 'KKTC', 'aday', 2, 'kullanici', 'yesil'
WHERE NOT EXISTS (SELECT 1 FROM sources WHERE url ILIKE '%biarabacik.com%');
