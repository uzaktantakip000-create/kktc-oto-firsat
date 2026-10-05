# KKTC OTO FIRSAT SİSTEMİ — DEĞERLEME MOTORU ("Beyin")
> Versiyon: 0.2 — 30 Eylül 2026
> v0.1'in yerine geçer. Kelly, portföy ve çoklu masraf tablosu kaldırıldı (kullanıcı kararı: masraf neredeyse yok, avukat onayı var). Odak: tek soru — "Bu aracı alıp satarsam %20 kazanır mıyım?"

---

## 1. TEMEL KURAL
```
satilabilir_fiyat = emsal_medyani × HIZLI_SATIS_CARPANI      (varsayılan 0.95)
kar_gbp          = satilabilir_fiyat − alis_fiyati − MASRAF   (MASRAF varsayılan £300; mesajda "masraf düşüldü" yazar)
kar_yuzde        = kar_gbp / alis_fiyati

kar_yuzde ≥ 0.20 ve net kâr ≥ £750 ve ≥8 doğrudan emsal → 🟢 FIRSAT (anında bildirim)
0.12 ≤ kar_yuzde < 0.20                                  → 🟡 pazarlıkla fırsat: yalnız KAYIT (günlük özet 04.10.2026'dan beri KAPALI)
az emsal + değer tablosu çok ucuz diyorsa                → 🟠 KONTROL ET (günde ≤3; şu an KAPALI, v4 planı 2.4'te 2 haftalık deneme)
aksi halde                                               → bildirim yok
```
(Not 05.10.2026: v0.2'deki "masraf £0" ve "🟡 günlük özet" artık geçerli değil; güncel kural dosyası `domain/settings.py`, `domain/decision.py`, `domain/alert_policy.py`.)
Pratikte %20 kâr = araç emsal medyanının yaklaşık **%21 altında** demek.
Tüm eşikler `settings` üzerinden değiştirilebilir (Telegram: `/esik 20`).

## 2. EMSAL (BENZER ARAÇ) BULMA
Regresyon yerine emsal yöntemi (az veride daha sağlam, açıklaması kolay).

Emsal = aynı **marka + model** (normalize) ve:
- yıl: ±1. İlk geçerli piyasa 8'den az emsalliyse ±2 denenir; 8'e ulaşan ilk adım seçilir (medyan, alt çeyrek ve km medyanı dar ve geniş piyasanın KÜÇÜĞÜ). ±2 piyasasında emsallerin medyan yılı ilanın yılından büyükse (yeni modeller pahalı) 🟢 verilmez, en fazla 🟡
- satıcı: bir satıcının (telefon ya da KKTCar satıcı kimliği) piyasaya en fazla 2 emsali girer (hedefe yıl+km'ce en yakın ikisi); emsaller en az 2 farklı satıcıdan gelmeli. Kimliği bilinmeyen emsal (KKTCar satılmış sayfaları) ayrı satıcı sayılır: bu gerçek güvence değil üst sınırdır
- km: aynı bant (0–50K, 50–100K, 100–150K, 150K+); emsal azsa komşu bant
- vites: aynı (otomatik/manuel)
- direksiyon: aynı (RHD ve LHD ASLA karışmaz)
- yakıt: aynı (hibrit/dizel/benzin/elektrik); emsal azsa gevşet
- zaman: son 90 gün içinde görülmüş ilanlar
- tekrarlar (duplicate_of dolu) çıkarılır; aynı aracın birden fazla ilanı 1 sayılır
- aykırı değerler (IQR dışı) çıkarılır
- para birimi tahmini (`currency_guess=true`) olan ilanlar emsal havuzuna girmez

Medyan hesaplanırken galeri + bireysel ilanlar birlikte kullanılır (galeri fiyatları doğal "üst referans").

## 3. GÜVEN SEVİYESİ (v0.2 tablosu; GÜNCEL: bölüm 9)
> 04.10.2026: mesajdan güven etiketleri kalktı; kapı artık tek: 🟢 için ≥8 doğrudan emsal (aşağıdaki tablo tarihî).

| Emsal sayısı | Güven | Davranış |
|---|---|---|
| ≥ 20 | YÜKSEK | Normal bildirim |
| 8–19 | ORTA | Bildirim, "orta güven" etiketiyle |
| 3–7 | DÜŞÜK | Sadece kâr ≥ %30 ise, "düşük güven — kontrol et" etiketiyle |
| < 3 | YOK | Bildirim yok; ilan kaydedilir (veri birikir) |

Ek güven düşürücüler: para birimi tahmin edildi, yıl belirsiz ("2019 (2024 çıkışlı)" → üretim yılı esas, not düşülür). km yazmıyor/şüpheli artık güveni düşürmez (sahip kararı 04.10.2026): kâr şartı aynı, mesajda uyarı çıkar.

## 4. TUZAK KONTROLÜ (Kırmızı Bayraklar)
Bildirimi engelleyen veya uyaran durumlar:
- Metinde: "hasarlı", "kazalı", "motor sorunlu", "şanzıman sorunlu", "as is", "parça", "yürümüyor", "ufak masrafları var" (uyarı), "çıkma motor"
- Modifiye/performans ("Stage 2", "370+ HP") → emsal dışı, uyarı
- Fiyat mantıksız: emsal medyanının %50'sinden düşük → muhtemelen yanlış yazım (ör. "9.500₺") → "yanlış fiyat?" etiketi, 🟢 değil
- Kira/taksit fiyatı ("aylık", "taksit", "peşinat") → emsal dışı
- Aynı telefon kısa sürede çok sayıda farklı araç → muhtemelen galerici (bireysel sayılmaz)

## 5. ACİLİYET VE PAZARLIK SİNYALLERİ (sıralamayı yükseltir)
- İfadeler (TR/EN/RU): "acil", "mezun", "adadan ayrılıyorum", "gidiyorum", "leaving", "urgent", "must sell", "срочно", "уезжаю"
- "Pazarlık mevcut / pazarlık payı var" → 🟡 adaylar için ağırlık
- Fiyat düşüşü (listing_history): her düşüş +1 sinyal
- Kaynak türü: Mezunum Satıyorum, kibrisarabaal "Acil Satılık" → +1
- Takas kabul ediliyor → nötr (bilgi olarak göster)

## 6. SONNET SON KONTROLÜ
Sadece 🟢 adaylar (ve düşük güvenli ≥%30 adaylar) için. Girdi: ilan metni + ilk 3 fotoğraf + emsal özeti (n, medyan, min, max, 5 örnek emsal). Çıktı (JSON):
```json
{
  "gercek_firsat_mi": true,
  "risk_notlari": ["..."],
  "fiyat_yorumu": "1 cümle",
  "pazarlik_onerisi_gbp": [6500, 6700],
  "sorulacak_sorular": ["Muayene tarihi?", "Tramer/kaza?"]
}
```
Sonnet "gerçek fırsat değil" derse 🟢 → 🟡'ye düşer (silinmez).

## 7. ÖĞRENME
- **Satış hızı:** Web sitelerinde ilanın kaybolma süresi kaydedilir. Model bazında medyan "kaybolma günü" → hızlı satılan modeller bildirimde "🔥 hızlı satılır" etiketi alır.
- **Geri bildirim:** Bildirimdeki butonlar (İlgileniyorum / Pas / Yanlış fiyat) ve partner raporları `feedback` tablosuna yazılır. "Yanlış fiyat" gelen model grupları için güven otomatik bir kademe düşürülür.
- **Gerçekleşen işlemler:** "aldım £X / sattım £Y" kayıtları, HIZLI_SATIS_CARPANI'nın gerçek değerini hesaplamak için kullanılır (10 işlemden sonra otomatik güncelleme önerisi).

## 8. PARAMETRELER (başlangıç)
```python
SETTINGS = {
    "strong_threshold": 0.20,
    "negotiable_threshold": 0.12,
    "quick_sale_factor": 0.95,
    "fixed_cost_gbp": 0,
    "min_comparables_alert": 3,
    "comparable_window_days": 90,
    "absurd_price_ratio": 0.50,
    "low_confidence_min_profit": 0.30,
}
```

## 9. GÜNCEL KURALLAR (RULES_VERSION "2026-10-04f", 05.10.2026 itibarıyla)
Tek karar noktası: `domain/decision.py::decide()` (otomatik tarama, "ilanı bota ilet" kontrolü `application/ad_check.py` ve altın test aynı kuralı kullanır).
🟢 FIRSAT şartları (hepsi):
- ≥8 doğrudan emsal (yöntem A), satıcı başına en fazla 2 emsal sayılır (ilanın yıl+km'ce en yakın ikisi), en az 2 farklı satıcı; ilk geçerli piyasa 8'den azsa yıl aralığı ±1 → ±2 genişler (£-yalnız önce); medyan/alt çeyrek/km medyanı dar ve geniş piyasanın KÜÇÜĞÜ.
- Yıl koruması `emsal_yili_yeni`: emsallerin medyan yılı ilanın yılından ≥1 yıl yeni ise (±1'de bile) ya da ±2 genişlemede biraz bile yeni ise 🟢 yok (yeni model pahalıdır).
- TL fiyatlı ilan en fazla 🟡; TL emsal yalnız £-yalnız piyasa <8 ise karışır (`gbp_only_min_comparables` 8).
- Fiyat medyanın %50'sinden ucuzsa (`fiyat_asiri_dusuk`, her emsal sayısında kayda geçer) bozuk veri sayılır: 🟢 yok.
- km yoksa/şüpheliyse (sahip kararı 04.10): engel DEĞİL, mesajda uyarı; kâr şartı %20 aynı. Düşük km kuralı: ≥10 yaşında araçta 15.000 km altı yazan km yok sayılır (`effective_km`).
- Engel kelimeler: pert/ağır hasar, airbag, vuruk/su basmış, hasarlı, motor/şanzıman sorunlu, parça araç, kira/taksit/peşinat, gümrüksüz/evraksız, **kredi devri/senet** ("senet yok" olumsuzlaması hariç) → bildirim yok.
- Karışık model anahtarı (`model_ambiguity.MIXED_KEYS`): Mazda cx, Honda cr, VW t, Mercedes benz, Land Rover rover, Ford transit → 🟢/🟠 yok; ayrıca Toyota yaris/corolla'da "Cross" yazan ya da 2020+ ilan (`CROSS_KEYS`). Model anahtarı doldurması bekliyor (v4 planı 1.1); sonra Ford transit hariç liste silinir.
- Gönderim koşulları (karar değil): ilan "taze" (ilk görülme/fiyat değişimi ≤36 sa), gönderimden hemen önce canlılık kontrolü (KKTCar ve KibrisArabaAl), sosyal gönderide yapay zekâ ikinci okuması.
- Kural dosyasında HENÜZ YOK (v4 planı 2.5: önce ölçülecek): "değer tablosu emsal medyanıyla ≤%15 uyumlu / oturmuş satır" şartı.
Mesaj: 🟢 FIRSAT ya da 🟠 KONTROL ET; araç, fiyat, piyasa ortası + emsal sayısı, tek satır "neden", 2 düğme (👍 İşe yarar / 👎 Yanlış); 10 oydan önce öğrenme (otomatik kaynak düşürme/kara liste) yok.
