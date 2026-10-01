from infrastructure.db.repository import Repository


class Conn:
    def __init__(self):
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split())[:40])


def repo():
    r = Repository.__new__(Repository)
    r.conn = Conn()
    return r


def new(amount, currency, gbp):
    return dict(price_amount=amount, currency=currency, price_gbp=gbp, price_raw="x", currency_guess=False, is_active=True)


OLD_TRY = dict(price_amount=600_000.0, currency="TRY", price_gbp=12_000.0)


def test_fx_move_is_not_a_price_change():
    r = repo()
    assert r.apply_refresh("id", OLD_TRY, new(600_000.0, "TRY", 11_800.0)) is None  # sadece kur oynadı
    assert not any(s.startswith("INSERT INTO listing_history") for s in r.conn.sql)


def test_real_price_drop_is_logged():
    r = repo()
    assert r.apply_refresh("id", OLD_TRY, new(540_000.0, "TRY", 10_800.0)) == "fiyat"
    assert any(s.startswith("INSERT INTO listing_history") for s in r.conn.sql)


def test_sold_page_deactivates():
    r = repo()
    assert r.apply_refresh("id", OLD_TRY, dict(is_active=False, urgency_signals=["satildi"])) == "pasif"
