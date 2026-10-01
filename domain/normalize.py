"""Marka/model adlarını emsal eşleştirmesi için standart anahtara çevirir."""
import re
import unicodedata

_BRANDS = {
    "bmw": "BMW", "mercedes": "Mercedes-Benz", "mercedes-benz": "Mercedes-Benz", "mercedes benz": "Mercedes-Benz",
    "vw": "Volkswagen", "volkswagen": "Volkswagen", "vauxhall": "Opel", "opel": "Opel", "mg": "MG",
    "mersedez": "Mercedes-Benz", "mersedes": "Mercedes-Benz", "mercedez": "Mercedes-Benz", "merc": "Mercedes-Benz",
    "land": "Land Rover", "land rover": "Land Rover", "range": "Land Rover", "citroën": "Citroen", "seat": "Seat",
}
# Çok kelimeli model aileleri (ilk kelime tek başına yetmez)
_TWO_WORD = {"transit connect", "civic type", "land cruiser", "model 3", "model y", "model s", "model x", "range rover",
             "golf plus", "grand vitara", "grand cherokee", "city crossover"}
_MERCEDES = re.compile(r"^(gla|glb|glc|gle|gls|cla|cls|clk|slk|sl|eqa|eqb|eqc|a|b|c|e|s|g|v|x)(?=\d|\s|$)")
_BMW = re.compile(r"^([1-8])\d{2}[a-z]{0,2}\b|^([1-8])\.\d{2}[a-z]?\b|^([1-8])\s*serisi")


def fold(text: str) -> str:
    """Küçük harf, Türkçe/aksan temizliği: 'Ni̇ssan' -> 'nissan', 'vıtz' -> 'vitz'."""
    text = text.replace("ı", "i").replace("İ", "i")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", text).strip()


# Araba olmayan kategoriler (KibrisArabaAl motosiklet, tekne, karavan, ticari vb. de listeler): değerlendirilmez, emsale girmez
NON_CAR_BRANDS = {
    "Yamaha", "Kamyon & Kamyonet", "Ticari Araclar", "Minibus & Midibus", "Cfmoto", "Harley Davidson", "Can-Am",
    "Cekme Karavan", "ATV", "Yuki", "KTM", "Alice", "Balikci Teknesi", "Kawasaki", "Moto Karavan", "Scooter", "SYM",
    "Vespa", "Volta", "Karavan", "Jet Ski", "Jawa", "Iveco", "Hino", "Abbey Karavan", "Surat Teknesi", "DAF", "Triumph",
    "TVS", "Vento", "MAN", "Lobster", "Bisiklet", "Motolux", "Motoryat", "Tesla Marin", "Ducati", "Sanya", "Access", "Test",
}


def is_car_brand(brand_norm: str | None) -> bool:
    return bool(brand_norm) and brand_norm not in NON_CAR_BRANDS


def normalize_brand(brand: str | None) -> str | None:
    if not brand:
        return None
    key = re.sub(r"\s*-\s*", "-", fold(brand))  # "Mercedes - Benz" = "Mercedes-Benz"
    if key in _BRANDS:
        return _BRANDS[key]
    return key.upper() if len(key) <= 3 else key.title()


def _land_rover(m: str) -> str | None:
    """Range Rover Evoque / Sport / Velar ve Discovery / Discovery Sport farklı araçlardır (fiyatları çok farklı)."""
    words = set(m.split())
    for key in ("evoque", "velar", "defender", "freelander"):
        if key in words:
            return key
    if "discovery" in words:
        return "discovery sport" if "sport" in words else "discovery"
    if "sport" in words:
        return "range rover sport"
    if m.startswith("range"):
        return "range rover"
    return m.split()[0] if m.split() else None


def normalize_model(brand_norm: str | None, model: str | None) -> str | None:
    if not model:
        return None
    m = fold(model).replace("-", " ")
    m = re.sub(r"[^a-z0-9. ]", "", m)
    if brand_norm == "Mercedes-Benz":
        m = re.sub(r"^(mercedes\s+)?(benz\s+)?(?=\S)", "", m)  # marka "Mercedes", model "Benz C200"
        mm = _MERCEDES.match(m)
        if mm:
            return mm.group(1)  # "e 220d", "e serisi", "e220" -> "e"
    if brand_norm == "BMW":
        mm = _BMW.match(m)
        if mm:
            return next(g for g in mm.groups() if g)  # "520d m sport" -> "5"
    if brand_norm == "Land Rover":
        return _land_rover(m)
    two = " ".join(m.split()[:2])
    if two in _TWO_WORD:
        return two
    first = m.split()[0] if m.split() else None
    return first
