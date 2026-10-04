"""Eksik/şüpheli veriyle 🟢 verilmesini engelleyen kural (en fazla 🟡)."""
from datetime import datetime

from domain.comparables import Market, effective_km
from domain.llm_read import MISMATCH_LABELS
from domain.model_ambiguity import model_ambiguous
from domain.settings import Settings

KM_UNKNOWN_WARNING = "km yazmıyor ya da şüpheli: aracı görmeden km'ye güvenme (fiyat kıyası km'siz yapıldı)"

GAP_LABELS = {
    "km_yok": "km yazmıyor",  # ESKİ kayıtlar için etiket: km eksikliği artık 🟢'yi engellemez, uyarı olarak gider (KM_UNKNOWN_WARNING)
    "para_birimi_tahmin": "para birimi tahmin",
    "tl_fiyat": "fiyat TL: TL ilanlar GBP ilanlara göre ortalama %12-23 ucuz görünüyor; kontrol et",
    "fiyat_asiri_dusuk": "fiyat emsallerin yarısından düşük (az emsal): yazım hatası ya da tuzak olabilir",
    "model_yok": "model okunamadı",
    "model_belirsiz": "model karışık havuzda (ör. CX-3/CX-5, Yaris/Yaris Cross): fiyat kıyası güvenilmez",
    "km_yuksek": "km emsallerden çok yüksek",
    "ucuz_ceyrek_degil": "fiyat benzer araçların en ucuz çeyreğinde değil",
    "emsal_yili_yeni": "az benzer araç bulundu, yıl aralığı genişletildi ve emsaller bu araçtan daha YENİ: fiyat kıyası güvenilmez",
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
    if km and med and km > med * s.km_high_ratio and km - med >= s.km_high_margin:
        gaps.append("km_yuksek")
    return gaps


def below_cheap_quartile(price: float, market: Market) -> bool:
    """🟢 için ilan, emsallerin en ucuz çeyreğinde (alt çeyrek ya da altı) olmalı; sadece medyandan %20 ucuz olmak yetmez."""
    return market.p25_gbp is None or price <= market.p25_gbp
