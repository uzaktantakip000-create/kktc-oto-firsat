# KKTC OTO FIRSAT SİSTEMİ — DEĞERLEME MOTORU ("Beyin")
> Versiyon: 0.2 — 30 Eylül 2026
> v0.1'in yerine geçer. Kelly, portföy ve çoklu masraf tablosu kaldırıldı (kullanıcı kararı: masraf neredeyse yok, avukat onayı var). Odak: tek soru — "Bu aracı alıp satarsam %20 kazanır mıyım?"

---

## 1. TEMEL KURAL
```
satilabilir_fiyat = emsal_medyani × HIZLI_SATIS_CARPANI      (varsayılan 0.95)
kar_gbp          = satilabilir_fiyat − alis_fiyati − MASRAF   (MASRAF varsayılan £0)
kar_yuzde        = kar_gbp / alis_fiyati

kar_yuzde ≥ 0.20          → 🟢 GÜÇLÜ FIRSAT (anında bildirim)
0.12 ≤ kar_yuzde < 0.20   → 🟡 PAZARLIKLA FIRSAT (günlük özet)
aksi halde                → bildirim yok
```
Pratikte %20 kâr = araç emsal medyanının yaklaşık **%21 altında** demek.
Tüm eşikler `settings` üzerinden değiştirilebilir (Telegram: `/esik 20`).

## 2. EMSAL (BENZER ARAÇ) BULMA
Regresyon yerine emsal yöntemi (az veride daha sağlam, açıklaması kolay).

Emsal = aynı **marka + model** (normalize) ve:
- yıl: ±1 (emsal azsa ±2'ye genişlet)
- km: aynı bant (0–50K, 50–100K, 100–150K, 150K+); emsal azsa komşu bant
- vites: aynı (otomatik/manuel)
- direksiyon: aynı (RHD ve LHD ASLA karışmaz)
- yakıt: aynı (hibrit/dizel/benzin/elektrik); emsal azsa gevşet
- zaman: son 90 gün içinde görülmüş ilanlar
- tekrarlar (duplicate_of dolu) çıkarılır; aynı aracın birden fazla ilanı 1 sayılır
- aykırı değerler (IQR dışı) çıkarılır
- para birimi tahmini (`currency_guess=true`) olan ilanlar emsal havuzuna girmez

Medyan hesaplanırken galeri + bireysel ilanlar birlikte kullanılır (galeri fiyatları doğal "üst referans").

## 3. GÜVEN SEVİYESİ
| Emsal sayısı | Güven | Davranış |
|---|---|---|
| ≥ 20 | YÜKSEK | Normal bildirim |
| 8–19 | ORTA | Bildirim, "orta güven" etiketiyle |
| 3–7 | DÜŞÜK | Sadece kâr ≥ %30 ise, "düşük güven — kontrol et" etiketiyle |
| < 3 | YOK | Bildirim yok; ilan kaydedilir (veri birikir) |

Ek güven düşürücüler: para birimi tahmin edildi, km yazmıyor, yıl belirsiz ("2019 (2024 çıkışlı)" → üretim yılı esas, not düşülür).

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
