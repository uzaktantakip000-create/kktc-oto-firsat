from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from application.llm_reader import LlmReader, listing_fields
from domain.caption_parser import is_sold_post, parse_caption, sold_ilan_no
from infrastructure.collectors.instagram_apify import PostList, RawPost, fetch_posts, username_from_url
from infrastructure.db.repository import Repository
from infrastructure.fx.frankfurter import gbp_rate


OVERLAP_MIN = 20  # önceki turun sonundan bu kadar geriden başla (kaçan gönderi olmasın; bilinenler tekrar eklenmez)
MONTHLY_BUDGET_USD = 10.0  # aylık Instagram (Apify) harcama tavanı; aşılırsa o ay toplama durur


@dataclass
class CollectStats:
    fetched: int = 0
    new: int = 0
    parsed: int = 0
    needs_llm: int = 0
    llm_read: int = 0  # kural okuyamadı, yapay zekâ okudu (en fazla 🟡)
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


def collect_sources(repo: Repository, apify_token: str, sources: list[dict], first_run_days: int = 3,
                    reader: LlmReader | None = None) -> dict[str, CollectStats]:
    """Tüm Instagram kaynaklarını tek Apify çalıştırmasında toplar (dakika ve maliyet tasarrufu).
    Başlangıç tarihi: en eski imleç (cursor); yeni eklenen kaynak varsa ilk-çalıştırma tarihi. Bilinen gönderiler atlanır."""
    if not sources:
        return {}
    now = datetime.now(timezone.utc)
    key = f"ig_spend:{now:%Y-%m}"
    spent_month = float(repo.get_state(key, "0"))
    if spent_month >= MONTHLY_BUDGET_USD:
        raise RuntimeError(f"Instagram toplama aylık tavana ulaştı (${spent_month:.2f} / ${MONTHLY_BUDGET_USD:.0f})")
    first_run = (now - timedelta(days=first_run_days)).strftime("%Y-%m-%d")
    # Gönderi başına ücret var: her turda yalnızca bir ÖNCEKİ turdan sonrasını isteriz (en eski imleç değil: yavaş bir hesap
    # tüm hesapların eski gönderilerini yeniden çektirir). Hiç taranmamış (imleçsiz) hesaplar ayrı, tek seferlik çağrı alır.
    established = [s for s in sources if s["cursor"]]
    fresh = [s for s in sources if not s["cursor"]]
    watermark = repo.get_state("ig_watermark", "")
    names = {username_from_url(s["url"]).lower(): s for s in sources}
    posts = PostList()
    try:
        if established:
            if watermark and watermark > first_run:
                since = (datetime.strptime(watermark, "%Y-%m-%dT%H:%M:%S") - timedelta(minutes=OVERLAP_MIN)).strftime("%Y-%m-%dT%H:%M:%S")
            else:
                since = max(min(s["cursor"] for s in established), first_run)
            got = fetch_posts(apify_token, [username_from_url(s["url"]) for s in established], since)
            posts.extend(got)
            posts.cost_usd += getattr(got, "cost_usd", 0.0)
        if fresh:
            got = fetch_posts(apify_token, [username_from_url(s["url"]) for s in fresh], first_run)
            posts.extend(got)
            posts.cost_usd += getattr(got, "cost_usd", 0.0)
    except Exception as e:  # çalıştırma başladıktan sonraki hata (süre aşımı, veri okunamadı): ücret yine de harcamaya yazılır
        posts.cost_usd += getattr(e, "cost_usd", 0.0)
        repo.set_state(key, f"{spent_month + posts.cost_usd:.4f}")
        print(f"Instagram: çalıştırma başarısız, maliyet ${posts.cost_usd:.4f} kaydedildi (ay: ${spent_month + posts.cost_usd:.2f})")
        raise
    repo.set_state(key, f"{spent_month + getattr(posts, 'cost_usd', 0.0):.4f}")
    print(f"Instagram: tur maliyeti ${getattr(posts, 'cost_usd', 0.0):.4f} (ay: ${spent_month + getattr(posts, 'cost_usd', 0.0):.2f} / ${MONTHLY_BUDGET_USD:.0f})")
    repo.set_state("ig_watermark", now.strftime("%Y-%m-%dT%H:%M:%S"))  # Apify saatleri UTC
    by_source: dict[str, list[RawPost]] = {n: [] for n in names}
    for post in posts:
        if post.owner in by_source:
            by_source[post.owner].append(post)
    result: dict[str, CollectStats] = {}
    for name, source in names.items():
        stats = result[source["name"]] = CollectStats()
        batch = by_source[name]
        stats.fetched = len(batch)
        known = repo.known_item_ids(source["id"]) if reader else set()
        newest = max((p.posted_at for p in batch if p.posted_at), default=None)
        for post in batch:
            data, parsed = listing_data(post)
            no = sold_ilan_no(post.caption)
            if (not parsed and reader and post.shortcode not in known and post.caption.strip()
                    and not is_sold_post(post.caption)):
                extra = listing_fields(reader.read(post.caption))  # sadece yeni ve okunamayan gönderi: bir kez ödenir
                if extra:
                    data = {**data, **extra}
                    stats.llm_read += 1
            if no:  # "SATILDI" paylaşımı: yeni ilan değil; eski ilan kapatılır, bu gönderi aktif ilan sayılmaz
                data = {**data, "is_active": False, "urgency_signals": ["satildi"]}
                stats.sold += repo.deactivate_by_ilan_no(source["id"], no)
            if repo.upsert_listing(source["id"], post.shortcode, data):
                stats.new += 1
                stats.parsed += parsed
                stats.needs_llm += not parsed
        repo.mark_checked(
            source["id"],
            cursor=newest.strftime("%Y-%m-%dT%H:%M:%S") if newest else (now.strftime("%Y-%m-%dT%H:%M:%S") if not source["cursor"] else None),
            last_post_at=newest,
            listings_7d=repo.count_recent(source["id"]),
        )
    return result
