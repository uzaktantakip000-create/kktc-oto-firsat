from infrastructure.llm.openrouter import mask_phones


def test_phone_numbers_masked_prices_kept():
    t = "Fiyat: 9.250 STG, 156.000 km. Tel: 0533 000 00 21 / +90 533 000 00 26 / 05330000021"
    out = mask_phones(t)
    assert "9.250 STG" in out and "156.000 km" in out
    assert "533" not in out and "542" not in out and out.count("[tel]") == 3
