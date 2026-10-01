from datetime import datetime, timedelta, timezone

from application.backtest import run_backtest

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def row(i, price):
    return dict(id=i, brand_norm="Toyota", model_norm="vitz", year=2015, km=80_000, steering="RHD",
                transmission="otomatik", fuel="benzin", price_gbp=price, currency_guess=False,
                first_seen_at=NOW - timedelta(days=5), is_active=True, duplicate_of=None)


def test_backtest_groups_by_confidence_and_flags_cheap_listing():
    pool = [row(i, 8000 + i * 50) for i in range(9)] + [row("cheap", 5000)]
    out = run_backtest(pool, now=NOW)
    assert list(out) == ["orta"]  # 9 emsal (10 ilan - kendisi)
    assert out["orta"]["ilan"] == 10 and out["orta"]["guclu_pct"] == 10.0
