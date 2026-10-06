"""kktcarabam.com: Cloudflare arkasında; sayfalar gerçek (headless) tarayıcıyla, normal bir ziyaretçi gibi açılır.
Bot kontrolünü çözme/atlatma YOK: tarayıcı modu da engellenirse kaynak atlanır."""
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from domain.duplicates import district
from domain.normalize import canon_fuel, canon_transmission, normalize_brand, normalize_model
from domain.photo_date import kktcarabam_photo_time
from domain.price import parse_price

BASE = "https://www.kktcarabam.com"
LIST_URL = BASE + "/kategori/satilik-otomobil"  # sirala= parametresi robots.txt ile yasak: kullanılmaz
CRAWL_DELAY = 3.0  # nazik hız: ilan sayfası istekleri arasında (diğer tarayıcı-parmak izli kaynaklarla aynı: Mezunum, KibrisCars, SahibindenArabaKibris)
DETAIL_TIMEOUT_MS = 25_000  # tek ilan sayfası için sınır (liste sayfası oturum varsayılanı 60 sn)
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
    photo_url: str | None = None  # kartın kapak fotoğrafı (liste kartı); yükleme anı adresinde: /uploads/images/YYYY/MM/DD/HH/...
    photo_at: datetime | None = None  # fotoğrafın yükleme anı (UTC; `domain.photo_date`); adres yok/bozuk/tanınmıyorsa None


def _photo_url(src: str | None) -> str | None:
    """Kart görselinin adresi (göreli olabilir: site adresine tamamlanır); boş/bozuksa None, hata fırlatmaz."""
    src = (src or "").strip()
    if not src:
        return None
    try:
        return urljoin(BASE + "/", src)
    except ValueError:  # ör. köşeli parantezi eksik adres
        return None


def parse_list(html: str) -> list[Card]:
    cards = []
    for a in HTMLParser(html).css("a.ilan"):
        href = a.attributes.get("href", "")
        m = re.search(r"/(\d{5,})-", href)
        if not m:
            continue
        h3, price = a.css_first("h3"), a.css_first(".fiyat")
        span = a.css_first(".resim span")
        img = a.css_first(".resim img")
        photo = _photo_url(img.attributes.get("src") if img is not None else None)
        cards.append(
            Card(
                m.group(1),
                href,
                h3.text(strip=True) if h3 else "",
                price.text(strip=True) if price else "",
                span.text(strip=True) if span else "",
                photo,
                kktcarabam_photo_time(photo),
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
    """İlan sayfasından alanlar. None = bu kartın ilan sayfası değil ya da okunamadı (şablon/engel sayfası: "Yıl:" yok; sayfadaki
    "İlan No:" kartın numarasından farklı: yönlendirme/yanlış sayfa, başka aracın km'si karta karışmasın).
    İlan tarihi (`posted_at`) sayfada yalnız GÜN olarak yazar ("1 Ekim 2026"; saat yok): UTC gece yarısı olarak saklanır, diğer siteler gibi
    (KibrisArabaAl, KibrisCars). Bu an KKTC saatiyle (Asia/Famagusta, yazın +3, kışın +2) AYNI takvim gününe düşer (02:00/03:00): `to_kktc`
    ile gösterilen gün de, `notify.posted_age_text`'in yazdığı gün de ilandaki gündür. Gerçek yayın saati o günün içinde herhangi biri
    olabilir: hesaplanan yaş gerçekten en çok 3 saat az, en çok 21 saat fazla çıkar (4 günlük tazelik penceresinde önemsiz; fazla
    görünmek güvenli yöndür: şüpheli ilan taze sayılmaz)."""
    tree = HTMLParser(html)
    for n in tree.css("script,style,noscript,svg"):
        n.decompose()
    lines = [l.strip() for l in tree.body.text(separator="\n").splitlines() if l.strip()]
    f = _pairs(lines)
    if "Yıl:" not in f:
        return None
    page_no = re.sub(r"\D", "", f.get("İlan No:", ""))
    if page_no and page_no != card.item_id:
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
    # Satıcı: 'Güvenlik' başlığından sonra gelen firma adı satırı; ardından "Şehir / İlçe" satırı gelmelidir. Güvenlik başlığının altında
    # donanım satırları varsa ("ABS"...) firma adı DEĞİL bir donanım okunurdu: şablon doğrulanamazsa satıcı boş kalır (yanlış ad yazılmaz)
    seller = None
    g = lines.index("Güvenlik") if "Güvenlik" in lines else None
    if g is not None and g + 2 < len(lines) and " / " in lines[g + 2]:
        seller = lines[g + 1]
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


def fetch_detail_html(session, url: str, timeout_ms: int = DETAIL_TIMEOUT_MS) -> str | None:
    """Tek ilan sayfası, AYNI tarayıcı oturumunda ve sınırlı süreyle (Scrapling oturumu hata alırsa kendi içinde 3 kez dener: en kötü
    durum ~3 x timeout; toplam süreyi toplayıcı bütçesi sınırlar). 200 değilse ya da gövde boşsa None; ağ/zaman aşımı hatası FIRLATIR
    (çağıran yakalar)."""
    page = session.fetch(url, timeout=timeout_ms)
    return page.html_content if page.status == 200 and page.html_content else None


def polite_sleep() -> None:
    time.sleep(CRAWL_DELAY)


# Liste kartının TAŞIDIĞI alanlar sayfayla çelişirse kart kalır: bu alanlar için sayfa yalnız kontrol amaçlıdır.
# Karta ait olmayan alanlar (km, ilan tarihi, satıcı adı, direksiyon) sayfadan DOLDURULUR. Direksiyon: kartta yok ve emsal seçimi bilinmeyen
# direksiyonu SAĞ sayar (domain/comparables.py); soldan direksiyonlu (ucuz) araç sağ direksiyonlularla kıyaslanıp sahte ucuz görünmesin.
_FILL_FROM_PAGE = ("km", "posted_at", "seller_handle", "steering")
_UNKNOWN_LOCATION = (None, "", "other")  # kart adresinde şehir yoksa ("other") sayfadaki konum doldurur


def merge_detail(card_data: dict, detail: dict) -> tuple[dict, list[str]]:
    """Kart ilanına (`card_to_listing`) ilan sayfasının (`parse_detail`) alanlarını ekler. Dönen: (birleşmiş ilan, çelişen alan adları).
    Fiyat, marka, model, yıl, vites, yakıt kartta olduğu gibi KALIR; kartta olmayan km, ilan tarihi, satıcı adı ve (kartta şehir yoksa)
    konum sayfadan gelir. Sayfadaki değer kartla ÇELİŞİRSE (yıl, normalleştirilmiş marka/model, yakıt, vites, ilçe) kart değeri kalır ve
    alanın adı çelişenler listesine girer. Kart değeri olmayan ya da sayfada bulunmayan alan çelişki sayılmaz."""
    out = dict(card_data)
    conflicts: list[str] = []
    for key in _FILL_FROM_PAGE:
        if out.get(key) is None and detail.get(key) is not None:
            out[key] = detail[key]
    if out.get("location") in _UNKNOWN_LOCATION and detail.get("location"):
        out["location"] = detail["location"]

    def differs(card_value, page_value) -> bool:
        return card_value is not None and page_value is not None and card_value != page_value

    if differs(card_data.get("year"), detail.get("year")):
        conflicts.append("year")
    brands = (normalize_brand(card_data.get("brand")), normalize_brand(detail.get("brand")))
    if differs(*brands):
        conflicts.append("brand")
    elif differs(normalize_model(brands[0], card_data.get("model")), normalize_model(brands[1], detail.get("model"))):
        conflicts.append("model")
    if differs(canon_fuel(card_data.get("fuel")), canon_fuel(detail.get("fuel"))):
        conflicts.append("fuel")
    if differs(canon_transmission(card_data.get("transmission")) or None, canon_transmission(detail.get("transmission")) or None):
        conflicts.append("transmission")
    if differs(district(card_data.get("location")), district(detail.get("location"))):
        conflicts.append("location")
    return out, conflicts


_CITIES = {"lefkosa", "girne", "gazimagusa", "magusa", "iskele", "guzelyurt", "lefke", "lapta", "alsancak", "other"}


def card_to_listing(card: Card) -> dict | None:
    """Sadece liste kartından ilan çıkarır (km, ilan tarihi, satıcı yok): bunlar yeni kartlar için ilan sayfasından eklenir (`merge_detail`);
    sayfa açılamazsa ilan böyle, kart haliyle kaydedilir.

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
