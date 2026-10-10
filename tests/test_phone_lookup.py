"""📞 Telefon → ilanlar (application/phone_lookup, 11.10.2026): levha fotoğrafı ya da numara → o numaranın taranan sitelerdeki ilanları.
Sahte veritabanı; ağ yok; numaralar uydurma."""
from datetime import timedelta

import pytest

from application import ad_check, phone_lookup
from domain.settings import Settings
from tests.test_ad_check import Reader
from tests.test_evaluate import NOW, POOL, car

PHONE = "905330000001"


@pytest.mark.parametrize("text,phones", [
    ("SATILIK 0533 000 00 01", [PHONE]),
    ("+90 (533) 000-0001", [PHONE]),
    ("05330000001 / 0548 111 22 33", [PHONE, "905481112233"]),
    ("533 000 00 01", [PHONE]),
    ("Tel: 0392 815 12 34", ["903928151234"]),
    ("2015 model 90.000 km 6.500 stg", []),  # yıl/km/fiyat numara sanılmaz
])
def test_phone_numbers_are_found_in_any_common_format(text, phones):
    assert phone_lookup.find_phones(text) == phones


def test_a_sign_is_told_apart_from_an_ad_text():
    assert phone_lookup.is_sign("SATILIK\n0533 000 00 01")
    assert phone_lookup.is_sign("SAHİBİNDEN SATILIK 2015 MODEL 0533 000 00 01")
    assert not phone_lookup.is_sign("2015 Toyota Vitz 80.000 km otomatik 5.000£ 0533 000 00 01")
    assert not phone_lookup.is_sign("merhaba 0533 000 00 01 bu arabayı sokakta gördüm çok temiz duruyordu sence alalım mı acaba")


def ad(i, price, **kw):
    return car(i, price, source_name="KibrisArabaAl", platform="web", url=f"https://kibrisarabaal.com/ilan/{i}-x/", price_amount=price,
               currency="GBP", posted_at=NOW - timedelta(days=3), seller_phone=PHONE, source_checked_at=NOW, location="Girne") | kw


class Repo:
    def __init__(self, rows=()):
        self.rows, self.state, self.asked = list(rows), {}, []

    def listings_by_phone(self, phone, limit=30):
        self.asked.append(phone)
        return [r for r in self.rows if r.get("seller_phone") == phone]

    def market_pool(self, days, keys=None):
        return POOL

    def get_state(self, k, default=None):
        return self.state.get(k, default)

    def set_state(self, k, v):
        self.state[k] = v

    def blocked_phones(self):
        return []

    def price_history(self, listing_id):
        return []

    def seller_active_count(self, l):
        return 0

    def twins(self, l):
        return []

    def facebook_similar_count(self, brand, model, year):
        return 0


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    import application.settings_store as st
    monkeypatch.setattr(st, "load_settings", lambda repo: Settings())
    monkeypatch.setattr(ad_check, "gbp_rate", lambda c: 1.0)


def test_unknown_number_says_so_and_asks_for_the_car_details():
    out = phone_lookup.reply(Repo([ad("old", 5000, is_active=False)]), [PHONE], Settings(), NOW)
    assert "aktif bir ilanda geçmiyor (geçmişte 1 ilanda görünmüştü)" in out and "marka, yıl, km ve fiyatı yaz" in out


def test_one_listing_gets_the_full_dossier():
    out = phone_lookup.reply(Repo([ad("a", 5000)]), [PHONE], Settings(), NOW)
    assert out.startswith("📞 Bu numaranın taranan sitelerde 1 aktif ilanı var:") and "📂 İLAN DOSYASI" in out and "🟢 FIRSAT" in out


def test_many_listings_are_listed_cheapest_first_and_social_posts_only_counted():
    rows = [ad("a", 9000), ad("b", 5000), ad("c", 7300), ad("f", 4000, platform="facebook", source_name="Grup", url=None)]
    out = phone_lookup.reply(Repo(rows), [PHONE], Settings(), NOW)
    lines = out.split("\n")
    assert lines[0] == "📞 Bu numaranın taranan sitelerde 3 aktif ilanı var → galeri/ticari olabilir."
    assert lines[2].startswith("🟢 2015 Vitz · 80.000 km · £5.000") and "kibrisarabaal.com/ilan/b-x" in lines[3]
    assert "Facebook/Instagram'da da 1 gönderisi var" in out and "£4.000" not in out
    assert PHONE not in out and "0533" not in out


def test_owner_sign_photo_goes_to_the_phone_lookup_but_a_subscriber_gets_the_ad_check():
    repo = Repo([ad("a", 5000)])
    out = ad_check.handle(repo, "", b"jpeg", Reader(image_text="SATILIK\n0533 000 00 01"), now=NOW)
    assert "📞 Bu numaranın taranan sitelerde 1 aktif ilanı var" in out and repo.asked == [PHONE]
    assert out.startswith("📞 Levhada okuduğum numara: 0533 000 00 01 (yanlışsa numarayı yazarak gönder)")  # tek rakam hatası = başka biri
    assert "Levhada okuduğum" not in ad_check.handle(Repo([ad("a", 5000)]), "0533 000 00 01", None, None, now=NOW)  # yazılan numara: zaten doğru
    out = ad_check.handle(Repo([ad("a", 5000)]), "", b"jpeg", Reader(image_text="SATILIK\n0533 000 00 01"), now=NOW, subscriber="9")
    assert "📞" not in out and "Okuyamadım" in out


def test_an_ad_text_with_a_phone_is_still_checked_and_gets_the_count_line():
    repo = Repo([ad("a", 5000), ad("b", 6000), ad("c", 7000)])
    out = ad_check.handle(repo, "2015 Toyota Vitz 80.000 km otomatik\nFiyat: 5.000£\n0533 000 00 01", None, None, now=NOW)
    assert "Okuduğum (kural): 2015 Toyota Vitz" in out
    assert out.endswith("📞 Bu numara taranan sitelerde 3 aktif ilanda geçiyor → galeri/ticari olabilir. Numarayı tek başına gönderirsen ilanlarını listelerim.")
