"""Fiyat tuzakları (21.10.2026; sosyal oturumun ölçümü 14–21.10, ~7.500 araç gönderisi; canlı veritabanında salt okunur teyit):
- Facebook satış kutusu "TRY…": ≤1.000 yer tutucu; 1.000–50.000 STG rakamı (TL okununca araç birkaç yüz sterline düşüyordu) → STG TAHMİNİ;
- yer tutucu tutar (11.111, 1.111.111, 123.456 …) hiçbir birimde fiyat değil (€ kuralıyla TL sanılıp 🟡'ya giriyordu);
- metindeki 50.000 TL altı tutar araç fiyatı değil (kapora, taksit, yol vergisi, parça);
- bunların hiçbiri taze TL (🟢 yolu) ya da ⚡ olamaz. Uydurma metinler; telefon/isim yok, ağ yok."""
from datetime import timedelta

import pytest

from application import urgent
from domain.data_gate import FIELD_PRICE, tl_fresh
from domain.freetext_parser import body_price, diagnose, parse_freetext
from domain.llm_read import parse_llm_read
from domain.price import TL_CAR_MIN, implausible_amount, placeholder_amount, price_tokens
from domain.settings import Settings
from tests.test_evaluate import NOW as EVAL_NOW
from tests.test_evaluate import car
from tests.test_social_import import NOW, FakeRepo, _file, _line, _rows
from tests.test_urgent import ev

S = Settings()


def _price(text):
    p = parse_freetext(text)
    return None if p is None else (p.price_amount, p.currency, p.currency_guess)


# --- yer tutucu tutar (domain.price.placeholder_amount) ---------------------------------------------------------------------------
@pytest.mark.parametrize("amount", [11_111, 99_999, 111_111, 555_555, 999_999, 1_111_111, 1234, 12_345, 123_456, 1_234_567,
                                    12_345_678, 98_765, 987_654, 1_111_111.0])
def test_placeholder_amounts(amount):
    assert placeholder_amount(amount)


@pytest.mark.parametrize("amount", [None, 0, -11_111, 1111, 9999, 5555, 6500, 12_500, 123_400, 123_500, 120_000, 1235, 9876, 98_764,
                                    185_000, 11_111.5, 2_000_000])
def test_real_looking_amounts_are_not_placeholders(amount):
    assert not placeholder_amount(amount)  # 4 haneli tekrar (5.555, 9.999) ve yuvarlak tutar gerçek fiyat olabilir


@pytest.mark.parametrize("amount,currency,want", [(40_000, "TRY", True), (TL_CAR_MIN - 1, "TRY", True), (TL_CAR_MIN, "TRY", False),
                                                  (40_000, "GBP", False), (6_500, "GBP", False), (1_234_567, "TRY", True),
                                                  (111_111, "EUR", True), (None, "TRY", False), (185_000, "TRY", False)])
def test_implausible_amount(amount, currency, want):
    assert implausible_amount(amount, currency) is want


def test_box_token_is_only_uppercase_try_glued_in_front():
    def box(text):
        return [(t.amount, t.currency, t.box) for t in price_tokens(text) if t.currency]
    assert box("TRY5,200") == [(5200, "TRY", True)] and box("TRY210.000") == [(210_000, "TRY", True)]
    assert box("TRY16,000 Girne") == [(16_000, "TRY", True)]
    assert box("5.200 TRY") == [(5200, "TRY", False)] and box("TRY 5.200") == [(5200, "TRY", False)]  # boşluklu/arkada: kullanıcı yazımı
    assert box("try5200") == [(5200, "TRY", False)] and box("5.200 TL") == [(5200, "TRY", False)]
    assert box("€5,200") == [(5200, "EUR", False)]  # € kutusu ayrı kural (application.social_ingest.fb_euro)


# --- Facebook kutusu "TRY…" ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("box", ["TRY1", "TRY111", "TRY321", "TRY820", "TRY1,000"])
def test_box_try_up_to_a_thousand_is_a_placeholder(box):
    text = f"2012 Toyota Vitz temiz\n{box}"
    assert parse_freetext(text) is None and diagnose(text) == "fiyat_yok"
    p = parse_freetext(text, allow_no_price=True)
    assert p is not None and p.price_amount is None  # fiyatsız ilan ("📞 FİYAT SOR")


@pytest.mark.parametrize("box,amount", [("TRY5,200", 5200), ("TRY9,000", 9000), ("TRY16,000 Beylerbeyi, Girne", 16_000),
                                        ("TRY1,001", 1001), ("TRY49,999", 49_999)])
def test_box_try_under_fifty_thousand_is_a_guessed_sterling(box, amount):
    assert _price(f"2008 BMW 116i otomatik\n{box}") == (amount, "GBP", True)  # TL okunsa araç birkaç yüz sterlin olurdu


@pytest.mark.parametrize("body", ["5.200 £", "Fiyat 5.200 STG", "5200 sterlin pazarlık payı var"])
def test_body_stating_the_same_number_in_sterling_is_not_a_guess(body):
    assert _price(f"2008 BMW 116i otomatik\n{body}\nTRY5,200") == (5200, "GBP", False)  # eskiden iki birim çelişiyordu: fiyatsız


def test_body_stating_a_different_price_wins_only_with_a_hint():
    assert _price("Toyota Vitz 2012 6.500 STG\nTRY6,000") is None  # ipuçsuz iki tutar: belirsiz (eski kural)
    assert _price("Toyota Vitz 2012\nFiyat: 6.500 STG\nTRY6,000") == (6500, "GBP", False)


def test_box_try_from_fifty_thousand_is_lira():
    assert _price("2011 Nissan March\nTRY185,000") == (185_000, "TRY", False)
    assert _price("2011 Nissan March 185.000 TL\nTRY185,000") == (185_000, "TRY", False)
    assert _price("2015 Toyota Corolla\nTRY50,000") == (50_000, "TRY", False)


def test_body_price_ignores_the_box_line_wherever_it_lies():
    assert body_price("2011 Nissan March\n+6\nTRY185,000\nGirne") is None  # okuyucu kutu satırını gövdeye yazmış
    assert body_price("2011 Nissan March 185.000 TL\nTRY185,000") == (185_000, "TRY")
    assert body_price("2008 BMW 116i\n5.200 £\nTRY5,200") == (5200, "GBP")


# --- yer tutucu tutar, her birimde ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("line", ["€1,111,111", "€111,111", "€11,111", "€12,345", "€1,234", "Fiyat: 123.456 TL", "Fiyat 1.234.567 TL",
                                  "Fiyat 99.999 STG", "98765 £", "TRY1,111,111", "TRY1,234,567"])
def test_placeholder_is_never_a_price(line):
    text = f"2019 Range Rover Vogue full paket\n{line}"
    assert parse_freetext(text) is None and diagnose(text) == "fiyat_yok"


def test_placeholder_still_competes_but_is_never_chosen():
    assert _price("2016 Honda Fit hybrid\nFiyat 7.500 STG\n€1,111,111") == (7500, "GBP", False)  # ipuçlu gerçek tutar kazanır
    assert _price("2016 Honda Fit hybrid 7.500 STG\n€12,345") is None  # ipuçsuz iki tutar: eskisi gibi belirsiz
    # yer tutucu düşürülseydi gövdedeki ekstra tutarı tek aday kalıp fiyat olurdu (14 günde 3 ilan)
    assert _price("2011 Nissan Juke nismo, bakımları yeni, 1500stg ye yakın extralıdır\n€111,111") is None
    assert _price("2012 Toyota Vitz temiz 6.500 STG\nTRY321") is None and _price("2012 Toyota Vitz\nFiyat 6.500 STG\nTRY321") == (
        6500, "GBP", False)


@pytest.mark.parametrize("text", ["2005 Peugeot 206\nFiyat: 40.000 TL\n€39,999", "1997 Mazda Familia\nFiyat : 30.000₺\n€30,000",
                                  "2008 BMW 116i\n5.200 TL\nTRY5,200"])
def test_small_lira_in_the_text_makes_the_same_box_number_lira_too(text):
    assert _price(text) is None  # € kuralıyla £39.999'luk 2005 Peugeot / STG tahmini olurdu


# --- metindeki 50.000 TL altı tutar ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["2005 Peugeot 206 benzinli\nFiyat: 40.000 TL", "2013 Hyundai i20\nPEŞİNAT 4.250 ₺",
                                  "2023 Mazda 2 otomatik\nYOL RUSHATI 15000 TL", "2000 Volkswagen Golf 1.6 fsi 35 bin tl",
                                  "2005 Citroen C3 toplasan 5 bin TL masrafı var"])
def test_small_lira_amount_is_not_a_car_price(text):
    assert parse_freetext(text) is None


@pytest.mark.parametrize("text,want", [
    ("2013 Hyundai i20 otomatik\nPEŞİNAT 4.250 ₺\nFiyat 6.500 STG", (6500, "GBP", False)),
    ("2023 Mazda 2 otomatik\nYOL RUSHATI 15000 TL\n1.150.000 TL", (1_150_000, "TRY", False)),
    ("2006 Seat Leon\nÜzerinde 30 bin tl lik ses sistemi var\nFiyat 285 bin TL", (285_000, "TRY", False)),
])
def test_larger_real_price_is_taken_over_a_small_lira_amount(text, want):
    assert _price(text) == want


# --- Facebook devri: kutu ek_metin'de ya da gövdede -----------------------------------------------------------------------------
def _imported(tmp_path, text, extra=None):
    from application import social_import as si
    repo = FakeRepo(_rows())
    _file(tmp_path, NOW, _line(text=text, ek_metin=extra) if extra else _line(text=text))
    si.import_facebook(repo, tmp_path)
    (data,) = repo.listings.values()
    return data


def test_try_box_only_becomes_a_guessed_sterling_marked_as_box_price(tmp_path):
    data = _imported(tmp_path, "Temiz araç, ilk sahibinden.", "2008 BMW 116i otomatik\nTRY5,200\nGirne")
    assert (data["price_amount"], data["currency"], data["currency_guess"], data["price_gbp"]) == (5200, "GBP", True, 5200)
    assert FIELD_PRICE in data["urgency_signals"]


def test_try_box_with_the_same_sterling_in_the_body_is_a_normal_listing(tmp_path):
    data = _imported(tmp_path, "2008 BMW 116i otomatik 5.200 £ pazarlık var", "2008 BMW 116i\nTRY5,200")
    assert (data["price_amount"], data["currency"], data["currency_guess"]) == (5200, "GBP", False)
    assert FIELD_PRICE not in (data.get("urgency_signals") or [])


def test_box_line_inside_the_body_is_still_a_box_price(tmp_path):
    data = _imported(tmp_path, "Honda Civic 2018 otomatik\n+6\nTRY650,000\nGirne")  # ek_metin yok, kutu satırı gövdede
    assert (data["price_amount"], data["currency"], data["currency_guess"]) == (650_000, "TRY", False)
    assert FIELD_PRICE in data["urgency_signals"]  # emsal olmaz, 🟢 olmaz (fiyat yalnız kutuda)


def test_body_without_box_line_and_without_extra_text_is_unchanged(tmp_path):
    data = _imported(tmp_path, "Honda Civic 2018 otomatik\nFiyat 650.000 TL")
    assert data["price_amount"] == 650_000 and FIELD_PRICE not in (data.get("urgency_signals") or [])


def test_euro_placeholder_box_gives_a_priceless_listing(tmp_path):
    data = _imported(tmp_path, "2019 Range Rover Vogue full paket", "2019 Range Rover Vogue\n€1,111,111")
    assert data["price_amount"] is None and "fiyatsiz" in data["urgency_signals"]  # eskiden €≥50.000 → TL tahmini (≈£16.800)


# --- ikinci kapı: eski satırlar taze TL ya da ⚡ olamaz ----------------------------------------------------------------------------
@pytest.mark.parametrize("amount", [6_000, 40_000, 49_900, 999_999, 99_999])
def test_small_or_placeholder_lira_is_never_fresh(amount):
    listing = car("t", 900, currency="TRY", price_amount=amount, posted_at=EVAL_NOW - timedelta(days=1))
    assert not tl_fresh(listing, S, EVAL_NOW)
    assert tl_fresh(listing | {"price_amount": 185_000}, S, EVAL_NOW)  # aynı ilan gerçek TL tutarla taze


@pytest.mark.parametrize("currency,amount", [("TRY", 1_234_567), ("TRY", 40_000), ("GBP", 11_111), ("GBP", 12_345)])
def test_implausible_amount_gets_no_urgent_message(currency, amount):
    bad = ev("yt", price=7600, currency=currency, price_amount=amount)
    assert urgent.candidates([bad], S, EVAL_NOW) == []
    assert urgent.candidates([ev("iyi", price=7600)], S, EVAL_NOW) != []


def test_llm_quote_with_box_try_or_placeholder_gives_no_price():
    def read(text, price, quote):
        return parse_llm_read({"arac_ilani_mi": True, "marka": "BMW", "yil": 2008, "yil_alinti": "2008 BMW", "fiyat": price,
                               "fiyat_alinti": quote}, text)
    assert read("2008 BMW 116i\nTRY5,200", 5200, "TRY5,200").price is None
    assert read("2008 BMW 116i\nFiyat 1.111.111 TL", 1111111, "Fiyat 1.111.111 TL").price is None
    assert read("2008 BMW 116i\nFiyat 40.000 TL", 40000, "Fiyat 40.000 TL").price is None
    assert read("2008 BMW 116i\nFiyat 5.200 STG", 5200, "Fiyat 5.200 STG").price == 5200
