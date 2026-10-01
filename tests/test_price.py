from domain.price import parse_price


def test_stg():
    p = parse_price("FİYAT: 6.900 STG")
    assert (p.amount, p.currency, p.currency_guess) == (6900, "GBP", False)


def test_pound_sign():
    assert parse_price("£6,900").amount == 6900


def test_try():
    p = parse_price("600.000 TL")
    assert (p.amount, p.currency, p.currency_guess) == (600000, "TRY", False)


def test_no_currency_is_guess():
    p = parse_price("Fiyat: 10.000")
    assert (p.amount, p.currency, p.currency_guess) == (10000, "GBP", True)


def test_none():
    assert parse_price("Fiyat için arayın") is None


def test_huge_amount_without_currency_is_try_guess():
    p = parse_price("350.000")
    assert (p.currency, p.currency_guess) == ("TRY", True)


def test_bin_and_k_suffix():
    assert parse_price("15 bin STG").amount == 15000
    assert parse_price("£7.5k").amount == 7500
    assert parse_price("7,5 bin stg").amount == 7500


def test_ownership_number_is_not_price():
    p = parse_price("Fiyat: 1. el 8.500 STG")
    assert (p.amount, p.currency) == (8500, "GBP")
    assert parse_price("2.el 7.000").amount == 7000


def test_number_next_to_currency_wins():
    assert parse_price("2018 model 9.500 STG").amount == 9500
