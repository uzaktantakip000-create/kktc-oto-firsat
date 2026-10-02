-- 015: GitHub Actions IP'sinden 403/429 veren iki site, tarayıcı parmak izli istemciyle (infrastructure/http/browserlike.py, Scrapling) yeniden aktif.
UPDATE sources SET status='aktif', alert_level='yesil' WHERE url LIKE '%kibriscars.com%';
UPDATE sources SET status='aktif', alert_level='yesil' WHERE url LIKE '%mezunumsatiyorumkibris.com.tr%';
