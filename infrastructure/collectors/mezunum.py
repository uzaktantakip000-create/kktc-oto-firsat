"""mezunumsatiyorumkibris.com.tr (genel ilan sitesi; robots.txt /ilanlar ve /ilan sayfalarına izin verir).
Fiyat ve para birimi JSON-LD `Product`tan gelir (kesin); marka/model/yıl/km serbest metinden okunur (domain.freetext_parser).
Sayfa başına istek aralığı nazik tutulur; engel/robots değişirse kaynak atlanır."""
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from selectolax.parser import HTMLParser

from domain.caption_parser import normalize_phone

BASE = "https://mezunumsatiyorumkibris.com.tr"
LIST_URL = BASE + "/ilanlar/kktc-araba"
UA = "Mozilla/5.0 (compatible; KKTCOtoFirsat/1.0; kisisel arac arama)"
CRAWL_DELAY = 3.0
KKTC_TZ = timezone(timedelta(hours=3))
_PRICE_LINE = re.compile(r"^(?:[£₺$€]\s*[\d.,]+|[\d.,]+\s*[£₺$€])$")
_PHONE = re.compile(r"\+?90\s*5\d{9}")


@dataclass(frozen=True)
class Entry:
    slug: str
    url: str


def polite_sleep() -> None:
    time.sleep(CRAWL_DELAY)


def new_client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": UA}, follow_redirects=True, timeout=30)


def parse_list(html: str) -> list[Entry]:
    seen: dict[str, Entry] = {}
    for a in HTMLParser(html).css('a[href*="/ilan/"]'):
        href = a.attributes.get("href") or ""
        m = re.match(rf"^{re.escape(BASE)}/ilan/([\w\-]+)$", href)
        if m and m.group(1) not in seen:
            seen[m.group(1)] = Entry(m.group(1), href)
    return list(seen.values())


def _product(tree: HTMLParser) -> dict | None:
    for s in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(s.text())
        except ValueError:
            continue
        for node in (data if isinstance(data, list) else [data]):
            if isinstance(node, dict) and node.get("@type") == "Product":
                return node
    return None


def parse_detail(html: str) -> dict | None:
    """None = ilan sayfası değil / fiyat okunamadı. Dönen alanlar listings satırına yakın; marka/yıl sonra metinden çıkar."""
    tree = HTMLParser(html)
    node = _product(tree)
    if not node:
        return None
    offer = node.get("offers") or {}
    try:
        amount = float(offer.get("price"))
    except (TypeError, ValueError):
        return None
    currency = {"GBP": "GBP", "TRY": "TRY", "EUR": "EUR", "USD": "USD"}.get(offer.get("priceCurrency"))
    if not currency or amount <= 0:
        return None
    for n in tree.css("script,style,noscript,svg"):
        n.decompose()
    lines = [l.strip() for l in tree.body.text(separator="\n").splitlines() if l.strip()]
    desc = ""
    if "İlan Açıklaması" in lines:
        i = lines.index("İlan Açıklaması") + 1
        out = []
        while i < len(lines) and not _PRICE_LINE.match(lines[i]):
            out.append(lines[i])
            i += 1
        desc = "\n".join(out)
    phone = next((normalize_phone(m.group()) for l in lines for m in [_PHONE.search(l)] if m and "X" not in l), None)
    try:
        posted = datetime.strptime(offer.get("validFrom", ""), "%Y-%m-%d %H:%M:%S").replace(tzinfo=KKTC_TZ)
    except ValueError:
        posted = None
    place = ((offer.get("availableAtOrFrom") or {}).get("address") or {}).get("addressLocality")
    return {"title": (node.get("name") or "").strip(), "description": desc, "price_amount": amount, "currency": currency,
            "posted_at": posted, "location": place, "seller_phone": phone}
