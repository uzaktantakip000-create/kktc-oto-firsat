# KKTC OTO FIRSAT SİSTEMİ — DEĞERLEME MOTORU ("Beyin")
> Versiyon: 0.2 — 30 Eylül 2026 (koda eşitlendi: 05 Ekim 2026)
> v0.1'in yerine geçer. Kelly, portföy ve çoklu masraf tablosu kaldırıldı. Odak: tek soru — "Bu aracı alıp satarsam %20 kazanır mıyım?"
> **Nasıl okunur:** Bölüm 1–8 ilk tasarımdır; her birinde "GÜNCEL" notu var. Bir yerde çelişki görürsen **bölüm 9 ve kod (`domain/` klasörü) doğrudur.** Kuralların tek karar noktası: `domain/decision.py`.

---

## 1. TEMEL KURAL
```
satilabilir_fiyat = emsal_medyani × HIZLI_SATIS_CARPANI      (varsayılan 0.95)
kar_gbp          = satilabilir_fiyat − alis_fiyati − MASRAF   (MASRAF varsayılan £300; mesajda "masraf düşüldü" yazar)
kar_yuzde        = kar_gbp / alis_fiyati

kar_yuzde ≥ 0.20 ve net kâr ≥ £750 ve ≥8 doğrudan emsal (+ bölüm 9'daki diğer kapılar) → 🟢 FIRSAT (anında bildirim)
0.12 ≤ kar_yuzde < 0.20                                  → 🟡 pazarlıkla fırsat: yalnız KAYIT, mesaj gitmez (günlük özet 02.10.2026'dan beri KAPALI: `digest.ENABLED = False`)
az emsal + değer tablosu çok ucuz diyorsa                → 🟠 KONTROL ET (günde ≤3; şu an KAPALI: gönderim kapısı `domain/alert_policy.py` 🟠'yı hiç geçirmiyor; v4 planı 2.4'te 2 haftalık deneme)
aksi halde                                               → bildirim yok
```
(Not 05.10.2026: v0.2'deki "masraf £0" ve "🟡 günlük özet" artık geçerli değil; güncel kural dosyası `domain/settings.py`, `domain/decision.py`, `domain/alert_policy.py`.)

**GÜNCEL — %20 kâr pratikte kaç % ucuzluk demek?** £300 masraf ve £750 asgari net kâr yüzünden "medyandan %21 ucuz" yetmez. Gereken ucuzluk (medyana göre, hızlı satış çarpanı 0,95 ile):
| Piyasa ortası (medyan) | Alış en çok | Medyandan ucuzluk |
|---|---|---|
| £4.000 | £2.750 | ≈ %31 (£750 şartı bağlar) |
| £5.000 | £3.700 | ≈ %26 |
| £10.000 | £7.667 | ≈ %23 |
| £20.000 | £15.583 | ≈ %22 |
| £30.000 ve üstü | | ≈ %21–22 |
Yani ucuz araçta çıta daha yüksek, pahalı araçta %20'ye yaklaşır (formül: alış ≤ (0,95 × medyan − 300) / 1,2; ayrıca kâr ≥ £750).

**GÜNCEL — Telegram'dan ne değişir?** Yalnız şunlar: 🟢 kâr eşiği (`/esik 25`, izinli aralık %15–50), alış bütçesi (`/butce 20000`), istenmeyen marka (`/istemiyorum fiat`), 🟠 aç/kapat (`/tahmini`; şu an etkisiz, çünkü kapı kapalı). Kod: `application/settings_store.py`. Masraf, çarpan, £750 şartı, emsal sayısı gibi diğer eşikler yalnız kodda (`domain/settings.py`) değişir.

## 2. EMSAL (BENZER ARAÇ) BULMA
Regresyon yerine emsal yöntemi (az veride daha sağlam, açıklaması kolay).

Emsal = aynı **marka + model** (normalize) ve:
- yıl: ±1. İlk geçerli piyasa 8'den az emsalliyse ±2 denenir; 8'e ulaşan ilk adım seçilir (medyan, alt çeyrek ve km medyanı dar ve geniş piyasanın KÜÇÜĞÜ). ±2 piyasasında emsallerin medyan yılı ilanın yılından büyükse (yeni modeller pahalı) 🟢 verilmez, en fazla 🟡
- satıcı: bir satıcının (telefon ya da KKTCar satıcı kimliği) piyasaya en fazla 2 emsali girer (hedefe yıl+km'ce en yakın ikisi); emsaller en az 2 farklı satıcıdan gelmeli. Kimliği bilinmeyen emsal (KKTCar satılmış sayfaları) ayrı satıcı sayılır: bu gerçek güvence değil üst sınırdır
- km (kmbant2 çalışması 05.10.2026; sahip onayı ve canlı 07.10.2026, RULES_VERSION 2026-10-07): km bandı YOK. Her emsalin fiyatı ilanın km'sine çekilir: aradaki her 10.000 km için %1,1 (emsal ilandan az km'liyse ucuzlatılır, çok km'liyse pahalılatılır). Katsayı veriden ölçüldü (aynı marka+model+yıl ilan çiftleri, Theil-Sen: %1,06; modeller arası fark gürültü düzeyinde, tek katsayı; 07.10 yeniden ölçüm: aktif %0,98, aktif + satıldı %1,15 → aynı kaldı). Medyan, alt çeyrek ve aykırı atma düzeltilmiş fiyatlarla. 🟢 için emsallerin en az 8'inin km'si ilanın km'sine ±50.000 km yakın olmalı (km'si yazmayan emsal sayılır); değilse en fazla 🟡 (`km_yakin_emsal_az`). İlanın ya da emsalin km'si bilinmiyorsa düzeltme yok; km'si bilinmeyen ilanda yakın emsal kuralı da yok. Düzeltilmiş piyasada `km_yuksek` kapısı YOK (07.10): yüksek km fiyattan zaten düşüldü, ikinci ceza olmaz (ölçüm: km/emsal km 1,3-1,6 olan ilanlarda düzeltilmiş medyana sapma %+1,0, 1,6+ olanlarda %-0,8: yüksek km'li ilan sistematik olarak ucuz görünmüyor); uzak km'yi yakın emsal kuralı tutar. Eski kural (tarihî): aynı bant 0–50K/50–100K/100–150K/150K+, komşu banda yalnız yıl ±2'de bakılırdı (149.999 km ile 150.000 km farklı emsal kümesi seçiyordu)
- vites: aynı (otomatik/manuel; "düz" = manuel sayılır). İlanlardan biri yazmıyorsa elenmez
- direksiyon: aynı (RHD ve LHD ASLA karışmaz); yazmıyorsa sağ (RHD) sayılır
- yakıt: aynı (hibrit/dizel/benzin/elektrik; "elektrik" = "elektrikli"). GEVŞETİLMEZ: iki ilanda da yazıyorsa ve farklıysa emsal olmaz; yazmıyorsa elenmez
- motor hacmi: iki ilanda da biliniyorsa fark en çok 0,3 litre (316i ile 340i karışmaz); bilinmiyorsa elenmez
- zaman: ilanın KENDİ tarihi (yayın tarihi; yoksa site verisi tarihi; o da yoksa sisteme giriş tarihi) son 90 gün içinde olmalı. Hâlâ yayında olan ama 60 günden eski ilan sayılmaz (satılamamış, istenen fiyat piyasa fiyatı değil)
- pasif ilan (kaldırılmış/arşiv): yalnızca "satıldı" işaretliyse emsal olur; belirsiz "arşiv/kaldırıldı" ilanlar sayılmaz (`domain/comparables.py`)
- tekrarlar (duplicate_of dolu) çıkarılır; aynı aracın birden fazla ilanı 1 sayılır
- aykırı değerler çıkarılır: 8 ve üzeri emsalde IQR dışı; 8'den azında medyanın yarısından az / iki katından çok olanlar
- fiyat £500'ün altında ya da £250.000'in üstündeyse ilan emsal olmaz
- para birimi tahmini (`currency_guess=true`) olan ilanlar emsal havuzuna girmez
- havuza hiç girmeyenler: gece bakımında karantinaya alınanlar, yapay zekâ okuması (`extraction_by='llm'`) ilanlar, sahibin 👎 ("yanlış fiyat") dediği ilanlar (`Repository.market_pool`)

Medyan hesaplanırken galeri + bireysel ilanlar birlikte kullanılır (galeri fiyatları doğal "üst referans"). Satıcı türü (galeri/bireysel) kaydedilir ama karar vermede KULLANILMAZ; galeri etkisini yalnız "satıcı başına en fazla 2 emsal" sınırı azaltır.

## 3. GÜVEN SEVİYESİ (v0.2 tablosu; GÜNCEL: bölüm 9)
> 04.10.2026: mesajdan güven etiketleri kalktı; kapı artık tek: 🟢 için ≥8 doğrudan emsal (aşağıdaki tablo tarihî).

| Emsal sayısı | Güven | Davranış |
|---|---|---|
| ≥ 20 | YÜKSEK | Normal bildirim |
| 8–19 | ORTA | Bildirim, "orta güven" etiketiyle |
| 3–7 | DÜŞÜK | Sadece kâr ≥ %30 ise, "düşük güven — kontrol et" etiketiyle |
| < 3 | YOK | Bildirim yok; ilan kaydedilir (veri birikir) |

Ek güven düşürücüler: para birimi tahmin edildi → en fazla 🟡. km yazmıyor/şüpheli artık güveni düşürmez (sahip kararı 04.10.2026): kâr şartı aynı, mesajda uyarı çıkar.
GÜNCEL: Kod güveni hâlâ hesaplar (`domain/profit.py`) ama mesajda göstermez. 3–7 emsalde 🟢 hiç verilmez (`low_confidence_can_alert = False`), en fazla 🟡. Yıl çelişkisinde (ör. KibrisArabaAl'da kayıt yılı ile model yılı) eski yıl alınır.

## 4. TUZAK KONTROLÜ (Kırmızı Bayraklar)
Bildirimi engelleyen veya uyaran durumlar. **GÜNCEL (kod: `domain/red_flags.py`, `domain/data_gate.py`):** bu kontroller yalnızca DEĞERLENEN ilanın metnine bakar. Emsal havuzu metne göre süzülmez (havuzdaki hasarlı/modifiyeli ilan emsal olabilir; havuzu yalnız gece bakımı karantinası ve sahibin 👎 oyları temizler).
- **ENGEL (bildirim yok):** pert/ağır hasar, airbag açık/patlak, vuruk/su basmış, "hasarlı/kazalı/hasar kayıtlı", motor/şanzıman sorunlu, "as is", parça araç/parçalık, çıkma motor, yürümüyor/çalışmıyor, kira/taksit/peşinat ("aylık", "taksitle satış"), kredi devri/senet, gümrüksüz/evraksız/haciz/icralık/mahkeme (tam liste bölüm 9)
- **UYARI (mesajda "⚠️ Dikkat"; engel değil):** "değişen var", "ufak masrafları var", modifiye/performans ("Stage 2", "370+ HP"). Bunlar emsal havuzunu AYIRMAZ, yalnız uyarı olur
- **En fazla 🟡:** "TR/yabancı plaka" yazıyorsa
- **Fiyat mantıksız:** emsal medyanının %50'sinden düşük → muhtemelen yanlış yazım (ör. "9.500₺"): `fiyat_asiri_dusuk` işareti, 🟢 yok; 8'den az emsalde hiç bildirim yok
- **Galerici tespiti YOK:** "aynı telefon kısa sürede çok araç" kuralı yazılmadı. Satıcı türü (`seller_type`) kaydedilir ama kararda kullanılmaz; yalnız satıcı başına en fazla 2 emsal sınırı piyasayı korur

## 5. ACİLİYET VE PAZARLIK SİNYALLERİ
**GÜNCEL:** Sinyaller sıralamayı ya da kararı DEĞİŞTİRMEZ. Tek etkisi: bildirimin "💡 Neden" satırına "ilanda 'acil' yazıyor" gibi bir not eklenir (`domain/red_flags.py` `URGENCY`, `application/notify.py`).
- İfadeler (TR/EN/RU): "acil", "mezun", "adadan ayrılıyorum", "gidiyorum", "leaving", "urgent", "must sell", "срочно", "уезжаю"
- "Pazarlık mevcut / pazarlık payı var" → aynı not satırı
- Fiyat düşüşü: `listing_history`'ye yazılır. Tek etkisi: son 36 saatte fiyatı değişen ilan yaşına bakılmadan "taze" sayılır (`is_fresh`); yani eskiden fırsat olmayan ilan fiyatı düşünce ilk kez bildirilebilir. Bir ilan aynı kişiye BİR KEZ gider; "fiyat düştü" diye ikinci mesaj yok. "Her düşüş +1 sinyal" puanlaması yok
- Kaynak türü "+1" (Mezunum, "Acil Satılık") ve "takas" bilgisi: kodda YOK

## 6. SONNET SON KONTROLÜ — KALDIRILDI (eski tasarım; Sonnet/Anthropic API kullanılmıyor)
Eski tasarımda Sonnet "gerçek fırsat değil" derse 🟢 → 🟡'ye düşecekti. Bu yazılmadı. Yerine bugün iki ayrı yapay zekâ işi var (ikisi de OpenRouter üzerinden; yapay zekâ ASLA 🟢 üretmez):
1. **Fırsat notu** (`infrastructure/llm/openrouter.py` `check_deal`; model = GitHub Secret `OPENROUTER_MODEL`): gönderilecek her 🟢 için çağrılır (anahtar ve model tanımlıysa). Girdi: ilan metni (ilk 1500 karakter, telefon maskeli) + emsal özeti (sayı, medyan, aralık); fotoğraf gitmez. Çıktı JSON: `gercek_firsat_mi`, `risk_notlari`, `fiyat_yorumu`, `sorulacak_sorular`. Mesaja yalnız şu girer: `gercek_firsat_mi=false` ise "⚠️ Yapay zekâ şüpheli buldu: <ilk risk notu>". **🟢'yi düşürmez**, ilan yine gider; pazarlık önerisi yok, `fiyat_yorumu` ve `sorulacak_sorular` mesaja girmez. Harcama okuyucuyla AYNI günlük $0,40 tavanından düşer; her (ilan, fiyat) için en çok bir kez sorulur (sonuç `bot_state` `deal_note:` önbelleğinde; hata da "soruldu" sayılır); bütçe doluysa not sorulmaz, ilan notsuz gider (`application/llm_reader.py` `deal_notes`; 05.10.2026). Yanıt tavanı `max_tokens` 1500.
2. **Bağımsız okuyucu** (`application/llm_reader.py`; model GLM `z-ai/glm-5.3-flash`, `OPENROUTER_READ_MODEL` ile değişir; günlük harcama tavanı $0,40): önce kural tabanlı ayrıştırıcı okur, okuyamazsa GLM okur. Gönderimden önce şu adayları bağımsız okur: sosyal medyadan ya da serbest yazıdan okunmuş ilanın (ör. Mezunum; bilgisi şablondan değil serbest metinden çıkan site ilanları) 🟢'si, ve her 🟠. Fiyat/yıl/marka/direksiyon uyuşmazlığı ya da gizli sorun (hasar, pert, borç...) bulursa 🟡'ye DÜŞÜRÜR. Okuma yapılamazsa ilan "⚠️ kontrol edilmedi" notuyla gider (fiyatını yapay zekânın okuduğu 🟠 gitmez). km farkı tek başına düşürmez, uyarı olur. KKTCar, KibrisArabaAl, KKTCarabam gibi şablonlu sitelerin 🟢'si bu okumadan geçmez.

## 7. ÖĞRENME — İLERİDE (çoğu henüz yok)
- **Satış hızı:** İlanın kaybolma zamanı ve nedeni KAYDEDİLİR (`listings.inactive_at`, `inactive_reason`; `domain/lifecycle.py`). Ama model bazında "kaybolma günü" hesabı ve "🔥 hızlı satılır" etiketi YOK (ileride).
- **Geri bildirim:** Bildirimde 2 düğme var: 👍 İşe yarar / 👎 Yanlış (telefon biliniyorsa üstte 📲 WhatsApp bağlantısı; mesajı sen yazarsın, sistem yazmaz). "İlgileniyorum / Pas / Yanlış fiyat" düğmeleri YOK; eski mesajlardaki eski düğmeler çalışmaya devam eder. Oylar `feedback` tablosuna yazılır; sahibin 👎'i o ilanı emsal havuzundan çıkarır. **10 oydan önce hiçbir otomatik öğrenme eylemi yok** (kaynak düşürme, 🟠'yı model bazında kapatma, kara liste: `application/learning.py`). "Güveni bir kademe düşür" yok. "Partner raporu" yazılmadı.
- **Gerçekleşen işlemler:** `/satti corolla 2014 120000km 7200` ile girilen gerçek satış değer tablosu eğrisinde 3 ilan ağırlığı alır (`owner_sale_weight`); komut "gerçek satış / tablo değeri" oranını yazar. HIZLI_SATIS_CARPANI'nı otomatik güncelleme ya da öneri YOK: çarpan elle `domain/settings.py`'da (ileride).

## 8. PARAMETRELER
**GÜNCEL (05.10.2026; kaynak: `domain/settings.py`, gönderim sayısı `domain/alert_policy.py`).** Eski blokta `fixed_cost_gbp` 0 ve emsal şartı 3'tü; bugün değerler şunlar:
```python
SETTINGS = {
    "strong_threshold": 0.20,         # 🟢 kâr eşiği (Telegram /esik ile %15–50 arası değişir)
    "negotiable_threshold": 0.12,     # 🟡 (yalnız kayıt)
    "quick_sale_factor": 0.95,
    "fixed_cost_gbp": 300,            # her araçtan düşülür; mesajda "masraf £300 düşüldü" yazar
    "min_strong_profit_gbp": 750,     # 🟢 için masraf SONRASI asgari net kâr
    "min_comparables_alert": 3,       # piyasa kurmak için en az emsal (3–7 emsalde 🟢 yok)
    "MIN_COMPARABLES_TO_SEND": 8,     # alert_policy.py: 🟢 göndermek için en az doğrudan emsal
    "widen_until_comparables": 8,     # ilk piyasa bundan azsa yıl ±1 → ±2 genişler
    "max_comparables_per_seller": 2,  # bir satıcının piyasaya katacağı en fazla emsal
    "min_distinct_sellers": 2,
    "comparable_window_days": 90,
    "active_max_age_days": 60,        # yayında ama 60+ gündür duran ilan emsal değil
    "absurd_price_ratio": 0.50,
    "low_confidence_min_profit": 0.30,
    "low_confidence_can_alert": False,
    "est_daily_limit": 3,             # 🟠 günde en çok (kapı şu an kapalı)
    "km_adjust_per_10k": 0.011,       # emsal fiyatı ilanın km'sine: 10.000 km başına %1,1 (veriden)
    "km_near_limit": 50_000,          # 🟢 için ≥8 emsal ilanın km'sine ±50.000 km yakın olmalı
    "km_near_min_comparables": 8,
}
```
Tüm liste (🟠 eğrisi, değer tablosu, km ve motor toleransı dahil) `domain/settings.py` içindedir; bu blok özetidir.

## 9. GÜNCEL KURALLAR (RULES_VERSION "2026-10-05", 05.10.2026 itibarıyla)
Tek karar noktası: `domain/decision.py::decide()` (otomatik tarama, "ilanı bota ilet" kontrolü `application/ad_check.py` ve altın test aynı kuralı kullanır).
🟢 FIRSAT şartları (hepsi):
- ≥8 doğrudan emsal (yöntem A), satıcı başına en fazla 2 emsal sayılır (ilanın yıl+km'ce en yakın ikisi), en az 2 farklı satıcı; ilk geçerli piyasa 8'den azsa yıl aralığı ±1 → ±2 genişler (£-yalnız önce); medyan/alt çeyrek/km medyanı dar ve geniş piyasanın KÜÇÜĞÜ.
- Yıl koruması `emsal_yili_yeni`: emsallerin medyan yılı ilanın yılından ≥1 yıl yeni ise (±1'de bile) ya da ±2 genişlemede biraz bile yeni ise 🟢 yok (yeni model pahalıdır).
- TL fiyatlı ilan en fazla 🟡; TL emsal yalnız £-yalnız piyasa <8 ise karışır (`gbp_only_min_comparables` 8).
- Fiyat medyanın %50'sinden ucuzsa (`fiyat_asiri_dusuk`, her emsal sayısında kayda geçer) bozuk veri sayılır: 🟢 yok.
- km yoksa/şüpheliyse (sahip kararı 04.10): engel DEĞİL, mesajda uyarı; kâr şartı %20 aynı. Düşük km kuralı: ≥10 yaşında araçta 15.000 km altı yazan km yok sayılır (`effective_km`).
- km 'bin' eksik istisnası (sahip onayı 05.10): ≥2 yaşında araçta ham km 1-999 yazıyorsa ("214" = 214.000) ve ×1000 okunursa km emsal km medyanının 1,3 katından ve +10.000 km'den fazlaysa (`km_yuksek` ile aynı koşul) eksik `km_bin_eksik_yuksek` eklenir: 🟢 en fazla 🟡. ×1000 okuması açıkça olumsuz değilse hiçbir şey değişmez (km bilinmiyor, engel yok; yukarıdaki sahip kuralı). `effective_km` değişmedi; 🟠 yolu km'yi zaten zorunlu ister, etkilenmez.
- Engel kelimeler: pert/ağır hasar, airbag, vuruk/su basmış, hasarlı/kazalı, motor/şanzıman sorunlu, "as is"/parça araç/çıkma motor/yürümüyor, kira/taksit/peşinat, gümrüksüz/evraksız/haciz/icralık/mahkeme, **kredi devri/senet** ("senet yok" olumsuzlaması hariç) → bildirim yok.
- Diğer 🟢 kapıları (en fazla 🟡'ye düşürür; `domain/data_gate.py`, `domain/decision.py`): fiyat benzer araçların en ucuz çeyreğinde değil (`ucuz_ceyrek_degil`), km'si ilana ±50.000 km yakın emsal 8'den az (`km_yakin_emsal_az`), km 'bin' eksik yazılmış ve ×1000 okunursa emsal km medyanının 1,3 katından ve +10.000 km'den fazla (`km_bin_eksik_yuksek`; eski `km_yuksek` kapısı yalnız km'ye göre düzeltilmemiş piyasada, 07.10.2026'dan beri gerçek turda işlemez), para birimi tahmin, model yok/belirsiz, yapay zekâ okuması (`llm_okudu`), "TR/yabancı plaka", değer tablosu bu modelde "şüpheli" (`deger_supheli`), TL fiyat.
- Kullanıcı kararları (`application/settings_store.py`): istenmeyen marka, bütçe üstü, kara listedeki satıcı → bildirim yok; 3 kez "pas" denen model → en fazla 🟡 (yeni mesajlarda "Pas" düğmesi olmadığından pratikte nadir).
- Karışık model anahtarı (`model_ambiguity.MIXED_KEYS`): Mazda cx, Honda cr, VW t, Mercedes benz, Land Rover rover, Ford transit → 🟢/🟠 yok; ayrıca Toyota yaris/corolla'da "Cross" yazan ya da 2020+ ilan (`CROSS_KEYS`). Model anahtarı doldurması (v4 planı 1.1) 05.10.2026 13:08 UTC'de YAPILDI (380 ilan). Liste SİLİNMEDİ (karar: spec §24.4 K2 ve §24.6): doldurmadan sonra bu anahtarlarda ilan kalmadı, ama ileride yalnız "CX"/"Benz" yazan ilan gelirse koruma sürsün.
- Gönderim koşulları (karar değil): ilan "taze" (ilk görülme ≤36 sa ya da son 36 saatte fiyat değişimi; yayın tarihi biliniyorsa en fazla 4 günlük; yayın tarihi YOKSA ve ilan sitenin "en yeni" listesine geri itilmiş eski bir KKTCarabam ilanıysa taze sayılmaz: tarihsiz KKTCarabam ilanından daha büyük numaralı bir KKTCarabam ilanı önceki bir turda görülmüşse, spec §24.11), gönderimden hemen önce canlılık kontrolü (yalnız KKTCar ve KibrisArabaAl), sosyal medya ya da serbest metinden okunan ilanlarda (ve her 🟠'da) yapay zekâ ikinci okuması (bölüm 6).
- Kural dosyasında HENÜZ YOK (v4 planı 2.5: önce ölçülecek): "değer tablosu emsal medyanıyla ≤%15 uyumlu / oturmuş satır" şartı.
- CANLI 07.10.2026 (kmbant2, sahip onayı "mevcut veri havuzunu da göz önünde bulundurarak mantıklı buluyorsan uygula"; RULES_VERSION 2026-10-07): km bandı kalkar; emsal fiyatları ilanın km'sine 10.000 km başına %1,1 ile çekilir; 🟢 için ≥8 emsalin km'si ilana ±50.000 km yakın olmalı (`km_yakin_emsal_az` → en fazla 🟡). km'si bilinmeyen ilan değişmez. Ölçüm ve gerekçe: bölüm 2, `domain/settings.py` yorumları ve kmbant2 raporu (canlı salt-okunur anlık görüntü: doğruluk |hata| medyanı %9,05 → %8,21; bugün 🟢 10 → 9; km uçurumu 77 → 2 ilan; ilanda km yazım hatasından sahte 🟢 2 → 1; altın dosyada yanlış 🟢 yok). 07.10 yeniden ölçüm ve `km_yuksek` değişikliği: spec §24.16.
Mesaj: 🟢 FIRSAT ya da 🟠 KONTROL ET; araç, fiyat, yer/kaynak/km/vites, piyasa ortası + emsal sayısı + masraf düşüldü, tek satır "neden", uyarılar (varsa), ilan tarihi, link; düğmeler: 👍 İşe yarar / 👎 Yanlış (+ telefon biliniyorsa 📲 WhatsApp); 10 oydan önce öğrenme (otomatik kaynak düşürme/kara liste) yok.
