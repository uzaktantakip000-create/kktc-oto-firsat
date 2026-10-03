"""kktcarabam.com: Cloudflare arkasında; sayfalar gerçek (headless) tarayıcıyla, normal bir ziyaretçi gibi açılır.
Bot kontrolünü çözme/atlatma YOK: tarayıcı modu da engellenirse kaynak atlanır."""
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from selectolax.parser import HTMLParser

from domain.normalize import canon_transmission
from domain.price import parse_price

BASE = "https://www.kktcarabam.com"
LIST_URL = BASE + "/kategori/satilik-otomobil"  # sirala= parametresi robots.txt ile yasak: kullanılmaz
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["ocak", "şubat", "mart", "nisan", "mayıs", "haziran", "temmuz", "ağustos", "eylül", "ekim", "kasım", "aralık"])}
_FUEL = {"benzin": "benzin", "dizel": "dizel", "hibrit": "hibrit", "elektrik": "elektrikli", "lpg": "lpg"}


@dataclass(frozen=True)
class Card:
    item_id: str
    url: str
    title: str  # "2013 Model Otomatik Mercedes-Benz E Serisi"
    price_text: str  # "14.999 GBP" / "0 TL"
    label: str = ""  # görsel üstündeki "Mercedes-Benz E Serisi"


def parse_list(html: str) -> list[Card]:
    cards = []
    for a in HTMLParser(html).css("a.ilan"):
        href = a.attributes.get("href", "")
        m = re.search(r"/(\d{5,})-", href)
        if not m:
            continue
        h3, price = a.css_first("h3"), a.css_first(".fiyat")
        span = a.css_first(".resim span")
        cards.append(
            Card(
                m.group(1),
                href,
                h3.text(strip=True) if h3 else "",
                price.text(strip=True) if price else "",
                span.text(strip=True) if span else "",
            )
        )
    return cards


def _pairs(lines: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for i, l in enumerate(lines[:-1]):
        if l.endswith(":") and l not in out and not lines[i + 1].endswith(":"):
            out[l] = lines[i + 1]
    return out


def parse_detail(html: str, card: Card) -> dict | None:
    tree = HTMLParser(html)
    for n in tree.css("script,style,noscript,svg"):
        n.decompose()
    lines = [l.strip() for l in tree.body.text(separator="\n").splitlines() if l.strip()]
    f = _pairs(lines)
    if "Yıl:" not in f:
        return None

    # Marka/model: breadcrumb "2. El Araçlar | Otomobil | Suzuki | Swift | 1.2 | 2024 Model ..."
    brand = model = None
    for k, l in enumerate(lines):
        if l in ("2. El Araçlar", "Sıfır Km Araçlar") and k + 3 < len(lines) and not lines[k + 1].startswith("("):
            brand, model = lines[k + 2], lines[k + 3]  # lines[k+1] = kategori (Otomobil, SUV ...)
            break
    km = re.sub(r"\D", "", f.get("Kilometre:", ""))
    price = parse_price(card.price_text)
    steer = f.get("Direksiyon Tipi:", "").lower()
    posted = None
    m = re.fullmatch(r"(\d{1,2}) (\w+) (\d{4})", f.get("İlan Tarihi:", ""), re.I)
    if m and m.group(2).lower() in _MONTHS:
        posted = datetime(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)), tzinfo=timezone.utc)
    # Satıcı: 'Güvenlik' bölümünden sonra gelen firma adı satırı
    seller = None
    if "Güvenlik" in lines and lines.index("Güvenlik") + 1 < len(lines):
        seller = lines[lines.index("Güvenlik") + 1]
    location = None
    for i, l in enumerate(lines):
        if l == "/" and i > 0 and i + 1 < len(lines) and "İlan No:" in lines[i + 2 : i + 4]:
            location = f"{lines[i - 1]} / {lines[i + 1]}"
            break
    return {
        "brand": brand,
        "model": model,
        "year": int(re.sub(r"\D", "", f["Yıl:"])) if re.sub(r"\D", "", f["Yıl:"]) else None,
        "km": int(km) if km and int(km) > 0 else None,
        "fuel": _FUEL.get(f.get("Yakıt Türü:", "").lower()),
        "transmission": canon_transmission(f.get("Vites Tipi:", "")) or None,
        "steering": "RHD" if "sağ" in steer else "LHD" if "sol" in steer else None,
        "location": location,
        "price_raw": card.price_text,
        "price_amount": price.amount if price else None,
        "currency": price.currency if price else None,
        "currency_guess": price.currency_guess if price else False,
        "raw_text": card.title,
        "posted_at": posted,
        "seller_handle": seller,
        "seller_type": "galeri" if seller and seller.isupper() else "bilinmiyor",
        "urgency_signals": [] if price else ["fiyatsiz"],
        "url": card.url,
    }


def open_session():
    """Gerçek headless tarayıcı oturumu (Scrapling). with bloğunda kullanılır."""
    from scrapling.fetchers import DynamicSession

    return DynamicSession(headless=True, network_idle=True, timeout=60000)


def fetch_html(session, url: str) -> str | None:
    page = session.fetch(url)
    return page.html_content if page.status == 200 and page.html_content else None


_CITIES = {"lefkosa", "girne", "gazimagusa", "magusa", "iskele", "guzelyurt", "lefke", "lapta", "alsancak", "other"}


def card_to_listing(card: Card) -> dict | None:
    """Sadece liste kartından ilan çıkarır (km yok). Sunucuya ilan başına ayrı istek atmamak için bilerek böyle.

    Başlık: '2013 Model Otomatik Mercedes-Benz E Serisi'; adres: '/263800-mercedes-benz-e-serisi-girne-dizel-otomatik'.
    """
    m = re.match(r"(\d{4}) Model (\w+) ", card.title)
    if not m or not card.label:
        return None
    price = parse_price(card.price_text)
    slug_tokens = card.url.rstrip("/").rsplit("/", 1)[-1].split("-")
    fuel = next((_FUEL[t] for t in slug_tokens if t in _FUEL), None)
    city = next((t for t in slug_tokens if t in _CITIES), None)
    brand, _, model = card.label.partition(" ")
    return {
        "brand": brand,
        "model": model or None,
        "year": int(m.group(1)),
        "transmission": canon_transmission(m.group(2)),
        "fuel": fuel,
        "location": city,
        "price_raw": card.price_text,
        "price_amount": price.amount if price else None,
        "currency": price.currency if price else None,
        "currency_guess": price.currency_guess if price else False,
        "raw_text": card.title,
        "urgency_signals": [] if price else ["fiyatsiz"],
        "seller_type": "bilinmiyor",
        "url": card.url,
    }
