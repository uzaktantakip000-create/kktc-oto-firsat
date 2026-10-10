"""kktcar.com: sitemap'ten ilan adreslerini bulur, ilan sayfasını ayrıştırır (robots.txt izin veriyor)."""
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from selectolax.parser import HTMLParser

from domain.engine import engine_liters
from domain.normalize import canon_transmission
from domain.price import parse_price
from domain.steering import steering_from_text

BASE = "https://kktcar.com"
UA = "KKTCOtoBot/0.1 (kisisel arac fiyat arastirmasi)"
_FUEL = {"dizel": "dizel", "benzin": "benzin", "hibrit": "hibrit", "elektrik": "elektrikli", "lpg": "lpg"}


@dataclass(frozen=True)
class SitemapEntry:
    url: str
    slug: str
    lastmod: datetime | None


def fetch_sitemap(client: httpx.Client) -> list[SitemapEntry]:
    """İlan adresleri. 08.10.2026'dan beri sitemap.xml bir DİZİN: ilanlar /sitemaps/sitemap/listings.xml'de ve yalnız yayındaki
    ilanları içerir (~500; eski tek dosya satılmışlarla ~3.400 idi). Eski biçim gelirse o da okunur."""
    r = client.get(f"{BASE}/sitemap.xml", timeout=60)
    r.raise_for_status()
    text = r.text
    if "<sitemapindex" in text:
        children = [u for u in re.findall(r"<loc>\s*(.*?)\s*</loc>", text)
                    if u.startswith(f"{BASE}/") and "listing" in u.rsplit("/", 1)[-1]]  # hubs/price-guides ilan değil
        if not children:
            raise RuntimeError("KKTCar: site haritası dizininde ilan haritası yok (biçim değişmiş olabilir)")
        parts = []
        for url in children:
            c = client.get(url, timeout=60)
            c.raise_for_status()
            parts.append(c.text)
        text = "\n".join(parts)
    out = []
    for block in re.findall(r"<url>(.*?)</url>", text, re.S):
        loc = re.search(r"<loc>(.*?)</loc>", block)
        if not loc or "/listing/" not in loc.group(1):
            continue
        lm = re.search(r"<lastmod>(.*?)</lastmod>", block)
        out.append(
            SitemapEntry(
                loc.group(1),
                loc.group(1).rsplit("/", 1)[-1],
                datetime.fromisoformat(lm.group(1)) if lm else None,
            )
        )
    return out


def _pairs(lines: list[str]) -> dict[str, str]:
    """'Yıl | 2014 | Kilometre | 156.000 km' gibi etiket-değer dizisinden sözlük çıkarır."""
    labels = {"Marka", "Model", "Yıl", "Kilometre", "Yakıt Tipi", "Vites", "Motor Hacmi", "Kasa Tipi", "Konum", "Takaslı"}
    out: dict[str, str] = {}
    start = lines.index("Araç Detayları") if "Araç Detayları" in lines else 0
    for i in range(start, len(lines) - 1):
        if lines[i] in labels and lines[i] not in out:
            out[lines[i]] = lines[i + 1]
    return out


# 22 Eylül 2026'da KKTCar ~1.040 ilanı TEK SEFERDE arşivledi (aynı an: 19:08:16 UTC): bu tarih gerçek satış tarihi DEĞİLDİR, yazılmaz.
BULK_ARCHIVE_FROM = datetime(2026, 9, 22, 19, 0, tzinfo=timezone.utc)
BULK_ARCHIVE_TO = datetime(2026, 9, 22, 19, 30, tzinfo=timezone.utc)
_ARCHIVED_AT = re.compile(r"Arşive alınma tarihi.{0,400}?<time[^>]*dateTime=\"([^\"]+)\"", re.S | re.I)


def archived_at(html: str) -> datetime | None:
    """Satılmış ilan sayfasındaki "Arşive alınma tarihi" (ilan sahibi "satıldı" işaretledi). Toplu arşivleme anı ya da çözülemeyen tarih → None."""
    m = _ARCHIVED_AT.search(html)
    if not m:
        return None
    try:
        dt = datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
    except ValueError:
        return None
    dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None if BULK_ARCHIVE_FROM <= dt <= BULK_ARCHIVE_TO else dt


def _parse_sold(title: str, lines: list[str], marker: str, signal: str) -> dict | None:
    """Satılmış ilan arşivi: 'Son ilan fiyatı' gerçekleşen satış fiyatı DEĞİLDİR (sayfa da böyle diyor)."""
    if marker not in lines:
        return None
    i = lines.index(marker)
    brand, model = lines[i - 2], lines[i - 1]
    f = _pairs(lines[lines.index("Arşivdeki araç bilgileri"):]) if "Arşivdeki araç bilgileri" in lines else {}
    if "Arşivdeki araç bilgileri" in lines:  # arşiv bloğunda 'Araç Detayları' yok, etiketleri doğrudan tara
        j = lines.index("Arşivdeki araç bilgileri")
        for k in range(j, len(lines) - 1):
            if lines[k] in ("Yıl", "Kilometre", "Yakıt Tipi", "Vites") and lines[k] not in f:
                f[lines[k]] = lines[k + 1]
    price_txt = next((lines[k + 1] for k, l in enumerate(lines) if l == "Son ilan fiyatı"), None)
    price = parse_price(price_txt) if price_txt else None
    if not f.get("Yıl"):
        return None
    km = re.sub(r"\D", "", f.get("Kilometre", ""))
    return {
        "brand": brand,
        "model": model,
        "year": int(re.sub(r"\D", "", f["Yıl"])),
        "km": int(km) if km else None,
        "fuel": _FUEL.get(f.get("Yakıt Tipi", "").lower(), f.get("Yakıt Tipi", "").lower() or None),
        "transmission": canon_transmission(f.get("Vites", "")) or None,
        "location": lines[i - 5] if i >= 5 and lines[i - 4].isdigit() else None,
        "price_raw": price_txt,
        "price_amount": price.amount if price else None,
        "currency": price.currency if price else None,
        "currency_guess": price.currency_guess if price else False,
        "raw_text": title,
        "posted_at": None,
        "negotiable": None,
        "swap": None,
        "is_active": False,
        "urgency_signals": [signal] if price else [signal, "fiyatsiz"],
    }


_SELLER_HREF = re.compile(r"^/seller/([0-9a-fA-F-]{8,64})/?$")


def seller_handle(tree: HTMLParser) -> str | None:
    """Aktif ilan sayfasındaki satıcı bağlantısından ("/seller/<kimlik>") "kktcar:<kimlik>". Satılmış/arşiv sayfalarında bağlantı yoktur.
    Önek zorunlu: başka sitelerin satıcı adlarıyla (KibrisArabaAl yazar adı, Instagram hesabı) karışmasın."""
    for a in tree.css("a[href]"):
        m = _SELLER_HREF.match(a.attributes.get("href") or "")
        if m:
            return f"kktcar:{m.group(1).lower()}"
    return None


_LD = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_LD_FUEL = (("hybrid", "hibrit"), ("diesel", "dizel"), ("gasoline", "benzin"), ("petrol", "benzin"), ("electric", "elektrikli"),
            ("lpg", "lpg"), ("autogas", "lpg"))  # sıra önemli: "HybridElectric" hibrittir
_LD_GEAR = {"automatictransmission": "otomatik", "manualtransmission": "manuel"}


def _ld_vehicle(html: str) -> dict | None:
    for m in _LD.finditer(html):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Vehicle":
            return data
    return None


def _ld_number(node) -> float | None:
    value = node.get("value") if isinstance(node, dict) else node
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_structured(html: str, title: str) -> dict | None:
    """09.10.2026'dan beri yeni ilanların sayfası (normal tarayıcıda da) "Araç Bulunamadı" gösteriyor; araç bilgisi yalnız sayfadaki
    schema.org Vehicle verisinde (JSON-LD) var. Yalnız görünür sayfa okunamayınca kullanılır; yalnız "satışta" (InStock) ilan.
    Satıcı adı OKUNMAZ (kişisel veri); satıcı bağlantısı bu veride yok."""
    v = _ld_vehicle(html)
    offer = (v or {}).get("offers") or {}
    if isinstance(offer, list):
        offer = offer[0] if offer else {}
    if not v or "InStock" not in str(offer.get("availability", "")):
        return None
    brand = (v.get("brand") or {}).get("name") if isinstance(v.get("brand"), dict) else v.get("brand")
    year = re.sub(r"\D", "", str(v.get("vehicleModelDate") or v.get("modelDate") or ""))[:4]
    if not brand or len(year) != 4:
        return None
    m = re.search(r"-\s*([\d.,]+\s*[£€$₺]|[£€$₺]\s*[\d.,]+)\s*-", title)  # görünür sayfadaki gibi başlıktan: fiyat yazımı yenilemede aynı kalır
    price = parse_price(m.group(1)) if m else None
    raw_price = m.group(1) if price else None
    amount, currency = (price.amount, price.currency) if price else (_ld_number(offer.get("price")), offer.get("priceCurrency"))
    if not amount or currency not in ("GBP", "TRY", "EUR", "USD"):
        amount, currency, raw_price = None, None, None
    mileage = v.get("mileageFromOdometer") or {}
    km = _ld_number(mileage) if (mileage.get("unitCode") if isinstance(mileage, dict) else None) in (None, "KMT") else None
    fuel_txt = str(v.get("fuelType") or "").lower()
    gear = str(v.get("vehicleTransmission") or "").rsplit("/", 1)[-1].lower()
    engine = _ld_number(((v.get("vehicleEngine") or {}).get("engineDisplacement")) or {})
    desc = str(v.get("description") or "").strip()
    posted = None
    try:
        when = datetime.fromisoformat(str(v.get("datePosted") or offer.get("validFrom") or "").replace("Z", "+00:00"))
        posted = datetime(when.year, when.month, when.day, tzinfo=timezone.utc)  # görünür sayfadaki gibi yalnız gün
    except ValueError:
        pass
    return {
        "seller_handle": None,
        "brand": brand,
        "model": v.get("model"),
        "year": int(year),
        "km": int(km) if km is not None else None,
        "fuel": next((tr for key, tr in _LD_FUEL if key in fuel_txt), None),
        "transmission": canon_transmission(_LD_GEAR.get(gear)) if gear in _LD_GEAR else None,
        "engine_l": engine_liters(str(engine)) if engine else None,
        "location": ((offer.get("areaServed") or {}).get("name") if isinstance(offer.get("areaServed"), dict) else None),
        "steering": steering_from_text(f"{title}\n{desc}"),
        "price_raw": raw_price,
        "price_amount": amount,
        "currency": currency,
        "currency_guess": price.currency_guess if price else False,
        "urgency_signals": None if amount else ["fiyatsiz"],
        "raw_text": f"{title}\n{desc}",
        "posted_at": posted,
        "negotiable": bool(re.search(r"pazarl[ıi]k", desc, re.I)) if desc else None,
        "swap": None,
    }


def parse_detail(html: str) -> dict | None:
    raw_html = html
    tree = HTMLParser(html)
    for n in tree.css("script,style,noscript,svg"):
        n.decompose()
    title = (tree.css_first("title").text() if tree.css_first("title") else "").strip()
    lines = [l.strip() for l in tree.body.text(separator="\n").splitlines() if l.strip()]
    if "Satıldı" in title:
        sold = _parse_sold(title, lines, "Bu araç satıldı.", "satildi")
        if sold is not None and (at := archived_at(html)) is not None:
            sold["sold_at"] = at  # kaynağın bildirdiği satış (arşive alınma) zamanı; "arsiv" (süre dolumu) sayfalarında YAZILMAZ: satıldığı kesin değil
        return sold
    if "İlan arşivi" in title:  # süresi dolmuş/güncelliği doğrulanamayan ilan: satıldığı kesin değil
        return _parse_sold(title, lines, "Bu ilan artık aktif değil.", "arsiv")
    f = _pairs(lines)
    if not f.get("Marka") or not f.get("Yıl"):
        return parse_structured(raw_html, title)  # görünür sayfa boş ("Araç Bulunamadı"): yapılandırılmış veriden

    # Güncel fiyat başlıkta: "BMW X5 2014 - 25.900£ - KKTCar" (üstü çizili eski fiyat varsa karışmaz)
    m = re.search(r"-\s*([\d.,]+\s*[£€$₺]|[£€$₺]\s*[\d.,]+)\s*-", title)
    price = parse_price(m.group(1)) if m else None
    if not price and "Fiyat Sorunuz" not in lines:
        return None  # tanımadığımız sayfa biçimi
    # "Fiyat Sorunuz": aktif ama fiyatsız ilan. Saklanır (her turda yeniden denenmesin), değerlendirilmez.

    km = re.sub(r"\D", "", f.get("Kilometre", ""))
    desc = ""
    if "Açıklama" in lines:
        i = lines.index("Açıklama") + 1
        j = lines.index("Keşfet") if "Keşfet" in lines[i:] else min(len(lines), i + 40)
        desc = "\n".join(lines[i:j])
    posted = None
    for l in lines:
        d = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{4})", l)
        if d:
            posted = datetime(int(d.group(3)), int(d.group(2)), int(d.group(1)), tzinfo=timezone.utc)
            break
    swap_txt = f.get("Takaslı", "").lower()
    return {
        "seller_handle": seller_handle(tree),
        "brand": f["Marka"],
        "model": f.get("Model"),
        "year": int(re.sub(r"\D", "", f["Yıl"])),
        "km": int(km) if km else None,
        "fuel": _FUEL.get(f.get("Yakıt Tipi", "").lower(), f.get("Yakıt Tipi", "").lower() or None),
        "transmission": canon_transmission(f.get("Vites", "")) or None,
        "engine_l": engine_liters(f.get("Motor Hacmi")),
        "location": f.get("Konum"),
        "steering": steering_from_text(f"{title}\n{desc}"),  # sayfada alan yok; ilan metninde yazıyorsa
        "price_raw": m.group(1) if price else None,
        "price_amount": price.amount if price else None,
        "currency": price.currency if price else None,
        "currency_guess": price.currency_guess if price else False,
        "urgency_signals": None if price else ["fiyatsiz"],
        "raw_text": f"{title}\n{desc}",
        "posted_at": posted,
        "negotiable": bool(re.search(r"pazarl[ıi]k", desc, re.I)) if desc else None,
        "swap": not swap_txt.startswith(("takas edilemez", "hayır")) if swap_txt else None,
    }


def fetch_detail(client: httpx.Client, entry: SitemapEntry) -> dict | None:
    r = client.get(entry.url, timeout=30)
    if r.status_code != 200:
        return None
    return parse_detail(r.text)


def new_client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": UA}, follow_redirects=True)


def polite_sleep() -> None:
    time.sleep(1.0)
