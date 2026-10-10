"""Devir dosyası -> ilan satırı (sosyal okuyucunun veritabanı yolu). VPS'teki okuyucu (kktc-social) gördüğü her YENİ gönderiyi ham haliyle
devir klasörüne yazar (infrastructure/social_files.HandoffWriter); bu aktarıcı botun turunda (kktc-tick, kktc-bot kullanıcısı) çalışır:
satırları okur, anahtarı sources satırına eşler, gönderileri sosyal ayrıştırma yolundan geçirir (social_ingest.ingest_facebook ->
collect_facebook.listing_data -> parse_freetext; ikinci bir ayrıştırma yok) ve Repository.upsert_listing ile yazar. Okuyucuya veritabanı
şifresi verilmez, güvenlik duvarı açılmaz.

Satır (surum 1): {"surum", "platform", "anahtar": "fb:<grup>", "gonderi", "url", "metin", "paylasim_utc", "goruldu_utc", "foto_url"}.
Dosyadaki adres KULLANILMAZ: bağlantı anahtar + gönderi kimliğinden kurulur (okuyucu tarafı ne yazarsa yazsın mesaja yalnız Facebook grup
gönderisi bağlantısı gider). Görsel adresi yalnız Facebook CDN'i ise alınır. Paylaşım zamanı okunamamışsa ilk görülme zamanı yazılır
(yoksa birikmiş eski satırlar aktarıldığı an "yeni" sayılırdı).
İmleç bot_state'te ("sosyal_devir:facebook" -> {"dosya", "satir"}): "\\n" ile bitmeyen son satır (yazım sürüyor) işlenmez, imleç onu geçmez.
Aynı satır iki kez gelirse upsert anahtarı (source_id, gönderi) yüzünden etkisizdir. Kaynak eşleme: kaynaklar.json ile aynı anahtar
(domain.source_links.key_of_url); yalnız açık (aktif/deneme) kaynağa yazılır. Gölge/yeşil ayrımı sources.alert_level'dadır, burada değil."""
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from application.collect_facebook import group_default_steering
from application.social_ingest import ingest_facebook
from application.social_port import SocialPost, SocialSource
from domain.source_links import key_of_url

VERSION = 1
PLATFORM = "facebook"
STATE_KEY = "sosyal_devir:facebook"
OPEN_STATUSES = ("aktif", "deneme")
ALL_STATUSES = ("aktif", "deneme", "aday", "pasif", "disari", "erisim_reddediyor")
MAX_LINES = 2000  # tur başına üst sınır (kalan bir sonraki tura)
MAX_TEXT = 8000
_FILE = re.compile(r"^facebook-\d{8}\.jsonl$")
_GROUP = re.compile(r"^[a-z0-9._-]{1,64}$")  # kaynaklar.json grup anahtarı (social_files._FB_GROUP ile aynı)
_POST_ID = re.compile(r"^(?:\d{5,25}|pfbid[A-Za-z0-9]{10,})$")  # facebook_browser._PID_OK ile aynı
_CDN = re.compile(r"^https://scontent[^/\s]*\.fbcdn\.net/\S+$", re.I)


@dataclass
class ImportReport:
    lines: int = 0  # okunan tam satır
    posts: int = 0  # geçerli gönderi (ayrıştırmaya giden)
    new_listings: int = 0  # yeni yazılan ilan
    not_listing: dict[str, int] = field(default_factory=dict)  # ilan sayılmayan gönderilerin nedeni (diagnose)
    skipped: dict[str, int] = field(default_factory=dict)  # bozuk_satir / surum / platform / alan / bilinmeyen_anahtar / kaynak_kapali
    sources: int = 0  # satırı olan kaynak
    pending: bool = False  # sınır yüzünden kalan satır var (sonraki turda)

    def summary(self) -> list[str]:
        if not self.lines:
            return ["sosyal devir: yeni satır yok"]
        out = [f"sosyal devir: {self.lines} satır, {self.posts} gönderi, {self.sources} kaynak, {self.new_listings} yeni ilan"
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
    group = key[3:] if isinstance(key, str) and key.startswith("fb:") else ""
    if not (_GROUP.match(group) and isinstance(pid, str) and _POST_ID.match(pid) and isinstance(text, str)):
        return "alan"
    photo = doc.get("foto_url")
    post = SocialPost(platform=PLATFORM, source_key=group, post_id=pid, url=f"https://www.facebook.com/groups/{group}/posts/{pid}/",
                      posted_at=_parse_time(doc.get("paylasim_utc")) or _parse_time(doc.get("goruldu_utc")), text=text[:MAX_TEXT],
                      image_url=photo if isinstance(photo, str) and _CDN.match(photo) else None)
    return key, post


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


def import_facebook(repo, devir_dir: Path | str, *, limit: int = MAX_LINES, log: Callable[[str], None] = lambda _: None) -> ImportReport:
    """Bir aktarım turu. repo: Repository (sources, get_state/set_state, upsert_listing, mark_checked, count_recent).
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
    for line in lines:
        got = parse_line(line)
        if isinstance(got, str):
            _count(rep.skipped, got)
            continue
        key, post = got
        row = sources.get(key)
        if row is None:
            _count(rep.skipped, "bilinmeyen_anahtar")
            continue
        if row["status"] not in OPEN_STATUSES:
            _count(rep.skipped, "kaynak_kapali")
            continue
        by_key.setdefault(key, []).append(post)
    for key, posts in by_key.items():
        row = sources[key]
        source = SocialSource(platform=PLATFORM, key=key[3:], url=row["url"], alias=key, source_id=row["id"],
                              default_steering=group_default_steering(row))
        st = ingest_facebook(posts, source, repo, reader=None)
        rep.posts += st.fetched
        rep.new_listings += st.new
        for why, n in st.reasons.items():
            _count(rep.not_listing, why, n)
        latest = max((p.posted_at for p in posts if p.posted_at), default=None)
        repo.mark_checked(row["id"], cursor=None, last_post_at=latest, listings_7d=repo.count_recent(row["id"]))
    rep.sources = len(by_key)
    repo.set_state(STATE_KEY, json.dumps({"dosya": new_cursor[0], "satir": new_cursor[1]}))
    for line in rep.summary():
        log(line)
    return rep
