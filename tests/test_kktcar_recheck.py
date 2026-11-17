"""17.11.2026: KKTCar'da süresi dolup "İlan arşivi" olan (nedeni belirsiz kapanan) ilan, satıcı yenileyince aynı adresle yeniden yayına
girer. Kapalı ilanlar günde bir yeniden okunur; sayfa yayında diyorsa ilan geri açılır ve fiyatı güncellenir."""
import httpx

from application import collect_kktcar as kk
from infrastructure.collectors import kktcar

SOURCE = {"id": "s1", "name": "KKTCar"}


class Repo:
    def __init__(self, rows):
        self.rows, self.touched, self.reopened, self.applied, self.asked = rows, [], [], [], []

    def stale_archived(self, source_id, days, hours, limit):
        self.asked.append((days, hours, limit))
        return self.rows[:limit]

    def touch(self, i):
        self.touched.append(i)

    def reopen(self, i):
        self.reopened.append(i)

    def apply_refresh(self, i, old, data):
        self.applied.append((i, data))
        return "fiyat" if data["price_amount"] != old["price_amount"] else None


def rows(n):
    return [dict(id=f"r{i}", url=f"https://x/{i}", source_item_id=f"s{i}", price_amount=23500.0, price_gbp=23500.0, currency="GBP")
            for i in range(n)]


def run(monkeypatch, repo, detail, now=lambda: 0.0):
    monkeypatch.setattr(kk, "gbp_rate", lambda currency: 1.0)
    monkeypatch.setattr(kktcar, "polite_sleep", lambda: None)
    monkeypatch.setattr(kktcar, "fetch_detail", lambda client, entry: detail(entry))
    stats = kk.KktcarStats()
    kk.recheck_archived(repo, SOURCE, None, stats, clock=now)
    return stats


def test_live_page_reopens_the_listing_and_records_the_price_drop(monkeypatch):
    repo = Repo(rows(1))
    stats = run(monkeypatch, repo, lambda e: {"price_amount": 21750.0, "currency": "GBP", "is_active": None, "archive_dated": None})
    assert repo.reopened == ["r0"] and stats.reopened == 1
    assert stats.price_changes == 1 and repo.applied[0][1]["price_gbp"] == 21750.0
    assert "archive_dated" not in repo.applied[0][1]
    assert repo.touched == []


def test_still_archived_or_unreadable_pages_only_move_back_in_the_queue(monkeypatch):
    pages = {"s0": {"is_active": False, "price_amount": 1.0, "currency": "GBP"}, "s1": None}

    def detail(e):
        if e.slug == "s2":
            raise httpx.ConnectTimeout("yavaş")
        return pages[e.slug]

    repo = Repo(rows(3))
    stats = run(monkeypatch, repo, detail)
    assert repo.reopened == [] and repo.applied == [] and stats.reopened == 0
    assert repo.touched == ["r0", "r1", "r2"]
    assert stats.failed == 0 and stats.refresh_failed == 0  # okuma oranı alarmına sayılmaz


def test_at_most_five_per_round_once_a_day_within_45_days(monkeypatch):
    repo = Repo(rows(12))
    run(monkeypatch, repo, lambda e: None)
    assert repo.asked == [(45, 24, 5)] and len(repo.touched) == 5


def test_time_budget_leaves_the_rest_for_the_next_round(monkeypatch):
    t = {"now": 0.0}

    def detail(e):
        t["now"] += 20  # yavaş site: 20 sn/ilan, bütçe 30 sn
        return None

    repo = Repo(rows(5))
    stats = run(monkeypatch, repo, detail, now=lambda: t["now"])
    assert len(repo.touched) == 2 and stats.time_limited


def test_current_live_page_template_is_read_as_active():
    """Gerçek sayfa (2020 BMW 218d, 17.11.2026, sitede yayında; bizde 19.10'da kapanmıştı): okuyucu onu yayında okur."""
    from pathlib import Path
    html = (Path(__file__).parent / "fixtures" / "kktcar_detail_live_reopened.html").read_text(encoding="utf-8")
    d = kktcar.parse_detail(html)
    assert d is not None and d.get("is_active") is not False
    assert d["price_amount"] == 21750.0 and d["currency"] == "GBP" and d["year"] == 2020
