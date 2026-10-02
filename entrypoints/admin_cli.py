"""Elle bakım komutları: python -m entrypoints.admin_cli reparse"""
import sys

from application.collect_instagram import listing_data
from domain.steering import steering_from_text
from infrastructure.collectors import kktcar
from infrastructure.collectors.instagram_apify import RawPost
from infrastructure.config import load_env, require
from infrastructure.db.repository import Repository


def reparse(all_rows: bool = False) -> None:
    """Parser'ı geliştirdikten sonra, çıkarımı henüz yapılmamış ilanları yeniden dener."""
    repo = Repository(require("DATABASE_URL"))
    rows = repo.conn.execute("SELECT id, raw_text FROM listings" + ("" if all_rows else " WHERE extraction_by IS NULL")).fetchall()
    ok = 0
    for r in rows:
        data, parsed = listing_data(RawPost("", "", None, r["raw_text"] or "", None, ""))
        if not parsed:
            continue
        for k in ("url", "posted_at", "raw_text", "photo_urls", "seller_handle"):
            data.pop(k, None)
        sets = ", ".join(f"{k}=%s" for k in data)
        repo.conn.execute(f"UPDATE listings SET {sets} WHERE id=%s", [*data.values(), r["id"]])
        ok += 1
    print(f"yeniden denenen={len(rows)} çözülen={ok} kalan={len(rows) - ok}")


def normalize_all(all_rows: bool = False) -> None:
    """Marka/model anahtarlarını üretir. --all: kurallar değişince hepsini yeniden hesaplar."""
    repo = Repository(require("DATABASE_URL"))
    where = "brand IS NOT NULL" + ("" if all_rows else " AND brand_norm IS NULL")
    rows = repo.conn.execute(f"SELECT id, brand, model FROM listings WHERE {where}").fetchall()
    for r in rows:
        k = repo.norm_keys(r["brand"], r["model"])
        repo.conn.execute("UPDATE listings SET brand_norm=%s, model_norm=%s WHERE id=%s", (k["brand_norm"], k["model_norm"], r["id"]))
    print(f"normalize edilen={len(rows)}")


def backtest() -> None:
    import json

    from application.backtest import run_backtest
    repo = Repository(require("DATABASE_URL"))
    print(json.dumps(run_backtest(repo.market_pool(days=120)), ensure_ascii=False, indent=1))


def weekly_report() -> None:
    from application.report import weekly_report_text
    print(weekly_report_text(Repository(require("DATABASE_URL"))))


def fill_steering() -> None:
    """Direksiyon yönü boş olan ilanlarda, ilan metninde açıkça yazıyorsa doldurur (tahmin yok)."""
    repo = Repository(require("DATABASE_URL"))
    rows = repo.conn.execute("SELECT id, raw_text FROM listings WHERE steering IS NULL AND raw_text IS NOT NULL").fetchall()
    n = 0
    for r in rows:
        st = steering_from_text(r["raw_text"])
        if st:
            repo.conn.execute("UPDATE listings SET steering=%s WHERE id=%s", (st, r["id"]))
            n += 1
    print(f"bakılan={len(rows)} doldurulan={n}")


def fill_data_as_of() -> None:
    """kktcar ilanlarının tarihini sitemap lastmod'dan doldurur (tek istek)."""
    repo = Repository(require("DATABASE_URL"))
    source = repo.sources("web", ("aktif",))
    kk = next(s for s in source if "kktcar.com" in s["url"])
    with kktcar.new_client() as client:
        entries = kktcar.fetch_sitemap(client)
    n = 0
    for e in entries:
        if e.lastmod:
            n += repo.conn.execute(
                "UPDATE listings SET data_as_of=%s WHERE source_id=%s AND source_item_id=%s AND data_as_of IS NULL",
                (e.lastmod, kk["id"], e.slug),
            ).rowcount
    print(f"sitemap={len(entries)} güncellenen={n}")


def fill_engine() -> None:
    """Aktif ve emsal penceresindeki kktcar ilanlarının motor hacmini sayfadan doldurur (arşiv sayfalarında alan yok)."""
    import time
    repo = Repository(require("DATABASE_URL"))
    rows = repo.conn.execute(
        "SELECT l.id, l.url, l.source_item_id FROM listings l JOIN sources s ON s.id=l.source_id "
        "WHERE s.url LIKE '%%kktcar.com%%' AND l.engine_l IS NULL AND l.is_active AND l.url IS NOT NULL").fetchall()
    n = 0
    with kktcar.new_client() as client:
        for r in rows:
            data = kktcar.fetch_detail(client, kktcar.SitemapEntry(r["url"], r["source_item_id"], None))
            if data and data.get("engine_l") is not None:
                repo.conn.execute("UPDATE listings SET engine_l=%s WHERE id=%s", (data["engine_l"], r["id"]))
                n += 1
            time.sleep(0.5)
    print(f"bakılan={len(rows)} doldurulan={n}")


def backfill_kaa() -> None:
    """kibrisarabaal geçmiş ilanlarını yerelde doldurur (robots.txt Crawl-delay 5 sn → ~3 saat). Kaldığı yerden devam eder.
    Kullanım: admin_cli kaa [en_fazla_ilan]"""
    from application.collect_kibrisarabaal import collect_kibrisarabaal
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 5000
    repo = Repository(require("DATABASE_URL"))
    from infrastructure.fx import frankfurter
    frankfurter.use_store(repo)
    source = next(s for s in repo.sources("web", ("aktif", "deneme")) if "kibrisarabaal.com" in s["url"])
    total = 0
    while total < limit:
        stats = collect_kibrisarabaal(repo, source, max_new=50)
        total += stats.fetched
        print(f"toplam={total} bu tur yeni={stats.new} başarısız={stats.failed} sitemap={stats.in_sitemap}", flush=True)
        if stats.fetched == 0 or (stats.new == 0 and stats.failed == stats.fetched):
            break


def shadow_tahmini() -> None:
    """🟠 kuru deneme (yalnızca okur): admin_cli shadow-tahmini --days 14"""
    from application.price_book_shadow import run_shadow
    days = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 14
    print(run_shadow(Repository(require("DATABASE_URL")), days))


if __name__ == "__main__":
    load_env()
    {
        "reparse": lambda: reparse("--all" in sys.argv),
        "normalize": lambda: normalize_all("--all" in sys.argv),
        "steering": fill_steering,
        "asof": fill_data_as_of,
        "engine": fill_engine,
        "kaa": backfill_kaa,
        "backtest": backtest,
        "report": weekly_report,
        "shadow-tahmini": shadow_tahmini,
    }[sys.argv[1]]()
