# KAYNAK HARİTASI v2

Hipotezin yarısı doğrulandı. Web siteleri gerçekten galeri ağırlıklı ve en iyi işe yaradıkları yer fiyat referansı. Ancak "fırsatların ağırlıkla sosyal kanallarda çıktığı" iddiasını destekleyen bağımsız bir veri bulunamadı. Bu iddia şu an yalnızca sitelerin kendi pazarlama metinlerine ve rehber yazılarına dayanıyor. Ayrıca en kalabalık sosyal kanallar (Facebook grupları ve Instagram sayfaları) aynı zamanda otomatik erişimin en zor ve hukuken en riskli olduğu yerler.

## Özet (TL;DR)
- **Hipotez: kısmen doğrulandı.** Galeri ağırlığı ve "web = fiyat referansı" rolü kaynaklarla uyumlu. kktcarabam.com vitrini galerilerle dolu, GalerimPlus "doğrulanmış galeri ağı"nı öne çıkarıyor. Buna karşılık "piyasa altı ilan bireyden gelir" iddiası ölçülmüş bir veriye değil, mezunumsatiyorumkibris.com.tr'nin kendi metnine ("Mezuniyet sebebiyle adadan ayrılacak öğrenciler, araçlarını piyasa değerinin altında ve hızlı satmak isteyebilirler") ve expat rehberlerine dayanıyor. Bunu kendi ölçümünle doğrulaman gerekiyor.
- **Otomasyona en uygun ve en değerli 3 kanal:** (1) **KKTCar.com**: 518 güncel ilan, 7 günlük "hâlâ satılık mı?" tazelik kontrolü, WhatsApp iletişimi ve bot engeli görülmedi. (2) **kibrisarabaal.com**: ardışık ilan ID'leri (#2630), "Acil Satılık" etiketi ve "index, follow" meta etiketi var. (3) **Mezunum Satıyorum**: 132+ aktif otomobil ilanı ve doğrudan öğrenci/bireysel satıcı segmenti. Sosyal tarafta en büyük havuzlar Instagram'daki @kktc_arabam (24K takipçi) ve @kktc.arabalari (22K), Rusça konuşan kitle için Telegram'daki "North Cyprus Cars" (10.000+ katılımcı). Ancak bunlar "izlenebilir" kaynaklar, "otomatik çekilebilir" değil.
- **En büyük engel:** Facebook gruplarının içeriği login duvarının arkasında. Meta v. Bright Data (23 Ocak 2024, Hakim Edward M. Chen, N.D. Cal.) yalnızca **oturum açılmadan** görülebilen public veriyi korudu. Quinn Emanuel'in analizine göre karar "does not opine on whether scraping data behind a log-in screen would violate Meta's terms of service"; yani grup içeriği için bu koruma pratikte işe yaramıyor. Ayrıca iki büyük yerli site (kibrisaraba.com, galerimplus.com) ve illakiburada.com otomatik erişimi reddediyor. İlanlardaki telefon numaraları da 89/2007 sayılı KKTC Kişisel Verilerin Korunması Yasası kapsamında kişisel veri.

## A. Web Siteleri

Yöntem notu: Tüm kontroller 30 Eylül 2026'da yapıldı. "Otomatik erişimi reddediyor" ifadesi, araştırma aracımızın robots.txt nedeniyle sayfayı çekmeyi reddettiği durumları gösteriyor. robots.txt dosyalarının kendisi ve Kullanım Koşulları metinleri okunamadı, bu yüzden hiçbir site için bot yasağı maddesi **alıntılanamadı**. Gizli fiyat oranı, ana sayfada veya listede görünen ilk 5–6 ilandan hesaplandı. Bu örneklem çok küçük ve sadece yön göstermek için verildi.

| Site | Aktif mi (Eyl 2026) | robots / ToS | API / RSS / entegrasyon | İlan sayısı (sitenin sayacı) | Bireysel % | Gizli fiyat % (örneklem) | Günlük yeni ilan | Not |
|---|---|---|---|---|---|---|---|---|
| kktcarabam.com | Evet. En yeni ID 263645 (Toyota Prado, 54.000 GBP) | robots metni okunamadı, fetch'e izin verdi. Footer'da "Kullanım Koşulları… kabul etmiş sayılırlar" ibaresi var, bot maddesi bulunamadı | Bulunamadı. Kurumsal üyelik sözleşmesi var | "2. El Araçlar (20060)", "0 Km (105)", "Yedek Parça (34105)" | Bulunamadı | 3/6 (%50) "0 TL" gösteriyor | Ölçülemedi (tek zaman noktası) | ID'ler ardışık ama yedek parça dahil tüm kategoriler aynı sayacı paylaşıyor. Footer: "Copyright © 2007-2025 … Troya Trading Ltd" |\[1\]
| galerimplus.com | Evet (yeni özellikler duyuruluyor)\[2\] | Otomatik erişimi reddediyor. ToS bulunamadı | Bulunamadı. "Kirpi" AI asistanı ve "Arabam Kaç Para?" aracı var, canlı ihale "çok yakında"\[2\] | "22.000+ Toplam İlan Verisi", Kirpi'de "20,000+ ilan", başka bir snippet'te "10.000+ aktif ilan" ve "500+ doğrulanmış galeri"\[2\] | Bulunamadı | Bir podcast'e göre premium ilanlarda "Fiyat Sorunuz" yaygın | Bulunamadı | Sitenin kendi rakamları kendi içinde çelişiyor ("120+ galeri" ve "500+ galeri")\[2\] |\[2\]\[3\]
| kibrisaraba.com | Muhtemelen aktif, doğrulanamadı | Otomatik erişimi reddediyor | Bulunamadı. "ARABAM KAÇ LİRA" değerleme linki var\[4\] | "binlerce" (sayı bulunamadı) | Bulunamadı | Bulunamadı | Bulunamadı | Kendini "KKTC'nin en büyük…" diye tanımlıyor (pazarlama iddiası) |\[5\]
| kibrisarabaal.com | Evet. Meta last-modified 2026-09-30, en yeni ID #2630 (2019 VW Golf, 16.100 £)\[6\] | Meta "index, follow".\[7\] ToS sayfası var ama okunamadı\[6\] | Bulunamadı | "1,952+ aktif araç ilanı"\[6\] | Bulunamadı | 0/5 (ticker'daki yeni ilanlar)\[6\] | Ölçülemedi. ID'ler ardışık (#2626–2630), iki tarihli ölçüm gerekiyor\[6\] | 1.952 aktif ilan ile en yüksek ID olan 2.630 birlikte düşünüldüğünde sayaç şüpheli görünüyor (bkz. Çelişen Bilgiler) |\[6\]
| illakiburada.com | Belirsiz. "Son 48 saatte eklenen ilanlar" bölümü var\[8\] | Otomatik erişimi reddediyor | Bulunamadı | Otomobil: "203 ilan" (tarihsiz snippet)\[9\] | Bulunamadı | Bulunamadı | Bulunamadı | Çok kategorili pazar yeri, araç hacmi küçük\[10\] |
| sahibindenarabakibris.com | Evet, liste erişilebilir (ilan tarihleri görünmüyor)\[7\] | Meta "index, follow" | WordPress/Elementor altyapısı. Standart WP sitemap/RSS olması muhtemel ama doğrulanmadı\[7\] | "287 Sonuçlar"\[7\] | Adı "sahibinden" ama oran bulunamadı | 3/6 (%50) "Fiyat için iletişime geç"\[7\] | Ölçülemedi. Post ID'leri (23687, 23676…) WP'nin genel sayacı, ilan hızı için kullanılamaz\[7\] | Başlıklarda "NAKİTTE ÇOK UYGUNA" gibi ifadeler geçiyor\[7\] |
| mezunumsatiyorumkibris.com.tr (.net de var) | Evet. En yeni ilanlar 29 Eylül (Suzuki Swift £4.000) ve 28 Eylül\[11\] | robots/ToS okunamadı, fetch'e izin verdi | iOS uygulaması var ("KKTC Bit Pazarı / Mezunum Satıyorum"), API bulunamadı\[11\]\[12\] | Otomobil: "132+ aktif ilan"\[11\] | Büyük olasılıkla yüksek (öğrenci odaklı) ama ölçülmedi\[13\] | 0/2 (bir ilanda 9.500₺ gibi gerçek dışı görünen bir fiyat var)\[11\] | ≈1–2/gün (üstteki ilanlar 28–29 Eylül tarihli, kaba tahmin) | Görsel yollarındaki ID'ler (2231199628→2231199633) ardışık görünüyor, ikinci ölçümle hız çıkarılabilir\[11\] |\[13\]\[14\]
| birarabakibris.com | **Doğrulanamadı**: 30 Eylül 2026'da 500 sunucu hatası verdi | Bulunamadı | Bulunamadı | Bulunamadı | Bulunamadı | Bulunamadı | Bulunamadı | "bireysel satıcılara ve galerilere tamamen ÜCRETSİZ" diyor\[15\] |
| biarabacik.com | Site açık, son ilan tarihi doğrulanamadı | ToS sayfası var ama okunamadı | Her ilanda **AI fiyat tahmini** var ("yapay zeka ile analiz edilerek KKTC'deki benzer ilanlara göre tahmin edilmiştir. Gerçek satış fiyatı değildir!")\[16\] | Bulunamadı. Kategori sayıları çok düşük (Mini: 1, Tofaş: 0)\[17\]\[18\] | Bulunamadı | Bulunamadı | Bulunamadı | Logosu 2025/04'te yüklenmiş.\[16\] Operatör: Daddiez Brand Yazılım Ticaret Ltd. Şti. |\[19\]
| kktcar.com | Evet. En yeni ilan "about 2 hours ago"\[20\] | Meta "index, follow", fetch'e izin verdi. ToS bulunamadı\[20\] | Mobil uygulamada **yeni ilan bildirimi**, "Deals" bölümü, "Car Value" ve "Price Guide" var. API bulunamadı\[20\] | "Current listings 518", "Total listings published 4,000+"\[20\] | Bulunamadı | 0/6\[20\] | ≈4–6/gün (son 6 ilan yaklaşık 1 gün içinde eklenmiş)\[20\] | Slug ID'ler rastgele (ör. "4bur3"), ardışık değil. Satıcı 7 gün yanıt vermezse ilan otomatik kalkıyor, bu da veri tazeliği için en iyi seçenek\[20\] |\[20\]
| pazarkibris.com | Blog aktif (2026 tarihli yazı var)\[21\] | Bulunamadı | Bulunamadı | Bulunamadı | Bulunamadı | Bulunamadı | Bulunamadı | Kendi 2026 yazısına göre ilanlar "usually shared through social media groups, Facebook Marketplace, and local listing platforms"\[21\] |
| whatsonintrnc.com/carsforsale | Sayfa var ama içerik çekilemedi (Wix)\[22\] | Bulunamadı | Bulunamadı | Bulunamadı. Bir ilan URL'sinde ID 4444 var\[23\] | Sayfa "Private sellers" diyor\[22\] | Bulunamadı | Bulunamadı | "TRNC Cars launching soon – free listings" duyurusu var. "300,000 monthly Google impressions" (pazarlama iddiası)\[24\] |

**kibrisaraba.com ile kibrisarabaal.com aynı işletme mi?** Aynı işletme olduklarına dair bir kanıt yok ve iletişim bilgileri tamamen farklı. kibrisarabaal.com'un bilgileri: "Kıbrıs Araba Al", +90 533 000 00 01, info@kibrisarabaal.com, Hamitköy/Lefkoşa. Kendini "yerli ve bağımsız araç ilan platformu" olarak tanımlıyor.\[6\] kibrisaraba.com'un bilgileri: +90 533 000 00 02, info@kibrisaraba.com. LinkedIn'de "Deniz Şahin – Director" görünüyor ve Twitter hesabı Aralık 2009'dan beri var.\[25\]\[26\]\[27\] Şirket sicili veya WHOIS verisi bulunamadığı için aynı işletme olma ihtimali kesin olarak dışlanamıyor. Yine de kibrisarabaal'ın isim benzerliğinden SEO avantajı sağlamaya çalışan yeni bir site olması en olası açıklama.

**Ek not:** Türkiye'deki arabam.com'un bir KKTC sayfası var (21 Eylül 2026'da güncellenmiş) ama sayfa "Toplam 1 sayfa" gösteriyor.\[28\] Hacmi ihmal edilebilir düzeyde.

## B. Sosyal Kanallar

| Kanal | Tür | Erişim | Büyüklük | Son aktivite | Dil | Fiyat formatı |
|---|---|---|---|---|---|---|
| @kktc_arabam | Instagram sayfası (ilanlar admin üzerinden) | Public profil | 24K takipçi, 1.580 gönderi\[29\] | Snippet tarihsiz | TR + "English Support"\[29\] | İlanlar WhatsApp üzerinden toplanıp yayınlanıyor. 2022 tarihli bir gönderide caption'da "FİYAT DÜŞTÜ… Marka/Model/Yıl" şablonu var (güncellik şüpheli)\[29\]\[30\] |
| @kktc.arabalari ("SARI SAYFA") | Instagram sayfası | Public profil | 22K takipçi, 941 gönderi\[31\] | Snippet tarihsiz | TR + EN\[31\] | Bulunamadı. "İLAN İÇİN TIKLAYINIZ" linki var\[31\] |
| @kktc.arabalar (alternatif yazım) | — | — | Bulunamadı | — | — | — |
| @alsatkibriss ("AL-SAT KIBRIS") | Instagram sayfası | Public profil | 3.233 takipçi, 522 gönderi\[32\] | Snippet tarihsiz | TR | İlan paylaşımı için bio'da telefon numaraları var\[32\] |
| FB "KKTC ARABA PAZARI" /groups/469402498541872 | FB grubu (seninkiyle aynı adı taşıyan **farklı** bir grup) | Login duvarı var | Bulunamadı | Bulunamadı | TR | Bulunamadı |
| FB /groups/539378399490159 (north cyprus cars and bikes for sale) | FB grubu | Login duvarı var\[33\]\[34\] | Bulunamadı | Bulunamadı | EN | Bulunamadı |
| FB /groups/1158632710849743, /1797997293768755, /KKTCARABAM, /372771683412684 | FB grupları | Doğrulanamadı | Bulunamadı | Bulunamadı | — | — |
| FB Marketplace (Kyrenia) | Marketplace | Kısmen görülebiliyor | Sayı yok | Aktif. Listede Kyrenia/Karmi ilanları var\[35\] | Karışık | Varsayılan 40 mil yarıçap Güney'i de kapsıyor (Larnaca, Strovolos, € fiyatlar), bu yüzden KKTC filtresi şart\[35\] |
| "North Cyprus Cars" t.me/cypruscars | Telegram sohbeti | Açık link | "10,000+ participants" (cyprus-faq, ≈9 ay önce güncellenmiş)\[36\] | Kaynağa göre "daily ads"\[36\] | RU | Sohbet içinde serbest metin |
| "Cyprus Car" t.me/cypruscar | Telegram kanalı + kapalı sohbet | Kanal public, sohbet bot davetiyle\[37\] | Bulunamadı | Bulunamadı | RU | Moderasyonlu ilan kanalı. Kuzey mi Güney mi odaklı olduğu belirsiz\[37\] |
| Nijeryalı / İranlı / Pakistanlı / Arap / Zimbabveli öğrenci toplulukları ve İngiliz expat WhatsApp grupları | — | — | **Bulunamadı** | — | — | — |
| Kibkom forumu "MOTORS" | Forum | Public | "This category has no forums"\[38\] | Araç bölümü fiilen boş görünüyor | EN | — |

Yorum: Instagram sayfaları kendi başına birer "aracı". İlanlar admin'e WhatsApp'tan iletiliyor ve admin yayınlıyor.\[29\] Bu yüzden bu sayfalar bireysel satıcıyla galeriyi aynı akışta karıştırıyor, "acil bireysel satıcı" sinyali seyreliyor. Facebook grup ve üye sayıları login duvarı nedeniyle arama snippet'lerinden görülemedi.

## C. Hız ve Rekabet

- **"İyi fiyatlı ilan kaç saatte gider?"** Somut, saat bazlı bir gözlem **bulunamadı**. Bulunan en yakın ifadeler niteliksel. Akıllı Galeri'nin 2026 blog yazısı Honda Fit, Mazda 2 ve Toyota Vitz için "galerinizde asla uzun süre beklemez" diyor.\[39\] PazarKıbrıs'ın 2026 yazısı ise gerekenden yüksek fiyatlı araçların "may stay online for a long time" olduğunu söylüyor.\[21\]
- **Mevcut alarm ve fırsat servisleri:** Doğrudan rakip var. **kktcilan.com** "Aradığın ilanlar, favorilerindeki fiyat düşüşleri ve yapay zeka seçimi avantajlı ilanlar anında bildirilsin" vaat ediyor.\[40\] Bu, senin ürününle neredeyse aynı değer önerisi, ama hacmi ve gerçekten çalışıp çalışmadığı doğrulanmadı. **KKTCar** uygulaması yeni ilan bildirimi, "Deals" bölümü ve değerleme aracı sunuyor.\[20\] **GalerimPlus** tarafında Kirpi "Ortalama piyasa fiyatı" veriyor (örnek: "£24.500 seviyesinde"), "Arabam Kaç Para?" ise tahmini fiyat aralığı gösteriyor.\[2\] Ayrıca kullanıcının bütçe girdiği ve "120+ galeri ve bireysel satıcı"nın teklif verdiği bir ters talep modeli var.\[2\] **BiArabacık** her ilana AI fiyat tahmini ekliyor.\[41\]\[42\] Bu da ilan bazında "piyasa altı" sinyalini zaten gösterdiği anlamına geliyor. **kibrisaraba.com**'da "ARABAM KAÇ LİRA",\[4\] **kibrisarabaal.com**'da "Acil Satılık" filtresi var.\[5\]\[6\] Akıllı Galeri bir "bulut tabanlı araç stok ve galeri yönetim yazılımı".\[39\] İlan sitelerine toplu aktarım yaptığına dair kanıt bulunamadı.
- **Galericilerin sahibinden ilanları nasıl takip ettiği** ("nakit alırız" ilanları, alıcı grupları): Kaynak **bulunamadı**.
- **2026 mezuniyet dönemi ve Eylül öğrenci girişi:** Araç piyasasına dair tarihli bir haber veya gözlem **bulunamadı**. Mezunum Satıyorum'da 28–29 Eylül tarihli ilanlar olması yalnızca kanalın canlı olduğunu gösteriyor.\[11\]

## D. Yasal/ToS Çerçevesi

- **Meta ve otomatik veri toplama:** Meta'nın koşulları kullanıcıların platformlardan "access[ing] or collect[ing] data… using automated means" yapmasını yasaklıyor.\[43\] *Meta v. Bright Data* davasında (N.D. Cal., Hakim Edward M. Chen) mahkeme 23 Ocak 2024 tarihli 37 sayfalık özet kararla şuna hükmetti: Meta'nın koşulları, MediaPost'un (25 Ocak 2024) aktardığı ifadeyle, "do not apply to and prohibit Bright Data's scraping of publicly available data while logged off". Bright Data oturum kapalıyken "user" sayılmaz. Quinn Emanuel'in müvekkil bülteninin ifadesiyle "even if Meta's terms prohibited scraping of data while logged-out of the platforms, once Bright Data terminated its accounts with Meta, Meta could no longer bind Bright Data to that prohibition"; hesap kapatıldıktan sonra da geçerli olmak üzere yazılmış "survival" maddesi uygulanamaz. Meta ardından 23 Şubat 2024'te kalan haksız müdahale (tortious interference) iddiasını "without prejudice" geri çekti ve Law360'ın (26 Şubat 2024) ve Eric Goldman Technology & Marketing Law Blog'un (28 Şubat 2024) aktardığına göre "waived its right to appeal". X Corp.'un Bright Data'ya açtığı dava da 9 Mayıs 2024'te Kıdemli Bölge Hakimi William Alsup (N.D. Cal., No. 3:23-cv-03698; 733 F.Supp.3d 832) tarafından, Skadden'in özetine göre Telif Hakkı Yasası'nın X'in sözleşme ihlali iddialarını önlediği (preemption) gerekçesiyle reddedildi. **Güncellik notu:** Bu kararlar 2024 tarihli. 2025–2026'da bunları değiştiren bir karar bulunamadı ama aranamadı da. Quinn Emanuel'in yorumu: karar public veri kazımasını "per se legal" yapmıyor ve başka hukuki iddialar hâlâ mümkün.\[44\] **Senin sistemine etkisi:** Facebook grup içeriği login gerektiriyor, dolayısıyla bu içtihadın koruduğu "logged-off public data" kapsamına girmiyor; Quinn Emanuel'e göre karar "does not opine on whether scraping data behind a log-in screen would violate Meta's terms of service" ve Meta login'li kazımaya dair kanıt sunmadığı için bu soru açık kaldı. Instagram'daki public profiller ve Marketplace'in login'siz görülebilen kısmı gri bölgede kalıyor. Apify gibi üçüncü parti servisler bu riski sana devrediyor.
- **KKTC kişisel veri mevzuatı:** 89/2007 sayılı Kişisel Verilerin Korunması Yasası 7 Nisan 2007'de yürürlüğe girdi. Kişisel Verileri Koruma Kurulu Mart 2019'da çalışmaya başladı. Yasa, verileri "tamamen ya da kısmen otomatik" yollarla işleyen gerçek ve tüzel kişilere uygulanıyor, toplama ve saklamadan aktarıma kadar tüm aşamaları kapsıyor.\[45\] 22 Ağustos 2024 tarihli Resmi Gazete'de "Ücret ve Ruhsat Tüzüğü" yayımlandı. Kurul, "KKTC dışına kişisel veri transferini gerçekleştiren tüm kurum ve kuruluşlar"ı yasal yükümlülüklerini yerine getirmeye çağırıyor.\[46\] Bir de "Bilgilendirme Yükümlülüğünün Yerine Getirilmesinde Uyulacak Usul ve Esaslar Tüzüğü" var.\[47\] İlan sahibinin telefon numarası kişisel veri. Sunucun veya Telegram altyapın KKTC dışındaysa yurt dışı aktarım hükümleri de devreye girebilir.

## Çelişen Bilgiler
- **GalerimPlus'ın rakamları:** "22.000+ toplam ilan verisi", "20,000+ ilan", "10.000+ aktif ilan", "500+ doğrulanmış galeri" ve "120+ galeri".\[2\] Hepsi sitenin kendi pazarlama metinleri ve birbiriyle uyuşmuyor. Bağımsız bir sayım yok.
- **kibrisarabaal.com:** "1,952+ aktif ilan" deniyor ama en yüksek ilan ID'si 2630.\[6\] Bu, şimdiye kadar yayınlanan ilanların yaklaşık %74'ünün hâlâ aktif olduğu anlamına gelir ki bu makul değil. Üstelik aynı sitenin metni hem "yüzlerce" hem "binlerce" ilan diyor.\[6\]
- **KKTCar:** Sitede "Current listings 518" yazıyor, arama başlığında ise "2,200+ Listings".\[20\] Tazelik kontrolü eklenince eski ilanların temizlenmiş olması muhtemel.
- **kktcarabam.com:** Sayaçta "2. El Araçlar (20060)" yazıyor,\[1\] cyprus-faq ise "more than 10,000 listings" diyor.\[36\] Sayaç motosiklet ve ticari araçlar dahil tüm ikinci el kategorilerini, muhtemelen pasif ilanları da kapsıyor.
- **@kktc.arabalari:** 2022 tarihli bir üçüncü parti kopya "2k Followers, 589 Posts" diyordu, güncel profil 22K.\[30\]\[31\] Kaynak güvenilir değil, bu yüzden büyüme oranı çıkarılmamalı.

## Cevaplanamayanlar (saha veya ölçüm gerektirenler)
1. Tüm sitelerin robots.txt dosyası ve Kullanım Koşulları'ndaki bot/scraping maddesi. Tarayıcıyla elle okunmalı, öncelik sırası: kibrisarabaal.com/sayfa/kullanim-kosullari/, biarabacik.com/kullanim-kosullari-13, kktcarabam üyelik sözleşmeleri.
2. Günlük yeni ilan hızı. kktcarabam, kibrisarabaal ve Mezunum Satıyorum için en yüksek ID'yi 7 gün arayla iki kez kaydedip (ID₂ − ID₁) / gün hesabı yapılmalı. kktcarabam'da sayaç kategoriler arasında paylaşıldığı için sadece otomobil ilanları filtrelenmeli.
3. Bireysel/galeri oranı ve gizli fiyat oranı. Her sitede en az 100 ilanlık örneklem alınıp kodlanmalı.
4. Beş Facebook grubunun public/private durumu, üye sayısı ve son gönderi tarihi. Normal bir kullanıcı hesabıyla elle kontrol edilmeli.
5. Öğrenci toplulukları (Nijerya, İran, Pakistan, Arap ülkeleri, Zimbabve) ve İngiliz expat'lar için WhatsApp/Telegram grupları. Üniversite öğrenci derneklerine sorularak bulunmalı.
6. "İyi fiyatlı ilan kaç saatte satılır?" sorusu. Birkaç hafta boyunca ilanların yayından kalkma süresi takip edilerek ölçülmeli (KKTCar'daki 7 günlük tazelik kuralı bu ölçümü kolaylaştırıyor).
7. kktcilan.com'un alarm özelliğinin gerçekten çalışıp çalışmadığı ve kaç kullanıcısı olduğu.
8. Mayıs–Temmuz 2026 mezuniyet dönemindeki ilan artışı. Ancak bundan sonraki mezuniyet döneminde ölçülebilir.

## Sources

1. [KKTCarabam.com | Kıbrıs'ın Otomobil İlan Sitesi](https://www.kktcarabam.com/)
2. [Kıbrıs & KKTC Araba İlanları | Satılık İkinci El Araçlar | GalerimPlus](https://www.galerimplus.com/)
3. [Kıbrıs Araba Fiyatları 2026 Ocak Ayı Güncel Durumu: En Avantajlı Fırsatlar GalerimPlus'ta-Podcast Series 7](https://music.amazon.com/podcasts/6ccce58d-6cc3-468c-8f92-60687efaace6/episodes/f5016668-a865-4381-a254-4c47e8e1be6d/podcast-series-7-k%C4%B1br%C4%B1s-araba-fiyatlar%C4%B1-2026-ocak-ay%C4%B1-g%C3%BCncel-durumu-en-avantajl%C4%B1-f%C4%B1rsatlar-galerimplus'ta)
4. [Kıbrıs Araba com - Plakasız, İkinci El, Sıfır, araba ilanları. Oto Galeri, Bayi, Sahibinden satılık araç fiyatları ilan sitesi - kktc](https://kibrisaraba.com/otomobil/sahibinden/ilan/sitesi/kibris/araba/ilanlari/fiyatlari/kibrista/satilik/arac/kktc/fiyatlari/kibris/arabam/com/ilan/)
5. [Kıbrıs Araba com - Araba ilanları. Oto Galeri, Bayi, Sahibinden satılık araç fiyatları ilan sitesi](https://www.kibrisaraba.com/)
6. [KKTC Araba İlanları - Kıbrıs 2. El Araba İlanları | Kıbrıs Araba Al](https://kibrisarabaal.com/)
7. [Listeler arşivi - Sahibinden Araba Kıbrıs](https://sahibindenarabakibris.com/vehicles/)
8. [Kolayca Al, Hızlıca Sat. Aklında Ne Varsa İllaki Burada!](https://www.illakiburada.com/)
9. [KKTC Kıbrıs Sıfır ve İkinci El Araba İlanları ve Fiyatları](https://www.illakiburada.com/17/vasita-otomobil)
10. [KKTC İkinci El Sıfır Satılık Veya Kiralık Audi İlanları ve Fiyatları](https://www.illakiburada.com/119/audi)
11. [KKTC KKTC Otomobil İlanları | Mezunum Satıyorum Kıbrıs](https://mezunumsatiyorumkibris.com.tr/ilanlar/otomobil)
12. [Kktc Satılık Araba 2026 - KKTC Bit Pazarı](https://kktcbitpazari.com/kktc-satilik-araba/)
13. [KKTC KKTC Araba İlanları | Mezunum Satıyorum Kıbrıs](https://mezunumsatiyorumkibris.com.tr/ilanlar/kktc-araba)
14. [Mezunum Satıyorum Kıbrıs: Orijinal Sayfası](https://mezunumsatiyorumkibris.com.tr/)
15. [KKTC Satılık Araba İlanları İkinci El | BirArabaKibris.com](https://www.birarabakibris.com/)
16. [KKTC Kıbrıs 2. El Araba İlanları | İkinci El Araç Alım Satım İlanları ve Fiyatları - Bi'Arabacık](https://biarabacik.com/)
17. [KKTC Mini Fiyatları & Modelleri - biarabacik.com](https://biarabacik.com/159/mini)
18. [KKTC Tofaş Fiyatları & Modelleri - biarabacik.com](https://biarabacik.com/180/tofas)
19. [Reklam Politikası - biarabacik.com Kıbrıs 2.El Araba İlanları](https://biarabacik.com/reklam-16)
20. [Cars for Sale in Northern Cyprus | KKTCar](https://kktcar.com/en/)
21. [Things to Consider When Selling a Car in North Cyprus (2026)](https://pazarkibris.com/blog/things-to-consider-when-selling-a-car/)
22. [CarsForSale (List) | Whats On In TRNC](https://www.whatsonintrnc.com/carsforsale)
23. [Cars for Sale in North Cyprus](https://www.whatsonintrnc.com/carsforsale/4444)
24. [Cars for Sale Northern Cyprus | TRNC Cars Launching with Free Listings](https://www.whatsonintrnc.com/post/cars-for-sale-northern-cyprus)
25. [Deniz Şahin - Director - kibrisaraba.com](https://cy.linkedin.com/in/deniz-%C5%9Fahin-1896a657)
26. [Kıbrıs Araba (@KIBRISARABA)](https://www.facebook.com/KIBRISARABA)
27. [KıbrısAraba.com (@KIBRISARABA) on X](https://twitter.com/kibrisaraba?lang=en)
28. [KKTC 2. El Araba Fiyatları ve Modelleri - arabam.com](https://www.arabam.com/ikinci-el/otomobil-kktc)
29. [KKTC ARABAM (@kktc\_arabam) • Instagram photos and videos](https://www.instagram.com/kktc_arabam/)
30. [Kktc Arabam (Kktc\_arabam) Instagram Photos And Videos](<https://lighthorse.org.au/kktc-arabam-(kktc_arabam)-instagram-photos-and-48372-videos>)
31. [KIBRIS ARABA ALIM-SATIM SAYFASI (@kktc.arabalari) • Fotos y vídeos de Instagram](https://www.instagram.com/kktc.arabalari/)
32. [AL-SAT KIBRIS 🚗 (@alsatkibriss) • Instagram photos and videos](https://www.instagram.com/alsatkibriss/)
33. [KKTC ARABA PAZARI](https://www.facebook.com/groups/469402498541872/?locale=tr_TR)
34. [north cyprus cars and bikes for sale](https://www.facebook.com/groups/539378399490159/)
35. [Cars for sale in Kyrenia | Facebook Marketplace | Facebook](https://www.facebook.com/marketplace/2100962630160679/cars/)
36. [Where to buy a used car in Northern Cyprus | CYPRUS FAQ](https://cyprus-faq.com/en/north/transport/gde-kupit-b-u-mashiny-na-severnom-kipre/)
37. [Cyprus Car – Telegram](https://t.me/s/cypruscar/1372)
38. [Kibkom North Cyprus Forum - MOTORS - Kibkom North Cyprus Forum](https://kibkomnorthcyprusforum.com/viewforum.php?f=34)
39. [KKTC'de Hangi Araçlar Alınmalıdır? 2026 Trendleri | Akıllı Galeri](https://akilligaleri.com/blog/kktc-hangi-araclar-alinmalidir/)
40. [KKTCilan.com | Kuzey Kıbrıs'ın İlan Platformu](https://kktcilan.com/)
41. [2021 MODEL OTOMATİK MAZDA 2 - biarabacik.com Kıbrıs 2.El Araba İlanları](https://biarabacik.com/2021-model-otomatik-mazda-2-955)
42. [Gelecek Zaten Burada! BMW i8 2020 – Hibrid Süper Spor Şıklığı - biarabacik.com Kıbrıs 2.El Araba İlanları](https://biarabacik.com/gelecek-zaten-burada-bmw-i8-2020-hibrid-super-spor-sikligi-331)
43. [Major Decision Affects Law of Scraping and Online Data Collection, Meta Platforms v. Bright Data](https://www.fbm.com/publications/major-decision-affects-law-of-scraping-and-online-data-collection-meta-platforms-v-bright-data/)
44. [What Does The Meta v. Bright Data Summary Judgment ...](https://www.quinnemanuel.com/media/bq0josrj/bright-data-questions-answered-and-unanswered-45.pdf)
45. [Kuzey Kıbrıs Türk Cumhuriyeti Kişisel Verilerin Korunması Yasası](https://elmacioglu.av.tr/post/kuzey-kibris-turk-cumhuriyeti-kisisel-verilerin-korunmasi-yasasi_66)
46. [Kişisel Verileri Koruma Kurulu \> ANASAYFA](https://kvkk.gov.ct.tr/)
47. [kişisel verilerin korunması yasası](https://kvkk.gov.ct.tr/Portals/24/RGY_Bilgilendirme%20Yukumlugunun%20Yerine%20Getirilmesinde%20Uyulacak%20Usul%20ve%20Esaslar%20Tuzugu.pdf)
