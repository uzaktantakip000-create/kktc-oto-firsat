"""KKTC grubu mu? (domain/kktc_region; spec §24.38). Keşfin bulduğu Facebook grubu yalnız adı KKTC'yi anıyorsa ya da gölgedeki ilanlarının
açık çoğunluğu KKTC'yi anıyorsa/KKTC telefonu taşıyorsa yeşile geçer. Grup adları herkese açık grupların adlarıdır; telefonlar uydurmadır."""
import pytest

from domain.kktc_region import KKTC_TEXT_MIN, kktc_listings, kktc_named, kktc_text


@pytest.mark.parametrize("name", [
    "KKTC ARABAM", "SAHIBINDEN AL SAT (KIBRIS)", "North Cyprus Online Market !!!", "GİRNE 2.EL INGILIZ PAZARI", "SATILIK ÜRÜNLER İSKELE",
    "İkinci El Pazarı Mağusa (Second Hand Market Famagusta )", "MEZUNUM SATIYORUM KKTC", "kktc motosiklet alim satim",
    "KKTC ARABA PAZARI", "Kktc Sol Direksiyon Araba Pazari", "north cyprus cars and bikes for sale", "Kıbrıs Araç Alım", "Girne Oto",
    "Lefkoşa Oto Pazarı", "Gazimağusa 2. el", "Güzelyurt'ta satılık", "TRNC cars", "#northcyprus market", "KUZEYKIBRIS ARAÇ",
    "Değirmenlik Pazar", "Lapta Alsancak Al Sat", "Türkiye - KKTC araç",  # güçlü sözcük Türkiye geçse de yeter
])
def test_names_that_name_kktc_pass(name):
    assert kktc_named(name)


@pytest.mark.parametrize("name", [
    "Bristol Uni Students - buy, sell and free stuff", "OLE MISS SEASON/REGULAR TICKETS", "Buy and Sell Student's Products",
    "TÜRKİYE İskele Kiralama Alım Satım Mantolama",  # yer adı + Türkiye: Türkiye'deki iskele
    "Turkey Girne Tours", "Bangladeshi Students UK", "Oto Pazarı 2. El", "Boğaziçi Oto", "Dikmen Galeri", "", None,
])
def test_names_that_do_not_name_kktc_fail(name):
    assert not kktc_named(name)


def test_place_suffix_is_free_so_iskelet_passes_known_limit():
    """Türkçe ekler serbest ("Girne'de", "Mağusada") olduğu için "iskelet" de geçer: kabul edilen sınır (grup adında nadir; ölçüm yolu aynı)."""
    assert kktc_named("Girne'de satılık") and kktc_named("Mağusada araba") and kktc_named("iskelet")


@pytest.mark.parametrize("text,location,phone,ok", [
    ("2012 Honda Fit 4500 stg Girne'de", None, None, True),
    ("2012 Honda Fit 4500 stg", "Lefkoşa", None, True),
    ("2012 Honda Fit 4500 stg tel 0533 000 00 01", None, None, True),
    ("2012 Honda Fit 4500 stg +90 548 000 0001", None, None, True),
    ("2012 Honda Fit 4500 stg", None, "905420000001", True),
    ("2012 Honda Fit 4500 stg 0392 000 00 01", None, None, True),
    ("Türkiye plakalı 2015 Clio, Mağusa", None, None, True),  # ilan metninde Türkiye geçmesi KKTC'yi silmez
    ("2012 Honda Fit £4500 Bristol, call 07700 900001", "Bristol", None, False),
    ("2012 Honda Fit 450.000 TL", None, "905320000001", False),  # yalnız TL ve Türkiye numarası (532): kanıt değil
    (None, None, None, False),
])
def test_a_listing_mentions_kktc(text, location, phone, ok):
    assert kktc_text(text, location, phone) is ok


def rows(kktc, other):
    return [{"raw_text": "2014 Toyota Vitz 5200 stg Girne"}] * kktc + [{"raw_text": "2014 Toyota Vitz £5200 Bristol BS1"}] * other


def test_listing_evidence_needs_a_clear_majority_and_enough_listings():
    assert kktc_listings(rows(2, 1)) and kktc_listings(rows(10, 5))  # en az üçte iki
    assert not kktc_listings(rows(3, 2)) and not kktc_listings(rows(0, 6))  # %60: açık çoğunluk değil
    assert not kktc_listings(rows(KKTC_TEXT_MIN - 1, 0)) and not kktc_listings([])  # tek-iki ilan grup hakkında bir şey söylemez
