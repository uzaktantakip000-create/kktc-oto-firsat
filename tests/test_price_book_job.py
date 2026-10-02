import json
from contextlib import contextmanager
from datetime import datetime, timezone

from application import price_book_job as job
from domain.price_book import BookRow, Curve, PriceBook
from domain.settings import Settings
from infrastructure.db.price_book_store import PriceBookStore
from tests.test_price_book import NOW, REF, car, synth


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeConn:
    def __init__(self, tables=None):
        self.tables, self.log = tables or {}, []

    def execute(self, sql, params=None):
        self.log.append(sql)
        if "FROM listings" in sql:
            return Result([{"id": i, "model": None} for i in params[0]])
        for name, rows in self.tables.items():
            if f"FROM {name}" in sql:
                return Result(rows)
        return Result([])

    @contextmanager
    def transaction(self):
        self.log.append("BEGIN")
        yield
        self.log.append("COMMIT")

    @contextmanager
    def cursor(self):
        conn = self

        class Cur:
            def executemany(self, sql, seq):
                conn.log.append((sql.split("(")[0].strip(), len(list(seq))))

        yield Cur()


class FakeRepo:
    def __init__(self, pool=(), state=None):
        self.pool, self.state, self.conn, self.alerted = list(pool), state or {}, FakeConn(), []

    def get_state(self, k, default=None):
        return self.state.get(k, default)

    def set_state(self, k, v):
        self.state[k] = v

    def alert_recent(self, key, hours):
        return key in self.alerted

    def mark_alerted(self, key):
        self.alerted.append(key)

    def market_pool(self, days=120):
        return self.pool


def test_summary_line():
    repo = FakeRepo(state={"pb:stats": json.dumps({"rows": 420, "settled": 310, "suspect": 6, "thin": 104, "error": 0.12, "unreliable": 0})})
    assert job.summary_line(repo) == "📘 Değer tablosu: 420 model-yıl, 310 oturmuş, 6 şüpheli · isabet %88"
    repo.state["pb:stats"] = json.dumps({"rows": 5, "settled": 1, "suspect": 0, "thin": 4, "error": None, "unreliable": 2})
    assert job.summary_line(repo) == "📘 Değer tablosu: 5 model-yıl, 1 oturmuş, 0 şüpheli · 2 model güvenilmez"
    assert job.summary_line(FakeRepo()) is None and job.summary_line(FakeRepo(state={"pb:stats": "bozuk"})) is None


def test_run_guard_and_run(monkeypatch):
    saved = {}

    class Store(PriceBookStore):
        def load_book(self):
            return PriceBook()

        def sales(self, days=365):
            return []

        def replace_book(self, book):
            saved["book"] = book

    monkeypatch.setattr(job, "PriceBookStore", Store)
    repo = FakeRepo(synth(n=60))
    assert job.run_price_book(repo, datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)) is None  # gündüz: çalışmaz
    assert saved == {}
    res = job.run_price_book(repo, NOW)  # gece 03:00 UTC
    assert res["curves"] >= 1 and res["rows"] >= 9 and res["settled"] >= 1 and "book" in saved
    assert repo.state["est_unreliable"] == "" and json.loads(repo.state["pb:stats"])["rows"] == res["rows"]
    assert repo.alerted == ["pb:last"]
    saved.clear()
    assert job.run_price_book(repo, NOW) is None  # 20 saat içinde ikinci kez çalışmaz
    assert job.run_price_book(repo, NOW, force=True) is not None and "book" in saved


def test_coverage_from_counts_a_b_and_total():
    s = Settings()
    book = PriceBook()
    book.rows[("Toyota", "vitz", "", 2015)] = BookRow("Toyota", "vitz", "", 2015, 8000, 7000, 9000, 100_000, 10, 5, "A", "oturmus")
    book.curves[("Toyota", "fit")] = Curve("Toyota", "fit", 9.0, -0.09, -0.03, 0.15, 40, 9, 30, 2010, 2020, 200_000, REF, 10.0, 10.0)
    pool = [car(1, 2015, 90_000, 8000),  # A
            {**car(2, 2016, 90_000, 8000, model="fit")},  # B
            car(3, 2012, 90_000, 8000, model="rare"),  # yok
            {**car(4, 2015, 90_000, 8000), "steering": "LHD"},  # sol direksiyon: A değil
            {**car(5, 2015, 90_000, 8000), "is_active": False},  # pasif: sayılmaz
            {**car(6, 2015, 90_000, 8000), "duplicate_of": "x"}]  # mükerrer: sayılmaz
    assert job.coverage_from(pool, book, s, NOW) == (1, 1, 4)


def test_coverage_none_without_book(monkeypatch):
    class Empty(PriceBookStore):
        def load_book(self):
            return PriceBook()

    monkeypatch.setattr(job, "PriceBookStore", Empty)
    assert job.coverage(FakeRepo()) is None and job.coverage_pct(FakeRepo()) is None


# --- store ---
def test_store_load_book_and_disabled_models():
    row = dict(brand_norm="Toyota", model_norm="vitz", variant="", year=2015, value_gbp=8000.0, low_gbp=7000.0, high_gbp=9000.0, ref_km=None,
               n=10, sellers=5, method="A", status="oturmus", cand_value=None, cand_nights=0, sales_n=0, sales_median_gbp=None)
    cur = dict(brand_norm="Toyota", model_norm="vitz", a=9.0, b_age=-0.09, b_km=-0.03, sigma=0.15, n=40, years=9, sellers=30, min_year=2010,
               max_year=2020, max_km=200_000, ref_year=2026, mean_age=10.0, mean_km10=10.0, km_per_year=12_000.0, year_counts="2015:5,2016:3")
    conn = FakeConn({"price_book": [row], "price_curves": [cur]})
    original = conn.execute

    def execute(sql, params=None):
        if "FROM bot_state" in sql:
            return Result([{"value": {"est_disabled": "Honda|fit,Opel|astra", "est_unreliable": "Opel|corsa"}[params[0]]}])
        return original(sql, params)

    conn.execute = execute
    book = PriceBookStore(conn).load_book()
    assert book.row("Toyota", "vitz", 2015).value_gbp == 8000 and book.curve("Toyota", "vitz").km_per_year == 12_000
    assert book.curve("Toyota", "vitz").year_counts == {2015: 5, 2016: 3}
    assert book.disabled_models == {("Honda", "fit"), ("Opel", "astra"), ("Opel", "corsa")}


def test_store_replace_book_is_one_transaction():
    conn = FakeConn()
    book = PriceBook()
    book.rows[("Toyota", "vitz", "", 2015)] = BookRow("Toyota", "vitz", "", 2015, 8000, 7000, 9000, None, 10, 5, "A", "oturmus")
    PriceBookStore(conn).replace_book(book)
    assert conn.log[0] == "BEGIN" and conn.log[-1] == "COMMIT"
    assert ("INSERT INTO price_book", 1) in conn.log and not any(isinstance(x, tuple) and "curves" in x[0] for x in conn.log)
