"""⚡ ACİL SATICI (application/urgent, 11.10.2026; sahip: "insanlar acil satıyor… en erken bulan kazanır"). Sahte değerlendirmeler; ağ yok;
ilanlar uydurma."""
import json
from datetime import timedelta

import pytest

from application import urgent
from application.evaluate import Evaluated
from domain.comparables import Market
from domain.profit import Confidence, ProfitResult, Tier
from domain.red_flags import urgency_signals
from domain.settings import Settings
from tests.test_evaluate import NOW, car


def ev(i="a", price=7600, median=9000, n=12, text="2015 Toyota Vitz İhtiyaçtan Acil", tier=Tier.NONE, blocking=(), **kw):
    listing = car(i, price, raw_text=text, url=f"https://kibrisarabaal.com/ilan/{i}/", source_name="KibrisArabaAl", location="Girne",
                  first_seen_at=NOW - timedelta(minutes=20), posted_at=NOW - timedelta(hours=1), seller_type="bireysel", platform="web",
                  currency="GBP", price_amount=price) | kw
    market = Market(n, median, median * 0.8, median * 1.2, 1, 0.0)
    profit = ProfitResult(median * 0.95, 0, 0, Confidence.MEDIUM, tier)
    return Evaluated(listing, market, profit, list(blocking), [], urgency_signals(text))


class Repo:
    def __init__(self):
        self.state = {}

    def get_state(self, k, default=None):
        return self.state.get(k, default)

    def set_state(self, k, v):
        self.state[k] = v


@pytest.mark.parametrize("text,why", [("2015 Toyota Vitz İhtiyaçtan Acil", "acil / ihtiyaçtan"),
                                      ("Leaving the island, quick sale", "acil / ihtiyaçtan, ayrılıyor / mezun"),
                                      ("adadan ayrılıyorum", "ayrılıyor / mezun"), ("nakit ihtiyacından satılık", "nakit ihtiyacı / borç")])
def test_urgent_individual_seller_ten_percent_under_market_gets_an_instant_message(text, why):
    (picked,) = urgent.candidates([ev(text=text)], Settings(), NOW)
    msg = urgent.message(picked, Settings(), NOW)
    assert msg.startswith("⚡ ACİL SATICI · piyasanın %16 altında\n2015 Toyota Vitz · 80.000 km · £7.600 · KibrisArabaAl · Girne")
    assert f"🗣 İlanda satıcının acelesi yazıyor: {why}" in msg
    assert "📊 Piyasa ortası £9.000 (12 emsal) · %20 kâr için en çok £6." in msg and "pazarlıkla" in msg
    assert "🕐 İlk görüldü: 20 dk önce" in msg and msg.endswith("https://kibrisarabaal.com/ilan/a/")


@pytest.mark.parametrize("kw", [
    dict(text="2015 Toyota Vitz temiz, pazarlık payı var"),  # pazarlık acele değildir
    dict(price=8200),  # piyasanın %10 altında değil
    dict(n=4),  # emsal az
    dict(price=4000),  # piyasanın yarısından ucuz: yanlış fiyat
    dict(seller_type="galeri"),
    dict(shadow=True),  # gölge kaynak
    dict(tier=Tier.STRONG),  # 🟢 zaten 🟢 olarak gidiyor
    dict(blocking=["hasarlı"]),
    dict(first_seen_at=NOW - timedelta(hours=7)),  # taze değil
    dict(brand_norm="Nissan"),  # /istemiyorum
])
def test_not_urgent_not_cheap_dealer_shadow_green_trap_or_old_gets_nothing(kw):
    s = Settings(blocked_brands=["Nissan"])
    assert urgent.candidates([ev(**kw)], s, NOW) == []


def test_cheapest_first_once_per_listing_and_daily_cap(monkeypatch):
    repo, sent = Repo(), []
    evs = [ev("a", price=8000), ev("b", price=7000), ev("c", price=7500)]
    assert urgent.run(repo, evs, sent.append, Settings(), NOW) == 3
    assert [m.split("\n")[0] for m in sent] == ["⚡ ACİL SATICI · piyasanın %22 altında", "⚡ ACİL SATICI · piyasanın %17 altında",
                                                "⚡ ACİL SATICI · piyasanın %11 altında"]
    assert urgent.run(repo, evs, sent.append, Settings(), NOW) == 0 and len(sent) == 3  # ilan başına bir kez
    monkeypatch.setattr(urgent, "MAX_PER_DAY", 4)
    assert urgent.run(repo, [ev("d"), ev("e")], sent.append, Settings(), NOW) == 1  # günde en çok 4
    assert json.loads(repo.state[urgent.SENT_KEY]) == ["b", "c", "a", "d"]


def test_tl_priced_listing_shows_the_original_price():
    msg = urgent.message(ev(currency="TRY", price_amount=480000), Settings(), NOW)
    assert "£7.600 (ilanda 480.000 TRY)" in msg


@pytest.mark.parametrize("text,signals", [("İhtiyaçtan Acil, Masrafsız, Servis Bakımlı", ["acil"]), ("borçsuz temiz araç", []),
                                          ("Türkiye'ye dönüş nedeniyle satılık", ["mezun/ayrılıyor"]),
                                          ("borçtan dolayı zararına", ["nakit/borç"]), ("servis bakımlı", []),
                                          ("katalitik dönüştürücü yeni", []), ("kesin dönüş, acil", ["acil", "mezun/ayrılıyor"])])
def test_urgency_reader(text, signals):
    assert urgency_signals(text) == signals
