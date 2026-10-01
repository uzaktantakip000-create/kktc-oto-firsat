from dataclasses import dataclass

from infrastructure.collectors import kktcarabam
from infrastructure.db.repository import Repository
from infrastructure.fx.frankfurter import gbp_rate


@dataclass
class KkaStats:
    seen: int = 0
    new: int = 0
    blocked: bool = False


def collect_kktcarabam(repo: Repository, source: dict) -> KkaStats:
    """Her çalıştırmada tek sayfa (en yeni 18 ilan). Site ikinci sayfa isteğinde 403 veriyor; bunu zorlamıyoruz."""
    stats = KkaStats()
    known = repo.known_item_ids(source["id"])
    with kktcarabam.open_session() as session:
        html = kktcarabam.fetch_html(session, f"{kktcarabam.LIST_URL}?p=1")
    if html is None:
        stats.blocked = True
        return stats
    cards = kktcarabam.parse_list(html)
    stats.seen = len(cards)
    for card in cards:
        if card.item_id in known:
            continue
        data = kktcarabam.card_to_listing(card)
        if not data:
            continue
        amount, cur = data["price_amount"], data["currency"]
        data["price_gbp"] = round(amount * gbp_rate(cur), 2) if amount and cur else None
        data["extraction_by"] = "parser"
        data["photo_urls"] = []
        if repo.upsert_listing(source["id"], card.item_id, data):
            stats.new += 1
    repo.mark_checked(source["id"], cursor=None, last_post_at=None, listings_7d=repo.count_recent(source["id"]))
    return stats
