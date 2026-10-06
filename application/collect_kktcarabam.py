import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from infrastructure.collectors import kktcarabam
from infrastructure.db.repository import Repository
from infrastructure.fx.frankfurter import gbp_rate

MAX_DETAIL_PAGES = 18  # tur başına en çok bu kadar ilan sayfası (sahip kararı; liste sayfası zaten 18 kart gösterir)
DETAIL_BUDGET_SECONDS = 240  # yeni bir ilan sayfası bu süreden sonra açılmaz (iş akışı sınırı 25 dk; tipik: 3 sn bekleme + ~6 sn sayfa = ~10 sn/ilan)
MAX_DETAIL_FAILS_IN_A_ROW = 3  # üst üste bu kadar ilan sayfası okunamazsa engel varsayılır: siteyi zorlamadan kalan sayfalar bu tur açılmaz
DETAIL_PAUSE_KEY = "kka_detail_paused_until"  # bot_state: ilan sayfaları bu ana kadar (UTC ISO) açılmaz; VPS ve GitHub turu aynı kaydı görür
DETAIL_PAUSE = timedelta(hours=24)  # hiçbir ilan sayfası okunamayan (hepsi engelli) turdan sonra ilan sayfaları bu kadar süre denenmez
_PAUSE_SKEW = timedelta(minutes=5)  # saat farkı payı: kayıt DETAIL_PAUSE + bu süreden fazla ilerideyse bozuk sayılır (sonsuza kadar duraklamasın)


@dataclass
class KkaStats:
    seen: int = 0
    new: int = 0
    blocked: bool = False
    detail_read: int = 0  # ilan sayfası okundu, km/tarih/satıcı karta eklendi
    detail_failed: int = 0  # engel, zaman aşımı ya da şablon: kart ilan sayfası olmadan, eskisi gibi kaydedildi
    detail_skipped: int = 0  # sınır/süre/üst üste hata yüzünden sayfası açılmayan yeni kart (eskisi gibi kaydedildi)
    detail_conflicts: int = 0  # sayfa kartla çelişti (kart değeri kaldı)
    detail_paused: bool = False  # ilan sayfaları duraklatılmıştı (önceki tur tümüyle engellendi): bu tur HİÇ denenmedi
    detail_pause_set: bool = False  # bu tur hiçbir ilan sayfası okunamadı ve üst üste hata sınırına varıldı: duraklatma kaydı yazıldı


class _DetailReader:
    """Yeni kartların ilan sayfaları, listeyi açan AYNI tarayıcı oturumunda, nazik hızla (her istekten önce `polite_sleep`).
    `read` HİÇBİR hata fırlatmaz: okunamayan sayfa kartı etkilemez (kart eskisi gibi kaydedilir), tek satır log yazılır (ilan numarası ve
    hata türü; satıcı adı/telefon/hata metni asla). Sınırlar: tur başına MAX_DETAIL_PAGES sayfa, DETAIL_BUDGET_SECONDS'tan sonra yeni sayfa
    yok, üst üste MAX_DETAIL_FAILS_IN_A_ROW hatada kalan sayfalar açılmaz (engel olabilir; site zorlanmaz)."""

    def __init__(self, session, stats: KkaStats, clock):
        self.session, self.stats, self.clock = session, stats, clock
        self.deadline = clock() + DETAIL_BUDGET_SECONDS
        self.opened = self.in_a_row = 0

    @property
    def blocked(self) -> bool:
        """Üst üste hata sınırına varıldı (engel varsayılır)."""
        return self.in_a_row >= MAX_DETAIL_FAILS_IN_A_ROW

    def read(self, card) -> dict | None:
        """Kartın sayfa alanları; okunamadıysa ya da sınırlar yüzünden açılmadıysa None."""
        if self.opened >= MAX_DETAIL_PAGES or self.clock() > self.deadline or self.blocked:
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


def _paused_until(repo: Repository, now: datetime) -> datetime | None:
    """Duraklatma sürüyorsa bitiş anı, yoksa None. Okunamayan kayıt (hata), bozuk/saat dilimsiz metin, geçmişte kalmış ya da çok ileri tarihli değer =
    duraklatma YOK: ilan sayfaları eskisi gibi denenir. Hata fırlatmaz (toplayıcı bu kayıt yüzünden hiçbir zaman düşmez)."""
    try:
        raw = repo.get_state(DETAIL_PAUSE_KEY)
    except Exception as e:
        print(f"KKTCarabam: ilan sayfası duraklatma kaydı okunamadı ({type(e).__name__}); ilan sayfaları eskisi gibi denenecek")
        return None
    try:
        until = datetime.fromisoformat(raw) if raw else None
    except (TypeError, ValueError):
        return None
    if until is None or until.tzinfo is None:
        return None
    return until if now < until <= now + DETAIL_PAUSE + _PAUSE_SKEW else None


def _start_pause(repo: Repository, now: datetime, stats: KkaStats) -> None:
    """Hiçbir ilan sayfası okunamadı ve üst üste hata sınırına varıldı (engel): bu çalışma anından DETAIL_PAUSE sonrasına kadar ilan sayfaları
    açılmasın (her turda boşuna 3 engelli istek sunucunun IP'sini liste sayfasından da engellettirebilir). Yazılamazsa tur bozulmaz: sonraki tur
    ilan sayfalarını eskisi gibi dener."""
    until = now + DETAIL_PAUSE
    try:
        repo.set_state(DETAIL_PAUSE_KEY, until.isoformat())
    except Exception as e:
        print(f"KKTCarabam: ilan sayfası duraklatma kaydı yazılamadı ({type(e).__name__}); sonraki tur ilan sayfalarını yine deneyecek")
        return
    stats.detail_pause_set = True
    print(f"KKTCarabam: ilan sayfaları {DETAIL_PAUSE // timedelta(hours=1)} saat duraklatıldı (hepsi engelli; sunucu zorlanmasın): "
          f"{until:%d.%m %H:%M} UTC'den sonraki ilk tur yeniden dener")


def _print_detail_summary(stats: KkaStats, paused_until: datetime | None = None) -> None:
    if paused_until is not None:  # hiç denenmedi: tek satır (engel/hata özetleri anlamsız)
        print(f"KKTCarabam: ilan sayfaları {paused_until:%d.%m %H:%M} UTC'ye kadar duraklatıldı (önceki tur hepsi engelli); "
              "kartlar eskisi gibi (km, tarih, satıcı olmadan) kaydedildi")
        return
    if stats.detail_failed and not stats.detail_read:  # hepsi başarısız: tek özet satırı (engel olabilir)
        print(f"KKTCarabam: {stats.detail_failed} ilan sayfasının hiçbiri okunamadı (engel olabilir); kartlar eskisi gibi (km, tarih, satıcı olmadan) kaydedildi")
    if stats.detail_skipped:
        print(f"KKTCarabam: {stats.detail_skipped} yeni ilanın sayfası bu tur açılmadı (sınır, süre ya da üst üste hata); kartlar eskisi gibi kaydedildi")


def collect_kktcarabam(repo: Repository, source: dict, clock=time.monotonic, now: datetime | None = None) -> KkaStats:
    """Her çalıştırmada tek liste sayfası (en yeni 18 ilan; site ikinci sayfa isteğinde 403 veriyor, zorlanmaz). Listede olmayan YENİ
    kartların kendi ilan sayfası aynı tarayıcı oturumunda okunur: km, ilan tarihi ("İlan Tarihi"), satıcı adı ve (kartta şehir yoksa)
    konum karta eklenir; kartın fiyat/marka/model/yıl değeri hiçbir zaman değişmez (çelişki tek satır log). Bilinen kartın sayfası
    açılmaz. Her kart kendi sayfasından hemen sonra kaydedilir (tur ortasında kesilse de önceki kartlar kayıtlıdır).
    Hiçbir ilan sayfası okunamayıp üst üste hata sınırına varılan tur, ilan sayfalarını 24 saat duraklatır (`bot_state.kka_detail_paused_until`):
    duraklama sürerken yalnız liste okunur, kartlar eskisi gibi kaydedilir; süre dolunca sonraki tur ilan sayfalarını normal dener.
    Kaydın okunması/yazılması hata verirse davranış eskisi gibidir (ilan sayfaları denenir). `now` yalnız sınama için."""
    stats = KkaStats()
    now = now or datetime.now(timezone.utc)
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
        paused_until = _paused_until(repo, now)
        stats.detail_paused = paused_until is not None
        reader, handled = (None if paused_until else _DetailReader(session, stats, clock)), set()
        for card in cards:
            if card.item_id in known or card.item_id in handled:  # bilinen kartın sayfası açılmaz; aynı numara iki kez işlenmez
                continue
            data = kktcarabam.card_to_listing(card)
            if not data:
                continue
            handled.add(card.item_id)
            detail = reader.read(card) if reader is not None else None  # duraklatıldıysa ilan sayfası hiç açılmaz
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
    _print_detail_summary(stats, paused_until)
    if reader is not None and not stats.detail_read and reader.blocked:  # hepsi engelli: sonraki turlar boşuna denemesin
        _start_pause(repo, now, stats)
    repo.mark_checked(source["id"], cursor=None, last_post_at=None, listings_7d=repo.count_recent(source["id"]))
    return stats
