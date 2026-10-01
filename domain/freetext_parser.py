"""Serbest yazılmış ilan metnini (Facebook grup gönderisi) ayrıştırır.
Ölçüt: yanlış ilan üretmektense hiç üretmemek. Marka + yıl + AÇIK para birimli TEK fiyat yoksa None döner."""
import re

from domain.caption_parser import ParsedCaption, normalize_phone, tr_lower
from domain.normalize import fold
from domain.price import parse_price

_BRANDS = [  # (aranan kelime, marka)
    "toyota", "honda", "nissan", "mazda", "suzuki", "hyundai", "kia", "ford", "opel", "vauxhall", "renault", "peugeot",
    "citroen", "fiat", "bmw", "mercedes-benz", "mercedes", "audi", "volkswagen", "vw", "skoda", "seat", "volvo",
    "mitsubishi", "subaru", "lexus", "jeep", "land rover", "range rover", "mini", "mg", "dacia", "chevrolet", "daihatsu",
    "isuzu", "porsche", "tesla", "alfa romeo", "jaguar", "smart", "saab", "ssangyong", "cupra", "byd", "chery",
]
_BRAND_RE = re.compile(r"(?<![a-zçğıöşü0-9])(" + "|".join(re.escape(b) for b in sorted(_BRANDS, key=len, reverse=True))
                       + r")(?![a-zçğıöşü0-9])", re.I)
# ilan olmayan / araç olmayan gönderiler
_SKIP = re.compile(r"kiral[ıi]k|aran[ıi]yor|al[ıi]n[ıi]r|al[ıi]yoruz|almak\s+istiyorum", re.I)  # ilan türü: kiralık / alım ilanı
_SKIP_TITLE = re.compile(r"jant|lastik|tekne|bisiklet|motosiklet|yedek\s+par[çc]a|bah[çc]e", re.I)  # araç olmayan ürün (başlıkta)
_SKIP_HEAD = 150  # 'aranıyor/kiralık/jant' gibi ilan türünü belirten sözcükler gönderinin başında olur
# fiyat olmayan para satırları (tramer, boya, taksit...)
_NOT_PRICE_LINE = re.compile(r"tramer|boya|kredi|taksit|pe[şs]inat|komisyon|depozito|kapora|vergi|muayene|sigorta|"
                             r"[öo]deme|masraf|bak[ıi]m", re.I)
_CURRENCY = re.compile(r"£|₺|€|\$|(?<![a-z])(?:stg|gbp|tl|try|eur|euro|usd|sterlin|dolar)(?![a-z])", re.I)
_PRICE_HINT = re.compile(r"fiyat|nakit|nakite|pe[şs]in|sat[ıi][şs]", re.I)
_PHONE = re.compile(r"(?<!\d)(?:\+?\s*9?0[\s.-]*)?\(?5\d{2}\)?[\s.-]?\d{3}[\s.-]?\d{2}[\s.-]?\d{2}(?!\d)")
_YEAR = re.compile(r"(?<![\d.,£€$])(19[89]\d|20[0-2]\d)(?![\d.,]?\d)(?!\s*(?:£|₺|€|\$|tl\b|stg\b|gbp\b))", re.I)
_KM = re.compile(r"(?<![\d.,])(\d{1,3}(?:[.,]\d{3})+|\d{4,6})\s*(km|mil\w*)\b", re.I)
_KM_LABEL = re.compile(r"\bkm\s*[:\-]?\s*(\d{1,3}(?:[.,]\d{3})+|\d{4,6})\b", re.I)
_CC = re.compile(r"\b(\d{3,4})\s*cc\b", re.I)
_MODEL_STOP = {"il", "ilk", "ilan", "sahibinden", "galeriden", "yeni", "temiz", "hasarsiz", "hatasiz", "boyasiz", "tek",
               "motor", "arac", "otomobil", "arabasi", "araba", "marka", "model", "aracimiz", "gunluk", "satilik"}
_CITIES = ["girne", "lefkoşa", "lefkosa", "gazimağusa", "gazimagusa", "mağusa", "magusa", "güzelyurt", "guzelyurt",
           "iskele", "lefke", "alsancak", "lapta", "karpaz", "dikmen", "yeni boğaziçi", "boğaz", "bogaz", "ercan"]


def _not_a_sale_ad(text: str) -> bool:
    """Kiralık/aranıyor gönderisi ya da araç olmayan ürün. Sözcükler yalnızca başta aranır: satış ilanının içinde
    'lastikler yeni', 'takas alınır' gibi ifadeler geçebilir."""
    first_line = text.strip().split("\n", 1)[0]
    return bool(_SKIP.search(text[:_SKIP_HEAD]) or _SKIP_TITLE.search(first_line))


def _clean_lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _price(lines: list[str]) -> tuple[float, str, str] | None:
    """Tek, açık para birimli fiyat. Birden çok farklı tutar varsa 'fiyat/nakit' ipuçlu satırdaki tek tutar; yoksa belirsiz -> None."""
    found: list[tuple[float, str, str, bool]] = []
    for ln in lines:
        if re.search(r"tramer|boya", ln, re.I) and not _PRICE_HINT.search(ln):
            continue
        scrubbed = _PHONE.sub(" ", ln)
        # "5000£ taksitli" gibi aynı satırda birden çok tutar olabilir: her sayıyı ayrı dene
        for m in re.finditer(r"(?<![\d.,])\d[\d.,]*(?:\s*(?:bin|k)\b)?", scrubbed, re.I):
            window = scrubbed[max(0, m.start() - 6): m.end() + 8]
            if not _CURRENCY.search(window):
                continue
            before, after = scrubbed[max(0, m.start() - 14): m.start()], scrubbed[m.end(): m.end() + 14]
            hint = bool(_PRICE_HINT.search(before))
            if (_NOT_PRICE_LINE.search(before) or _NOT_PRICE_LINE.search(after)) and not hint:
                continue  # "taksitli 5000£", "peşinat 3000£", "tramer 32.000 TL"
            p = parse_price(window.strip())
            if p and not p.currency_guess and 300 <= p.amount <= 2_000_000:
                found.append((p.amount, p.currency, ln, hint or bool(_PRICE_HINT.search(ln)) and not _NOT_PRICE_LINE.search(ln)))
    if not found:
        return None
    amounts = {(a, c) for a, c, _, _ in found}
    if len(amounts) == 1:
        a, c, ln, _ = found[0]
        return a, c, ln
    hinted = {(a, c) for a, c, _, h in found if h}
    if len(hinted) == 1:
        a, c = next(iter(hinted))
        return a, c, next(ln for x, y, ln, _ in found if (x, y) == (a, c))
    return None


def _km(text: str) -> int | None:
    m = _KM.search(text)
    if m:
        if m.group(2).lower().startswith("mil"):
            return None  # mil = mil (İngiliz yolcu araçları): km'ye çevirmek yerine bilinmiyor say, veri kapısı en fazla 🟡 yapar
        digits = int(re.sub(r"[.,]", "", m.group(1)))
        return digits if 1000 <= digits <= 600_000 else None
    m = _KM_LABEL.search(text)
    if m:
        digits = int(re.sub(r"[.,]", "", m.group(1)))
        return digits if 1000 <= digits <= 600_000 else None
    return None


def parse_freetext(text: str, default_steering: str | None = None, max_year: int = 2027,
                   known_price: tuple[float, str] | None = None) -> ParsedCaption | None:
    """known_price=(tutar, para_birimi): sitenin yapılandırılmış alanından gelen kesin fiyat (metinde aranmaz)."""
    if not text or _not_a_sale_ad(text):
        return None
    brand_m = _BRAND_RE.search(text)
    if not brand_m:
        return None
    phones = [p for p in (normalize_phone(m.group()) for m in _PHONE.finditer(text)) if p]
    no_phone = _PHONE.sub(" ", text)
    years = [int(y) for y in _YEAR.findall(no_phone) if int(y) <= max_year]
    if not years:
        return None
    if known_price:
        amount, currency = known_price
        price_line = f"{amount:g} {currency}"
    else:
        price = _price(_clean_lines(no_phone))
        if price is None:
            return None
        amount, currency, price_line = price

    out = ParsedCaption()
    out.brand = brand_m.group(1).title() if brand_m.group(1).lower() not in ("bmw", "mg", "vw", "byd") else brand_m.group(1).upper()
    after = no_phone[brand_m.end():]
    words = re.findall(r"[A-Za-zÇĞİÖŞÜçğıöşü]*\d*[A-Za-zÇĞİÖŞÜçğıöşü0-9\-]*", after[:60])
    words = [w for w in words if w]
    model = None
    for i, w in enumerate(words[:4]):
        if re.fullmatch(r"(19|20)\d{2}|modeli?|otomatik|manuel|full", w, re.I) or fold(w) in _MODEL_STOP:
            continue
        model = w
        if w.lower() == "i" and i + 1 < len(words) and words[i + 1].isdigit():  # "İ 30" -> "i30"
            model = "i" + words[i + 1]
        break
    out.model = model
    line_start = no_phone.rfind("\n", 0, brand_m.start()) + 1
    line_end = no_phone.find("\n", brand_m.end())
    brand_line = no_phone[line_start: line_end if line_end != -1 else None]
    near = [int(y) for y in _YEAR.findall(brand_line) if int(y) <= max_year]
    out.year = near[0] if near else years[0]  # önce marka satırındaki yıl ("2021 HONDA FIT"), yoksa metindeki ilk yıl
    out.km = _km(no_phone)
    low = tr_lower(no_phone)
    if re.search(r"otomatik|automatic|\bauto\b|dsg|tiptronic", low):
        out.transmission = "otomatik"
    elif re.search(r"manuel|manual|düz vites|duz vites", low):
        out.transmission = "manuel"
    if re.search(r"dizel|diesel|tdi|cdi", low):
        out.fuel = "dizel"
    elif re.search(r"hybrid|hibrit", low):
        out.fuel = "hibrit"
    elif re.search(r"elektrik(li)?\s*(araç|arac|motor)|\bev\b", low) and not re.search(r"elektrikli (cam|ayna|bagaj|koltuk|park)", low):
        out.fuel = "elektrik"
    elif re.search(r"benzin|petrol", low):
        out.fuel = "benzin"
    if re.search(r"sol\s+direksiyon|\blhd\b", low):
        out.steering = "LHD"
    elif re.search(r"sağ\s+direksiyon|sag\s+direksiyon|\brhd\b", low):
        out.steering = "RHD"
    else:
        out.steering = default_steering
    out.location = next((c.title() for c in _CITIES if c in low), None)
    out.phone = phones[0] if phones else None
    out.price_raw = price_line[:80]
    out.price_amount, out.currency, out.currency_guess = amount, currency, False
    out.negotiable = bool(re.search(r"pazarl[ıi]k", low))
    out.swap = True if re.search(r"takas\s+(olur|var|yap[ıi]l[ıi]r|kabul)|takasa\s+uygun", low) else None
    return out


def diagnose(text: str) -> str:
    """Gönderi neden ilan sayılmadı? ('ok' = okunur). Yalnızca sayaç için; metin saklanmaz."""
    if not text or _not_a_sale_ad(text):
        return "arac_degil"
    if not _BRAND_RE.search(text):
        return "marka_yok"
    no_phone = _PHONE.sub(" ", text)
    if not [y for y in _YEAR.findall(no_phone) if int(y) <= 2027]:
        return "yil_yok"
    if _price(_clean_lines(no_phone)) is None:
        return "fiyat_yok"
    return "ok"
