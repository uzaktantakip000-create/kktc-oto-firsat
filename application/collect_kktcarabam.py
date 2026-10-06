import time
from dataclasses import dataclass

from infrastructure.collectors import kktcarabam
from infrastructure.db.repository import Repository
from infrastructure.fx.frankfurter import gbp_rate

MAX_DETAIL_PAGES = 18  # tur başına en çok bu kadar ilan sayfası (sahip kararı; liste sayfası zaten 18 kart gösterir)
DETAIL_BUDGET_SECONDS = 240  # yeni bir ilan sayfası bu süreden sonra açılmaz (iş akışı sınırı 25 dk; tipik: 3 sn bekleme + ~6 sn sayfa = ~10 sn/ilan)
MAX_DETAIL_FAILS_IN_A_ROW = 3  # üst üste bu kadar ilan sayfası okunamazsa engel varsayılır: siteyi zorlamadan kalan sayfalar bu tur açılmaz


@dataclass
class KkaStats:
    seen: int = 0
    new: int = 0
    blocked: bool = False
    detail_read: int = 0  # ilan sayfası okundu, km/tarih/satıcı karta eklendi
    detail_failed: int = 0  # engel, zaman aşımı ya da şablon: kart ilan sayfası olmadan, eskisi gibi kaydedildi
    detail_skipped: int = 0  # sınır/süre/üst üste hata yüzünden sayfası açılmayan yeni kart (eskisi gibi kaydedildi)
    detail_conflicts: int = 0  # sayfa kartla çelişti (kart değeri kaldı)


class _DetailReader:
    """Yeni kartların ilan sayfaları, listeyi açan AYNI tarayıcı oturumunda, nazik hızla (her istekten önce `polite_sleep`).
    `read` HİÇBİR hata fırlatmaz: okunamayan sayfa kartı etkilemez (kart eskisi gibi kaydedilir), tek satır log yazılır (ilan numarası ve
    hata türü; satıcı adı/telefon/hata metni asla). Sınırlar: tur başına MAX_DETAIL_PAGES sayfa, DETAIL_BUDGET_SECONDS'tan sonra yeni sayfa
    yok, üst üste MAX_DETAIL_FAILS_IN_A_ROW hatada kalan sayfalar açılmaz (engel olabilir; site zorlanmaz)."""

    def __init__(self, session, stats: KkaStats, clock):
        self.session, self.stats, self.clock = session, stats, clock
        self.deadline = clock() + DETAIL_BUDGET_SECONDS
        self.opened = self.in_a_row = 0

    def read(self, card) -> dict | None:
        """Kartın sayfa alanları; okunamadıysa ya da sınırlar yüzünden açılmadıysa None."""
        if self.opened >= MAX_DETAIL_PAGES or self.clock() > self.deadline or self.in_a_row >= MAX_DETAIL_FAILS_IN_A_ROW:
            self.stats.detail_skipped += 1
            return None
        self.opened += 1
        kktcarabam.polite_sleep()
        try:
            html = kktcarabam.fetch_detail_html(self.session, card.url)
            detail = kktcarabam.parse_detail(html, card) if html else None
            reason = "sayfa açılamadı ya da şablon tanınmadı"
        except Exception as e:  # engel, zaman aşımı, tarayıcı hatası, ayrıştırma hatası: hiçbiri turu düşürmez
            detail, reason = None, type(e).__name__
        if detail is None:
            self.stats.detail_failed += 1
            self.in_a_row += 1
            print(f"KKTCarabam ilan {card.item_id}: ilan sayfası okunamadı ({reason}); kart eskisi gibi kaydedilecek")
            return None
        self.in_a_row = 0
        self.stats.detail_read += 1
        return detail


def _print_detail_summary(stats: KkaStats) -> None:
    if stats.detail_failed and not stats.detail_read:  # hepsi başarısız: tek özet satırı (engel olabilir)
        print(f"KKTCarabam: {stats.detail_failed} ilan sayfasının hiçbiri okunamadı (engel olabilir); kartlar eskisi gibi (km, tarih, satıcı olmadan) kaydedildi")
    if stats.detail_skipped:
        print(f"KKTCarabam: {stats.detail_skipped} yeni ilanın sayfası bu tur açılmadı (sınır, süre ya da üst üste hata); kartlar eskisi gibi kaydedildi")


def collect_kktcarabam(repo: Repository, source: dict, clock=time.monotonic) -> KkaStats:
    """Her çalıştırmada tek liste sayfası (en yeni 18 ilan; site ikinci sayfa isteğinde 403 veriyor, zorlanmaz). Listede olmayan YENİ
    kartların kendi ilan sayfası aynı tarayıcı oturumunda okunur: km, ilan tarihi ("İlan Tarihi"), satıcı adı ve (kartta şehir yoksa)
    konum karta eklenir; kartın fiyat/marka/model/yıl değeri hiçbir zaman değişmez (çelişki tek satır log). Bilinen kartın sayfası
    açılmaz. Her kart kendi sayfasından hemen sonra kaydedilir (tur ortasında kesilse de önceki kartlar kayıtlıdır)."""
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
        reader, handled = _DetailReader(session, stats, clock), set()
        for card in cards:
            if card.item_id in known or card.item_id in handled:  # bilinen kartın sayfası açılmaz; aynı numara iki kez işlenmez
                continue
            data = kktcarabam.card_to_listing(card)
            if not data:
                continue
            handled.add(card.item_id)
            detail = reader.read(card)
            if detail:
                data, conflicts = kktcarabam.merge_detail(data, detail)
                if conflicts:
                    stats.detail_conflicts += 1
                    print(f"KKTCarabam ilan {card.item_id}: ilan sayfası kartla çelişiyor ({', '.join(conflicts)}); kart değeri kaldı")
            amount, cur = data["price_amount"], data["currency"]
            data["price_gbp"] = round(amount * gbp_rate(cur), 2) if amount and cur else None
            data["extraction_by"] = "parser"
            # kapak fotoğrafının adresi (yükleme tarihi içinde): `notify.is_fresh` yalnız tazeliği SIKILAŞTIRMAK için okur; tarih okunamadıysa boş
            data["photo_urls"] = [card.photo_url] if card.photo_at else []
            if repo.upsert_listing(source["id"], card.item_id, data):
                stats.new += 1
    _print_detail_summary(stats)
    repo.mark_checked(source["id"], cursor=None, last_post_at=None, listings_7d=repo.count_recent(source["id"]))
    return stats
