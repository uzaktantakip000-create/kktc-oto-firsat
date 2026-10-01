"""Herkese açık Facebook gruplarının gönderilerini Apify üzerinden (girişsiz, çerezsiz) çeker.
Gizlilik: yazar adı/kimliği, yorumlar, profil bağlantıları ve üye bilgisi Apify'dan gelse bile BURADA atılır, hiçbir yere yazılmaz."""
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from apify_client import ApifyClient

ACTOR = "memo23/facebook-public-group-posts-scraper"
START_COST = 0.008  # çalıştırma başlangıç ücreti (GB başına)
POST_COST = 0.0015  # gönderi başına
_ID = re.compile(r"/permalink/(\d+)")


@dataclass(frozen=True)
class RawGroupPost:
    post_id: str
    url: str
    posted_at: datetime | None
    text: str
    group_url: str  # çalıştırmaya verilen grup adresi (kaynağı bulmak için)


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
