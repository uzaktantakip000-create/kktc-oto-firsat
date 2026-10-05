"""Yapay zekânın ilan metninden çıkardığı alanları doğrular ve kural ayrıştırıcının okumasıyla karşılaştırır.
Saf mantık (ağ yok). Kural: modelin verdiği her sayı için ilan metninde BİREBİR geçen bir alıntı şart;
alıntı metinde yoksa ya da sayıyı içermiyorsa o alan atılır (uydurmaya karşı sigorta)."""
import re
from dataclasses import dataclass
from datetime import date

from domain.caption_parser import tr_lower
from domain.model_year import max_model_year
from domain.normalize import fold, normalize_brand
from domain.price import parse_price

YEAR_MIN = 1980  # üst sınır: bu yıl + 1 (domain/model_year.py)
KM_MAX = 600_000
PRICE_RANGE = (300, 2_000_000)
KM_TOLERANCE = 0.05
PRICE_TOLERANCE = 0.01

# format_alert / günlük özet / karşılaştırma nedenleri (GAP_LABELS ile uyumlu)
MISMATCH_LABELS = {
    "okuma_fiyat": "yapay zekâ fiyatı farklı okudu",
    "okuma_yil": "yapay zekâ yılı farklı okudu",
    "okuma_km": "yapay zekâ km'yi farklı okudu",
    "okuma_marka": "yapay zekâ markayı farklı okudu",
    "okuma_direksiyon": "yapay zekâ direksiyonu farklı okudu",
    "okuma_pesinat": "yapay zekâ: peşinat/kredi/borç devri var",
    "okuma_satildi": "yapay zekâ: ilan satılmış görünüyor",
    "okuma_sorun": "yapay zekâ: ilanda sorun belirtisi var",  # alıntı notta/red_flags'te ayrıca gider
}


@dataclass
class LlmRead:
    is_car: bool = False
    brand: str | None = None
    model: str | None = None
    year: int | None = None
    km: int | None = None
    price: float | None = None
    currency: str | None = None
    steering: str | None = None  # RHD / LHD
    credit_or_deposit: bool = False
    sold: bool = False
    problem: str | None = None  # ucuzluğun gizli nedeni (hasar/pert/borç...): ilandan birebir doğrulanmış alıntı


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", tr_lower(text or ""))


def quote_in_text(quote, text: str) -> bool:
    return isinstance(quote, str) and len(quote.strip()) >= 2 and _squash(quote) in _squash(text)


def _number_tokens(quote: str) -> set[int]:
    return {int(re.sub(r"\D", "", t)) for t in re.findall(r"\d{1,3}(?:[.,]\d{3})+|\d+", quote)}


def _int_field(data: dict, key: str, quote_key: str, text: str, lo: int, hi: int) -> int | None:
    value, quote = data.get(key), data.get(quote_key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
        return None
    value = int(value)
    if not lo <= value <= hi or not quote_in_text(quote, text) or value not in _number_tokens(quote):
        return None
    return value


def parse_llm_read(data, text: str, today: date | None = None) -> LlmRead | None:
    """Ham model çıktısı -> doğrulanmış alanlar. Geçersiz/dict olmayan çıktı: None. Araç ilanı değilse is_car=False."""
    if not isinstance(data, dict):
        return None
    if data.get("arac_ilani_mi") is not True:
        return LlmRead(is_car=False)
    out = LlmRead(is_car=True)
    out.year = _int_field(data, "yil", "yil_alinti", text, YEAR_MIN, max_model_year(today))
    out.km = _int_field(data, "km", "km_alinti", text, 0, KM_MAX)
    brand, model = data.get("marka"), data.get("model")
    if isinstance(brand, str) and 2 <= len(brand) <= 30 and fold(brand) in fold(text):
        out.brand = brand.strip().title()
        if isinstance(model, str) and 1 <= len(model) <= 40:
            first = fold(model).split(" ")[0]
            if first and first in fold(text):
                out.model = model.strip()
    price, quote = data.get("fiyat"), data.get("fiyat_alinti")
    if isinstance(price, (int, float)) and not isinstance(price, bool) and quote_in_text(quote, text):
        parsed = parse_price(quote)  # para birimini modele bırakmıyoruz: alıntıdan kendi kodumuz okur
        if (parsed and not parsed.currency_guess and PRICE_RANGE[0] <= parsed.amount <= PRICE_RANGE[1]
                and abs(parsed.amount - price) <= PRICE_TOLERANCE * parsed.amount):
            out.price, out.currency = parsed.amount, parsed.currency
    if data.get("direksiyon") in ("RHD", "LHD"):
        out.steering = data["direksiyon"]
    out.credit_or_deposit = data.get("pesinat_veya_kredi_devri") is True
    out.sold = data.get("satildi") is True
    quote = data.get("sorun_alinti")
    if data.get("sorun") is True and quote_in_text(quote, text):  # uydurma alıntı = sorun yok
        out.problem = " ".join(quote.split())[:100]
    return out


def compare(listing: dict, read: LlmRead) -> tuple[list[str], bool]:
    """(uyuşmazlık nedenleri, fiyat bağımsız olarak doğrulandı mı). Neden boşsa ve ikinci değer True ise ilan temiz."""
    reasons: list[str] = []
    price_confirmed = False
    l_amount, l_cur = listing.get("price_amount"), listing.get("currency")
    if read.price is not None and l_amount is not None:
        l_amount = float(l_amount)
        if read.currency != l_cur or abs(read.price - l_amount) > PRICE_TOLERANCE * max(read.price, l_amount):
            reasons.append("okuma_fiyat")
        else:
            price_confirmed = True
    if read.year is not None and listing.get("year") is not None and read.year != listing["year"]:
        reasons.append("okuma_yil")
    if read.km is not None and listing.get("km"):
        if abs(read.km - listing["km"]) > KM_TOLERANCE * max(read.km, listing["km"]):
            reasons.append("okuma_km")
    if read.brand and listing.get("brand") and normalize_brand(read.brand) != normalize_brand(listing["brand"]):
        reasons.append("okuma_marka")
    if read.steering == "LHD" and listing.get("steering") != "LHD":
        reasons.append("okuma_direksiyon")  # sağ direksiyon varsayımıyla emsallerle karışmasın
    elif read.steering == "RHD" and listing.get("steering") == "LHD":
        reasons.append("okuma_direksiyon")
    if read.credit_or_deposit:
        reasons.append("okuma_pesinat")
    if read.sold:
        reasons.append("okuma_satildi")
    if read.problem:
        reasons.append("okuma_sorun")
    return reasons, price_confirmed
