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
    """Fiyatı fotoğraftan okunmuş FB ilanı (£5.000 < %20 sınırı £6.600). Fiyatsız ilan: post(i, price_gbp=None, urgency_signals=["fiyatsiz"])."""
    return car(i, None, price_gbp=5000, urgency_signals=["foto_fiyat"], url=f"https://www.facebook.com/groups/1/posts/{i}/", seller_type=None,
               seller_phone=None, posted_at=NOW - timedelta(minutes=30), first_seen_at=NOW - timedelta(minutes=10), raw_text=NO_PRICE,
               location=None) | kw


def test_priceless_posts_get_no_card_any_more():
    """Sahip 11.10 ~18:50: "fiyatsız ilana şu an ihtiyaç yok, zaten arayıp soruyoruz; önemli olan fiyatı olanlar"."""
    assert ask_price.pick([post("a", price_gbp=None, urgency_signals=["fiyatsiz"])], POOL, Settings(), NOW) == []


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
    dict(urgency_signals=["foto_fiyat", "tarih_belirsiz"]),  # tarihsiz, okuyucu imlece ulaşmadan durdu: eski olabilir (13.10.2026)
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
    assert msg.startswith("🖼 FOTOĞRAFTA FİYAT · Facebook'ta fiyatı yalnız fotoğrafta yazan 2 ilan %20 kâr sınırının altında") \
        and "km yazmıyor · sahibinden" in msg and "🖼 fotoğrafta £5.000 yazıyor" in msg
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


# --- fotoğraftan fiyat (sahip onayı 11.10.2026) ---
class PhotoReader:
    def __init__(self, text):
        self.text, self.images, self.texts = text, 0, 0

    def read_image(self, image, mime="image/jpeg"):
        self.images += 1
        return self.text

    def read(self, text):
        self.texts += 1
        return None


def test_handoff_photo_copy_is_read_locally_and_only_photos_go_to_the_reader(tmp_path):
    (tmp_path / "foto").mkdir()
    (tmp_path / "foto" / "1000000001.jpg").write_bytes(b"\xff\xd8jpeg")
    repo, reader = FakeRepo(_rows()), PhotoReader("SATILIK Fiyat 4.500 STG")
    _file(tmp_path, SI_NOW, _line("1000000001", text=NO_PRICE, foto_dosya="foto/1000000001.jpg"),
          _line("1000000002", text="2014 Honda Fit otomatik, irtibat", foto_dosya="foto/9999999999.jpg"),  # başka gönderinin adı: yok sayılır
          _line("1000000003", text="2016 Mazda Demio 60.000 km Fiyat 6.000 STG"))  # fiyatlı, km'li: yapay zekâya gitmez
    rep = si.import_facebook(repo, tmp_path, make_reader=lambda r: reader)
    assert reader.images == 1 and reader.texts == 0 and rep.priceless == 1
    photo = next(d for (s, i), d in repo.listings.items() if i == "1000000001")
    assert photo["price_amount"] == 4500 and photo["extraction_by"] == "llm" and photo["urgency_signals"] == ["foto_fiyat"]


def test_read_photo_accepts_only_its_own_small_local_copy(tmp_path):
    (tmp_path / "foto").mkdir()
    (tmp_path / "foto" / "12345.jpg").write_bytes(b"x" * 10)
    (tmp_path / "foto" / "77777.jpg").write_bytes(b"x" * (si.PHOTO_MAX_BYTES + 1))
    assert si.read_photo(tmp_path, "file:foto/12345.jpg") == (b"x" * 10, "image/jpeg")
    for url in ("file:foto/77777.jpg", "file:foto/../12345.jpg", "file:foto/yok00.jpg", "https://scontent.xx.fbcdn.net/a.jpg", None):
        assert si.read_photo(tmp_path, url) is None, url


@pytest.mark.parametrize("photo,card", [(5000, True), (6600, True), (6700, False), (3000, False)])
def test_photo_price_must_be_under_the_limit_and_plausible(photo, card):
    """£6.600 = %20 sınırı. Üstü fırsat değil; piyasa ortasının yarısından ucuzu (£4.350) yanlış okuma sayılır: fiyatsız gibi, kart yok."""
    picked = ask_price.pick([post("a", price_gbp=photo)], POOL, Settings(), NOW)
    assert bool(picked) is card
    if card:
        ((l, med, n, off),) = picked
        money = f"£{photo:,.0f}".replace(",", ".")
        assert f"🖼 fotoğrafta {money} yazıyor (yapay zekâ okudu, teyit et) → %20 sınırının altında ✅" in ask_price.card(l, med, n, off)

