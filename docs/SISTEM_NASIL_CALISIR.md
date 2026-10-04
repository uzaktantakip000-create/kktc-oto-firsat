# Sistem nasıl çalışıyor? (sade anlatım)

Bu belge kodu bilmeyen birine sistemi anlatmak içindir. Teknik ayrıntı için `KKTC_OTO_SISTEM_SPEC.md` var.

## Tek cümle
KKTC'deki ikinci el araç ilanlarını her gün gezen, her arabayı benzerleriyle kıyaslayan ve "benzerlerinden %20 ucuz, alıp satınca kâr kalır" diyorsa sahibine Telegram'dan haber veren bir robot. Robot **yalnızca haber verir**: kimseye mesaj atmaz, teklif vermez, satın almaz.

## Parçalar (sırayla)
1. **İlan siteleri.** Ana kaynaklar KibrisArabaAl ve KKTCar. Küçükler: KKTCarabam, Mezunum. Instagram ve Facebook şu an kapalı.
2. **Toplayıcı robot.** GitHub'da çalışan bir program. cron-job.org adlı dış servis onu 15 dakikada bir uyandırır. Siteyi baştan okumaz (aşağıya bak).
3. **Veritabanı (Supabase).** Okunan her ilan bir satır: marka, model, yıl, km, fiyat, şehir, satıcı, link.
4. **Değerlendirme ("beyin").** Yeni ilana benzer araçları bulur (aynı marka ve model, yıl ±1, benzer km), onların ortalama fiyatını hesaplar. Alış fiyatı bu ortalamadan en az %20 kâr bırakıyorsa ilan "fırsat adayı" olur. Aday, kurallardan ve kontrollerden geçerse gönderilir.
5. **Telegram.** Fırsat varsa sahibine mesaj gider. Mesajdaki 👍/👎 düğmeleri ve komutlarla sahip geri bildirim verir; bir ilanı bota iletirse aynı kurallarla incelenir.

## Her turda ne taranıyor?
Siteyi her seferinde baştan okumaz.
- **KibrisArabaAl ve KKTCar:** sitenin "ilan listesi" dosyasını (site haritası) her turda okur; bu çok hafif bir listedir. Listede olup veritabanında olmayan **yeni** ilanları açar (tur başına en çok 10 tane KibrisArabaAl, 25 tane KKTCar). Ayrıca daha önce kaydettiği ilanlardan en eski kontrol edilenleri (tur başına yaklaşık 25 tane) yeniden açar: fiyat düştü mü, satıldı mı? Listeden kaybolan ilan pasif olur.
- **KKTCarabam:** yalnızca ilk sayfayı (en yeni ~18 ilan) okur; yaklaşık 2–6 saatte bir.
- **Mezunum:** ilk 2 sayfa, yeni ilanlar için yapay zekâ yardımıyla.

## Hangi güvenlikler var?
- Siteler nazik hızda okunur (KibrisArabaAl'da 5 saniye aralık).
- Bir site bir anda "tüm ilanlar kalktı" derse veya okuma bozulursa robot durur ve alarm verir; yanlış toplu silme yapmaz.
- Şüpheli ilanlar (bozuk fiyat, peşinat/taksit, hasarlı, çok eksik bilgi) fırsat sayılmaz.
- Yapay zekâ yalnızca okuyucu ve kontrolcüdür; tek başına fırsat ilan etmez.
- Sistem durursa veya testler bozulursa sahibe Telegram'dan mesaj gelir.

## Fırsat çeşitleri
- 🟢 **Güçlü:** en az 8 benzer araç var, %20+ kâr, tüm kontroller temiz.
- 🟠 **Tahmini:** az benzer var; değer tablosuna göre çok ucuz (şimdilik gölgede, kapalı).
- 🟡 **Pazarlık:** olabilir ama kanıt zayıf; tek tek mesaj atılmaz.

## Bilinen sınırlar
- Sadece ilan fiyatını görür; gerçek satış fiyatını bilmez.
- Az ilanı olan model/yıl için kıyas yapamaz; bu ilanlar hiç değerlendirilmez.
- İlandaki bilgi yanlışsa (km, fiyat) karar da yanlış çıkabilir; bu yüzden veri doğruluğu ayrıca denetleniyor.
