"""kibrisarabaal.com: sitemap'ten ilan adreslerini bulur, ilan sayfasındaki JSON-LD + görünür alanları ayrıştırır. Giriş/çerez yok.
10.10.2026'dan beri site bizi 403 ile engelliyor; istekler YALNIZ application/kaa_gate.py'nin kapısından geçer (izin, robots.txt, kota,
aralık, engel beklemesi). Bu dosya yalnız adresleri, ayrıştırmayı ve düz httpx istemcisini bilir: tarayıcı taklidi (browserlike/Scrapling),
proxy, çerez YOK. Yönlendirme kendiliğinden izlenmez: her istek sayılsın ve aralıklansın (kaldırılan ilanın ana sayfası hiç istenmez)."""
import json
import re
import urllib.robotparser
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx
from urllib.parse import urljoin, urlparse
from selectolax.parser import HTMLParser

from domain.engine import engine_liters

BASE = "https://kibrisarabaal.com"
SITEMAP = f"{BASE}/sitemap-listings.xml"
ROBOTS = f"{BASE}/robots.txt"
AGENT = "KKTCOtoBot"  # robots.txt'de aranan ad ve User-Agent'ın başı (sitenin bizi tanıyıp kural yazabilmesi için)
BLOCK_CODES = (403, 429, 503)  # engel/yavaşla: tur hemen durur, 24 saat istek yok (kaa_gate.on_blocked)
_REDIRECTS = (301, 302, 303, 307, 308)
MAX_HOPS = 3  # ilan sayfası kendi adresine yönlenirse (sondaki / vb.) en çok bu kadar adım izlenir


class Blocked(Exception):
    """Site 403/429/503 döndü: engel ya da "yavaşla". retry_after: sitenin Retry-After başlığındaki bekleme (yoksa None)."""

    def __init__(self, status: int, url: str, retry_after: timedelta | None = None):
        super().__init__(f"KibrisArabaAl HTTP {status} ({urlparse(url).path or '/'})")
        self.status, self.retry_after = status, retry_after


def retry_after(value: str | None, now: datetime | None = None) -> timedelta | None:
    """Retry-After başlığı: saniye ("3600") ya da HTTP tarihi. Okunamaz/geçmiş ise None."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return timedelta(seconds=min(int(value), 366 * 86400))  # akıl dışı büyük sayı taşmasın (kaa_gate zaten 30 güne kırpar)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None or when.tzinfo is None:
        return None
    wait = when - (now or datetime.now(timezone.utc))
    return wait if wait > timedelta(0) else None


def raise_if_blocked(r) -> None:
    """403/429/503 -> Blocked (hangi adres olursa olsun: robots.txt, site haritası, ilan sayfası)."""
    if r.status_code in BLOCK_CODES:
        headers = getattr(r, "headers", None) or {}
        raise Blocked(r.status_code, str(getattr(r, "url", "") or ""), retry_after(headers.get("Retry-After")))


def robots_policy(status: int, text: str) -> urllib.robotparser.RobotFileParser:
    """robots.txt yanıtından kural (RFC 9309): 200 -> metin; 401/403 -> her şey yasak; diğer 4xx (404 vb.) -> serbest.
    5xx ve yönlendirmeyi çağıran (kaa_gate) bu tura hiç almaz; 403/429/503 zaten Blocked."""
    rp = urllib.robotparser.RobotFileParser(ROBOTS)
    if status == 200:
        rp.parse(text.splitlines())
    elif status in (401, 403):
        rp.disallow_all = True
    else:
        rp.allow_all = True
    rp.modified()
    return rp


def crawl_delay(rp: urllib.robotparser.RobotFileParser) -> float:
    """robots.txt'in bize istediği aralık (Crawl-delay ya da Request-rate); yazmıyorsa 0."""
    delay = float(rp.crawl_delay(AGENT) or 0)
    rate = rp.request_rate(AGENT)
    if rate and rate.requests:
        delay = max(delay, rate.seconds / rate.requests)
    return delay
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


def fetch_sitemap(client) -> list[Entry]:
    """`client`: kaa_gate.PoliteClient (izin/robots/kota/aralık). Yönlendirme de hata sayılır (raise_for_status 3xx'te de yükselir)."""
    r = client.get(SITEMAP, timeout=60)
    raise_if_blocked(r)
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
    # Satıcının başlığı ("2013 Model Otomatik Nissan Juke") ile "Yıl:" alanı (plakasız araçta çoğu zaman ithal/kayıt yılı: 2022)
    # çelişebiliyor: eski yılı al (değeri düşük tahmin eder; yanlış 🟢/🟠 yerine kaçan fırsat)
    h1 = tree.css_first("h1")
    m = re.match(r"\s*((?:19|20)\d{2})\s+model\b", h1.text(strip=True).lower()) if h1 else None
    if m and 1970 <= int(m.group(1)) < year:
        year = int(m.group(1))
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
    # Açıklama: "<yıl> <marka> <model>[ – <şehir>]. KKTC satılık…": şehir ayracı boşluklu UZUN tire. Kısa tire marka/model adındadır
    # ("Mercedes - Benz SLK 200", "C-HR", "T-Roc"): şehir yazmayan ilanda onu ayraç saymak şehre "Benz SLK"/"Roc" yazıyordu (10.10 bulgusu)
    loc = re.search(r"\s–\s*([^.–]+?)\.\s*KKTC", node.get("description") or "")
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


def _is_listing_url(final_url: str, entry: Entry) -> bool:
    """Kaldırılmış ilan ana sayfaya (ya da ilan numarası içermeyen bir adrese) yönlenir."""
    path = urlparse(str(final_url)).path.strip("/")
    return bool(path) and entry.item_id in path


def fetch_detail(client, entry: Entry) -> dict | None:
    """None = geçici hata ya da OKUNAMADI (şablon değişirse sessizce 'kaldırıldı' yazılmaz). 403/429/503 -> Blocked.
    GONE = ilan kaldırılmış: yalnızca 404/410 ya da ilan sayfası olmayan yönlendirme (hedefi İSTENMEZ: ana sayfaya boşuna istek yok).
    İlanın kendi adresine yönlenme (sondaki / vb.) en çok MAX_HOPS adım izlenir; her adım ayrı, sayılan ve aralıklanan istektir."""
    r = client.get(entry.url, timeout=30)
    for _ in range(MAX_HOPS):
        raise_if_blocked(r)
        if r.status_code not in _REDIRECTS:
            break
        target = urljoin(entry.url, (getattr(r, "headers", None) or {}).get("location") or "")
        if not _is_listing_url(target, entry) or not (urlparse(target).hostname or "").endswith("kibrisarabaal.com"):
            return dict(GONE)
        r = client.get(target, timeout=30)
    raise_if_blocked(r)
    if r.status_code in (404, 410):
        return dict(GONE)
    if r.status_code != 200:
        return None
    if not _is_listing_url(r.url, entry):
        return dict(GONE)
    return parse_detail(r.text)


def new_client(headers: dict[str, str]) -> httpx.Client:
    """Düz httpx: tarayıcı parmak izi yok, ortamdaki proxy ayarları okunmaz (trust_env=False: istek hep sunucunun kendi IP'sinden,
    kimliğimizle gider), yönlendirme kendiliğinden izlenmez. Başlıklar (dürüst User-Agent, From, varsa erişim anahtarı) kaa_gate'ten."""
    return httpx.Client(headers=headers, follow_redirects=False, trust_env=False, timeout=30)
