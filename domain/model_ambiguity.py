"""Model anahtarı birden çok FARKLI aracı birleştiriyorsa o ilanda 🟢 (ve 🟠) verilmez.
`normalize_model` çoğu markada yalnızca ilk kelimeyi alır: CX-3/CX-5/CX-30 hepsi "cx", CR-V/CR-Z "cr", T-Cross/T-Roc "t";
Yaris/Yaris Cross ve Corolla/Corolla Cross "yaris"/"corolla". Havuz karıştığı için ucuz araç pahalı olanla kıyaslanıp sahte fırsat çıkar.
GEÇİCİ YAMA: kalıcı çözüm model adı tablosudur (yol haritası İş 9); o gelince bu liste boşalır."""
import re

from domain.normalize import fold

MIXED_KEYS = {("Mazda", "cx"), ("Honda", "cr"), ("Volkswagen", "t")}  # farklı modeller tek anahtarda: ilanın kendisi hangisi olursa olsun havuz karışık
CROSS_KEYS = {("Toyota", "yaris"), ("Toyota", "corolla")}  # düz model ile "Cross" aynı anahtarda
CROSS_FROM_YEAR = 2020  # Cross sürümleri bu yıldan sonra var; daha eski ilanın emsal penceresinde Cross yok
_CROSS = re.compile(r"\bcross\b")


def model_ambiguous(listing: dict) -> bool:
    key = (listing.get("brand_norm"), listing.get("model_norm"))
    if key in MIXED_KEYS:
        return True
    if key in CROSS_KEYS:
        year = listing.get("year")
        return bool(_CROSS.search(fold(listing.get("model") or ""))) or bool(year and year >= CROSS_FROM_YEAR)
    return False
