import re
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class ParsedPrice:
    amount: float
    currency: str  # GBP / TRY / EUR / USD
    currency_guess: bool  # para birimi yazmıyordu, STG varsayıldı


@dataclass(frozen=True)
class PriceToken:
    """Metindeki bir sayı ve (varsa) yanındaki para birimi. Konumlar verilen metne göredir."""
    amount: float
    currency: str | None
    start: int
    end: int
    tight: bool = False  # para birimi boşluksuz bitişik: "£6500", "6500£"
    cur_span: tuple[int, int] | None = None  # para biriminin metindeki yeri (iki sayı aynı birimi paylaşıyor mu?)
    yearish: bool = False  # ayraçsız 1980–2039: model yılı olabilir


_NUM = re.compile(r"(?<![\d.,])\d[\d.,]*")
_SCALE = re.compile(r"\s*(?:bin|k)(?![a-zçğıöşü])", re.I)  # "15 bin", "7.5k"
_ORDINAL = re.compile(r"\s*(?:el\b|sahib)", re.I)  # "1. el", "2.el", "ilk sahibi": fiyat değil
_YEARISH = re.compile(r"19[89]\d|20[0-3]\d")
_CURRENCIES = [
    ("TRY", r"(?<![a-z])(?:tl|try)(?![a-z])|₺"),
    ("GBP", r"(?<![a-z])(?:stg|gbp|sterlin|sterling|str|pound|pounds|paund)(?![a-z])|£"),  # "6100str", "49900 paund"
    ("EUR", r"(?<![a-z])(?:eur|euro)(?![a-z])|€"),
    ("USD", r"(?<![a-z])(?:usd|dolar)(?![a-z])|\$"),
]
_GAP = r"[\s()\[\]:=/–-]*"  # sayı ile para birimi arasında olabilenler: "6.500 (STG)", "STG: 6.500", "£ - 6.500"
_ANY = [(code, re.compile(p, re.I)) for code, p in _CURRENCIES]
_AFTER = [(code, re.compile(f"({_GAP})({p})", re.I)) for code, p in _CURRENCIES]
_BEFORE = [(code, re.compile(f"({p})({_GAP})$", re.I)) for code, p in _CURRENCIES]
_BEFORE_REACH = 12  # "sterling - " gibi en uzun önek
_SEP = "[ \\u00a0\\u202f\\u2009/]"  # boşluk türleri (bölünmez dahil) ve "/" ("12/800 £", "180/000 km")
_SPLIT_THOUSANDS = re.compile(rf"(?<![\d.,/])(\d{{1,3}})((?:{_SEP}\d{{3}})+)(?![\d.,/]?\d)")


def join_split_thousands(text: str) -> str:
    """Boşluk ya da '/' ile yazılmış binlik ayracı: '6 500 STG' -> '6.500 STG', '1 650 000 TL' -> '1.650.000 TL', '12/800 £' -> '12.800 £'
    (yoksa '500' / '800' ayrı sayı sanılır). Üç haneli bir sayının ardından iki ya da daha çok grup gelirse (100 milyon üstü) baştaki
    sayı model adıdır, katılmaz: 'Peugeot 308 120 000 km' -> 'Peugeot 308 120.000 km'."""
    def join(m: re.Match) -> str:
        head, groups = m.group(1), re.findall(r"\d{3}", m.group(2))
        if len(head) == 3 and len(groups) >= 2:
            return head + " " + ".".join(groups)
        return ".".join([head, *groups])
    return _SPLIT_THOUSANDS.sub(join, text)


def _to_float(token: str) -> float | None:
    token = token.strip(".,")
    if not token:
        return None
    # "10.000" / "10,000" binlik ayraç; "10.5" ondalık
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", token):
        return float(re.sub(r"[.,]", "", token))
    try:
        return float(token.replace(",", "."))
    except ValueError:  # "1.2.3" gibi bozuk sayı
        return None


def _adjacent_currency(text: str, start: int, end: int) -> tuple[str, bool, tuple[int, int]] | None:
    """(para birimi, boşluksuz bitişik mi, yeri): önce sayının arkası, sonra önü."""
    for code, pattern in _AFTER:
        m = pattern.match(text, end)
        if m:
            return code, not m.group(1), m.span(2)
    lo = max(0, start - _BEFORE_REACH)
    for code, pattern in _BEFORE:
        m = pattern.search(text, lo, start)
        if m:
            return code, not m.group(2), m.span(1)
    return None


def _resolve_shared(tokens: list[PriceToken]) -> list[PriceToken]:
    """Bir para birimi iki sayının arasındaysa ('2014 £6500', '9500 STG 2016') tek sayıya verilir: boşluksuz bitişik olana; ikisi de
    ayrıksa yıl görünümlü olmayana. Karar verilemezse ikisinde de kalır."""
    by_span: dict[tuple[int, int], list[int]] = {}
    for i, t in enumerate(tokens):
        if t.cur_span:
            by_span.setdefault(t.cur_span, []).append(i)
    drop: set[int] = set()
    for idx in by_span.values():
        if len(idx) < 2:
            continue
        keep = [i for i in idx if tokens[i].tight] or [i for i in idx if not tokens[i].yearish] or idx
        drop.update(i for i in idx if i not in keep)
    return [replace(t, currency=None, tight=False, cur_span=None) if i in drop else t for i, t in enumerate(tokens)]


def price_tokens(text: str) -> list[PriceToken]:
    """Metindeki fiyat adayları (sıra korunur). Boşluklu binlik için önce join_split_thousands uygulanmış olmalı."""
    out = []
    for match in _NUM.finditer(text):
        token = match.group().rstrip(".,")
        end = match.start() + len(token)
        if _ORDINAL.match(text, end) or (text[end:end + 1] == "." and _ORDINAL.match(text, end + 1)):
            continue  # "1. el"
        amount = _to_float(token)
        if amount is None or amount <= 0:
            continue
        yearish = bool(_YEARISH.fullmatch(token))
        scale = _SCALE.match(text, end)
        if scale and amount < 1000:
            amount *= 1000
            end = scale.end()
            yearish = False
        cur = _adjacent_currency(text, match.start(), end)
        out.append(PriceToken(amount, cur[0] if cur else None, match.start(), end, bool(cur and cur[1]), cur[2] if cur else None,
                              yearish))
    return _resolve_shared(out)


def parse_price(text: str) -> ParsedPrice | None:
    """'Fiyat: 6.900 STG', '600.000 TL', '£6,900', '15 bin STG', '1. el 8.500 STG', '6 500 STG', '12/800 £' -> ParsedPrice."""
    if not text:
        return None
    text = join_split_thousands(text)
    candidates = price_tokens(text)
    if not candidates:
        return None
    with_currency = next((c for c in candidates if c.currency), None)
    if with_currency:
        return ParsedPrice(with_currency.amount, with_currency.currency, False)
    chosen = next((c for c in candidates if not c.yearish), candidates[0])  # "2014 model, fiyat 6500 (TL değil)" -> 6500
    for code, pattern in _ANY:
        if pattern.search(text):
            return ParsedPrice(chosen.amount, code, False)
    # Para birimi yazmıyor: araç için £60.000 üstü gerçekçi değil, TL varsay (tahmin olarak işaretli)
    return ParsedPrice(chosen.amount, "TRY" if chosen.amount >= 60000 else "GBP", True)
