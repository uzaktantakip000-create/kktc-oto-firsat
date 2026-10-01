"""Eksik/şüpheli veriyle 🟢 verilmesini engelleyen kural (en fazla 🟡)."""
from domain.comparables import Market, effective_km
from domain.llm_read import MISMATCH_LABELS
from domain.settings import Settings

GAP_LABELS = {
    "km_yok": "km yazmıyor",
    "para_birimi_tahmin": "para birimi tahmin",
    "model_yok": "model okunamadı",
    "km_yuksek": "km emsallerden çok yüksek",
    "ucuz_ceyrek_degil": "fiyat benzer araçların en ucuz çeyreğinde değil",
    "plaka_uyari": "TR/yabancı plaka yazıyor",
    "sessiz_model": "bu modele 3 kez 'pas' dedin (özete alındı)",
    "llm_okudu": "kural okuyamadı, yapay zekâ okudu (kontrol et)",
}
GAP_LABELS.update(MISMATCH_LABELS)


def data_gaps(listing: dict, market: Market, settings: Settings | None = None) -> list[str]:
    s = settings or Settings()
    gaps = []
    if not effective_km(listing):  # yok ya da şüpheli (eski araçta <1000 km)
        gaps.append("km_yok")
    if listing.get("currency_guess"):
        gaps.append("para_birimi_tahmin")
    if not listing.get("model_norm"):
        gaps.append("model_yok")
    if listing.get("extraction_by") == "llm":
        gaps.append("llm_okudu")  # yapay zekâ okuması tek başına 🟢 vermez
    km, med = effective_km(listing), market.median_km
    if km and med and km > med * s.km_high_ratio and km - med >= s.km_high_margin:
        gaps.append("km_yuksek")
    return gaps


def below_cheap_quartile(price: float, market: Market) -> bool:
    """🟢 için ilan, emsallerin en ucuz çeyreğinde (alt çeyrek ya da altı) olmalı; sadece medyandan %20 ucuz olmak yetmez."""
    return market.p25_gbp is None or price <= market.p25_gbp
