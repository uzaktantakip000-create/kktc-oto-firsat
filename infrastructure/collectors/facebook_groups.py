"""Herkese açık Facebook gruplarının gönderilerini Apify üzerinden (girişsiz, çerezsiz) çeker.
Gizlilik: yazar adı/kimliği, yorumlar, profil bağlantıları ve üye bilgisi Apify'dan gelse bile BURADA atılır, hiçbir yere yazılmaz."""
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

import httpx
from apify_client import ApifyClient

ACTOR = "memo23/facebook-public-group-posts-scraper"
START_COST = 0.008  # çalıştırma başlangıç ücreti (GB başına)
POST_COST = 0.0015  # gönderi başına
_ID = re.compile(r"/permalink/(\d+)")
MAX_IMAGE_BYTES = 5_000_000  # fotoğraftan fiyat okuma: bundan büyük görsel indirilmez
IMAGE_TIMEOUT = 15
# Aktörün görsel alanı örnek veriyle DOĞRULANMADI (kayıtlı ham satır yok): bilinen/olası anahtarlar sırayla denenir
_IMAGE_KEYS = ("image", "imageUrl", "image_url", "full_picture", "picture", "thumbnail", "photo", "photoUrl",
               "media", "images", "photos", "attachments", "attachment")
_IMG_URL = re.compile(r"^https?://\S+$")


@dataclass(frozen=True)
class RawGroupPost:
    post_id: str
    url: str
    posted_at: datetime | None
    text: str
    group_url: str  # çalıştırmaya verilen grup adresi (kaynağı bulmak için)
    image_url: str | None = None  # gönderinin ilk fotoğrafı (yalnızca fiyatı yazıda olmayan araç gönderisinde okunur)


def _first_image(value, depth: int = 0) -> str | None:
    """Görsel alanından (metin, sözlük ya da liste) ilk http(s) görsel adresi."""
    if isinstance(value, str):
        return value if _IMG_URL.match(value) else None
    if depth > 3:
        return None
    if isinstance(value, dict):
        for k in ("uri", "url", "src", "photo_image", "image", "full_picture", "original", "large", "thumbnail"):
            if k in value and (u := _first_image(value[k], depth + 1)):
                return u
    elif isinstance(value, list):
        for v in value:
            if u := _first_image(v, depth + 1):
                return u
    return None


def item_image(item: dict) -> str | None:
    for key in _IMAGE_KEYS:
        if key in item and (u := _first_image(item[key])):
            return u
    return None


def fetch_image(url: str, client: httpx.Client | None = None) -> tuple[bytes, str] | None:
    """Fotoğrafı indirir: yalnızca image/*, en çok 5 MB, 15 sn. Başarısızlıkta None (gönderi fotoğrafsız ele alınır)."""
    own = client is None
    client = client or httpx.Client(follow_redirects=True, timeout=IMAGE_TIMEOUT)
    try:
        with client.stream("GET", url, timeout=IMAGE_TIMEOUT) as r:
            mime = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
            if r.status_code != 200 or not mime.startswith("image/"):
                return None
            size = int(r.headers.get("content-length") or 0)
            if size > MAX_IMAGE_BYTES:
                return None
            data = bytearray()
            for chunk in r.iter_bytes():
                data += chunk
                if len(data) > MAX_IMAGE_BYTES:
                    return None
            return (bytes(data), mime) if data else None
    except (httpx.HTTPError, ValueError):
        return None
    finally:
        if own:
            client.close()


def parse_item(item: dict) -> RawGroupPost | None:
    """Apify satırından yalnızca ilan için gerekli alanları alır (yazar/yorum alanları bilerek okunmaz)."""
    url = item.get("url") or ""
    m = _ID.search(url)
    post_id = item.get("legacyId") or (m.group(1) if m else None)
    text = (item.get("text") or "").strip()
    if not post_id or not text:  # metinsiz (yalnızca fotoğraf/Marketplace kartı) gönderi değerlendirilemez
        return None
    ts = item.get("time")
    return RawGroupPost(
        post_id=str(post_id),
        url=url,
        posted_at=datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None,
        text=text,
        group_url=(item.get("inputUrl") or "").rstrip("/"),
        image_url=item_image(item),
    )


def estimate_cost(n_groups: int, max_items: int) -> float:
    """En kötü durum maliyeti (her grup max_items gönderi döndürürse)."""
    return round(START_COST + n_groups * max_items * POST_COST * 1.3, 3)  # %30 pay: ek veri ücretleri


def fetch_group_posts(token: str, group_urls: list[str], hours: int, max_items: int) -> tuple[list[RawGroupPost], float, int]:
    """Gruplardaki son `hours` saatin gönderileri. Döner: (gönderiler, tahmini maliyet USD, Apify'ın döndürdüğü satır sayısı)."""
    client = ApifyClient(token)
    run = client.actor(ACTOR).call(
        run_input={
            "startUrls": group_urls,
            "maxItems": max_items,
            "onlyPostsNewerThanHours": hours,
            "viewOption": "CHRONOLOGICAL",
            "includeComments": False,
            "fetchAllComments": False,
            "includeCommentReplies": False,
        },
        max_total_charge_usd=Decimal(str(estimate_cost(len(group_urls), max_items))),
        run_timeout=timedelta(minutes=10),
        logger=None,
    )
    if run is None:
        raise RuntimeError("Apify çalıştırması başlamadı")
    posts, rows = [], 0
    for item in client.dataset(run.default_dataset_id).iterate_items():
        rows += 1
        post = parse_item(item)
        if post:
            posts.append(post)
    spent = max(float(getattr(run, "usage_total_usd", 0) or 0), START_COST + rows * POST_COST)
    return posts, round(spent, 4), rows
