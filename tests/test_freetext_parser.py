import json
from pathlib import Path

import pytest

from domain.freetext_parser import diagnose, parse_freetext

POSTS = {p["id"]: p["text"] for p in json.loads((Path(__file__).parent / "fixtures/facebook_group_posts.json").read_text())}


def test_cash_price_chosen_over_installment():
    p = parse_freetext(POSTS["1535038928644885"])
    assert (p.brand, p.model.lower(), p.year, p.price_amount, p.currency) == ("Honda", "civic", 2005, 4500, "GBP")
    assert p.transmission == "otomatik" and p.phone == "905330000005"


def test_template_post_with_labels():
    p = parse_freetext(POSTS["1535038428644935"])
    assert (p.brand, p.year, p.price_amount, p.currency, p.fuel, p.transmission) == ("Ford", 2017, 15900, "GBP", "dizel", "manuel")


def test_tramer_amount_is_not_the_price():
    assert parse_freetext(POSTS["1816682422690217"]) is None  # fiyat yok, sadece tramer TL tutarı


def test_offer_floor_price_and_year_from_brand_line():
    p = parse_freetext(POSTS["1816663639358762"])
    assert (p.year, p.km, p.price_amount, p.currency) == (2019, 80000, 25000, "GBP")


def test_no_price_means_no_listing():
    assert parse_freetext(POSTS["1535034115312033"]) is None
    assert parse_freetext(POSTS["1816662732692186"]) is None  # sadece peşinat/taksit
    assert parse_freetext(POSTS["3123785707817095"]) is None


def test_not_a_car_or_rental_is_skipped():
    assert parse_freetext(POSTS["3124656827729983"]) is None
    assert parse_freetext(POSTS["x2"]) is None


def test_full_free_text_listing():
    p = parse_freetext(POSTS["x1"])
    assert (p.brand, p.model, p.year, p.km, p.price_amount, p.currency) == ("Toyota", "Auris", 2013, 98000, 7250, "GBP")
    assert (p.fuel, p.transmission, p.steering, p.location, p.negotiable, p.phone) == (
        "benzin", "otomatik", "LHD", "Girne", True, "905330000009")


def test_miles_are_not_converted_to_km():
    p = parse_freetext("2018 Honda Jazz 40.000 mil 6.500 STG")
    assert p.km is None and p.price_amount == 6500


def test_default_steering_for_lhd_group():
    p = parse_freetext("2015 Kia Ceed 5.000 STG", default_steering="LHD")
    assert p.steering == "LHD"


def test_currency_must_be_explicit():
    assert parse_freetext("2015 Kia Ceed 5000") is None


def test_junk_words_are_not_models():
    p = parse_freetext("Ford İlk sahibinden Focus 2016 4.350 stg")
    assert p.model == "Focus"


def test_diagnose_reasons():
    from domain.freetext_parser import diagnose
    assert diagnose("Satılık koltuk takımı 300£") == "marka_yok"
    assert diagnose("Kiralık Toyota Corolla 2015 40£") == "arac_degil"
    assert diagnose("Toyota Corolla 2015 detaylı bilgi için arayın") == "fiyat_yok"
    assert diagnose("Toyota Corolla temiz araç 5000£") == "yil_yok"
    assert diagnose("2015 Toyota Corolla 5000£") == "ok"


def test_space_separated_prices():  # 09.10 FB denemesi: "6 500 STG" £500, "650 000 TL" yıl (2014) fiyat sanılıyordu
    assert parse_freetext("Toyota Corolla 2015 Fiyat: 6 500 STG").price_amount == 6500
    p = parse_freetext("Mazda Demio 2014 650 000 TL")
    assert (p.year, p.price_amount, p.currency) == (2014, 650000, "TRY")
    assert parse_freetext("Peugeot 308 2012 120 000 km 4.250 STG").km == 120000


def test_pound_sign_before_price_after_year():  # "2014 £6500": yıl reddediliyordu, düzeltilince fiyat 2014 olacaktı
    for text in ("Toyota Vitz 2014 £6500", "Toyota Vitz 2014 £ 6.500", "Toyota Aqua 2014\n£6500"):
        p = parse_freetext(text)
        assert (p.year, p.price_amount, p.currency) == (2014, 6500, "GBP"), text
    p = parse_freetext("Honda Fit 7500 STG 2015 model")
    assert (p.year, p.price_amount) == (2015, 7500)


def test_year_followed_by_its_own_currency_is_still_a_price():
    assert diagnose("Toyota Corolla 2000 £") == "yil_yok"


def test_ambiguous_year_or_price_is_skipped():
    assert parse_freetext("Toyota Vitz Fiyat 2000 STG 2008 model") is None


def test_currency_spellings_seen_in_groups():
    assert parse_freetext("Nissan note 2015 6100str").price_amount == 6100
    assert parse_freetext("Range Rover Vogue 2020 49900 paund").price_amount == 49900


def test_brand_and_model_names_match_site_keys():
    assert parse_freetext("NİSSAN NOTE 2016 9.500 STG").brand == "Nissan"  # .title() "Ni̇ssan" yapıyordu
    assert parse_freetext("MERCEDES BENZ CLA 220 2016 16.500 STG").model == "CLA"
    assert parse_freetext("BMW F30 320İ 2013 11.500 STG").model == "320İ"
    assert parse_freetext("Toyota CH R HYBRİD 2018 15.000 stg").model == "C-HR"
    assert parse_freetext("Mazda CX 5 2017 14.500 stg").model == "CX-5"
    p = parse_freetext("RANGE ROVER EVOQUE 2020 £38.000")
    assert (p.brand, p.model) == ("Land Rover", "Range Rover EVOQUE")


def test_slash_thousands_in_post():
    p = parse_freetext("Mercedes Benz C180 model 2013\n180/000 km\n12/800 £")
    assert (p.model, p.km, p.price_amount) == ("C180", 180000, 12800)


@pytest.mark.parametrize("text,km", [
    ("Honda Civic 2012\n145 bin km\n4.900 stg", 145000),  # km etiketi alt satırdaki fiyatı alıyordu (4900)
    ("Honda Civic 2012 145bin km 4900 stg", 145000),
    ("Honda Civic 2012\n145k km\n4.900 stg", 145000),
    ("Honda Civic 2012\n145 BİN KM\n4.900 stg", 145000),
    ("Honda Civic 2012\nkm 145 bin\n4.900 stg", 145000),  # yıl + alt satırdaki "km" (2012 km) okunuyordu
    ("Honda Civic 2012\nKM:\n120.000\n4.900 stg", 120000),  # ":" varsa değer alt satırda olabilir
    ("Toyota Vitz 2014 km 85.000, 5.500 stg", 85000),  # "2014 km" yıl
    ("Honda Civic 2012\nkm\n4.900 stg", None),
    ("Honda Civic 2012\nkm'si düşük\n4.900 stg", None),
    ("Mazda 3 2012 km: 4.900 stg", None),  # para birimli sayı km değil
    ("Toyota Vitz 2014 145km 5.500 stg", None),
    ("Toyota Vitz 2014 96000km 5.500 stg", 96000),
])
def test_km_does_not_cross_lines_and_reads_bin(text, km):
    p = parse_freetext(text)
    assert p.km == km and p.year in (2012, 2014) and p.price_amount in (4900, 5500)


@pytest.mark.parametrize("text,km", [
    ("Mazda CX-5 2022\nTüm bakımları 2 bin km önce yapıldı\n18.500 stg", None),  # bakım: km 2.000 değil
    ("Toyota Corolla 2015\nbakımları her 5 bin km de bir yapıldı\n6.500 stg", None),
    ("Toyota Corolla 2015\nLASTİKLER 5.000 KM ÖNCE DEĞİŞTİRİLDİ\n6.500 stg", None),
    ("Mazda Demio 2007\nMotoru 40 bin km de araçtan sökülüp takıldı\n3.000 stg", None),  # parça km'si
    ("Honda Fit 2013\n140 bin km, bakımları her 5.000 km'de bir yapıldı\n5.500 stg", 140000),
    ("Toyota Vitz 2014\nson servis 5000 km önce, 96.000 km'de\n5.500 stg", 96000),
    ("Nissan March 2012\nAraç 213.000 km olup bakımları eksiksiz\n3.500 stg", 213000),  # büyük sayı: aracın km'si
    ("Suzuki Swift 2014 Araç temiz bakımlı bir araç 95 bin km'de 4.500 stg", 95000),  # "bakımlı" bakım değil
])
def test_service_interval_km_is_not_the_car_km(text, km):
    assert parse_freetext(text).km == km


@pytest.mark.parametrize("text,km", [
    ("Toyota Vitz 2014\nKİLOMETRE : 160.000\n5.500 stg", 160000),  # yalnız "km" etiketi tanınıyordu
    ("Nissan Note 2018 | Kilometre: 123.000 | 6.000 stg", 123000),
    ("Toyota Vitz 2014 🛣 Kilometre:120,000 | 5.500 stg", 120000),
    ("Nissan Note 2020 Mileage: 82000 7.500 stg", 82000),
    ("Mercedes E220d 2018\nKilometre: 129.000 mil\n20.000 stg", None),  # birim mil: km'ye çevrilmez
    ("Toyota Vitz 2014 kilometresi gün geçtikçe artar 5.500 stg", None),
])
def test_kilometre_and_mileage_labels(text, km):
    assert parse_freetext(text).km == km
