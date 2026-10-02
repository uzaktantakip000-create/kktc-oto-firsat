"""pazarkibris.com (Laravel + Inertia): "Vasıta > Arabalar" liste sayfaları ilan verisini (fiyat, para birimi, metin, telefon, tarih)
sayfaya gömülü JSON (data-page) olarak verir: ayrı ilan sayfası istemeyiz, tur başına yalnızca birkaç liste sayfası okunur.
robots.txt: yalnızca /admin /users /api /login /register /verify-phone /password kapalı (biz /s/cars okuruz); kullanım şartlarında
otomatik okuma yasağı yok. Crawl-delay yok: 3 sn bekleriz. DİKKAT: ilanların çoğunda fiyat alanı boş (≈%85); site ilanları kendisi
başka kaynaklardan (Facebook/Telegram) derlediği için mükerrer oranı yüksektir."""
import html as htmllib
import json
import re
import time
from datetime import datetime

import httpx

from domain.caption_parser import normalize_phone

BASE = "https://pazarkibris.com"
LIST_URL = f"{BASE}/s/cars"
UA = "KKTCOtoBot/0.1 (kisisel arac fiyat arastirmasi)"
CRAWL_DELAY = 3.0
_CURRENCY = {"£": "GBP", "₺": "TRY", "$": "USD", "€": "EUR", "GBP": "GBP", "TRY": "TRY", "USD": "USD", "EUR": "EUR"}


def new_client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": UA}, follow_redirects=True, timeout=30)


def polite_sleep() -> None:
    time.sleep(CRAWL_DELAY)


def parse_list_page(html: str) -> tuple[list[dict], int]:
    """(ilanlar, toplam ilan sayısı). Sayfa Inertia değilse ya da şablon değiştiyse ([], 0)."""
    m = re.search(r'data-page="([^"]*)"', html)
    if not m:
        return [], 0
    try:
        listings = json.loads(htmllib.unescape(m.group(1)))["props"]["listings"]
        return [parse_item(x) for x in listings["data"]], int(listings["total"])
    except (ValueError, KeyError, TypeError):
        return [], 0


def parse_item(x: dict) -> dict:
    """Liste öğesi -> ilan alanları. Fiyatı olmayan ilanda price_amount None."""
    try:
        amount = float(x["price"]) if x.get("price") not in (None, "") else None
    except ValueError:
        amount = None
    contact = x.get("contact_info") or {}
    phone = next((p for p in (normalize_phone(str(contact.get(k) or "")) for k in ("mobile", "whatsapp")) if p), None)
    try:
        posted = datetime.fromisoformat(x["published_at"].replace("Z", "+00:00")) if x.get("published_at") else None
    except ValueError:
        posted = None
    text = "\n".join(s.strip() for s in (x.get("title"), x.get("headline"), x.get("text")) if s and s.strip())
    return {
        "item_id": x["uuid"],
        "url": f"{BASE}/v/{x['slug']}/{x['uuid']}",
        "title": (x.get("title") or "").strip(),
        "description": "\n".join(s.strip() for s in (x.get("headline"), x.get("text")) if s and s.strip()),
        "raw_text": text,
        "price_amount": amount if amount and amount > 0 else None,
        "currency": _CURRENCY.get(x.get("currency")) if amount else None,
        "posted_at": posted,
        "location": None,
        "seller_phone": phone,
        "photo_urls": [i["original"] for i in (x.get("image_urls") or [])[:3] if i.get("original")],
        "active": x.get("status") == "approved" and not x.get("is_expired_for_search") and not x.get("deleted_at"),
    }
