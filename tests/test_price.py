from domain.price import join_split_thousands, parse_price, price_tokens


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


def test_space_thousands_separator():  # 09.10: "6 500 STG" £500, "1 650 000 TL" 1 TL okunuyordu
    assert (parse_price("6 500 STG").amount, parse_price("12 500 £").amount) == (6500, 12500)
    assert (parse_price("1 650 000 TL").amount, parse_price("£ 12 500").amount) == (1650000, 12500)
    assert parse_price("6 500 STG").amount == 6500  # bölünmez boşluk


def test_model_number_is_not_joined_to_big_number():
    assert join_split_thousands("Peugeot 308 120 000 km") == "Peugeot 308 120.000 km"
    assert join_split_thousands("C 200 2015 model") == "C 200 2015 model"  # 4 haneli grup binlik değildir


def test_symbol_between_year_and_price_belongs_to_attached_number():
    assert parse_price("2014 £6500").amount == 6500
    assert parse_price("6500£ 2014").amount == 6500
    assert parse_price("2016 STG 8.750").amount == 8750  # ikisi de ayrık: yıl görünümlü olan elenir
    assert parse_price("9500 STG 2016").amount == 9500


def test_more_currency_spellings():
    assert (parse_price("6100str").amount, parse_price("6100str").currency) == (6100, "GBP")
    assert parse_price("49900 paund").currency == "GBP"
    assert parse_price("6.500 (STG)").currency == "GBP"


def test_guess_prefers_non_year_number():
    p = parse_price("2015 model 10.000")
    assert (p.amount, p.currency_guess) == (10000, True)


def test_malformed_number_does_not_crash():
    assert parse_price("1.2.3 TL") is None


def test_tokens_report_shared_currency_once():
    toks = [t for t in price_tokens("2014 £6500") if t.currency]
    assert [(t.amount, t.tight) for t in toks] == [(6500, True)]


def test_slash_thousands_separator():  # FB: "12/800 £" 800 £ okunuyordu
    assert parse_price("12/800 £").amount == 12800
    assert join_split_thousands("180/000 km") == "180.000 km"
    assert join_split_thousands("12/10/2025") == "12/10/2025"  # tarih değişmez
