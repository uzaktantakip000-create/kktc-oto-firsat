"""kibrisarabaal.com: sitemap'ten ilan adreslerini bulur, ilan sayfasındaki JSON-LD + görünür alanları ayrıştırır.
robots.txt: User-agent * için izin var, Crawl-delay 5 (uyuyoruz). Giriş/çerez yok."""
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from selectolax.parser import HTMLParser

from domain.engine import engine_liters

BASE = "https://kibrisarabaal.com"
SITEMAP = f"{BASE}/sitemap-listings.xml"
UA = "KKTCOtoBot/0.1 (kisisel arac fiyat arastirmasi)"
CRAWL_DELAY = 5.0  # robots.txt
_MONTHS = {m: i for i, m in enumerate(
    ["ocak", "şubat", "mart", "nisan", "mayıs", "haziran", "temmuz", "ağustos", "eylül", "ekim", "kasım", "aralık"], 1)}
_FUEL = {"benzin": "benzin", "dizel": "dizel", "hibrit": "hibrit", "elektrik": "elektrikli", "elektrikli": "elektrikli",
         "lpg": "lpg", "hybrid": "hibrit"}
_TRANS = {"otomatik": "otomatik", "manuel": "manuel", "düz": "manuel", "yarı otomatik": "yarı otomatik"}
_LABELS = ("İlan Tarihi:", "Direksiyon Tipi:", "Yıl:", "Kilometre:", "Vites Tipi:", "Yakıt Türü:", "Motor Hacmi (cc):", "Araç Durumu:")


@dataclass(frozen=True)
class Entry:
    url: str
    item_id: str
    lastmod: datetime | None


def fetch_sitemap(client: httpx.Client) -> list[Entry]:
    r = client.get(SITEMAP, timeout=60)
    r.raise_for_status()
    out = []
    for block in re.findall(r"<url>(.*?)</url>", r.text, re.S):
        loc = re.search(r"<loc>(.*?)</loc>", block)
        m = re.search(r"/ilan/(\d+)-", loc.group(1)) if loc else None
        if not m:
            continue
        lm = re.search(r"<lastmod>(.*?)</lastmod>", block)
        out.append(Entry(loc.group(1), m.group(1), datetime.fromisoformat(lm.group(1)) if lm else None))
    return out


def _vehicle_node(tree: HTMLParser) -> dict | None:
    for s in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(s.text())
        except ValueError:
            continue
        for node in (data.get("@graph") if isinstance(data, dict) and "@graph" in data else [data]):
            if isinstance(node, dict) and "Car" in (node.get("@type") if isinstance(node.get("@type"), list) else [node.get("@type")]):
                return node
    return None


def _date(text: str) -> datetime | None:
    m = re.fullmatch(r"(\d{1,2})\s+(\w+)\s+(\d{4})", text.strip().lower())
    if not m or m.group(2) not in _MONTHS:
        return None
    return datetime(int(m.group(3)), _MONTHS[m.group(2)], int(m.group(1)), tzinfo=timezone.utc)


def parse_detail(html: str) -> dict | None:
    tree = HTMLParser(html)
    node = _vehicle_node(tree)
    if not node:
        return None  # ilan sayfası değil (kaldırılmış ilan ana sayfaya yönleniyor)
    offer = node.get("offers") or {}
    if "OutOfStock" in str(offer.get("availability")):
        return {"is_active": False, "urgency_signals": ["satildi"]}
    for n in tree.css("script,style,noscript,svg"):
        n.decompose()
    lines = [l.strip() for l in tree.body.text(separator="\n").splitlines() if l.strip()]
    f = {}
    for i, l in enumerate(lines[:-1]):
        if l in _LABELS and l not in f:
            f[l] = lines[i + 1]
    try:
        year = int(node.get("vehicleModelDate") or re.sub(r"\D", "", f.get("Yıl:", "")))
    except ValueError:
        return None
    price = offer.get("price")
    amount = float(price) if price not in (None, "") else None
    currency = offer.get("priceCurrency") or None
    km_val = (node.get("mileageFromOdometer") or {}).get("value")
    km = int(km_val) if km_val else (int(re.sub(r"\D", "", f["Kilometre:"])) if re.sub(r"\D", "", f.get("Kilometre:", "")) else None)
    steering_txt = f.get("Direksiyon Tipi:", "").lower()
    steering = "RHD" if "sağ" in steering_txt else "LHD" if "sol" in steering_txt else None
    desc = ""
    if "Konum Bilgisi" in lines:  # satıcının kendi açıklaması bu başlıktan sonra gelir, benzer ilanlar listesine kadar
        tail = lines[lines.index("Konum Bilgisi") + 1:]
        out = []
        for l in tail:
            if re.match(r"^\d{4} Model ", l):
                break
            out.append(l)
        desc = "\n".join(out)
    loc = re.search(r"[–-]\s*([^.–-]+?)\.\s*KKTC", node.get("description") or "")
    phone = re.search(r'https://wa\.me/(\d{10,15})', html)  # satıcı düğmesi (sayfa başlığındaki tel: site telefonudur)
    status = f.get("Araç Durumu:", "")
    posted = _date(f.get("İlan Tarihi:", ""))
    brand, model = (node.get("brand") or {}).get("name"), node.get("model")
    return {
        "brand": brand,
        "model": model,
        "year": year,
        "km": km,
        "fuel": _FUEL.get(f.get("Yakıt Türü:", "").lower()),
        "transmission": _TRANS.get(f.get("Vites Tipi:", "").lower()),
        "engine_l": engine_liters(f.get("Motor Hacmi (cc):")),
        "steering": steering,
        "location": loc.group(1).strip() if loc else None,
        "price_raw": f"{price} {currency}" if amount else None,
        "price_amount": amount,
        "currency": currency,
        "currency_guess": False,
        "urgency_signals": None if amount else ["fiyatsiz"],
        "raw_text": "\n".join(x for x in (node.get("name"), status if "plaka" in status.lower() else "", desc) if x),
        "posted_at": posted,
        "negotiable": bool(re.search(r"pazarl[ıi]k", desc, re.I)) if desc else None,
        "seller_phone": phone.group(1) if phone else None,
        "seller_handle": (node.get("author") or {}).get("name"),
        "seller_type": "bireysel" if "Bireysel Üye" in lines else "bilinmiyor",
    }


GONE = {"is_active": False, "urgency_signals": ["kaldirildi"]}


def fetch_detail(client: httpx.Client, entry: Entry) -> dict | None:
    """None = geçici hata (tekrar denenir). GONE = ilan kaldırılmış (404/410 ya da ilan sayfası olmayan yönlendirme)."""
    r = client.get(entry.url, timeout=30)
    if r.status_code in (404, 410):
        return dict(GONE)
    if r.status_code != 200:
        return None
    return parse_detail(r.text) or dict(GONE)


def new_client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": UA}, follow_redirects=True)


def polite_sleep() -> None:
    time.sleep(CRAWL_DELAY)
