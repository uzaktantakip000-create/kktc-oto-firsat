"""📞 FİYAT SOR (application/ask_price, 11.10.2026): fiyatı yazmayan Facebook araç gönderisi → fiyatsız ilan kaydı → sahibe kart (piyasa ortası
ve "%20 kâr için en çok"). Sahte veritabanı; ağ yok; metinler ve kimlikler uydurma."""
import json
from datetime import timedelta

import pytest

from application import ask_price
from application import social_import as si
from domain.freetext_parser import parse_freetext
from domain.profit import buy_ceiling
from domain.settings import Settings
from tests.test_evaluate import NOW, POOL, car
from tests.test_social_import import NOW as SI_NOW, FakeRepo, _file, _line, _rows

NO_PRICE = "2015 Toyota Vitz otomatik 80.000 km klimalı, irtibat 0533 000 00 01"


def test_parser_reads_brand_and_year_without_price_only_when_asked():
    assert parse_freetext(NO_PRICE) is None  # eski davranış aynı
    p = parse_freetext(NO_PRICE, allow_no_price=True)
    assert (p.brand, p.year, p.km, p.price_amount, p.price_raw, p.transmission) == ("Toyota", 2015, 80_000, None, None, "otomatik")
    priced = parse_freetext("2015 Toyota Vitz 80.000 km Fiyat 5.000 STG", allow_no_price=True)
    assert priced.price_amount == 5000 and priced.price_raw  # fiyatlı metinde sonuç değişmez
    assert parse_freetext("Satılık temiz araç, irtibat", allow_no_price=True) is None  # marka yok: yine yok


def test_handoff_import_stores_a_priceless_car_post_but_not_a_non_car(tmp_path):
    repo = FakeRepo(_rows())
    _file(tmp_path, SI_NOW, _line("1000000001", text=NO_PRICE), _line("1000000002", text="Kiralık daire 2+1"))
    rep = si.import_facebook(repo, tmp_path)
    assert rep.new_listings == 1 and rep.priceless == 1 and "(1 fiyatsız)" in rep.summary()[0]
    (data,) = repo.listings.values()
    assert data["price_gbp"] is None and data["urgency_signals"] == ["fiyatsiz"] and data["brand"] == "Toyota" and data["year"] == 2015


class Repo:
    def __init__(self, rows, pool=POOL):
        self.rows, self.pool, self.state, self.keys = list(rows), pool, {}, None

    def priceless_recent(self, hours, platform="facebook"):
        return [r for r in self.rows if r["first_seen_at"] > NOW - timedelta(hours=hours)]

    def market_pool(self, days=120, keys=None):
        self.keys = keys
        return self.pool

    def get_state(self, k, default=None):
        return self.state.get(k, default)

    def set_state(self, k, v):
        self.state[k] = v


def post(i, **kw):
    return car(i, None, price_gbp=None, url=f"https://www.facebook.com/groups/1/posts/{i}/", seller_type=None, seller_phone=None,
               posted_at=NOW - timedelta(minutes=30), first_seen_at=NOW - timedelta(minutes=10), raw_text=NO_PRICE, location=None) | kw


def test_card_gives_the_market_middle_and_the_most_to_pay_for_twenty_percent():
    ((l, median, n, offer),) = ask_price.pick([post("a")], POOL, Settings(), NOW)
    assert n == 8 and median == 8700 and offer == buy_ceiling(8700 * 0.95, Settings()) // 50 * 50
    text = ask_price.card(l, median, n, offer)
    assert text.startswith("2015 Toyota Vitz · otomatik · 80.000 km")
    assert f"piyasa ortası £8.700 (8 emsal) → %20 kâr için en çok £{offer:,.0f}".replace(",", ".") in text
    assert "facebook.com/groups/1/posts/a/" in text and "0533" not in text


@pytest.mark.parametrize("kw", [
    dict(seller_type="galeri"),
    dict(raw_text="AFANDI MOTORS 2015 Toyota Vitz taksit imkanı"),
    dict(raw_text="Car dealership 2015 Toyota Vitz"),
    dict(seller_phone="905330000009"),
    dict(brand_norm="Nissan"),  # /istemiyorum
    dict(posted_at=NOW - timedelta(hours=13)),
    dict(model_norm="nadir", model="Nadir"),  # emsal yok
])
def test_dealers_blocked_old_and_unvaluable_posts_get_no_card(kw):
    s = Settings(blocked_phones=["905330000009"], blocked_brands=["Nissan"])
    assert ask_price.pick([post("a", **kw)], POOL, s, NOW) == []


def test_few_comparables_get_no_card():
    assert ask_price.pick([post("a")], POOL[:7], Settings(), NOW) == []


def test_turn_sends_one_message_once_and_respects_the_daily_cap(monkeypatch):
    repo = Repo([post("a"), post("b", km=None, seller_type="bireysel"), post("eski", first_seen_at=NOW - timedelta(hours=4))])
    sent = []
    assert ask_price.run(repo, sent.append, Settings(), NOW) == 2
    (msg,) = sent
    assert msg.startswith("📞 FİYAT SOR · Facebook'ta fiyatı yazmayan 2 yeni ilan") and "km yazmıyor · sahibinden" in msg
    assert repo.keys == [("Toyota", "vitz")] and "eski" not in msg
    assert ask_price.run(repo, sent.append, Settings(), NOW) == 0 and len(sent) == 1  # ikinci turda aynı ilan yok
    monkeypatch.setattr(ask_price, "MAX_PER_DAY", 3)
    repo.rows += [post(f"x{i}", km=90_000 + i * 1000) for i in range(4)]  # farklı araçlar (aynı metin + farklı km)
    assert ask_price.run(repo, sent.append, Settings(), NOW) == 1  # günde en çok 3: 2 gitmişti
    assert ask_price.run(Repo([]), sent.append, Settings(), NOW) == 0


def test_processed_ids_are_written_before_sending():
    repo = Repo([post("a")])

    def boom(text):
        raise RuntimeError("telegram")

    with pytest.raises(RuntimeError):
        ask_price.run(repo, boom, Settings(), NOW)
    assert json.loads(repo.state[ask_price.SENT_KEY]) == ["a"]  # tur çökse de ikinci kez gitmez


def test_damaged_or_unplated_car_gets_no_card():
    assert ask_price.pick([post("a", raw_text="2015 Toyota Vitz kazalı hasarlı, irtibat")], POOL, Settings(), NOW) == []


@pytest.mark.parametrize("text", ["2015 Toyota Vitz plakasız, yeni geldi", "2015 Toyota Vitz TR plakalı"])
def test_unplated_import_or_foreign_plate_gets_no_card(text):
    assert ask_price.pick([post("a", raw_text=text)], POOL, Settings(), NOW) == []


def test_a_repost_of_the_same_car_gets_one_card_in_a_turn_and_later():
    repo = Repo([post("a"), post("b")])  # aynı metin, aynı araç, iki gönderi
    sent = []
    assert ask_price.run(repo, sent.append, Settings(), NOW) == 1
    repo.rows.append(post("c"))
    assert ask_price.run(repo, sent.append, Settings(), NOW) == 0 and len(sent) == 1
