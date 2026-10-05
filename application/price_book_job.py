"""Değer tablosunu gece kurar (gece bakımından sonra): havuzdan satır + eğri, öz-kontrol, sonuç bot_state'e.
Sabah raporunda tek satır: "📘 Değer tablosu: N model-yıl, M oturmuş, K şüpheli · isabet %X"."""
import json
from datetime import datetime, timedelta, timezone

from domain.normalize import is_car_brand
from domain.price_book import STATUS_SETTLED, STATUS_SUSPECT, STATUS_THIN, PriceBook, build_book, estimate_from_book, self_check
from domain.settings import Settings
from infrastructure.db.price_book_store import PriceBookStore
from infrastructure.db.repository import Repository

NIGHT_HOURS_UTC = range(0, 4)  # gece bakımıyla aynı UTC penceresi (bilerek UTC): KKTC yazın 03:00–07:00, kışın 02:00–06:00
STATE_LAST, STATE_STATS, STATE_UNRELIABLE = "pb:last", "pb:stats", "est_unreliable"
POOL_DAYS = 120
SELF_CHECK_DAYS = 30
COVERAGE_DAYS = 60


def load_pool(repo: Repository, with_model: bool = True) -> list[dict]:
    """Emsal havuzu (araç markaları). market_pool ham model metnini taşımaz; varyant (320, 180...) için eklenir."""
    pool = [r for r in repo.market_pool(days=POOL_DAYS) if is_car_brand(r.get("brand_norm"))]
    if with_model and pool:
        models = {r["id"]: r["model"] for r in repo.conn.execute(
            "SELECT id, model FROM listings WHERE id = ANY(%s)", ([r["id"] for r in pool],)).fetchall()}
        for r in pool:
            r["model"] = models.get(r["id"])
    return pool


def _stats(book: PriceBook, error: float | None, unreliable: int) -> dict:
    rows = [r for r in book.rows.values() if r.variant == ""]
    return {"rows": len(rows), "settled": sum(r.status == STATUS_SETTLED for r in rows),
            "suspect": sum(r.status == STATUS_SUSPECT for r in rows), "thin": sum(r.status == STATUS_THIN for r in rows),
            "curves": sum(k[1] != "*" for k in book.curves), "error": error, "unreliable": unreliable}


def run_price_book(repo: Repository, now: datetime | None = None, force: bool = False) -> dict | None:
    now = now or datetime.now(timezone.utc)
    if not force and (now.hour not in NIGHT_HOURS_UTC or repo.alert_recent(STATE_LAST, 20)):
        return None
    s, store = Settings(), PriceBookStore(repo.conn)
    pool = load_pool(repo)
    book = build_book(pool, store.sales(), now, store.load_book(), s)
    recent = [r for r in pool if r["first_seen_at"] > now - timedelta(days=SELF_CHECK_DAYS)]
    error, unreliable = self_check(book, recent, s)
    store.replace_book(book)
    repo.set_state(STATE_UNRELIABLE, ",".join(sorted(f"{b}|{m}" for b, m in unreliable)))
    result = {"at": now.isoformat(), **_stats(book, error, len(unreliable))}
    repo.set_state(STATE_STATS, json.dumps(result))
    repo.mark_alerted(STATE_LAST)
    return result


def summary_line(repo: Repository) -> str | None:
    """Sabah durum mesajı için tek satır (en son tablo kurulumu)."""
    try:
        r = json.loads(repo.get_state(STATE_STATS) or "")
    except ValueError:
        return None
    if not r:
        return None
    line = f"📘 Değer tablosu: {r['rows']} model-yıl, {r['settled']} oturmuş, {r['suspect']} şüpheli"
    if r.get("error") is not None:
        line += f" · isabet %{max(0, round(100 - r['error'] * 100))}"
    if r.get("unreliable"):
        line += f" · {r['unreliable']} model güvenilmez"
    return line


def coverage_from(pool: list[dict], book: PriceBook, s: Settings, now: datetime) -> tuple[int, int, int]:
    """(benzer ilanla fiyatı bilinen A, değer eğrisiyle bilinen B, toplam): son 60 günün aktif, mükerrer olmayan ilanları."""
    a = b = total = 0
    for r in pool:
        ref = r.get("ref_date") or r.get("first_seen_at")
        if not r.get("is_active", True) or r.get("duplicate_of") or (ref and ref < now - timedelta(days=COVERAGE_DAYS)):
            continue
        total += 1
        row = book.row(r["brand_norm"], r["model_norm"], r["year"]) if r.get("year") is not None else None
        if row and row.method == "A" and r.get("steering") != "LHD" and row.n >= s.min_comparables_alert:
            a += 1
        elif estimate_from_book(r, book, s):
            b += 1
    return a, b, total


def coverage(repo: Repository, now: datetime | None = None) -> tuple[int, int, int] | None:
    """Tablo yoksa ya da okunamazsa None (çağıran eski hesaba döner)."""
    now = now or datetime.now(timezone.utc)
    s = Settings()
    book = PriceBookStore(repo.conn).load_book()
    if not book.rows:
        return None
    return coverage_from(load_pool(repo, with_model=False), book, s, now)


def coverage_pct(repo: Repository, now: datetime | None = None) -> float | None:
    c = coverage(repo, now)
    return (c[0] + c[1]) * 100 / c[2] if c and c[2] else None
