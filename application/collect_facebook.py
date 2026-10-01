import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from domain.freetext_parser import diagnose, parse_freetext
from infrastructure.collectors import facebook_groups
from infrastructure.collectors.facebook_groups import RawGroupPost
from infrastructure.db.repository import Repository
from infrastructure.fx.frankfurter import gbp_rate

MONTHLY_BUDGET_USD = 45.0  # aylık Apify harcama tavanı (grup toplama); aşılırsa o ay toplama durur (kullanıcı kararı: gündüz 2 saatte bir)
MAX_ITEMS_PER_GROUP = 40
MAX_HOURS = 14


@dataclass
class FbStats:
    fetched: int = 0
    new: int = 0
    skipped: int = 0  # ilan değil / fiyat-yıl-marka belirsiz: HİÇBİR ŞEY saklanmaz
    spent_usd: float = 0.0
    reasons: dict[str, int] = field(default_factory=dict)  # ilan sayılmayan gönderilerin nedeni (sayaç)


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


def _count_funnel(repo: Repository, result: dict[str, FbStats], now: datetime) -> None:
    """Aylık huni sayacı: kaç gönderiye bakıldı, kaçı ilan, ilan olmayanlar neden. Metin/yazar saklanmaz."""
    key = f"fb_funnel:{now:%Y-%m}"
    try:
        total = json.loads(repo.get_state(key, "{}") or "{}")
    except ValueError:
        total = {}
    for st in result.values():
        total["gonderi"] = total.get("gonderi", 0) + st.fetched
        total["ilan"] = total.get("ilan", 0) + (st.fetched - st.skipped)
        for why, n in st.reasons.items():
            total[why] = total.get(why, 0) + n
    repo.set_state(key, json.dumps(total))


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
            why = diagnose(post.text)
            st.reasons[why] = st.reasons.get(why, 0) + 1
            continue
        if repo.upsert_listing(source["id"], post.post_id, data):
            st.new += 1
    _count_funnel(repo, result, now)
    share = round(spent / len(sources), 4)
    for source in sources:
        result[source["name"]].spent_usd = share
        last_post = newest.get(source["name"])
        repo.mark_checked(source["id"], cursor=None, last_post_at=last_post, listings_7d=repo.count_recent(source["id"]))
    return result
