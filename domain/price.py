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
    box: bool = False  # Facebook satış kutusu yazımı "TRY5,200": büyük harf "TRY" sayının ÖNÜNE bitişik (FB_BOX_TRY)


# TL ile bundan küçük tutar araç fiyatı değildir (21.10.2026; 50.000 TL ≈ £760, kur ~66 TL/£): metinde kapora/peşinat/taksit, masraf, yol
# vergisi, parça ("PEŞİNAT 4.250 ₺", "YOL RUSHATI 15000 TL", "30 bin tl lik ses sistemi"; fiyatı böyle okunmuş 17 Facebook ilanı = 7 araç:
# 4'ünde tutar fiyat değil, 3'ü £450–600'lük hurda) ya da Facebook satış kutusunda TL seçilip yazılmış STG rakamı (FB_BOX_TRY).
# Sitelerin yapılandırılmış TL fiyatı (KKTCar'da 1990'ların aracı 25–48 bin TL) ayrıştırıcıdan geçmez; ikinci kapılar (taze TL, ⚡) onları da
# dışarıda bırakır ama bu tutarda 🟢/⚡ zaten çıkmaz (asgari kâr £750, piyasanın yarısından ucuz fiyat).
# application.social_ingest.FB_EURO_TL_FROM aynı sınırdır (€ kutusu tutarı bundan büyükse TL).
TL_CAR_MIN = 50_000
# Facebook satış kutusu fiyatı "TRY5,200" diye gösterir (satıcının seçtiği birim; okuyucu satırı "ek_metin"e ya da gövdeye "+6⏎TRY100⏎…"
# biçiminde yazar). Ölçüm (21.10.2026, bütün kaynaklar): "TRY" yazımı yalnız Facebook'ta ve yalnız bu biçimde (254 kez); kullanıcı TL'yi
# "TL"/"₺" yazar. Kutuda TRY ≤ FB_BOX_TRY_PLACEHOLDER_MAX yer tutucudur (TRY1, TRY111, TRY321: 73 ilan); 1.000–50.000 arası STG rakamıdır
# (83 ilan: gövdesinde fiyat yazan 20'sinin hepsinde gövde AYNI rakamı £ ile yazıyor, "5.200 £" + kutu TRY5,200; farklı tutar 0).
FB_BOX_TRY = "TRY"
FB_BOX_TRY_PLACEHOLDER_MAX = 1_000


def placeholder_amount(amount: float | None) -> bool:
    """Yer tutucu tutar (comparables.PLACEHOLDER_KM'nin fiyat karşılığı): satıcı fiyat yazmak istemiyor ama kutu/şablon fiyat istiyor.
    Aynı rakamın en az 5 kez tekrarı (11.111, 111.111, 1.111.111, 999.999) ya da 1234… / 98765… sırası (1.234, 12.345, 123.456,
    1.234.567; 98.765, 987.654). Para birimi fark etmez; kuruşlu tutar yer tutucu sayılmaz. 4 haneli tekrar (5.555, 9.999) gerçek fiyat
    olabilir, sayılmaz. Ölçüm (21.10.2026, 9.175 fiyatlı ilan): 160 ilan, HEPSİ Facebook (€1.111.111 ×68, €111.111 ×54, €11.111 ×10,
    1.234 / 12.345 / 123.456 / 1.234.567 ×27); sitelerde 0. € kuralıyla TL/STG tahmini sanılıyordu (€1.111.111 ≈ £16.800): 155 € ilanının
    61'i değerlendirildi, 51 değerlendirme 🟡 (🟢/⚡ 0; emsal olmadı: para birimi tahmini)."""
    if amount is None:
        return False
    a = float(amount)
    if a <= 0 or a != int(a):
        return False
    d = str(int(a))
    return (len(d) >= 5 and len(set(d)) == 1) or (len(d) >= 4 and "1234567890".startswith(d)) or (len(d) >= 5 and "9876543210".startswith(d))


def implausible_amount(amount: float | None, currency: str | None) -> bool:
    """Araç fiyatı olamayacak tutar: yer tutucu (placeholder_amount) ya da TL_CAR_MIN altı TL. Ayrıştırıcıdan kaçmış ESKİ satırlar ve yapay
    zekâ okuması için ikinci kapı (data_gate.tl_fresh, application.urgent, llm_read)."""
    if amount is None:
        return False
    return placeholder_amount(amount) or (currency == "TRY" and float(amount) < TL_CAR_MIN)


# Noktaya yapışık sayı yalnız noktadan önce harf varsa ("tamamdır.250.000 tl"); "1.250"nin "250"si ya da "..5" ayrı sayı değildir (27.10.2026)
_NUM = re.compile(r"(?<![\d,])(?:(?<=[^\W\d_]\.)|(?<!\.))\d[\d.,]*")
_SCALE = re.compile(r"\s*(?:bin|k)(?![a-zçğıöşü])", re.I)  # "15 bin", "7.5k"
_ORDINAL = re.compile(r"\s*(?:el\b|sahib)", re.I)  # "1. el", "2.el", "ilk sahibi": fiyat değil
_YEARISH = re.compile(r"19[89]\d|20[0-3]\d")
# Birime yapışık Türkçe ek-fiil "-dır/-dir/-tır/-dur...": "5.200 sterlindir", "8500 stgdir", "250 bin tldir" (27.10.2026). Yönelme eki ("tlye",
# "stge kadar") birim sayılmaz: alım ilanındaki bütçe olur.
_TR_COPULA = r"(?:(?![a-z])|(?=[dt][ıiuü]r(?![a-zçğıöşü])))"
_CURRENCIES = [
    ("TRY", rf"(?<![a-z])(?:tl|try){_TR_COPULA}|₺"),
    ("GBP", rf"(?<![a-z])(?:stg|gbp|sterlin|sterling|str|pound|pounds|paund){_TR_COPULA}|£"),  # "6100str", "49900 paund"
    ("EUR", r"(?<![a-z])(?:eur|euro)(?![a-z])|€"),
    ("USD", r"(?<![a-z])(?:usd|dolar)(?![a-z])|\$"),
]
_GAP = r"(?:\.-)?[\s()\[\]:=/–-]*"  # sayı ile para birimi arasında olabilenler: "6.500 (STG)", "STG: 6.500", "£ - 6.500", "8.500.-stg"
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
    return [replace(t, currency=None, tight=False, cur_span=None, box=False) if i in drop else t for i, t in enumerate(tokens)]


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
        box = bool(cur and cur[1] and cur[2][1] == match.start() and text[cur[2][0]:cur[2][1]] == FB_BOX_TRY)  # "TRY5,200"
        out.append(PriceToken(amount, cur[0] if cur else None, match.start(), end, bool(cur and cur[1]), cur[2] if cur else None,
                              yearish, box))
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
