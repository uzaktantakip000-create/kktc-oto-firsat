from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from domain.caption_parser import parse_caption, sold_ilan_no
from infrastructure.collectors.instagram_apify import RawPost, fetch_posts, username_from_url
from infrastructure.db.repository import Repository
from infrastructure.fx.frankfurter import gbp_rate


MONTHLY_BUDGET_USD = 8.0  # aylık Instagram (Apify) harcama tavanı; aşılırsa o ay toplama durur


@dataclass
class CollectStats:
    fetched: int = 0
    new: int = 0
    parsed: int = 0
    needs_llm: int = 0
    sold: int = 0


def listing_data(post: RawPost) -> tuple[dict, bool]:
    """Gönderiyi listings satırına çevirir. İkinci değer: kural tabanlı parser başarılı mı."""
    base = {
        "url": post.url,
        "posted_at": post.posted_at,
        "raw_text": post.caption,
        "photo_urls": [post.photo_url] if post.photo_url else [],
        "seller_handle": post.owner,
    }
    p = parse_caption(post.caption)
    if p is None:
        return base | {"extraction_by": None}, False
    price_gbp = round(p.price_amount * gbp_rate(p.currency), 2) if p.price_amount and p.currency else None
    return base | {
        "brand": p.brand,
        "model": p.model,
        "year": p.year,
        "km": p.km,
        "fuel": p.fuel,
        "transmission": p.transmission,
        "steering": p.steering,
        "location": p.location,
        "price_raw": p.price_raw,
        "price_amount": p.price_amount,
        "currency": p.currency,
        "currency_guess": p.currency_guess,
        "price_gbp": price_gbp,
        "seller_phone": p.phone,
        "negotiable": p.negotiable,
        "extraction_by": "parser",
    }, True


def collect_sources(repo: Repository, apify_token: str, sources: list[dict], first_run_days: int = 3) -> dict[str, CollectStats]:
    """Tüm Instagram kaynaklarını tek Apify çalıştırmasında toplar (dakika ve maliyet tasarrufu).
    Başlangıç tarihi: en eski imleç (cursor); yeni eklenen kaynak varsa ilk-çalıştırma tarihi. Bilinen gönderiler atlanır."""
    if not sources:
        return {}
    now = datetime.now(timezone.utc)
    key = f"ig_spend:{now:%Y-%m}"
    spent_month = float(repo.get_state(key, "0"))
    if spent_month >= MONTHLY_BUDGET_USD:
        raise RuntimeError(f"Instagram toplama aylık tavana ulaştı (${spent_month:.2f} / ${MONTHLY_BUDGET_USD:.0f})")
    first_run = (datetime.now(timezone.utc) - timedelta(days=first_run_days)).strftime("%Y-%m-%d")
    newer_than = max(min(s["cursor"] or first_run for s in sources), first_run)  # en fazla 3 gün geri (maliyet sınırı)
    names = {username_from_url(s["url"]).lower(): s for s in sources}
    posts = fetch_posts(apify_token, [username_from_url(s["url"]) for s in sources], newer_than)
    repo.set_state(key, f"{spent_month + getattr(posts, 'cost_usd', 0.0):.4f}")
    by_source: dict[str, list[RawPost]] = {n: [] for n in names}
    for post in posts:
        if post.owner in by_source:
            by_source[post.owner].append(post)
    result: dict[str, CollectStats] = {}
    for name, source in names.items():
        stats = result[source["name"]] = CollectStats()
        batch = by_source[name]
        stats.fetched = len(batch)
        newest = max((p.posted_at for p in batch if p.posted_at), default=None)
        for post in batch:
            data, parsed = listing_data(post)
            no = sold_ilan_no(post.caption)
            if no:  # "SATILDI" paylaşımı: yeni ilan değil; eski ilan kapatılır, bu gönderi aktif ilan sayılmaz
                data = {**data, "is_active": False, "urgency_signals": ["satildi"]}
                stats.sold += repo.deactivate_by_ilan_no(source["id"], no)
            if repo.upsert_listing(source["id"], post.shortcode, data):
                stats.new += 1
                stats.parsed += parsed
                stats.needs_llm += not parsed
        repo.mark_checked(
            source["id"],
            cursor=newest.strftime("%Y-%m-%dT%H:%M:%S") if newest else None,
            last_post_at=newest,
            listings_7d=repo.count_recent(source["id"]),
        )
    return result
