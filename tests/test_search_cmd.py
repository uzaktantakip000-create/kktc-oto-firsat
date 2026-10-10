"""/bul (application/search_cmd, 10.10.2026): serbest arama metni → filtre; sonuçlar taranan ilanla aynı kararla, piyasaya göre en ucuzdan.
Sahte veritabanı; ağ yok."""
from datetime import timedelta

import pytest

from application import search_cmd
from domain.price_book import BookRow, PriceBook
from domain.settings import Settings
from tests.test_evaluate import NOW, POOL, car


@pytest.fixture(autouse=True)
def no_fx(monkeypatch):
    monkeypatch.setattr(search_cmd, "gbp_rate", lambda c: {"TRY": 0.02, "USD": 0.8, "EUR": 0.85}.get(c, 1.0))


@pytest.mark.parametrize("text,expect", [
    ("fit 2015-2018 7000", dict(words=["fit"], year_min=2015, year_max=2018, price_max=7000)),
    ("bmw 3 2012 sonrası 10bin", dict(words=["bmw", "3"], year_min=2012, price_max=10000)),
    ("corolla 150bin km otomatik", dict(words=["corolla"], km_max=150000, transmission="otomatik")),
    ("Vitz 2015 £6.500", dict(words=["vitz"], year_min=2015, year_max=2015, price_max=6500)),
    ("mini 6000 altı", dict(words=["mini"], price_max=6000)),
    ("golf 2010'dan önce", dict(words=["golf"], year_max=2010)),
    ("juke 5000-8000", dict(words=["juke"], price_min=5000, price_max=8000)),
    ("note 300.000 TL", dict(words=["note"], price_max=6000)),
    ("vitz 2000 stg", dict(words=["vitz"], price_max=2000)),  # para işaretli: yıl değil fiyat
    ("c 180 2014 manuel 90.000 km", dict(words=["c", "180"], year_min=2014, year_max=2014, km_max=90000, transmission="manuel")),
])
def test_free_text_becomes_filters(text, expect):
    q = search_cmd.parse_query(text, 2026)
    got = {k: getattr(q, k) for k in expect}
    assert got == expect, (text, q)


def row(brand, model, year=2015, n=40):
    return BookRow(brand_norm=brand, model_norm=model, variant="", year=year, value_gbp=8000, low_gbp=7000, high_gbp=9000, n=n, sellers=10,
                   status="oturmus", method="A", ref_km=80000)


BOOK = PriceBook({(r.brand_norm, r.model_norm, "", r.year): r for r in (row("Toyota", "vitz"), row("Toyota", "corolla"), row("BMW", "3"),
                                                                       row("Honda", "fit"))})


def listing(i, price, **kw):
    return car(i, price, brand="Toyota", model="Vitz", source_name="KibrisArabaAl", platform="web", url=f"https://kibrisarabaal.com/ilan/{i}-x/",
               posted_at=NOW - timedelta(days=2), total=None) | kw


class Repo:
    def __init__(self, rows):
        self.rows, self.asked, self.state = rows, [], {}

    def search_active(self, *args):
        self.asked.append(args)
        return self.rows

    def market_pool(self, days, keys=None):
        return POOL

    def get_state(self, k, default=None):
        return self.state.get(k, default)

    def set_state(self, k, v):
        self.state[k] = v


@pytest.fixture(autouse=True)
def book(monkeypatch):
    monkeypatch.setattr(search_cmd, "load_book", lambda repo: BOOK)


def test_results_are_sorted_cheapest_against_market_and_marked_with_the_same_decision():
    repo = Repo([listing("a", 9000), listing("b", 5000), listing("c", 7300), listing("d", 1)])
    out = search_cmd.search(repo, "vitz 2015 10000", Settings(), NOW)
    lines = out.split("\n")
    assert lines[0] == "🔎 Toyota Vitz · 2015 · en çok £10.000"
    assert repo.asked == [("Toyota", "vitz", 2015, 2015, None, 10000, None, None, search_cmd.CANDIDATES)]
    assert "3 aktif ilan" in lines[1]  # £1 yer tutucu fiyat elendi
    assert lines[2].startswith("🟢 2015 Vitz · 80.000 km · £5.000 · piyasanın %43 altında · KibrisArabaAl")
    assert lines[3] == "   https://kibrisarabaal.com/ilan/b-x/"
    assert lines[4].startswith("🟡") or lines[4].startswith("➖")
    assert lines[6].startswith("➖ 2015 Vitz · 80.000 km · £9.000 · piyasanın %3 üstünde")
    assert lines[-1].startswith("Bir ilanın tam dosyası için")


def test_brand_alone_searches_every_model_and_unknown_car_says_so():
    repo = Repo([])
    assert "Şu an taranan sitelerde bu aramaya uyan aktif ilan yok" in search_cmd.search(repo, "bmw", Settings(), NOW)
    assert repo.asked[-1][:2] == ("BMW", None)
    assert "diye bir araç bulamadım" in search_cmd.search(repo, "zzzqx 2015", Settings(), NOW)
    assert search_cmd.search(repo, "2015 7000", Settings(), NOW) == search_cmd.HELP


def test_old_ad_is_flagged_and_no_market_is_listed_last():
    old = listing("o", 6000, posted_at=NOW - timedelta(days=200))
    rare = listing("r", 3000, model_norm="nadir", model="Nadir")
    out = search_cmd.search(Repo([rare, old]), "vitz", Settings(), NOW)
    lines = [l for l in out.split("\n") if not l.startswith("   ")]
    assert "⚠️" in lines[2] and "tarihli ilan" in lines[2]
    assert lines[3].startswith("❔ 2015 Nadir") and "emsal az" in lines[3]


def test_handle_uses_the_shared_quota_and_empty_args_show_help(monkeypatch):
    import application.settings_store as st
    monkeypatch.setattr(st, "load_settings", lambda repo: Settings())
    repo = Repo([listing("b", 5000)])
    assert search_cmd.handle(repo, "  ") == search_cmd.HELP and repo.state == {}
    assert "🟢" in search_cmd.handle(repo, "vitz", now=NOW)
    repo.state[f"adcheck:{NOW:%Y-%m-%d}:9"] = str(search_cmd.MAX_PER_DAY_SUBSCRIBER)
    assert "sınırına ulaşıldı" in search_cmd.handle(repo, "vitz", now=NOW, subscriber="9")


def test_foreign_currency_listing_shows_the_ad_price_and_suspicious_km_is_marked():
    tl = listing("tl", 5000, currency="TRY", price_amount=250_000, km=195, year=2012)
    out = search_cmd.search(Repo([tl]), "vitz", Settings(), NOW)
    assert "£5.000 (ilanda 250.000 TL)" in out and "195 km (şüpheli)" in out


def test_unreadable_source_gets_one_warning_line():
    rows = [listing("a", 5000, source_checked_at=NOW - timedelta(days=3)), listing("b", 6000, source_checked_at=NOW - timedelta(days=3))]
    out = search_cmd.search(Repo(rows), "vitz", Settings(), NOW)
    assert out.count("okunamıyor") == 1 and "ℹ️ KibrisArabaAl 3 gündür okunamıyor: oradaki ilanlar satılmış olabilir" in out


def test_negotiable_line_says_how_low_the_price_must_go():
    out = search_cmd.search(Repo([listing("y", 7000)]), "vitz", Settings(), NOW)
    line = next(l for l in out.split("\n") if l.startswith("🟡"))
    assert "· %20 kâr için ≤£6.600 ·" in line  # piyasa ortası £8.700 → hızlı satış £8.265; (8.265 − 300) / 1,2 = 6.637 → £50'ye aşağı
