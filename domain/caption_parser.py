"""Şablonlu ilan sayfası caption'larını (Instagram) ayrıştırır. Şablon bozuksa None döner (Haiku'ya düşer)."""
import re
from dataclasses import dataclass, field

from domain.price import parse_price

_LABEL_KEYS = [  # (anahtar kelime, alan) — etiketin içinde geçmesi yeterli
    ("marka", "model_line"),
    ("yıl", "year"),
    ("yakıt", "fuel"),
    ("vites", "transmission"),
    ("kilometre", "km"),
    ("dümen", "steering"),
    ("direksiyon", "steering"),
    ("konum", "location"),
    ("telefon", "phone"),
    ("iletişim", "phone"),
    ("fiyat", "price"),
    ("takas", "swap"),
]
_LINE = re.compile(r"^[^\w]*([^:\n]{2,40}?)\s*:\s*(.*)$", re.UNICODE)


_SOLD = re.compile(r"sat[ıi]ld[ıi]|sat[ıi]lm[ıi][şs]", re.I)
_ILAN_NO = re.compile(r"[İI]lan\s*(?:Numaras[ıi]|No)\s*[:.]?\s*\W{0,4}(\d{3,8})", re.I)


def is_sold_post(caption: str) -> bool:
    return bool(caption and _SOLD.search(caption))


def sold_ilan_no(caption: str) -> str | None:
    """Sayfanın "SATILDI" paylaşımı hangi ilan numarasını kapatıyor? (aynı sayfanın eski ilanı satıldı sayılır)"""
    if not caption or not _SOLD.search(caption):
        return None
    m = _ILAN_NO.search(caption)
    return m.group(1) if m else None


def tr_lower(text: str) -> str:
    return text.replace("İ", "i").replace("I", "ı").lower()


@dataclass
class ParsedCaption:
    brand: str | None = None
    model: str | None = None
    year: int | None = None
    km: int | None = None
    fuel: str | None = None
    transmission: str | None = None
    steering: str | None = None  # RHD / LHD
    location: str | None = None
    phone: str | None = None  # 90533xxxxxxx
    price_raw: str | None = None
    price_amount: float | None = None
    currency: str | None = None
    currency_guess: bool = False
    negotiable: bool = False
    swap: bool | None = None
    notes: list[str] = field(default_factory=list)


def normalize_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("90") and len(digits) == 12:
        return digits
    if digits.startswith("0") and len(digits) == 11:
        return "9" + digits
    if len(digits) == 10 and digits.startswith("5"):
        return "90" + digits
    return None


def _year(value: str) -> int | None:
    m = re.search(r"\b(19|20)\d{2}\b", value)
    return int(m.group()) if m else None


def _km(value: str) -> int | None:
    if "*" in value:
        return None  # "174.*** KM" -> belirsiz
    digits = re.sub(r"[.,\s]", "", re.sub(r"(?i)km", "", value))
    return int(digits) if digits.isdigit() and int(digits) > 0 else None


def _steering(value: str) -> str | None:
    v = tr_lower(value)
    if "sağ" in v or "sag" in v:
        return "RHD"
    if "sol" in v:
        return "LHD"
    return None


def parse_caption(caption: str) -> ParsedCaption | None:
    fields: dict[str, str] = {}
    for line in caption.splitlines():
        m = _LINE.match(line.strip())
        if not m:
            continue
        label = tr_lower(m.group(1))
        key = next((f for kw, f in _LABEL_KEYS if kw in label), None)
        if key and key not in fields:
            fields[key] = m.group(2).strip()

    # Zorunlu alanlar yoksa şablon değil
    if not all(k in fields for k in ("model_line", "year", "price")):
        return None

    out = ParsedCaption()
    parts = fields["model_line"].split(None, 1)
    out.brand = parts[0].title() if parts else None
    out.model = parts[1].strip() if len(parts) > 1 else None
    out.year = _year(fields["year"])
    if re.search(r"(19|20)\d{2}\s*[/(]", fields["year"]):
        out.notes.append("yil_belirsiz")
    out.km = _km(fields.get("km", ""))
    out.fuel = tr_lower(fields.get("fuel", "")) or None
    out.transmission = tr_lower(fields.get("transmission", "")) or None
    out.steering = _steering(fields.get("steering", ""))
    out.location = fields.get("location") or None
    out.phone = normalize_phone(re.split(r"[^\d\s+()-]", fields.get("phone", ""))[0])
    out.price_raw = fields["price"]
    price = parse_price(fields["price"])
    if price:
        out.price_amount, out.currency, out.currency_guess = price.amount, price.currency, price.currency_guess
    out.negotiable = bool(re.search(r"pazarl[ıi]k\s+pay[ıi]\s+var|pazarl[ıi]k\s+var|pazarl[ıi]k\s+mevcut", caption, re.I))
    swap = tr_lower(fields.get("swap", ""))
    if swap.startswith(("var", "evet")):
        out.swap = True
    elif swap.startswith(("yok", "hayır", "hayir")):
        out.swap = False
    return out
