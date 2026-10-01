from datetime import datetime, timezone

from application import maintenance
from domain.quality import find_quarantine


def row(i, price, year=2015, km=80_000, brand="Toyota", model="vitz"):
    return dict(id=i, brand_norm=brand, model_norm=model, year=year, km=km, price_gbp=price)


PEERS = [row(f"p{i}", 8000 + i * 100) for i in range(10)]


def test_extreme_prices_are_quarantined_normal_are_not():
    rows = PEERS + [row("cheap", 2500), row("rich", 25000), row("ok", 8300)]
    q = find_quarantine(rows, 2026)
    assert q == {"cheap": "fiyat_ucuz_supheli", "rich": "fiyat_pahali_supheli"}


def test_small_groups_are_not_judged():
    assert find_quarantine([row(f"p{i}", 8000) for i in range(5)] + [row("x", 100)], 2026) == {}


def test_implausible_year_and_km():
    q = find_quarantine([row("y1", 5000, year=1960), row("y2", 5000, year=2031), row("k1", 5000, year=2015, km=900_000),
                         row("k2", 5000, year=2020, km=700_000), row("ok", 5000, year=2022, km=40_000)], 2026)
    assert q == {"y1": "yil_supheli", "y2": "yil_supheli", "k1": "km_supheli", "k2": "km_supheli"}


class Repo:
    def __init__(self, rows):
        self.rows, self.state, self.quarantine = rows, {}, None

    def listings_for_quality(self):
        return self.rows

    def set_quarantine(self, reasons):
        self.quarantine = reasons
        return len(reasons)

    def get_state(self, k, default=None):
        return self.state.get(k, default)

    def set_state(self, k, v):
        self.state[k] = v

    def alert_recent(self, key, hours):
        return key in self.state

    def mark_alerted(self, key):
        self.state[key] = "x"


def test_maintenance_runs_once_at_night_and_reports():
    repo = Repo(PEERS + [row("cheap", 2500), row("moto", 1, brand="Yamaha")])
    day = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
    night = datetime(2026, 10, 2, 1, tzinfo=timezone.utc)
    assert maintenance.run_maintenance(repo, day) is None            # gündüz çalışmaz
    res = maintenance.run_maintenance(repo, night)
    assert res["karantina"] == 1 and repo.quarantine == {"cheap": "fiyat_ucuz_supheli"} and res["kontrol"] == 11  # moto elendi
    assert maintenance.run_maintenance(repo, night) is None          # aynı gece ikinci kez çalışmaz
    assert "1 şüpheli ilan karantinada" in maintenance.summary_line(repo) and "fiyat çok ucuz" in maintenance.summary_line(repo)
