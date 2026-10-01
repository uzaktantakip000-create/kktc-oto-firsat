import json
from pathlib import Path

from domain.freetext_parser import parse_freetext

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
