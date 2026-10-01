from dataclasses import dataclass
from datetime import datetime

from apify_client import ApifyClient

ACTOR = "apify/instagram-post-scraper"


POST_COST = 0.0017  # apify/instagram-post-scraper: gönderi başına (başlangıç ücreti yok)


class PostList(list):
    """Gönderi listesi + bu çalıştırmanın tahmini maliyeti (harcama tavanı takibi için)."""
    cost_usd: float = 0.0


@dataclass(frozen=True)
class RawPost:
    shortcode: str
    url: str
    posted_at: datetime | None
    caption: str
    photo_url: str | None
    owner: str


def fetch_posts(token: str, usernames: list[str], newer_than: str, limit: int = 20) -> PostList:
    """Birden çok profilin yeni gönderilerini TEK Apify çalıştırmasında çeker (resultsLimit profil başınadır).
    newer_than: 'YYYY-MM-DD' veya ISO zaman. Her gönderide owner = gönderiyi paylaşan hesap."""
    client = ApifyClient(token)
    run = client.actor(ACTOR).call(
        run_input={
            "username": usernames,
            "resultsLimit": limit,
            "onlyPostsNewerThan": newer_than,
            "dataDetailLevel": "basicData",
        },
        max_total_charge_usd=round(0.02 + 0.04 * len(usernames), 2),  # profil başına en fazla 20 gönderi ≈ $0.034
        logger=None,
    )
    if run is None:
        raise RuntimeError("Apify çalıştırması başlamadı")
    posts = PostList()
    for item in client.dataset(run.default_dataset_id).iterate_items():
        ts = item.get("timestamp")
        if not item.get("shortCode"):
            continue
        posts.append(
            RawPost(
                shortcode=item["shortCode"],
                url=item.get("url") or f"https://www.instagram.com/p/{item['shortCode']}/",
                posted_at=datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None,
                caption=item.get("caption") or "",
                photo_url=item.get("displayUrl"),
                owner=(item.get("ownerUsername") or "").lower(),
            )
        )
    posts.cost_usd = round(max(float(getattr(run, 'usage_total_usd', 0) or 0), len(posts) * POST_COST), 4)
    return posts


def username_from_url(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]
