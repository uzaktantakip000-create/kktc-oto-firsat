"""Marka/model adlarını emsal eşleştirmesi için standart anahtara çevirir."""
import re
import unicodedata

from domain.model_keys import model_key

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


# Vites/yakıt yazımı kaynaklar arasında farklı (KKTCarabam "düz"/"elektrik", KAA "manuel"/"elektrikli"): aynı araç tipi emsal olabilsin diye
# tek standart yazıma çevrilir. Yalnızca belirsizliği olmayan eşlemeler (bilinmeyen değer olduğu gibi kalır: "benzin / hibrit" gibi).
_FUEL_CANON = {"elektrik": "elektrikli", "electric": "elektrikli", "hybrit": "hibrit", "hybrid": "hibrit", "mazot": "dizel"}
_TRANSMISSION_CANON = {"düz": "manuel", "duz": "manuel", "düz vites": "manuel", "duz vites": "manuel", "manual": "manuel",
                       "automatic": "otomatik", "atomatik": "otomatik", "otamatık": "otomatik", "otomotik": "otomatik"}


def canon_fuel(value: str | None) -> str | None:
    """Yakıt yazımını standarda çevirir ('elektrik' -> 'elektrikli'); boş/None olduğu gibi döner."""
    if not value:
        return value
    key = value.strip().lower()
    return _FUEL_CANON.get(key, key)


def canon_transmission(value: str | None) -> str | None:
    """Vites yazımını standarda çevirir ('düz' -> 'manuel'); boş/None olduğu gibi döner."""
    if not value:
        return value
    key = value.strip().lower()
    return _TRANSMISSION_CANON.get(key, key)


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
    "Motosiklet", "Piaggio", "Aprilia", "Kymco", "Benelli", "Husqvarna", "Royal Enfield", "Mv Agusta",
}

# Araba markası altında listelenen motosiklet/scooter/kamyon/tekne modelleri (model_norm ilk kelimedir: "CBR 1000 RR" -> "cbr").
# (marka_norm -> [(model_norm deseni, hedef marka)]). Ham marka sütunu değişmez; yalnızca brand_norm düzeltilir.
_D = r"(?:\d+[a-z]*)?$"  # sayı eki: "pcx125", "cb500x"
NON_CAR_MODELS: dict[str, list[tuple[re.Pattern, str]]] = {
    brand: [(re.compile(p), target) for p, target in rules] for brand, rules in {
        "Honda": [(r"^(?:cb|cbr|cbf|cbx|crf|crm|nc|nx|nt|xl|xr|xre|x|pcx|sh|forza|nss|vfr|vtx|cmx|gl|transalp|africa|rebel|grom|monkey|dax|cub|"
                   r"supercub|vision|lead|dio|activa|today|joker|spacy|pantheon|goldwing|gold|shadow|hornet|varadero|deauville|silverwing|"
                   r"swing|msx|ctx|ct)" + _D, "Motosiklet"),
                  (r"^400x$", "Motosiklet"), (r"^tekne$", "Balikci Teknesi")],
        "BMW": [(r"^(?:f|g|r|c|k|s|ce)$|^(?:f|g|r|c|k|s)\d{3,4}[a-z]*$|^ce\d+$|^r18$", "Motosiklet")],  # f30/g20 gibi 2 haneli şasi kodları araba
        "Suzuki": [(r"^(?:gsx|gsr|gs|sv|dr|drz|rm|rmz|v|vstrom|burgman|an|adress|address|intruder|hayabusa|bandit|boulevard|gn|gz|lt|ltz|df|"
                    r"skywave|katana|vl|vz)" + _D, "Motosiklet")],
        "Peugeot": [(r"^(?:speedfight|django|kisbee|tweet|vivacity|ludix|jetforce|trekker|elystar|citystar)" + _D, "Motosiklet")],
        "Mercedes-Benz": [(r"^(?:actros|atego|axor|unimog|zetros)" + _D, "Kamyon & Kamyonet")],
        "Mitsubishi": [(r"^(?:canter|fuso)" + _D, "Kamyon & Kamyonet")],
        "Isuzu": [(r"^(?:elf|nqr|npr|nkr)" + _D + r"|^n\d{2}[\d.]*$", "Kamyon & Kamyonet")],
    }.items()
}
# model_norm yetmediğinde ham modele bakılanlar: BMW "M 1000 RR" motosiklet, "M Serisi M4" araba (ikisi de model_norm "m")
_NON_CAR_RAW: dict[str, list[tuple[re.Pattern, str]]] = {"BMW": [(re.compile(r"^m ?1000"), "Motosiklet")]}


def reclassify_non_car(brand_norm: str | None, model_norm: str | None, model: str | None = None) -> str | None:
    """Araba markası altındaki motosiklet/kamyon/tekne modelini doğru kategoriye çevirir ('Motosiklet' vb.); değilse marka aynen döner."""
    if not brand_norm:
        return brand_norm
    for pat, target in NON_CAR_MODELS.get(brand_norm, ()):
        if model_norm and pat.search(model_norm):
            return target
    raw = fold(model or "").replace("-", " ")
    for pat, target in _NON_CAR_RAW.get(brand_norm, ()):
        if pat.search(raw):
            return target
    return brand_norm


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
    m = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9. ]", "", m)).strip()  # baştaki boşluk önek temizliğini engelliyordu ("- Benz GLE" -> "benz")
    if not m:
        return None
    if brand_norm == "Land Rover":
        m = re.sub(r"^rover\s+(?=\S)", "", m)  # KKTCarabam: marka "Land", model "Rover Range Rover Evoque"
    if brand_norm == "Mercedes-Benz":
        m = re.sub(r"^(mercedes\s+)?(benz\s+)?(?=\S)", "", m)  # marka "Mercedes", model "Benz C200"
        key = model_key(brand_norm, m)
        if key:
            return key
        mm = _MERCEDES.match(m)
        if mm:
            return mm.group(1)  # "e 220d", "e serisi", "e220" -> "e"
    if brand_norm == "BMW":
        key = model_key(brand_norm, m)
        if key:
            return key
        mm = _BMW.match(m)
        if mm:
            return next(g for g in mm.groups() if g)  # "520d m sport" -> "5"
    if brand_norm == "Land Rover":
        return _land_rover(m)
    key = model_key(brand_norm, m)
    if key:
        return key
    two = " ".join(m.split()[:2])
    if two in _TWO_WORD:
        return two
    first = m.split()[0] if m.split() else None
    return first
