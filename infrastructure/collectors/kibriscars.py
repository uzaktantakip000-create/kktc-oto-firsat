"""kibriscars.com (WordPress/Boxcar): sitemap'ten ilan adreslerini bulur, ilan sayfasındaki görünür alanları ayrıştırır.
robots.txt boş (her şeye izin); kullanım şartlarında otomatik okuma yasağı yok. Crawl-delay yok: nazik olmak için 3 sn bekleriz.
Sitenin ilanlarında JSON-LD araç verisi yok: marka kırıntı (breadcrumb) bağlantısından, model başlıktan gelir."""
import json
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from selectolax.parser import HTMLParser
from urllib.parse import urlparse

from domain.caption_parser import normalize_phone
from domain.engine import engine_liters
from domain.price import parse_price

BASE = "https://kibriscars.com"
SITEMAP = f"{BASE}/listing-sitemap.xml"
UA = "KKTCOtoBot/0.1 (kisisel arac fiyat arastirmasi)"
CRAWL_DELAY = 3.0
_FUEL = {"benzin": "benzin", "dizel": "dizel", "hibrit": "hibrit", "hybrid": "hibrit", "elektrik": "elektrikli", "elektrikli": "elektrikli", "lpg": "lpg"}
_TRANS = {"otomatik": "otomatik", "automatic": "otomatik", "manuel": "manuel", "manual": "manuel", "düz": "manuel", "yarı otomatik": "yarı otomatik"}
_TITLE_TAIL = {"otomatik", "manual", "manuel", "cvt", "düz", "2", "3", "4"}  # başlık sonundaki vites eki / tekrar numarası


@dataclass(frozen=True)
class Entry:
    url: str
    item_id: str  # adres sonundaki ilan adı (slug)
    lastmod: datetime | None


def fetch_sitemap(client: httpx.Client) -> list[Entry]:
    r = client.get(SITEMAP, timeout=60)
    r.raise_for_status()
    out = []
    for block in re.findall(r"<url>(.*?)</url>", r.text, re.S):
        loc = re.search(r"<loc>(.*?)</loc>", block)
        m = re.fullmatch(rf"{re.escape(BASE)}/araba-ilani/([^/]+)/?", loc.group(1)) if loc else None
        if not m:  # liste sayfası vb.
            continue
        lm = re.search(r"<lastmod>(.*?)</lastmod>", block)
        out.append(Entry(loc.group(1), m.group(1), datetime.fromisoformat(lm.group(1)) if lm else None))
    return out


def _model(title: str, brand: str | None, year: int | None) -> str | None:
    t = re.sub(r"\s+", " ", title).strip()
    if year:
        t = re.sub(rf"^{year}\s+", "", t)
    if brand:
        for variant in {brand, brand.replace("-", " ")}:
            if t.lower().startswith(variant.lower() + " "):
                t = t[len(variant) + 1:]
                break
    words = t.split()
    while words and words[-1].lower() in _TITLE_TAIL:
        words.pop()
    return " ".join(words) or None


def _breadcrumb_brand(tree: HTMLParser) -> str | None:
    for s in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(s.text())
        except ValueError:
            continue
        for node in (data.get("@graph") if isinstance(data, dict) else None) or []:
            if isinstance(node, dict) and node.get("@type") == "BreadcrumbList":
                for it in node.get("itemListElement", []):
                    if "/marka/" in str(it.get("item")):
                        return it.get("name")
    return None


def _field(tree: HTMLParser, title: str) -> str | None:
    for li in tree.css(".listing-detail-detail li"):
        t = li.css_first(".field-title")
        if t and t.text().strip() == title:
            v = li.css_first(".content-value")
            return re.sub(r"\s+", " ", v.text()).strip() if v else None
    return None


def parse_detail(html: str) -> dict | None:
    """None = ilan sayfası değil / okunamadı. Fiyat yoksa price_amount None döner (ilan 'fiyatsiz' işaretlenir)."""
    tree = HTMLParser(unicodedata.normalize("NFC", html))
    h1 = tree.css_first("h1")
    if not h1 or not tree.css_first(".listing-detail-detail"):
        return None
    title = re.sub(r"\s+", " ", h1.text()).strip()
    try:
        year = int(re.sub(r"\D", "", _field(tree, "Yıl") or ""))
    except ValueError:
        return None
    brand = _breadcrumb_brand(tree)
    price = None
    main = tree.css_first(".listing-detail-price .main-price")
    if main:
        price = parse_price(re.sub(r"\s+", "", main.text()))
    km_txt = re.sub(r"\D", "", _field(tree, "Kilometre") or "")
    km = int(km_txt) if km_txt else None
    posted = None
    for line in tree.body.text(separator="\n").splitlines():
        m = re.fullmatch(r"\s*(\d{2})/(\d{2})/(\d{4})\s*", line)
        if m:
            posted = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), tzinfo=timezone.utc)
            break
    phone_a = tree.css_first(".agent-phone a.phone")
    phone = normalize_phone(phone_a.text()) if phone_a else None
    desc_n = tree.css_first(".listing-detail-description")
    desc = "\n".join(l.strip() for l in desc_n.text(separator="\n").splitlines() if l.strip()) if desc_n else ""
    fuel = _FUEL.get((_field(tree, "Yakıt Tipi") or "").lower())
    trans = _TRANS.get((_field(tree, "Vites") or "").lower())
    amount = price.amount if price else None
    return {
        "brand": brand,
        "model": _model(title, brand, year),
        "year": year,
        "km": km,
        "fuel": fuel,
        "transmission": trans,
        "engine_l": engine_liters(_field(tree, "Motor Hacmi (cc)")),
        "steering": None,  # sitede direksiyon alanı yok
        "location": None,
        "price_raw": f"{amount:g} {price.currency}" if amount else None,
        "price_amount": amount,
        "currency": price.currency if price else None,
        "currency_guess": False,
        "urgency_signals": None if amount else ["fiyatsiz"],
        "raw_text": "\n".join(x for x in (title, desc) if x),
        "posted_at": posted,
        "negotiable": bool(re.search(r"pazarl[ıi]k", desc, re.I)) if desc else None,
        "seller_phone": phone,
        "seller_handle": None,
        "seller_type": "bilinmiyor",
    }


GONE = {"is_active": False, "urgency_signals": ["kaldirildi"]}


def _is_listing_url(final_url: str, entry: Entry) -> bool:
    """Kaldırılmış ilan ana sayfaya ya da başka bir sayfaya yönlenir."""
    return entry.item_id in urlparse(str(final_url)).path


def fetch_detail(client: httpx.Client, entry: Entry) -> dict | None:
    """None = geçici hata ya da OKUNAMADI (tekrar denenir). GONE = 404/410 ya da ilan sayfası olmayan yönlendirme."""
    r = client.get(entry.url, timeout=30)
    if r.status_code in (404, 410):
        return dict(GONE)
    if r.status_code != 200:
        return None
    if not _is_listing_url(r.url, entry):
        return dict(GONE)
    return parse_detail(r.text)


def new_client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": UA}, follow_redirects=True)


def polite_sleep() -> None:
    time.sleep(CRAWL_DELAY)
