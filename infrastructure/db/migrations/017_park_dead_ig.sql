-- 017: hiç gönderi getirmeyen iki Instagram hesabını parka al (her tur Apify'a boşuna sorgu gidiyordu).
-- 02.10.2026 ölçümü: araba.kibris ve araba.kktc 'deneme' durumundaydı, bugüne kadar 0 gönderi/ilan geldi (hesap kapalı ya da yeniden adlandırılmış olabilir).
-- 'aday' = taranmaz; yeni kullanıcı adı bulunursa /kaynak ile geri açılır. Satırlar silinmez.
UPDATE sources SET status='aday'
 WHERE platform='instagram'
   AND url IN ('https://www.instagram.com/araba.kibris/', 'https://www.instagram.com/araba.kktc/')
   AND status <> 'aday';
