-- Herkese açık Kuzey Kıbrıs araç grupları: gölge modda toplanır (bildirim yok). Üyelere özel / Güney grupları açılmaz.
UPDATE sources SET status='deneme', alert_level='golge'
WHERE platform='facebook' AND status='aday' AND url IN (
  'https://www.facebook.com/groups/469402498541872/',
  'https://www.facebook.com/groups/405189333280604/',
  'https://www.facebook.com/groups/682061759485628/',
  'https://www.facebook.com/groups/460146661032088/',
  'https://www.facebook.com/groups/1797997293768755/',
  'https://www.facebook.com/groups/Kibris.Arabam/');
