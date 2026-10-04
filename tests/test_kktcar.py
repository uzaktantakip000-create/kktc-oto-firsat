from pathlib import Path

from infrastructure.collectors.kktcar import parse_detail

HTML = (Path(__file__).parent / "fixtures/kktcar_detail_bmw_x5.html").read_text()


def test_detail_parse():
    d = parse_detail(HTML)
    assert (d["brand"], d["model"], d["year"], d["km"]) == ("BMW", "X5", 2014, 156000)
    assert (d["price_amount"], d["currency"], d["currency_guess"]) == (25900, "GBP", False)
    assert d["fuel"] == "dizel" and d["transmission"] == "otomatik" and d["location"] == "Girne"
    assert d["negotiable"] is True and d["swap"] is False
    assert d["posted_at"].day == 30 and d["posted_at"].month == 9


def test_seller_handle_is_prefixed_and_only_on_active_pages():
    assert parse_detail(HTML)["seller_handle"] == "kktcar:59974999-2294-49b5-abf0-16f412ae6dd7"
    sold = (Path(__file__).parent / "fixtures/kktcar_detail_sold.html").read_text()
    d = parse_detail(sold)
    assert d is not None and not d.get("seller_handle")  # satılmış sayfada satıcı bağlantısı yok
    assert parse_detail(HTML.replace("/seller/59974999-2294-49b5-abf0-16f412ae6dd7", "/profil/x"))["seller_handle"] is None


def test_not_a_listing():
    assert parse_detail("<html><body>404</body></html>") is None


def test_sold_archive_page():
    d = parse_detail((Path(__file__).parent / "fixtures/kktcar_detail_sold.html").read_text())
    assert (d["brand"], d["model"], d["year"], d["km"]) == ("Mercedes-Benz", "CLA 180", 2022, 20000)
    assert (d["price_amount"], d["currency"]) == (33500, "GBP")
    assert d["is_active"] is False and d["urgency_signals"] == ["satildi"]


def test_archived_page():
    d = parse_detail((Path(__file__).parent / "fixtures/kktcar_detail_archived.html").read_text())
    assert (d["brand"], d["model"], d["year"], d["price_amount"]) == ("BMW", "118i", 2021, 19000)
    assert d["is_active"] is False and d["urgency_signals"] == ["arsiv"]


def test_archived_without_price_is_kept_as_marker():
    html = (Path(__file__).parent / "fixtures/kktcar_detail_archived.html").read_text()
    d = parse_detail(html.replace("Son ilan fiyatı", "Baska"))
    assert d["price_amount"] is None and "fiyatsiz" in d["urgency_signals"]


def test_try_price_in_title():
    html = (Path(__file__).parent / "fixtures/kktcar_detail_bmw_x5.html").read_text().replace("25.900£", "135.000₺")
    d = parse_detail(html)
    assert (d["price_amount"], d["currency"]) == (135000, "TRY")


def test_price_on_request_listing_is_stored_without_price():
    html = open("tests/fixtures/kktcar_detail_fiyat_sorunuz.html", encoding="utf-8").read()
    d = parse_detail(html)
    assert d is not None
    assert (d["brand"], d["model"], d["year"], d["km"]) == ("Toyota", "Passo", 2015, 12000)
    assert d["price_amount"] is None and d["urgency_signals"] == ["fiyatsiz"]


def test_engine_size_parsed_from_detail_page():
    from pathlib import Path
    html = Path(__file__).parent.joinpath("fixtures", "kktcar_detail_bmw_x5.html").read_text()
    assert parse_detail(html)["engine_l"] == 2.5
