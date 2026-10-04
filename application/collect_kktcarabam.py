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
    if html is None:  # engel/zaman aşımı: sessiz "başarılı tur" değil hata (sayaç + alarm, kaynak "kontrol edildi" işaretlenmez)
        stats.blocked = True
        raise RuntimeError(f"{source['name']}: liste sayfası alınamadı (engel ya da zaman aşımı olabilir)")
    cards = kktcarabam.parse_list(html)
    stats.seen = len(cards)
    if not cards:  # sayfa geldi ama hiç ilan kartı yok: şablon değişmiş ya da engel/boş sayfa
        raise RuntimeError(f"{source['name']}: liste sayfasında hiç ilan kartı bulunamadı — site şablonu değişmiş ya da engel sayfası olabilir")
    repo.mark_alive(source["id"], [c.item_id for c in cards if c.item_id in known])  # listede görüldü: canlı
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
