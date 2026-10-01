-- Kaynak seviyeleri sadeleşti: taranan kaynaklar anlık bildirim verir ("yesil"); "sari" = günlük özete düşmüş (otomatik/elle).
-- Güven: sosyal medya 🟢'leri yayımdan önce yapay zekâyla bağımsız okutulur (application/llm_reader.py); yeni kaynak mesajda 🆕 etiketi alır.
UPDATE sources SET alert_level = 'yesil' WHERE status IN ('aktif', 'deneme') AND alert_level = 'golge';
-- Az emsalli (3-7) değerlendirmeler artık en fazla 🟡 (Settings.low_confidence_can_alert=False): mevcut kayıtlar da düşürülür.
UPDATE evaluations SET tier = 'pazarlik' WHERE tier = 'guclu' AND confidence = 'dusuk';
