from pathlib import Path

from infrastructure.collectors.kibrisarabaal import parse_detail

HTML = (Path(__file__).parent / "fixtures/kibrisarabaal_detail_demio.html").read_text()


def test_detail_parse_structured_fields():
    d = parse_detail(HTML)
    assert (d["brand"], d["model"], d["year"], d["km"]) == ("Mazda", "Demio 1.3i", 2015, 109000)
    assert (d["price_amount"], d["currency"], d["currency_guess"]) == (7450.0, "GBP", False)
    assert d["fuel"] == "benzin" and d["transmission"] == "otomatik" and d["steering"] == "RHD"
    assert d["engine_l"] == 1.3 and d["location"] == "Güzelyurt"
    assert d["posted_at"].year == 2026 and d["posted_at"].month == 10 and d["posted_at"].day == 1
    assert d["seller_type"] == "bireysel"


def test_seller_phone_is_listing_button_not_site_header():
    assert parse_detail(HTML)["seller_phone"] == "905550000000"  # başlıktaki tel: site telefonudur


def test_description_in_raw_text():
    d = parse_detail(HTML)
    assert "buji değişti" in d["raw_text"] and "Benzer" not in d["raw_text"]


def test_not_a_listing_page():
    assert parse_detail("<html><body>Ana sayfa</body></html>") is None
