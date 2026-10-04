"""kktcar.com: sitemap'ten ilan adreslerini bulur, ilan sayfasını ayrıştırır (robots.txt izin veriyor)."""
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
    r = client.get(f"{BASE}/sitemap.xml", timeout=60)
    r.raise_for_status()
    out = []
    for block in re.findall(r"<url>(.*?)</url>", r.text, re.S):
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


def parse_detail(html: str) -> dict | None:
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
        return None

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
