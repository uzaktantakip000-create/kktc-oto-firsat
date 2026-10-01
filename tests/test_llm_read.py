from domain.llm_read import compare, parse_llm_read

TEXT = "2016 Honda Fit 1.3 otomatik\n53.000 km\nFiyat: 7.500£ nakit\nSağ direksiyon, Girne"


def raw(**kw):
    base = {"arac_ilani_mi": True, "marka": "Honda", "model": "Fit", "yil": 2016, "yil_alinti": "2016 Honda Fit",
            "km": 53000, "km_alinti": "53.000 km", "fiyat": 7500, "fiyat_alinti": "Fiyat: 7.500£", "direksiyon": "RHD",
            "pesinat_veya_kredi_devri": False, "satildi": False}
    return base | kw


def test_valid_read_is_accepted_and_currency_comes_from_quote():
    r = parse_llm_read(raw(), TEXT)
    assert (r.year, r.km, r.price, r.currency, r.brand, r.model, r.steering) == (2016, 53000, 7500, "GBP", "Honda", "Fit", "RHD")


def test_invented_quote_or_wrong_number_drops_the_field():
    r = parse_llm_read(raw(fiyat_alinti="Fiyat: 5.500£", km_alinti="153.000 km", yil=2018), TEXT)
    assert r.price is None and r.km is None and r.year is None  # alıntı metinde yok / sayı alıntıda yok
    r = parse_llm_read(raw(km=53, km_alinti="53.000 km"), TEXT)
    assert r.km is None  # 53 != 53.000 (binlik atlanmış)


def test_number_inside_a_bigger_number_is_not_accepted():
    text = "Honda Fit 2016 fiyat 17.500£"
    r = parse_llm_read(raw(fiyat=7500, fiyat_alinti="17.500£"), text)
    assert r.price is None  # alıntıdaki tutar 17.500, model 7.500 dedi


def test_currency_must_be_explicit_in_quote():
    r = parse_llm_read(raw(fiyat_alinti="Fiyat: 7.500"), TEXT.replace("7.500£", "7.500"))
    assert r.price is None  # para birimi yazmıyor: yapay zekâya bırakmıyoruz


def test_non_car_and_garbage_outputs():
    assert parse_llm_read({"arac_ilani_mi": False}, TEXT).is_car is False
    assert parse_llm_read(None, TEXT) is None and parse_llm_read("x", TEXT) is None
    r = parse_llm_read(raw(marka="Ferrari"), TEXT)
    assert r.brand is None  # marka metinde geçmiyor


LISTING = {"price_amount": 7500, "currency": "GBP", "year": 2016, "km": 53000, "brand": "Honda", "steering": None}


def test_compare_clean_listing():
    assert compare(LISTING, parse_llm_read(raw(), TEXT)) == ([], True)


def test_compare_flags_each_mismatch():
    r = parse_llm_read(raw(), TEXT)
    assert "okuma_fiyat" in compare({**LISTING, "price_amount": 4500}, r)[0]
    assert "okuma_fiyat" in compare({**LISTING, "currency": "EUR"}, r)[0]
    assert "okuma_yil" in compare({**LISTING, "year": 2012}, r)[0]
    assert "okuma_km" in compare({**LISTING, "km": 90000}, r)[0]
    assert "okuma_marka" in compare({**LISTING, "brand": "Toyota"}, r)[0]


def test_lhd_and_credit_and_sold_make_the_candidate_suspect():
    lhd = parse_llm_read(raw(direksiyon="LHD"), TEXT)
    assert "okuma_direksiyon" in compare(LISTING, lhd)[0]
    assert compare({**LISTING, "steering": "LHD"}, lhd)[0] == []
    r = parse_llm_read(raw(pesinat_veya_kredi_devri=True, satildi=True), TEXT)
    assert {"okuma_pesinat", "okuma_satildi"} <= set(compare(LISTING, r)[0])


def test_price_not_read_is_unconfirmed_not_mismatch():
    r = parse_llm_read(raw(fiyat=None, fiyat_alinti=None), TEXT)
    assert compare(LISTING, r) == ([], False)


def test_brand_spelling_variants_are_not_a_mismatch():
    r = parse_llm_read(raw(marka="Mercedes", model="C200"), "Mercedes C200 2016 53.000 km 7.500£ Fiyat: 7.500£ 2016 Honda Fit")
    assert r.brand == "Mercedes"
    for spelled in ("Mercedes-Benz", "Mercedes - Benz", "MERCEDES"):
        assert "okuma_marka" not in compare({**LISTING, "brand": spelled}, r)[0]
