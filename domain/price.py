import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedPrice:
    amount: float
    currency: str  # GBP / TRY / EUR / USD
    currency_guess: bool  # para birimi yazmıyordu, STG varsayıldı


_NUM = re.compile(r"(?<![\d.,])\d[\d.,]*")
_SCALE = re.compile(r"\s*(?:bin|k)(?![a-zçğıöşü])", re.I)  # "15 bin", "7.5k"
_ORDINAL = re.compile(r"\s*(?:el\b|sahib)", re.I)  # "1. el", "2.el", "ilk sahibi": fiyat değil
_CURRENCIES = [
    ("TRY", r"(?<![a-z])(?:tl|try)(?![a-z])|₺"),
    ("GBP", r"(?<![a-z])(?:stg|gbp|sterlin)(?![a-z])|£"),
    ("EUR", r"(?<![a-z])(?:eur|euro)(?![a-z])|€"),
    ("USD", r"(?<![a-z])(?:usd|dolar)(?![a-z])|\$"),
]
_ANY = [(code, re.compile(p, re.I)) for code, p in _CURRENCIES]
_AFTER = [(code, re.compile(r"\s*(?:" + p + ")", re.I)) for code, p in _CURRENCIES]
_BEFORE = [(code, re.compile(r"(?:" + p + r")\s*$", re.I)) for code, p in _CURRENCIES]


def _to_float(token: str) -> float | None:
    token = token.strip(".,")
    if not token:
        return None
    # "10.000" / "10,000" binlik ayraç; "10.5" ondalık
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", token):
        return float(re.sub(r"[.,]", "", token))
    return float(token.replace(",", "."))


def _adjacent_currency(text: str, start: int, end: int) -> str | None:
    for code, pattern in _AFTER:
        if pattern.match(text, end):
            return code
    for code, pattern in _BEFORE:
        if pattern.search(text[max(0, start - 6):start]):
            return code
    return None


def parse_price(text: str) -> ParsedPrice | None:
    """'Fiyat: 6.900 STG', '600.000 TL', '£6,900', '15 bin STG', '1. el 8.500 STG' -> ParsedPrice."""
    if not text:
        return None
    candidates = []  # (amount, currency yanında var mı, para birimi)
    for match in _NUM.finditer(text):
        token = match.group().rstrip(".,")
        end = match.start() + len(token)
        if _ORDINAL.match(text, end) or (text[end:end + 1] == "." and _ORDINAL.match(text, end + 1)):
            continue  # "1. el"
        amount = _to_float(token)
        if amount is None or amount <= 0:
            continue
        scale = _SCALE.match(text, end)
        if scale and amount < 1000:
            amount *= 1000
            end = scale.end()
        candidates.append((amount, _adjacent_currency(text, match.start(), end)))
    if not candidates:
        return None
    amount, currency = next((c for c in candidates if c[1]), candidates[0])
    if currency:
        return ParsedPrice(amount, currency, False)
    for code, pattern in _ANY:
        if pattern.search(text):
            return ParsedPrice(amount, code, False)
    # Para birimi yazmıyor: araç için £60.000 üstü gerçekçi değil, TL varsay (tahmin olarak işaretli)
    return ParsedPrice(amount, "TRY" if amount >= 60000 else "GBP", True)
