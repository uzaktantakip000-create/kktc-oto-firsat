"""sahibindenarabakibris.com (WordPress/Vehica): sitemap'ten ilan adreslerini bulur, ilan sayfasındaki nitelik tablosunu ayrıştırır.
robots.txt: User-agent * için Disallow boş (her şeye izin); kullanım şartı sayfası yok. Crawl-delay yok: nazik olmak için 3 sn bekleriz.
Telefon sayfada "göster" düğmesinin arkasında gizli: o isteği (kullanıcı eylemi) YAPMAYIZ; yalnızca açıklamaya yazılmışsa okunur."""
import json
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

import httpx
from selectolax.parser import HTMLParser

from domain.caption_parser import normalize_phone
from domain.engine import engine_liters
from domain.freetext_parser import parse_freetext
from domain.price import ParsedPrice, parse_price
from infrastructure.http.browserlike import new_browserlike_client

BASE = "https://sahibindenarabakibris.com"
SITEMAP = f"{BASE}/vehica_car-sitemap.xml"
UA = "KKTCOtoBot/0.1 (kisisel arac fiyat arastirmasi)"
CRAWL_DELAY = 3.0
_FUEL = {"benzin": "benzin", "dizel": "dizel", "hibrit": "hibrit", "hybrid": "hibrit", "elektrik": "elektrikli", "elektrikli": "elektrikli", "lpg": "lpg"}
_TRANS = {"otomatik": "otomatik", "automatic": "otomatik", "manuel": "manuel", "manual": "manuel", "düz": "manuel", "yarı otomatik": "yarı otomatik"}
_PHONE = re.compile(r"(?<!\d)(?:\+?90)?\s?0?5\d{2}[\s.-]?\d{3}[\s.-]?\d{2}[\s.-]?\d{2}(?!\d)")


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
        m = re.fullmatch(rf"{re.escape(BASE)}/vehicle/([^/]+)/?", loc.group(1)) if loc else None
        if not m:  # /vehicles/ liste sayfası
            continue
        lm = re.search(r"<lastmod>(.*?)</lastmod>", block)
        out.append(Entry(loc.group(1), m.group(1), datetime.fromisoformat(lm.group(1)) if lm else None))
    return out


def _attrs(tree: HTMLParser) -> dict[str, str]:
    out: dict[str, str] = {}
    for n in tree.css(".vehica-car-attributes__name"):
        v = n.next
        while v is not None and v.tag == "-text":
            v = v.next
        key = n.text().strip().rstrip(":")
        if v is not None and key not in out:
            out[key] = re.sub(r"\s+", " ", v.text()).strip()
    return out


def _published(tree: HTMLParser) -> datetime | None:
    for s in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(s.text())
        except ValueError:
            continue
        for node in (data.get("@graph") if isinstance(data, dict) else None) or []:
            if isinstance(node, dict) and node.get("datePublished"):
                try:
                    return datetime.fromisoformat(node["datePublished"])
                except ValueError:
                    return None
    return None


def parse_detail(html: str) -> dict | None:
    """None = ilan sayfası değil / okunamadı. Fiyat 'iletişime geç' ise price_amount None döner."""
    tree = HTMLParser(unicodedata.normalize("NFC", html))  # bazı ilanlar ayrışık (NFD) Unicode ile yazılmış
    a = _attrs(tree)
    h1 = tree.css_first("h1")
    if not a.get("Marka") or not h1:
        return None
    year_txt = re.sub(r"\D", "", a.get("Yılı", ""))
    year = int(year_txt) if year_txt else None
    price_n = tree.css_first(".vehica-car-price")
    price = parse_price(re.sub(r"\s+", "", price_n.text())) if price_n and re.search(r"\d", price_n.text()) else None
    km_txt = re.sub(r"\D", "", a.get("Kilometre", "").split(".")[0].replace(",", ""))
    km = int(km_txt) if km_txt else None
    desc_n = tree.css_first(".vehica-car-description__inner") or tree.css_first(".vehica-car-description")
    desc = "\n".join(l.strip() for l in desc_n.text(separator="\n").splitlines() if l.strip()) if desc_n else ""
    low = desc.lower()
    steering = "RHD" if re.search(r"d[üu]men\s*:?\s*sa[gğ]|sa[gğ] direksiyon|sa[gğ]dan direksiyon", low) else \
        "LHD" if re.search(r"d[üu]men\s*:?\s*sol|sol direksiyon|soldan direksiyon", low) else None
    by = None
    if not year or not price or not km:  # bazı ilanlarda nitelik alanları boş, değerler açıklamada ("Model : 2012 Fiyat : 7.000 STG")
        free = parse_freetext(f"{h1.text()}\n{desc}")
        if free:
            if not year and free.year:
                year, by = free.year, "parser_serbest"
            if not price and free.price_amount:
                price, by = ParsedPrice(free.price_amount, free.currency, False), "parser_serbest"
            km = km or free.km
    if not year:  # nitelik ve serbest ayrıştırıcı yıl bulamadı (fiyatsız ilan): açıklama/başlıktaki ilk makul yıl
        ym = re.search(r"(?<![\d.,])(19[89]\d|20[0-2]\d)(?![\d.,]?\d)", _PHONE.sub(" ", f"{h1.text()}\n{desc}"))
        year = int(ym.group(1)) if ym else None
    if not year:
        return None
    pm = _PHONE.search(desc)
    phone = normalize_phone(pm.group()) if pm else None
    body = tree.body.text(separator="\n")
    amount = price.amount if price else None
    return {
        "brand": a["Marka"],
        "model": a.get("Model"),
        "year": year,
        "km": km,
        "fuel": _FUEL.get(a.get("Yakıt Türü", "").lower()),
        "transmission": _TRANS.get(a.get("Şanzıman", "").lower()),
        "engine_l": engine_liters(a.get("Motor gücü")),
        "steering": steering,
        "location": None if a.get("Bölge") in (None, "Belirtilmemiş") else a["Bölge"],
        "price_raw": f"{amount:g} {price.currency}" if amount else None,
        "price_amount": amount,
        "currency": price.currency if price else None,
        "currency_guess": False,
        "urgency_signals": None if amount else ["fiyatsiz"],
        "raw_text": "\n".join(x for x in (re.sub(r"\s+", " ", h1.text()).strip(), desc) if x),
        "posted_at": _published(tree),
        "negotiable": bool(re.search(r"pazarl[ıi]k\s*(var|pay|yap)", low)) if desc else None,
        "seller_phone": phone,
        "seller_handle": None,
        "seller_type": "bireysel" if "Özel Satıcı" in body else "bilinmiyor",
        **({"extraction_by": by} if by else {}),  # yıl/fiyat serbest metinden geldiyse: bağımsız okuma kontrolünden geçer
    }


GONE = {"is_active": False, "urgency_signals": ["kaldirildi"]}


def _is_listing_url(final_url: str, entry: Entry) -> bool:
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


def new_client():
    """Veri merkezi engelini (GitHub Actions 403/429) aşmak için tarayıcı parmak izli istemci; Scrapling yoksa httpx."""
    return new_browserlike_client(UA)


def polite_sleep() -> None:
    time.sleep(CRAWL_DELAY)
