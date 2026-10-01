import json
from pathlib import Path

from domain.caption_parser import normalize_phone, parse_caption

POSTS = {p["shortCode"]: p for p in json.loads((Path(__file__).parent / "fixtures/kibris_car_captions.json").read_text())}


def parse(code):
    return parse_caption(POSTS[code]["caption"])


def test_mazda_demio():
    r = parse("Dd2CUJ6DZ_3")
    assert (r.brand, r.model, r.year, r.km) == ("Mazda", "Demio", 2019, 95000)
    assert (r.price_amount, r.currency, r.currency_guess) == (9250, "GBP", False)
    assert r.steering == "RHD" and r.phone == "905330000015" and r.swap is True
    assert "yil_belirsiz" in r.notes


def test_price_without_currency_and_negotiable():
    r = parse("Dd15S2yDaan")
    assert (r.price_amount, r.currency_guess, r.negotiable) == (4400, True, True)
    assert r.km == 187000 and r.steering == "RHD"


def test_pound_sign_and_plain_km():
    r = parse("DduIsK2Dad7")
    assert (r.price_amount, r.currency, r.km, r.phone) == (6400, "GBP", 130150, "905330000017")
    assert r.brand == "Toyota"


def test_missing_km():
    assert parse("Dd7ODXjja03").km is None


def test_masked_km_and_year_range():
    r = parse("Dd7NvVYDczH")
    assert r.km is None and r.year == 2017 and r.price_amount == 21950
    assert "yil_belirsiz" in r.notes


def test_not_a_template_returns_none():
    assert parse_caption("Satılık araç, arayın 0533 000 00 25") is None


def test_phone_normalize():
    assert normalize_phone("0533 000 00 15") == "905330000015"
    assert normalize_phone("+90 533 000 00 16") == "905330000016"
    assert normalize_phone("123") is None


def test_sold_post_names_the_closed_listing():
    from domain.caption_parser import sold_ilan_no
    assert sold_ilan_no("✅ ARABALAR KIBRIS ARACILIĞIYLA SATILDI ✅\n\nİlan Numarası : 🔻19469\n@x") == "19469"
    assert sold_ilan_no("✅✅ 2 GÜNDE SATILDI ✅✅") is None  # numara yok: hangi ilan olduğu belli değil
    assert sold_ilan_no("İlan Numarası : 19470 Toyota Passo 7250 STG") is None  # satıldı yok


def test_brand_typos_map_to_mercedes():
    from domain.normalize import normalize_brand
    assert normalize_brand("Mersedez") == "Mercedes-Benz" and normalize_brand("Ni̇ssan") == "Nissan"
