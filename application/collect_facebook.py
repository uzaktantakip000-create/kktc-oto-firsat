from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from domain.freetext_parser import parse_freetext
from infrastructure.collectors import facebook_groups
from infrastructure.collectors.facebook_groups import RawGroupPost
from infrastructure.db.repository import Repository
from infrastructure.fx.frankfurter import gbp_rate

MONTHLY_BUDGET_USD = 15.0  # aylık Apify harcama tavanı (grup toplama); aşılırsa o ay toplama durur
MAX_ITEMS_PER_GROUP = 40
MAX_HOURS = 14


@dataclass
class FbStats:
    fetched: int = 0
    new: int = 0
    skipped: int = 0  # ilan değil / fiyat-yıl-marka belirsiz: HİÇBİR ŞEY saklanmaz
    spent_usd: float = 0.0


def group_default_steering(source: dict) -> str | None:
    return "LHD" if "sol direksiyon" in (source.get("name") or "").lower() else None


def listing_data(post: RawGroupPost, source: dict) -> dict | None:
    """Gönderiyi listings satırına çevirir; ilan değilse/belirsizse None. Yazar, yorum, profil bilgisi SAKLANMAZ."""
    p = parse_freetext(post.text, default_steering=group_default_steering(source))
    if p is None:
        return None
    return {
        "url": post.url,
        "posted_at": post.posted_at,
        "raw_text": post.text,
        "photo_urls": [],
        "brand": p.brand, "model": p.model, "year": p.year, "km": p.km, "fuel": p.fuel,
        "transmission": p.transmission, "steering": p.steering, "location": p.location,
        "price_raw": p.price_raw, "price_amount": p.price_amount, "currency": p.currency,
        "currency_guess": False, "price_gbp": round(p.price_amount * gbp_rate(p.currency), 2),
        "seller_phone": p.phone, "negotiable": p.negotiable, "extraction_by": "parser_serbest",
    }


def _month_key(now: datetime) -> str:
    return f"fb_spend:{now:%Y-%m}"


def collect_facebook_groups(repo: Repository, apify_token: str, sources: list[dict],
                            fetch=facebook_groups.fetch_group_posts, now: datetime | None = None) -> dict[str, FbStats]:
    now = now or datetime.now(timezone.utc)
    if not sources:
        return {}
    key = _month_key(now)
    spent_month = float(repo.get_state(key, "0"))
    if spent_month >= MONTHLY_BUDGET_USD:
        raise RuntimeError(f"Facebook grup toplama aylık tavana ulaştı (${spent_month:.2f} / ${MONTHLY_BUDGET_USD:.0f})")
    last = min((s["last_checked_at"] for s in sources if s["last_checked_at"]), default=None)
    hours = MAX_HOURS if last is None else min(MAX_HOURS, max(2, int((now - last) / timedelta(hours=1)) + 1))
    by_url = {s["url"].rstrip("/"): s for s in sources}
    posts, spent, _rows = fetch(apify_token, list(by_url), hours, MAX_ITEMS_PER_GROUP)
    repo.set_state(key, f"{spent_month + spent:.4f}")
    result = {s["name"]: FbStats() for s in sources}
    newest: dict[str, datetime] = {}
    for post in posts:
        source = by_url.get(post.group_url)
        if source is None:
            continue
        st = result[source["name"]]
        st.fetched += 1
        if post.posted_at and (source["name"] not in newest or post.posted_at > newest[source["name"]]):
            newest[source["name"]] = post.posted_at
        data = listing_data(post, source)
        if data is None:
            st.skipped += 1
            continue
        if repo.upsert_listing(source["id"], post.post_id, data):
            st.new += 1
    share = round(spent / len(sources), 4)
    for source in sources:
        result[source["name"]].spent_usd = share
        last_post = newest.get(source["name"])
        repo.mark_checked(source["id"], cursor=None, last_post_at=last_post, listings_7d=repo.count_recent(source["id"]))
    return result
