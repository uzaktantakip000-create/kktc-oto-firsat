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


@pytest.mark.parametrize("text, brand, model", [  # canlı Facebook gönderilerindeki yazımlar (27.10.2026): eskiden "marka_yok" düşüyordu
    ("Mazda3 2015 7.500 stg", "Mazda", "3"),
    ("MAZDA2 2012 otomatik 5.200 stg", "Mazda", "2"),
    ("MERSEDES C180 2014 12.000 stg", "Mercedes", "C180"),
    ("MERSEDEZ E220 2012 11.500 stg", "Mercedes", "E220"),
    ("Mercedez A180 2016 14.000 stg", "Mercedes", "A180"),
    ("Suziki Swift 2010 3.800 stg", "Suzuki", "Swift"),
    ("SUZİKİ SWİFT 2010 3.800 stg", "Suzuki", "SWİFT"),
    ("WOLKSWAGEN GOLF 2013 8.900 stg", "Volkswagen", "GOLF"),
    ("FORT FOCUS 2011 4.500 stg", "Ford", "FOCUS"),
    ("Fort Fiesta 2009 2.900 stg", "Ford", "Fiesta"),
    ("Mazda CX-5 2017 14.500 stg", "Mazda", "CX-5"),  # ayrı yazım değişmez
])
def test_misspelled_and_glued_brands(text, brand, model):
    p = parse_freetext(text)
    assert (p.brand, p.model) == (brand, model) and p.year > 2000 and p.price_amount > 2000


@pytest.mark.parametrize("text", [
    "Fort kale yolu üzerinde 2015 yapımı ev 75.000 stg",  # "fort" sıradan sözcük
    "FORT 2011 4.500 stg",  # Focus/Fiesta yok: marka sayılmaz
    "Mazda323 1998 1.200 stg",  # yapışık rakam tek hane değil
    "Corolla 2015 7.500 stg",  # modelden marka çıkarılmaz
])
def test_brand_aliases_do_not_create_false_brands(text):
    assert parse_freetext(text) is None
    assert diagnose(text) == "marka_yok"


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


@pytest.mark.parametrize("text,why", [
    ("NİSSAN NOTE 2019 MODEL 52.000 KM TEK ŞAHIS EVRAKLAR TAM SERVİS YAPILMIŞTIR 2023 YILI TRAFİĞE ÇIKIŞLI JANTLAR", "fiyat_yok"),  # tek satır FB
    ("2014 Honda Fit takas alınır fiyat 5000 stg", "ok"),  # takas alınır: satış ilanı
    ("Satılık 17 jant BMW 2015 uyumlu 300 STG", "arac_degil"),  # ürün sözcüğü markadan önce
    ("BMW jant takımı 4 adet", "arac_degil"),  # marka var, yıl yok
    ("Araba alınır nakit ödeme", "arac_degil"),
    ("Kiralık 2015 Toyota Vitz", "arac_degil"),
])
def test_one_line_facebook_posts_and_trade_in_are_not_mistaken_for_non_car_ads(text, why):
    """11.10.2026: FB gönderisi çoğu zaman tek satır; sondaki 'jantlar' ya da 'takas alınır' ilanı 'araç değil' yapmamalı."""
    assert diagnose(text) == why


# --- Gizli haneli km (22.10.2026): "192***KM" km'nin ALT SINIRIdır; eskiden km bilinmiyor okunuyordu (21.10 ⚡ 2008 Swift) ---
# Metinler uydurmadır (gerçek ilan/telefon yok).
@pytest.mark.parametrize("text,km", [
    ("Suzuki Swift 2008 192***KM 2.300 stg", 192000),
    ("Suzuki Swift 2008\n192.***km\n2.300 stg", 192000),
    ("Suzuki Swift 2008\n85xxx km\n2.300 stg", 85000),
    ("Suzuki Swift 2008 192 *** km 2.300 stg", 192000),
    ("Suzuki Swift 2008 192 XXX KM 2.300 stg", 192000),
    ("Suzuki Swift 2008\nKM: 192.***\n2.300 stg", 192000),  # etiketten sonra
    ("Suzuki Swift 2008 Kilometre: 85xxx Fiyat 2.300 stg", 85000),
    ("Peugeot 308 2012 120 *** km 4.250 STG", 120000),  # önündeki model numarası (308) katılmaz
    ("Suzuki Jimny 2008 4x4 120xxx km 2.300 stg", 120000),  # binlik birleştirme "4x4 120" yapmaz
    ("Suzuki Swift 2008 192***KM tel 0533 000 00 00 2.300 stg", 192000),
])
def test_masked_km_is_read_as_its_lower_bound(text, km):
    p = parse_freetext(text)
    assert (p.km, p.notes) == (km, ["km_alt_sinir"])
    assert p.year in (2008, 2012) and p.price_amount in (2300, 4250) and p.currency == "GBP"  # fiyat ve yıl aynen


@pytest.mark.parametrize("text,km", [
    ("Suzuki Swift 2008 model\nKm: 533 xxx xx xx\n2.300 stg", None),  # gizli telefon km etiketinin arkasında: km değil
    ("Suzuki Swift 2008 2.300 stg\nTel: 0533 xxx xx xx", None),  # gizli telefon: km birimi/etiketi yok
    ("Suzuki Swift 2008 *** km 2.300 stg", None),  # yıl gizli hanelerin önünde: 2.008.000 ya da 8.000 değil
    ("Suzuki Swift 2008 xxx km 2.300 stg", None),  # rakam yok
    ("Suzuki Swift 2008 99xxxx km 2.300 stg", None),  # 990.000 km: aralık dışı, bilinmiyor
    ("Suzuki Swift 2008 85xxxxx km 2.300 stg", None),  # 5 gizli hane: belirsiz
    ("Suzuki Swift 2008 192.** km 2.300 stg", None),  # ayraçtan sonra üç gizli hane değil: belirsiz
    ("Suzuki Swift 2008 192** km 2.300 stg", None),  # iki gizli hane: 19.200 mü 192.xxx mi belirsiz (düşük okumak yanlış 🟢 riski)
    ("Suzuki Swift 2008 85xxx mil 2.300 stg", None),  # mil km'ye çevrilmez
    ("Suzuki Swift 2008 85xxx km'de motor takıldı 2.300 stg", None),  # parça km'si (yazılı sayıyla aynı kural)
    ("Suzuki Swift 2008 150.000 km, motoru 85xxx km'de değişti 2.300 stg", 150000),  # ilk ve açık km
    ("Toyota Vitz 2014 96.000 km 5.500 stg", 96000),  # gizli hane yok: işaret yok
])
def test_masked_km_never_misreads_phones_years_or_ranges(text, km):
    p = parse_freetext(text)
    assert (p.km, p.notes) == (km, [])
    assert p.year in (2008, 2014) and p.price_amount in (2300, 5500)


def test_masked_price_is_not_unmasked():
    """Gizli hane açma yalnız km içindir: "2.xxx stg" fiyat sayılmaz (fiyat yok = ilan yok, eskisi gibi)."""
    assert parse_freetext("Suzuki Swift 2008 85xxx km fiyat 2.xxx stg") is None


@pytest.mark.parametrize("text", ["MERSEDES-BENZ C180 2014 12.000 stg", "MERCEDEZ-BENZ C180 2014 12.000 stg", "Mersedez-Benz C180 2014 12.000 stg"])
def test_hyphenated_misspelled_mercedes_keeps_the_real_model(text):
    r = parse_freetext(text)
    assert r.brand == "Mercedes-Benz" and r.model == "C180"


# Canlı denetim düzeltmeleri (27.10.2026) ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, model", [
    ("KKTC 05330000011 Renault Clio 2013 5500 stg", "Clio"),
    ("Tel 0533 000 00 12\nMazda demio 2002 140 bin tl", "demio"),
    ("0533 000 00 14\n2015 Toyota Corolla 1.6 otomatik 85.000 km 8500 stg", "Corolla"),
    ("2015 Toyota Corolla 1.6 otomatik 85.000 km 8500 stg 0533 000 00 14", "Corolla"),
])
def test_phone_before_the_brand_does_not_shift_the_model(text, model):
    p = parse_freetext(text)
    assert p.model == model and p.phone


@pytest.mark.parametrize("unit", ["60 BİN MİL", "60.000 MİL", "60 bin mil", "60.000 Mil"])
def test_uppercase_turkish_mil_is_miles_not_km(unit):
    assert parse_freetext(f"2022 Mercedes C 220 d {unit} 12.000 STG").km is None


def test_km_still_read_in_uppercase():
    assert parse_freetext("2022 Mercedes C 220 d 60 BİN KM 12.000 STG").km == 60000


@pytest.mark.parametrize("text, year", [
    ("Honda Civic 2000cc\n2015 model\n8000 stg", 2015),
    ("Hyundai Tucson 2000 cc 2015 8000 stg", 2015),
    ("Mercedes C200\nBenzin 1998 cc\n2009 model 5000 stg", 2009),
    ("Honda S2000\nYIL : 2001\n9000 stg", 2001),
    ("Suzuki SWIFT2018 MODEL 9000 stg", 2018),
])
def test_engine_size_and_model_name_are_not_the_year(text, year):
    assert parse_freetext(text).year == year


def test_engine_size_alone_gives_no_year():
    assert parse_freetext("Seat Leon cupra 2000cc 6250 stg") is None


@pytest.mark.parametrize("text, model", [
    ("TOYOTA YARIS CROSS 1.5 HYBRID 2021 £20.500", "Yaris Cross"),
    ("Toyota Corolla Cross 2022 £31.900", "Corolla Cross"),
    ("Toyota RAV 4 2015 15000 stg", "RAV4"),
    ("Toyota RAV-4 2015 15000 stg", "RAV4"),
    ("Honda WRV 2020 15000 stg", "WR-V"),
    ("Toyota Land Cruiser Prado 2023 £56.000", "Land Cruiser Prado"),
    ("Jeep Grand Cherokee 2012 9000 stg", "Grand Cherokee"),
    ("2012 Model Otomatik Mercedes - Benz Vito\nFiyat: 15.200 £", "Vito"),
    ("Mercedes - C220 CDI 2003 4350 STG", "C220"),
])
def test_multi_word_and_spaced_models(text, model):
    assert parse_freetext(text).model == model


@pytest.mark.parametrize("text, brand, model", [
    ("*2015 model *Motor 1.3 *Start stop *Smart stop *Koltuk ısıtma Toyota Vitz €6,400", "Toyota", "Vitz"),
    ("smart key 2016 Honda Fit 7000 stg", "Honda", "Fit"),
    ("Smart Fortwo 2015 5000 stg", "Smart", "Fortwo"),
    ("2006 model Wolswagen POLO 150.000 TL", "Volkswagen", "POLO"),
    ("Toyoto auris 2012 6000 stg", "Toyota", "auris"),
    ("LANDER ROVER FREELANDER 2008 5000 stg", "Land Rover", "FREELANDER"),
])
def test_smart_feature_is_not_a_brand_and_brand_typos(text, brand, model):
    p = parse_freetext(text)
    assert (p.brand, p.model) == (brand, model)


@pytest.mark.parametrize("text, price", [
    ("2019 Volkswagen Tiguan\nEski Fiyat: 21.000 £\nACİL SATILIK 18.000 £", 18000),
    ("2018 Mazda CX-5\nESKİ FİYAT £36.000\n£32.499", 32499),
    ("2016 Honda Fit Peşin Fiyat: 15.900 Stg", 15900),
    ("2016 Honda Fit\nNakit 12.000 stg", 12000),
    ("Range Rover Velar 2022\nFİYAT 39.500 STERLİN\nTAKASLI FİYATI:42.000 STERLİN", 39500),
])
def test_old_price_and_trade_in_price_are_not_the_price(text, price):
    assert parse_freetext(text).price_amount == price


@pytest.mark.parametrize("text", [
    "2001 Toyota Fielder Installment Plan Is Available= 1500STG Advance\nTotal Price 3200",
    "Mazda Demio 2010\nFİYAT : 2.500STG\nKAMPANYALI FİYAT 2.000STG",  # iki farklı fiyat: belirsiz kalır
    "Nissan Note 2015 7000 stg\n€6,900",
])
def test_ambiguous_or_advance_amounts_give_no_price(text):
    assert parse_freetext(text) is None


@pytest.mark.parametrize("text, amount, currency", [
    ("7900£\nMazda demio 2016 model\nOtomatik full paket benzin\n€7,900", 7900, "GBP"),
    ("5.500 STG Nissan Cube 2010\n€5,500", 5500, "GBP"),
    ("Nissan Note 2015 100000 TL\n€100,000", 100000, "TRY"),
])
def test_same_amount_marketplace_euro_box_is_an_echo(text, amount, currency):
    p = parse_freetext(text)
    assert (p.price_amount, p.currency, p.currency_guess) == (amount, currency, False)


@pytest.mark.parametrize("text", [
    "Mercedes C250 2012 TAKAS EDİLİR ICINE ARABA ALINIR £10,000",
    "2024 MAZDA CX-5 NIGHT EDITION İÇERİSİNE ARAÇ ALINIR 25000 stg",
    "GALERİDEN SATILIK! KİRALIK GEÇMİŞİ YOK! Ford Focus 2013 6000 stg",
])
def test_trade_in_and_no_rental_history_are_sale_ads(text):
    assert diagnose(text) == "ok"


@pytest.mark.parametrize("text", [
    "Araba alınır nakit ödeme", "Temiz dizel arac alinir €1,000", "2015 Toyota Corolla alınır 3000 stg",
    "Toyota Corolla 2015 3 gün içinde araç alınır 5000 stg",
])
def test_wanted_ads_still_skipped(text):
    assert diagnose(text) == "arac_degil"


@pytest.mark.parametrize("line, amount, currency", [
    ("fiatı 5.200 sterlindir", 5200, "GBP"), ("8500 stgdir", 8500, "GBP"), ("tamamdır.250.000 tl son fiyat", 250000, "TRY"),
    ("8.500.-stg", 8500, "GBP"), ("950.000.-TL", 950000, "TRY"),
])
def test_price_suffix_glued_period_and_dash(line, amount, currency):
    p = parse_freetext(f"2012 Toyota Corolla\n{line}")
    assert (p.price_amount, p.currency) == (amount, currency)


def test_directional_suffix_is_not_a_price():
    assert parse_freetext("2012 Toyota Corolla\n300.000 tlye kadar") is None


def test_phone_does_not_join_two_lines():
    """Telefonun önündeki satır sonu silinmez: takaslı fiyat satırı asıl fiyat satırıyla birleşip fiyatı belirsiz yapmıyor (27.10.2026)."""
    text = ("2014 Model C180 Benzin\nUygun fiyatli arac takasa alinir.takas fiyati.16.500 stg dir.\n0533 0000013 FİYAT:15.500 STG\n"
            "C180 Mercedes\n€15,500")
    p = parse_freetext(text)
    assert (p.price_amount, p.currency, p.model) == (15500, "GBP", None)  # model fiyat rakamı ("15") değil


@pytest.mark.parametrize("text, model", [("Mazda 3 2015 1.500 cc 9000 stg", "3"), ("BMW 3.18 2009 5500 stg", "3")])
def test_model_digit_is_kept_when_not_a_price(text, model):
    assert parse_freetext(text).model == model
