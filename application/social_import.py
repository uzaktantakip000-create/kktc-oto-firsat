"""Devir dosyası -> ilan satırı (sosyal okuyucunun veritabanı yolu). VPS'teki okuyucu (kktc-social) gördüğü her YENİ gönderiyi ham haliyle
devir klasörüne yazar (infrastructure/social_files.HandoffWriter); bu aktarıcı botun turunda (kktc-tick, kktc-bot kullanıcısı) çalışır:
satırları okur, anahtarı sources satırına eşler, gönderileri sosyal ayrıştırma yolundan geçirir (social_ingest.ingest_facebook ->
collect_facebook.listing_data -> parse_freetext; ikinci bir ayrıştırma yok) ve Repository.upsert_listing ile yazar. Okuyucuya veritabanı
şifresi verilmez, güvenlik duvarı açılmaz.

Satır (surum 1): {"surum", "platform", "anahtar": "fb:<grup>" | "fbp:<sayfa>", "gonderi", "url", "metin", "paylasim_utc", "goruldu_utc", "foto_url"}; isteğe bağlı
(satıcı radarı, 10.10.2026; eski satırlarda yok): "satici_gonderi_14g" (telefonun 14 günde geçtiği FARKLI gönderi sayısı) ve "satici_grup_14g".
Radar sayısı ilanın seller_type'ına çevrilir (social_ingest.seller_kind); telefon satırda yoktur.
İsteğe bağlı "ilk_gonderi" (20.10.2026): aynı metnin okuyucunun gördüğü ilk gönderisi; aktarımdan sonra bu ilan o gönderinin ilanının kopyası
olur (Repository.link_first_post: aynı marka/model/yıl, aynı tutar, gölge/aktiflik sırası). Bulunamazsa ya da koşul tutmazsa yok sayılır;
saatlik kopya taraması (application/dedupe) yine çalışır.
Dosyadaki adres KULLANILMAZ: bağlantı anahtar + gönderi kimliğinden kurulur (okuyucu tarafı ne yazarsa yazsın mesaja yalnız Facebook grup
gönderisi bağlantısı gider). Görsel adresi yalnız Facebook CDN'i ise alınır. Paylaşım zamanı okunamamışsa ilk görülme zamanı yazılır
(yoksa birikmiş eski satırlar aktarıldığı an "yeni" sayılırdı).
İmleç bot_state'te ("sosyal_devir:facebook" -> {"dosya", "satir"}): "\\n" ile bitmeyen son satır (yazım sürüyor) işlenmez, imleç onu geçmez.
Aynı satır iki kez gelirse upsert anahtarı (source_id, gönderi) yüzünden etkisizdir. Kaynak eşleme: kaynaklar.json ile aynı anahtar
(domain.source_links.key_of_url); yalnız açık (aktif/deneme) kaynağa yazılır. Gölge/yeşil ayrımı sources.alert_level'dadır, burada değil."""
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from application.collect_facebook import group_default_steering
from application.social_ingest import ReadBudget, ingest_facebook
from application.social_port import SocialPost, SocialSource
from domain.kktc_time import to_kktc
from domain.price import price_tokens
from domain.source_links import key_of_url

VERSION = 1
PLATFORM = "facebook"
STATE_KEY = "sosyal_devir:facebook"
HANDOFF_DIR = os.environ.get("SOCIAL_DEVIR_DIR", "/var/lib/kktc-social-devir")  # okuyucunun devir klasörü (VPS)
OPEN_STATUSES = ("aktif", "deneme")
ALL_STATUSES = ("aktif", "deneme", "aday", "pasif", "disari", "erisim_reddediyor")
MAX_LINES = 2000  # tur başına üst sınır (kalan bir sonraki tura)
MAX_TEXT = 8000
EXTRA_MAX_LINES = 8  # "ek_metin": satış kutusu satırları (sosyal oturum 13.10.2026: ≤8 satır, satır ≤120 karakter)
EXTRA_LINE_MAX = 120
FRESHNESS = ("kesin", "imlec", "belirsiz")  # devir "tazelik" (sosyal oturum 13.10.2026); "belirsiz" ilan anlık bildirim almaz (domain.data_gate.DATE_UNSURE)
FUTURE_SLACK = timedelta(minutes=10)  # paylaşım saati görülme anından bundan fazla ilerideyse yanlış okunmuştur (saat dilimi): yok sayılır
MAX_RADAR = 10_000  # satıcı radarı sayısının makul üst sınırı (bozuk değer yok sayılır)
_FILE = re.compile(r"^facebook-\d{8}\.jsonl$")
_GROUP = re.compile(r"^[a-z0-9._-]{1,64}$")  # kaynaklar.json grup anahtarı (social_files._FB_GROUP ile aynı)
_PAGE = re.compile(r"^[a-z0-9.]{1,64}$")  # "fbp:<sayfa>" (domain.source_links.FB_PAGE; okuyucunun sayfa kalıbı)
_POST_ID = re.compile(r"^(?:\d{5,25}|pfbid[A-Za-z0-9]{10,})$")  # facebook_browser._PID_OK ile aynı
_CDN = re.compile(r"^https://scontent[^/\s]*\.fbcdn\.net/\S+$", re.I)
# Fotoğraf devri (sosyal oturum 43bba41; sahip onayı 11.10.2026 "fotoğraftan fiyat okuma: evet"): okuyucu fiyatı yazmayan araç gönderisinin
# sayfada zaten yüklenmiş ilk görselini <devir>/foto/<gonderi>.jpg olarak kaydeder (ek istek yok), satıra "foto_dosya" yazar. Bot o dosyayı
# yerelden okur ve görüntü okuyucuya verir; ağdan görsel ÇEKİLMEZ (CDN adresi asla indirilmez).
# 15 dakikalık tur (entrypoints/tick) ile Facebook hızlı şeridi (entrypoints/fb_fast, 11.10.2026) aynı satırları aynı anda aktarmasın (çift
# fotoğraf okuması, geri giden imleç): bot_state kilidi. Takılan (çöken) bir aktarımın kilidi IMPORT_LOCK_MIN dakika sonra devralınır.
IMPORT_LOCK = "sosyal_devir"
IMPORT_LOCK_MIN = 10
FILE_PREFIX = "file:"
PHOTO_READS = 20  # tur başına en çok fotoğraf okuması (okuyucunun tur tavanıyla aynı)
PHOTO_MAX_BYTES = 400_000
_PHOTO_FILE = re.compile(r"^foto/[A-Za-z0-9]{5,60}\.jpg$")
# Satış kutusu fiyatının gelip gelmediği (27.10.2026: 24.10'dan beri "ek_metin"de hiç fiyat yok, önceden günde ~900 satır; bot fark etmedi):
# aktarım anının KKTC gününe göre okunan gönderi satırı ve "ek_metin"i para birimli tutar taşıyanı sayılır. Yargı handoff_watch.box_alert'te.
BOX_KEY = "devir_kutu:"  # + "YYYY-MM-DD" (KKTC günü) -> {"lines", "priced"}


@dataclass
class ImportReport:
    lines: int = 0  # okunan tam satır
    posts: int = 0  # geçerli gönderi (ayrıştırmaya giden)
    new_listings: int = 0  # yeni yazılan ilan (fiyatsızlar dahil)
    priceless: int = 0  # bunlardan fiyatı yazmayan araç gönderisi (fiyatsız ilan, "📞 FİYAT SOR")
    field_price: int = 0  # fiyatı yalnız Facebook satış kutusunda yazan ilan ("fiyat_alani": en fazla 🟡, emsal olmaz)
    not_listing: dict[str, int] = field(default_factory=dict)  # ilan sayılmayan gönderilerin nedeni (diagnose)
    skipped: dict[str, int] = field(default_factory=dict)  # bozuk_satir / surum / platform / alan / bilinmeyen_anahtar / kaynak_kapali
    sources: int = 0  # satırı olan kaynak
    pending: bool = False  # sınır yüzünden kalan satır var (sonraki turda)
    linked: int = 0  # "ilk_gonderi" ile ilk gönderinin ilanına kopya olarak bağlanan ilan

    def summary(self) -> list[str]:
        if not self.lines:
            return ["sosyal devir: yeni satır yok"]
        out = [f"sosyal devir: {self.lines} satır, {self.posts} gönderi, {self.sources} kaynak, {self.new_listings} yeni ilan"
               + (f" ({self.priceless} fiyatsız)" if self.priceless else "")
               + (f" ({self.field_price} fiyatı satış kutusundan)" if self.field_price else "")
               + (f" ({self.linked} ilk gönderiye bağlandı)" if self.linked else "")
               + (" (kalan var, sonraki turda)" if self.pending else "")]
        if self.not_listing:
            out.append("  ilan değil: " + ", ".join(f"{k}={v}" for k, v in sorted(self.not_listing.items())))
        if self.skipped:
            out.append("  atlanan satır: " + ", ".join(f"{k}={v}" for k, v in sorted(self.skipped.items())))
        return out


def _count(d: dict[str, int], key: str, n: int = 1) -> None:
    d[key] = d.get(key, 0) + n


def _cursor(raw: str | None) -> tuple[str, int]:
    try:
        doc = json.loads(raw or "")
        name, line = doc["dosya"], doc["satir"]
        if isinstance(name, str) and isinstance(line, int) and not isinstance(line, bool) and line >= 0:
            return name, line
    except (ValueError, KeyError, TypeError):
        pass
    return "", 0


def read_new_lines(dir: Path | str, cursor: tuple[str, int], limit: int = MAX_LINES) -> tuple[list[str], tuple[str, int], bool]:
    """İmleçten sonraki TAM satırlar (dosya adı tarih sırası = yazım sırası). Dönüş: (satırlar, yeni imleç, kalan var mı)."""
    d = Path(dir)
    files = sorted(f.name for f in d.iterdir() if _FILE.match(f.name) and f.is_file()) if d.is_dir() else []
    name0, line0 = cursor
    out: list[str] = []
    new = cursor
    for name in files:
        if name < name0:
            continue
        complete = (d / name).read_bytes().split(b"\n")[:-1]  # son öğe "\n"den sonrası: boş ya da yazımı süren yarım satır
        start = min(line0, len(complete)) if name == name0 else 0
        take = complete[start:start + (limit - len(out))]
        out.extend(b.decode("utf-8", "replace") for b in take)
        new = (name, start + len(take))
        if len(out) >= limit:
            return out, new, start + len(take) < len(complete) or name != files[-1]
    return out, new, False


def _parse_time(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        t = datetime.fromisoformat(value)
    except ValueError:
        return None
    return t if t.tzinfo is not None else None


def _extra_text(value) -> str | None:
    """Devir "ek_metin" alanı: boş olmayan satırlar, en çok EXTRA_MAX_LINES; sınırdan uzun satır atılır. Dize değilse ya da boşsa None
    (satır yine aktarılır: alan isteğe bağlı). Facebook'un zaman/"Sponsored" yazısını harf harf bölüp araya koyduğu görünmez birleştirici
    (U+034F) atılır; 2'den az harf/rakamlı satır gürültüdür (13.10.2026: ilk satırların hepsinin 8 satırını tek harfler doldurmuştu)."""
    if not isinstance(value, str):
        return None
    lines = [ln.replace("\u034f", "").strip() for ln in value.split("\n")]
    keep = [ln for ln in lines if len(ln) <= EXTRA_LINE_MAX and ln.isprintable() and sum(ch.isalnum() for ch in ln) >= 2]
    return "\n".join(keep[:EXTRA_MAX_LINES]) or None


def parse_line(line: str) -> tuple[str, SocialPost] | str:
    """Satır -> (anahtar, gönderi) ya da atlama nedeni. Gönderinin grup anahtarı (source_key) DB satırına eşlenirken doldurulur."""
    try:
        doc = json.loads(line)
    except ValueError:
        return "bozuk_satir"
    if not isinstance(doc, dict):
        return "bozuk_satir"
    if doc.get("surum") != VERSION:
        return "surum"
    if doc.get("platform") != PLATFORM:
        return "platform"
    key, pid, text = doc.get("anahtar"), doc.get("gonderi"), doc.get("metin")
    page = key[4:] if isinstance(key, str) and key.startswith("fbp:") else ""
    group = key[3:] if isinstance(key, str) and key.startswith("fb:") else ""
    if not ((_GROUP.match(group) or _PAGE.match(page)) and isinstance(pid, str) and _POST_ID.match(pid) and isinstance(text, str)):
        return "alan"
    if page:  # Facebook sayfası (sosyal oturum 0be07c6): bağlantı yine anahtar + kimlikten kurulur; reel yalnız dosyadaki adres tam reel biçimiyse
        reel = f"https://www.facebook.com/reel/{pid}/"  # Facebook'un kendi reel bağlantısı (okuyucu kimliği href="/reel/<kimlik>/"den alır)
        url = reel if doc.get("url") in (reel, f"https://www.facebook.com/{page}/reel/{pid}/") else f"https://www.facebook.com/{page}/posts/{pid}/"
    else:
        url = f"https://www.facebook.com/groups/{group}/posts/{pid}/"
    photo, radar, photo_file = doc.get("foto_url"), doc.get("satici_gonderi_14g"), doc.get("foto_dosya")
    image = photo if isinstance(photo, str) and _CDN.match(photo) else None
    if isinstance(photo_file, str) and photo_file == f"foto/{pid}.jpg" and _PHOTO_FILE.match(photo_file):
        image = FILE_PREFIX + photo_file  # yerel kopya (yalnız bu gönderinin adıyla)
    posted, seen = _parse_time(doc.get("paylasim_utc")), _parse_time(doc.get("goruldu_utc"))
    if posted and seen and posted > seen + FUTURE_SLACK:
        posted = None  # gelecekte paylaşılmış olamaz: saat dilimi/okuma hatası (14.10.2026: okuyucu zamanı Facebook yazısından okumaya başladı)
    post = SocialPost(platform=PLATFORM, source_key=page or group, post_id=pid, url=url,
                      posted_at=posted or seen, text=strip_sharer(text)[:MAX_TEXT], image_url=image, dated=posted is not None,
                      seller_posts_14d=radar if type(radar) is int and 1 <= radar <= MAX_RADAR else None,
                      extra_text=_extra_text(doc.get("ek_metin")),
                      freshness=doc.get("tazelik") if doc.get("tazelik") in FRESHNESS else None,
                      first_post=_first_post(doc.get("ilk_gonderi"), pid))
    return key, post


# Facebook'un paylaşılmış fotoğraf gönderisi başlığı ("+16 Photos from <Ad Soyad>'s post"): paylaşanın ADI ilan metnine girmesin
# (sosyal oturum 27.10.2026: okuyucu da artık siliyor; burada ikinci kez, eski/yeni okuyucudan bağımsız)
_SHARER = re.compile(r"(?:\+\d+\s*)?Photos from [^\n]{1,80}?'s post", re.I)


def strip_sharer(text: str) -> str:
    return _SHARER.sub("", text)


def _first_post(value, pid: str) -> str | None:
    """Devir "ilk_gonderi": gönderi kimliği biçiminde ve satırın kendi kimliğinden farklıysa; değilse None (alan isteğe bağlı, bozuk değer yok sayılır)."""
    return value if isinstance(value, str) and _POST_ID.match(value) and value != pid else None


def read_photo(devir_dir: Path | str, image_url: str | None) -> tuple[bytes, str] | None:
    """Devir klasöründeki fotoğraf kopyası (bytes, mime). Yalnız "file:foto/<gonderi>.jpg" okunur; CDN adresi, eksik/okunamayan ya da çok
    büyük dosya None (fotoğraf okunmaz, ilan fiyatsız kalır). Dosyaya yazılmaz, silinmez (okuyucu 3 günde siler)."""
    if not image_url or not image_url.startswith(FILE_PREFIX) or not _PHOTO_FILE.match(image_url[len(FILE_PREFIX):]):
        return None
    path = Path(devir_dir) / image_url[len(FILE_PREFIX):]
    try:
        if path.stat().st_size > PHOTO_MAX_BYTES:
            return None
        return path.read_bytes(), "image/jpeg"
    except OSError:
        return None


def source_map(rows: list[dict]) -> dict[str, dict]:
    """anahtar -> sources satırı. Aynı anahtarda birden çok satır varsa açık olan (aktif/deneme) kazanır (source_export.build gibi)."""
    out: dict[str, dict] = {}
    for r in rows:
        key = key_of_url(PLATFORM, r.get("url"))
        if key is None:
            continue
        if key not in out or (r["status"] in OPEN_STATUSES and out[key]["status"] not in OPEN_STATUSES):
            out[key] = r
    return out


def import_facebook(repo, devir_dir: Path | str, *, limit: int = MAX_LINES, log: Callable[[str], None] = lambda _: None,
                    make_reader=None, now: datetime | None = None) -> ImportReport:
    """Bir aktarım turu. repo: Repository (sources, get_state/set_state, upsert_listing, mark_checked, count_recent).
    Fotoğraf kopyası olan satır varsa görüntü okuyucu kurulur (make_reader; yoksa llm_reader.from_env): yalnız fotoğraf okur, turda en çok
    PHOTO_READS; anahtar yoksa ya da günlük bütçe dolduysa ilan fiyatsız kalır.
    İmleç ancak bütün satırlar yazıldıktan sonra ilerler (yarıda kalırsa satırlar bir dahaki turda yeniden gelir; upsert etkisiz)."""
    rep = ImportReport()
    cursor = _cursor(repo.get_state(STATE_KEY))
    lines, new_cursor, rep.pending = read_new_lines(devir_dir, cursor, limit)
    rep.lines = len(lines)
    if not lines:
        if new_cursor != cursor:
            repo.set_state(STATE_KEY, json.dumps({"dosya": new_cursor[0], "satir": new_cursor[1]}))
        return rep
    sources = source_map(repo.sources(PLATFORM, ALL_STATUSES))
    by_key: dict[str, list[SocialPost]] = {}
    box_lines = box_priced = 0
    for line in lines:
        got = parse_line(line)
        if isinstance(got, str):
            _count(rep.skipped, got)
            continue
        key, post = got
        box_lines += 1
        box_priced += box_has_price(post.extra_text)
        row = sources.get(key)
        if row is None:
            _count(rep.skipped, "bilinmeyen_anahtar")
            continue
        if row["status"] not in OPEN_STATUSES:
            _count(rep.skipped, "kaynak_kapali")
            continue
        by_key.setdefault(key, []).append(post)
    reader, budget = None, ReadBudget(km_left=0, photos_left=PHOTO_READS)
    if any(p.image_url and p.image_url.startswith(FILE_PREFIX) for ps in by_key.values() for p in ps):
        if make_reader is None:
            from application import llm_reader  # tembel: yapay zekâ bağımlılıkları yalnız fotoğraf varken
            make_reader = llm_reader.from_env
        reader = make_reader(repo)
    for key, posts in by_key.items():
        row = sources[key]
        source = SocialSource(platform=PLATFORM, key=key.split(":", 1)[1], url=row["url"], alias=key, source_id=row["id"],
                              default_steering=group_default_steering(row))
        st = ingest_facebook(posts, source, repo, reader=reader, fetch_image=lambda url: read_photo(devir_dir, url), budget=budget,
                             priceless=True, text_llm=False)
        rep.posts += st.fetched
        rep.new_listings += st.new
        rep.priceless += st.priceless
        rep.field_price += st.field_price
        for why, n in st.reasons.items():
            _count(rep.not_listing, why, n)
        latest = max((p.posted_at for p in posts if p.posted_at), default=None)
        repo.mark_checked(row["id"], cursor=None, last_post_at=latest, listings_7d=repo.count_recent(row["id"]))
        rep.linked += _link_first_posts(repo, row["id"], posts, log)
    rep.sources = len(by_key)
    repo.set_state(STATE_KEY, json.dumps({"dosya": new_cursor[0], "satir": new_cursor[1]}))
    _note_box(repo, now or datetime.now(timezone.utc), box_lines, box_priced)
    for line in rep.summary():
        log(line)
    return rep


def box_has_price(extra: str | None) -> bool:
    """Satış kutusu satırlarında para birimli bir tutar var mı (€6,500, £4.900, TRY650,000, 650.000 TL; ayrıştırıcının fiyat adayı belirteci)."""
    return bool(extra) and any(t.currency for t in price_tokens(extra))


def _note_box(repo, now: datetime, lines: int, priced: int) -> None:
    """Günün satış kutusu sayacına ekler (BOX_KEY). Bozuk eski değer sıfırdan sayılır."""
    if not lines:
        return
    key = BOX_KEY + to_kktc(now).date().isoformat()
    try:
        old = json.loads(repo.get_state(key) or "{}")
    except ValueError:
        old = {}
    old = old if isinstance(old, dict) else {}
    was_lines, was_priced = (v if type(v) is int and v >= 0 else 0 for v in (old.get("lines"), old.get("priced")))
    repo.set_state(key, json.dumps({"lines": was_lines + lines, "priced": was_priced + priced}))


def _link_first_posts(repo, source_id, posts: list[SocialPost], log) -> int:
    """"ilk_gonderi"li gönderilerin ilanını ilk gönderinin ilanına kopya olarak bağlar; bağlanan sayısı. Kopya bağı bir iyileştirmedir: hata
    aktarımı durdurmaz (yalnız hata türü yazılır; saatlik kopya taraması yine çalışır)."""
    n = 0
    for p in posts:
        if not p.first_post:
            continue
        try:
            n += repo.link_first_post(source_id, p.post_id, p.first_post)
        except Exception as e:
            log(f"sosyal devir: ilk gönderi bağı kurulamadı: {type(e).__name__}")
            return n
    return n


def import_facebook_locked(repo, devir_dir: Path | str, **kw) -> ImportReport | None:
    """import_facebook, aktarım kilidiyle. Kilit öbür yoldaysa (tur ya da hızlı şerit şu an aktarıyor) None: satırlar orada işlenir."""
    token = repo.acquire_lock(IMPORT_LOCK, minutes=IMPORT_LOCK_MIN)
    if not token:
        return None
    try:
        return import_facebook(repo, devir_dir, **kw)
    finally:
        repo.release_lock(IMPORT_LOCK, token)


def has_new_lines(repo, devir_dir: Path | str) -> bool:
    """İmleçten sonra en az bir TAM satır var mı (yalnız dosya okunur; hızlı şeridin "çalışırken yeni satır geldi mi" sorusu)."""
    lines, _, _ = read_new_lines(devir_dir, _cursor(repo.get_state(STATE_KEY)), 1)
    return bool(lines)
