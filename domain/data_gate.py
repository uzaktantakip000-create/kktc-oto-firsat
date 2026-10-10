"""Eksik/şüpheli veriyle 🟢 verilmesini engelleyen kural (en fazla 🟡)."""
from datetime import date, datetime

from domain.comparables import Market, effective_km
from domain.llm_read import MISMATCH_LABELS
from domain.model_ambiguity import model_ambiguous
from domain.settings import Settings

KM_UNKNOWN_WARNING = "km yazmıyor ya da şüpheli: aracı görmeden km'ye güvenme (fiyat kıyası km'siz yapıldı)"

GAP_LABELS = {
    "km_yok": "km yazmıyor",  # ESKİ kayıtlar için etiket: km eksikliği artık 🟢'yi engellemez, uyarı olarak gider (KM_UNKNOWN_WARNING)
    "para_birimi_tahmin": "para birimi tahmin",
    "para_birimi_supheli": "fiyat USD/EUR yazıyor; aynı rakam STG olsaydı fırsat değil (satıcı para birimini yanlış seçmiş olabilir)",
    "tl_fiyat": "fiyat TL: TL ilanlar GBP ilanlara göre ortalama %12-23 ucuz görünüyor; kontrol et",
    "fiyat_asiri_dusuk": "fiyat emsallerin yarısından düşük: yazım hatası ya da tuzak olabilir",
    "model_yok": "model okunamadı",
    "model_belirsiz": "model karışık havuzda (ör. CX-3/CX-5, Yaris/Yaris Cross): fiyat kıyası güvenilmez",
    "km_yuksek": "km emsallerden çok yüksek",
    "km_bin_eksik_yuksek": "km çok düşük yazıyor (bin eksik olabilir): ×1000 okunursa emsallerden çok yüksek",
    "km_yakin_emsal_az": "km'si bu araca yakın (±50.000 km) benzer ilan 8'den az: fiyat kıyası uzak km'li emsallerin düzeltilmesine dayanıyor",
    "ucuz_ceyrek_degil": "fiyat benzer araçların en ucuz çeyreğinde değil",
    "emsal_yili_yeni": "benzer araçların model yılı bu araçtan daha YENİ ağırlıklı (yeni model pahalı): fiyat kıyası güvenilmez",
    "plaka_uyari": "TR/yabancı plaka yazıyor",
    "sessiz_model": "bu modele 3 kez 'pas' dedin (özete alındı)",
    "deger_supheli": "değer tablosu bu modelde yeni değişti, bekleniyor",
    "tahmini_az_emsal": "az emsal: değer tablosu eğrisinden tahmin",
    "llm_okudu": "kural okuyamadı, yapay zekâ okudu (kontrol et)",
}
GAP_LABELS.update(MISMATCH_LABELS)


def km_unknown(listing: dict, now: datetime | None = None) -> bool:
    """km yok ya da şüpheli (eski araçta <1000 km). Sahip kararı (04.10.2026): tek başına fırsatı ENGELLEMEZ; mesajda uyarı olur."""
    return not effective_km(listing, now.date() if now else None)


def km_thousand_missing_high(listing: dict, market: Market, s: Settings, today: date | None = None) -> bool:
    """İlandaki km 1-999 ve araç ≥2 yaşında (tam olarak `effective_km`ın "bilinmiyor" saydığı <1000 km durumu) ve ×1000 okunursa km emsal
    medyanından AÇIKÇA yüksek mi (`km_yuksek` ile aynı koşul: ×1000 > medyan·km_high_ratio ve fark ≥ km_high_margin)? Örnek: 2014 Demio "214 km"
    (büyük ihtimalle 214.000), emsal medyanı 129.000. Sahip kuralı (04.10: yanlış km tek başına engel değil) bozulmaz: ×1000 okuması açıkça
    olumsuz DEĞİLSE ilan eskisi gibi ele alınır (km bilinmiyor, engel yok). `effective_km`a dokunulmaz."""
    km = listing.get("km")
    if km is None or not 0 < km < 1000 or effective_km(listing, today) is not None:
        return False  # km yok / 1-999 değil / yeni araçta (effective_km km'yi makul sayar): kural işlemez
    med = market.median_km
    return bool(med) and km * 1000 > med * s.km_high_ratio and km * 1000 - med >= s.km_high_margin


def data_gaps(listing: dict, market: Market, settings: Settings | None = None, now: datetime | None = None) -> list[str]:
    s = settings or Settings()
    today = now.date() if now else None
    gaps = []
    if listing.get("currency_guess"):
        gaps.append("para_birimi_tahmin")
    if listing.get("currency") == "TRY":
        gaps.append("tl_fiyat")  # TL ilanlar tabloya göre %6-10 ucuz görünür: 🟢/🟠 olmaz (en fazla 🟡)
    if not listing.get("model_norm"):
        gaps.append("model_yok")
    elif model_ambiguous(listing):
        gaps.append("model_belirsiz")  # farklı modeller aynı anahtarda (geçici yama; kalıcısı model adı tablosu)
    if listing.get("extraction_by") == "llm":
        gaps.append("llm_okudu")  # yapay zekâ okuması tek başına 🟢 vermez
    km, med = effective_km(listing, today), market.median_km
    if km and med and market.near_n is None and km > med * s.km_high_ratio and km - med >= s.km_high_margin:
        gaps.append("km_yuksek")  # yalnız km'ye göre DÜZELTİLMEMİŞ piyasada (07.10.2026): düzeltilmiş fiyatta yüksek km zaten düşülmüş; yüksek km'li
        # ilanda düzeltilmiş medyan sapmasız ölçüldü (km/emsal km 1,3-1,6: %+1,0; 1,6+: %-0,8), ikinci ceza yanlış 🟡 üretiyordu. Uzak km'yi yakın emsal kapısı tutar.
    if km_thousand_missing_high(listing, market, s, today):
        gaps.append("km_bin_eksik_yuksek")  # "214 km" ≈ 214.000 ve emsallerden çok yüksek: km_yuksek gibi 🟢'yi 🟡'ya düşürür (bkz. DEGER_MOTORU §9)
    if market.near_n is not None and market.near_n < s.km_near_min_comparables:
        gaps.append("km_yakin_emsal_az")  # "8 benzer araç" kanıtı uzak km'li emsallerle tamamlanmaz (km'si bilinmeyen ilanda near_n yok: kural yok)
    return gaps


def below_cheap_quartile(price: float, market: Market) -> bool:
    """🟢 için ilan, emsallerin en ucuz çeyreğinde (alt çeyrek ya da altı) olmalı; sadece medyandan %20 ucuz olmak yetmez."""
    return market.p25_gbp is None or price <= market.p25_gbp
