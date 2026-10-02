"""Değer tablosunun (price_book, price_curves, owner_sales) kayıt işleri. `conn`: Repository.conn (psycopg, dict_row)."""
from dataclasses import fields

from domain.price_book import BookRow, Curve, PriceBook

_ROW_COLS = [f.name for f in fields(BookRow)]
_CURVE_COLS = [f.name for f in fields(Curve)]


def _pack_years(d: dict) -> str:
    return ",".join(f"{y}:{n}" for y, n in sorted(d.items()))


def _unpack_years(v: str | None) -> dict[int, int]:
    return {int(y): int(n) for y, n in (x.split(":") for x in (v or "").split(",") if ":" in x)}


def _pairs(value: str | None) -> set[tuple[str, str]]:
    return {tuple(x.split("|", 1)) for x in (value or "").split(",") if "|" in x}


class PriceBookStore:
    def __init__(self, conn):
        self.conn = conn

    def _state(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM bot_state WHERE key=%s", (key,)).fetchone()
        return row["value"] if row else None

    def load_book(self) -> PriceBook:
        """Tablo + eğriler + 🟠'su kapalı modeller (bot_state: 'est_disabled' elle/geri bildirimle, 'est_unreliable' gece öz-kontrolüyle)."""
        book = PriceBook()
        for r in self.conn.execute(f"SELECT {','.join(_ROW_COLS)} FROM price_book").fetchall():
            row = BookRow(**{k: r[k] for k in _ROW_COLS})
            book.rows[(row.brand_norm, row.model_norm, row.variant, row.year)] = row
        for r in self.conn.execute(f"SELECT {','.join(_CURVE_COLS)} FROM price_curves").fetchall():
            c = Curve(**{k: (_unpack_years(r[k]) if k == "year_counts" else r[k]) for k in _CURVE_COLS})
            book.curves[(c.brand_norm, c.model_norm)] = c
        book.disabled_models = _pairs(self._state("est_disabled")) | _pairs(self._state("est_unreliable"))
        return book

    def replace_book(self, book: PriceBook) -> None:
        """Tabloyu ve eğrileri tek işlemde değiştirir (yarım kalırsa eski tablo durur)."""
        with self.conn.transaction():
            self.conn.execute("DELETE FROM price_book")
            self.conn.execute("DELETE FROM price_curves")
            with self.conn.cursor() as cur:
                if book.rows:
                    cur.executemany(
                        f"INSERT INTO price_book ({','.join(_ROW_COLS)}) VALUES ({','.join(['%s'] * len(_ROW_COLS))})",
                        [[getattr(r, c) for c in _ROW_COLS] for r in book.rows.values()])
                if book.curves:
                    cur.executemany(
                        f"INSERT INTO price_curves ({','.join(_CURVE_COLS)}) VALUES ({','.join(['%s'] * len(_CURVE_COLS))})",
                        [[_pack_years(c.year_counts) if k == "year_counts" else getattr(c, k) for k in _CURVE_COLS]
                         for c in book.curves.values()])

    def add_sale(self, sale: dict) -> None:
        keys = ["brand_norm", "model_norm", "brand", "model", "year", "km", "price_amount", "currency", "price_gbp"]
        self.conn.execute(f"INSERT INTO owner_sales ({','.join(keys)}) VALUES ({','.join(['%s'] * len(keys))})",
                          [sale.get(k) for k in keys])

    def sales(self, days: int = 365) -> list[dict]:
        return self.conn.execute(
            """SELECT brand_norm, model_norm, year, km, price_gbp::float8 AS price_gbp, created_at FROM owner_sales
               WHERE created_at > NOW() - make_interval(days => %s) ORDER BY created_at""", (days,)).fetchall()

    def stats(self) -> dict[str, int]:
        """Durum başına satır sayısı (+ 'curves')."""
        out = {r["status"]: r["n"] for r in self.conn.execute("SELECT status, count(*) AS n FROM price_book GROUP BY status").fetchall()}
        out["curves"] = self.conn.execute("SELECT count(*) AS n FROM price_curves").fetchone()["n"]
        return out
