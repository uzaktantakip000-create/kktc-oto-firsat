"""Serbest yazılmış ilan metnini (Facebook grup gönderisi) ayrıştırır.
Ölçüt: yanlış ilan üretmektense hiç üretmemek. Marka + yıl + AÇIK para birimli TEK fiyat yoksa None döner."""
import re
from datetime import date

from domain.caption_parser import ParsedCaption, normalize_phone, tr_lower
from domain.model_year import max_model_year
from domain.normalize import fold
from domain.price import join_split_thousands, price_tokens

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
_PRICE_HINT = re.compile(r"fiyat|nakit|nakite|pe[şs]in|sat[ıi][şs]", re.I)
_PHONE = re.compile(r"(?<!\d)(?:\+?\s*9?0[\s.-]*)?\(?5\d{2}\)?[\s.-]?\d{3}[\s.-]?\d{2}[\s.-]?\d{2}(?!\d)")
# 1980–2039: kalıp 2030'ları da tanır; geleceğe dönük yıl yine reddedilir (bulunan yıl, model yılı tavanından = bu yıl + 1 büyükse elenir)
# Ardından gelen para birimi yılı fiyat yapar ("2000 STG"); ama birimin arkasında sayı varsa birim o sayınındır ("2014 £6500")
_YEAR = re.compile(r"(?<![\d.,£€$])(19[89]\d|20[0-3]\d)(?![\d.,]?\d)"
                   r"(?![ \t]*(?:£|₺|€|\$|tl\b|stg\b|gbp\b|sterlin\b|str\b)(?![ \t]*\d))", re.I)
# Sayı ile birim/etiket arası satır sonunu GEÇMEZ ([^\S\n]: satır sonu dışındaki boşluk): "2012\nkm ..." yıl, "km\n4.900 stg" fiyat olurdu.
_KM_NUM = r"\d{1,3}(?:[.,]\d{3})+|\d{4,6}|\d{1,3}[^\S\n]*(?:bin|k)(?![a-zçğıöşü])"  # "145 bin km", "145k km"
_KM = re.compile(rf"(?<![\d.,])({_KM_NUM})[^\S\n]*(km|mil(?!eage)\w*)\b", re.I)  # "mileage" birim değil etiket: "2020 Mileage: 82000"
# Etiket ("km: 120.000", "KİLOMETRE : 160.000", "Mileage: 82000"): değer yalnız ":"/"-" varsa alt satıra geçebilir ("KM:\n120.000");
# arkasından para birimi gelen sayı km değildir; arkasından "mil" gelirse ("Kilometre: 129.000 mil") km bilinmiyor sayılır (_km).
_KM_LABEL = re.compile(rf"\b(?:km|kilometre(?:si)?|kilometer|mileage)(?:[^\S\n]*[:\-]\s*|[^\S\n]*)({_KM_NUM})(?![\d.,]?\d)"
                       r"(?![^\S\n]*(?:£|₺|€|\$|(?:tl|try|stg|gbp|sterlin|sterling|str|eur|euro|usd|dolar)(?![a-z])))", re.I)
# Bakım/parça cümlesindeki km aracın km'si değil: "bakımları 2 bin km önce", "her 5 bin km'de bir", "40 bin km'de araçtan sökülüp takıldı".
# Sitelerin km alanıyla karşılaştırma (10.10.2026, 5.749 ilan): bu cümleler km'yi 2.000–10.000 okutuyordu (değer kaçırılırsa km bilinmiyor
# kalır, veri kapısı en fazla 🟡 yapar; düşük km ise aracı değerli gösterir). Bakım sözcüğü yalnız küçük sayıda engeller: "213.000 km olup
# bakımları eksiksiz" aracın km'sidir. "bakımlı" (aracın niteliği) bakım sayılmaz.
_KM_CLAUSE_END = re.compile(r"[.!?;,(\n|]")
_KM_SERVICE = re.compile(r"bak[ıi]m(?!l[ıi])|servis|ya[gğ][ıi]?(?![a-zçğıöşü])|ya[gğ]lar|de[gğ]i[sş]|pompa|lastik", re.I)
_KM_PART = re.compile(r"tak[ıi]l|s[öo]k[üu]l|getir|al[ıi]nm[ıi][sş]", re.I)  # parça/motor takıldı, araç ... km'de alınmış
_KM_SERVICE_AFTER = re.compile(r"(?:['’][a-zçğıöşü]+)?[^\S\n]*(?:[dt][ae][^\S\n]+bir\b|bir\b|[öo]nce\b|sonra\b|kala\b|kadar\b)", re.I)
_KM_SERVICE_MAX = 20_000
_CC = re.compile(r"\b(\d{3,4})\s*cc\b", re.I)
_MODEL_STOP = {"il", "ilk", "ilan", "sahibinden", "galeriden", "yeni", "temiz", "hasarsiz", "hatasiz", "boyasiz", "tek",
               "motor", "arac", "otomobil", "arabasi", "araba", "marka", "model", "aracimiz", "gunluk", "satilik", "benz"}
_CHASSIS = re.compile(r"[efgu]\d{2}|w\d{3}", re.I)  # BMW F30 / Mercedes W204: şasi kodu; model arkasındaki sözcük ("F30 320i")
_SPLIT_MODELS = [(re.compile(r"c[\s-]*h[\s-]*r(?![a-z0-9])", re.I), "C-HR"),  # "CH R", "C HR": sözcüklere bölünüp "CH" kalıyordu
                 (re.compile(r"(cx|cr|hr|zr)[\s-]+(v|\d{1,2})(?![a-z0-9])", re.I), None)]  # "CX 5", "CR V" -> "CX-5", "CR-V"
_UPPER_BRANDS = {"bmw", "mg", "vw", "byd"}
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
    """Tek, açık para birimli fiyat. Birden çok farklı tutar varsa 'fiyat/nakit' ipuçlu satırdaki tek tutar; yoksa belirsiz -> None.
    Para birimi sayının HEMEN yanında olmalı (price_tokens): pencerede başka sayı varsa birim ona verilmez ('2014 £6500')."""
    found: list[tuple[float, str, str, bool]] = []
    for ln in lines:
        if re.search(r"tramer|boya", ln, re.I) and not _PRICE_HINT.search(ln):
            continue
        scrubbed = join_split_thousands(_PHONE.sub(" ", ln))
        # "5000£ taksitli" gibi aynı satırda birden çok tutar olabilir: her sayı ayrı aday
        for t in price_tokens(scrubbed):
            if not t.currency:
                continue
            before, after = scrubbed[max(0, t.start - 14): t.start], scrubbed[t.end: t.end + 14]
            hint = bool(_PRICE_HINT.search(before))
            if (_NOT_PRICE_LINE.search(before) or _NOT_PRICE_LINE.search(after)) and not hint:
                continue  # "taksitli 5000£", "peşinat 3000£", "tramer 32.000 TL"
            if 300 <= t.amount <= 2_000_000:
                found.append((t.amount, t.currency, ln, hint or bool(_PRICE_HINT.search(ln)) and not _NOT_PRICE_LINE.search(ln)))
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


def _km_value(token: str) -> int | None:
    m = re.fullmatch(r"(\d{1,3})[^\S\n]*(?:bin|k)", token, re.I)
    digits = int(m.group(1)) * 1000 if m else int(re.sub(r"[.,]", "", token))
    return digits if 1000 <= digits <= 600_000 else None


def _service_km(text: str, start: int, end: int, value: int) -> bool:
    """start..end (sayı + birim) aracın km'si değil de bir bakım/parça cümlesindeki km mi?"""
    before = text[max(0, start - 40):start]
    before = before[max((m.end() for m in _KM_CLAUSE_END.finditer(before)), default=0):]
    after = text[end:end + 40]
    stop = _KM_CLAUSE_END.search(after)
    after = after[:stop.start()] if stop else after
    if re.search(r"\bher[^\S\n]+$", before, re.I) or _KM_SERVICE_AFTER.match(text, end) or _KM_PART.search(before + " " + after):
        return True
    return value < _KM_SERVICE_MAX and bool(_KM_SERVICE.search(before + " " + after))


def _km(text: str) -> int | None:
    for m in _KM.finditer(text):
        if re.fullmatch(r"19[89]\d|20[0-3]\d", m.group(1)) and re.match(r"km[^\S\n]*[:\-]?[^\S\n]*\d", text[m.start(2):], re.I):
            continue  # "2014 km 85.000", "2012 km: ...": sayı model yılı, km etiketi arkasında
        value = _km_value(m.group(1))
        if value is not None and _service_km(text, m.start(), m.end(), value):
            continue
        if m.group(2).lower().startswith("mil"):
            return None  # mil = mil (İngiliz yolcu araçları): km'ye çevirmek yerine bilinmiyor say, veri kapısı en fazla 🟡 yapar
        return value
    m = _KM_LABEL.search(text)
    if m is None or re.match(r"[^\S\n]*mil", text[m.end():], re.I):
        return None  # etiket "kilometre" dese de birim mil: km'ye çevrilmez (yukarıdaki mil kuralı)
    return _km_value(m.group(1))


def parse_freetext(text: str, default_steering: str | None = None, max_year: int | None = None,
                   known_price: tuple[float, str] | None = None, today: date | None = None) -> ParsedCaption | None:
    """known_price=(tutar, para_birimi): sitenin yapılandırılmış alanından gelen kesin fiyat (metinde aranmaz).
    Model yılı tavanı: max_year verilmişse o, yoksa bu yıl + 1 (today verilmezse UTC bugünü)."""
    if not text or _not_a_sale_ad(text):
        return None
    if max_year is None:
        max_year = max_model_year(today)
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
    if not known_price and amount in years and any(y != amount for y in years):
        years = [y for y in years if y != amount]  # "Fiyat 2000 STG, 2008 model": 2000 fiyattır, yıl değil

    out = ParsedCaption()
    brand = fold(brand_m.group(1))  # .title() Türkçe İ'yi bozuyordu: "NİSSAN" -> "Ni̇ssan"
    out.brand = "Land Rover" if brand == "range rover" else brand.upper() if brand in _UPPER_BRANDS else brand.title()
    after = no_phone[brand_m.end():]
    words = [w for w in re.finditer(r"[A-Za-zÇĞİÖŞÜçğıöşü]*\d*[A-Za-zÇĞİÖŞÜçğıöşü0-9\-]*", after[:60]) if w.group()]
    model = None
    for i, wm in enumerate(words[:4]):
        w = wm.group()
        nxt = words[i + 1].group() if i + 1 < len(words) else ""
        if re.fullmatch(r"(19|20)\d{2}|modeli?|otomatik|manuel|full", w, re.I) or fold(w) in _MODEL_STOP:
            continue
        if _CHASSIS.fullmatch(w) and re.fullmatch(r"\d{3}[a-zçğıöşüİ]{0,2}|[a-z]{1,3}\d{2,3}[a-z]?", nxt, re.I):
            continue  # "BMW F30 320i" -> "320i" (emsal 3 serisiyle eşleşir; "f30" eşleşmez)
        model = w
        split = next(((pat.match(after, wm.start()), name) for pat, name in _SPLIT_MODELS if pat.match(after, wm.start())), None)
        if split:
            model = split[1] or f"{split[0].group(1)}-{split[0].group(2)}".upper()
        elif w.lower() == "i" and nxt.isdigit():  # "İ 30" -> "i30"
            model = "i" + nxt
        break
    if brand == "range rover":  # sitelerde marka Land Rover, model "Range Rover Evoque"
        model = f"Range Rover {model}" if model else "Range Rover"
    out.model = model
    line_start = no_phone.rfind("\n", 0, brand_m.start()) + 1
    line_end = no_phone.find("\n", brand_m.end())
    brand_line = no_phone[line_start: line_end if line_end != -1 else None]
    near = [int(y) for y in _YEAR.findall(brand_line) if int(y) <= max_year and int(y) in years]
    out.year = near[0] if near else years[0]  # önce marka satırındaki yıl ("2021 HONDA FIT"), yoksa metindeki ilk yıl
    out.km = _km(join_split_thousands(no_phone))  # "120 000 km"
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
        out.fuel = "elektrikli"
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


def diagnose(text: str, today: date | None = None) -> str:
    """Gönderi neden ilan sayılmadı? ('ok' = okunur). Yalnızca sayaç için; metin saklanmaz."""
    if not text or _not_a_sale_ad(text):
        return "arac_degil"
    if not _BRAND_RE.search(text):
        return "marka_yok"
    no_phone = _PHONE.sub(" ", text)
    if not [y for y in _YEAR.findall(no_phone) if int(y) <= max_model_year(today)]:
        return "yil_yok"
    if _price(_clean_lines(no_phone)) is None:
        return "fiyat_yok"
    return "ok"
