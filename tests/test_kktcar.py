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


# --- 09.10.2026: yeni ilanların sayfası (normal tarayıcıda da) "Araç Bulunamadı"; bilgi yalnız schema.org Vehicle verisinde (JSON-LD) ---
LD_ONLY = (Path(__file__).parent / "fixtures/kktcar_detail_jsonld_only.html").read_text()  # gerçek sayfadan kırpıldı; satıcı adı silindi


def test_page_without_visible_details_is_read_from_structured_data():
    d = parse_detail(LD_ONLY)
    assert (d["brand"], d["model"], d["year"], d["km"]) == ("Mazda", "Demio", 2013, 107000)
    assert (d["price_raw"], d["price_amount"], d["currency"], d["currency_guess"]) == ("6.000£", 6000, "GBP", False)  # başlıktan: yenilemede aynı yazım
    assert (d["fuel"], d["transmission"], d["engine_l"], d["location"]) == ("benzin", "otomatik", 1.3, "Lefkoşa")
    assert d["posted_at"].isoformat() == "2026-10-09T00:00:00+00:00" and d["urgency_signals"] is None
    assert d["raw_text"].startswith("Mazda Demio 2013 - 6.000£ - KKTCar\nTemiz, Bakımlı")
    assert d["seller_handle"] is None and "Satıcı Adı" not in str(d)  # satıcı adı okunmaz


def test_structured_data_only_for_listings_in_stock_and_only_when_the_page_is_empty():
    assert parse_detail(LD_ONLY.replace("schema.org/InStock", "schema.org/SoldOut")) is None  # satışta değil: bilinmiyor say
    assert parse_detail(LD_ONLY.replace('"@type": "Vehicle"', '"@type": "Product"')) is None
    assert parse_detail(LD_ONLY.replace("application/ld+json", "text/plain")) is None
    no_title_price = parse_detail(LD_ONLY.replace(" - 6.000£ - ", " - "))
    assert (no_title_price["price_amount"], no_title_price["currency"], no_title_price["price_raw"]) == (6000, "GBP", None)
    assert parse_detail(HTML)["seller_handle"]  # görünür sayfa okunabiliyorsa eskisi gibi (satıcı bağlantısı dahil)


def test_structured_fuel_and_gear_mapping():
    diesel = parse_detail(LD_ONLY.replace("schema.org/Gasoline", "schema.org/DieselFuel").replace("AutomaticTransmission", "ManualTransmission"))
    assert (diesel["fuel"], diesel["transmission"]) == ("dizel", "manuel")
    hybrid = parse_detail(LD_ONLY.replace("schema.org/Gasoline", "schema.org/HybridElectric"))
    assert hybrid["fuel"] == "hibrit"


# --- 14.10.2026: fiyatsız ilan artık "Fiyatı satıcıya sor" diyor ve Vehicle verisinde teklif (offers) yok: satışta, fiyatsız saklanır ---
ASK_SELLER = """<html><head><title>Mini Cooper 2009 - KKTCar</title>
<script type="application/ld+json">{"@context": "https://schema.org", "@type": "Vehicle", "brand": {"@type": "Brand", "name": "Mini"},
 "model": "Cooper", "vehicleModelDate": "2009", "fuelType": "https://schema.org/Gasoline", "datePosted": "2026-09-20T08:00:00Z",
 "description": "Temiz araç"}</script></head>
<body><div>Ana sayfa</div><div>Mini</div><div>Cooper</div><div>PRICE</div><div>Yıl</div><div>2009</div><div>Açıklama</div><div>Temiz araç</div>
<div>Keşfet</div><div>Benzer ilanlar</div><div>SIMILAR</div></body></html>"""


def page(price: str, similar: str) -> str:
    return ASK_SELLER.replace("PRICE", price).replace("SIMILAR", similar)


def test_ask_the_seller_listing_is_stored_without_price():
    d = parse_detail(page("Fiyatı satıcıya sor", "£6.000"))
    assert (d["brand"], d["model"], d["year"], d["fuel"]) == ("Mini", "Cooper", 2009, "benzin")
    assert d["price_amount"] is None and d["currency"] is None and d["urgency_signals"] == ["fiyatsiz"]
    assert d["posted_at"].isoformat() == "2026-09-20T00:00:00+00:00"


def test_missing_offer_is_not_read_as_for_sale_unless_the_listing_itself_asks_for_price():
    assert parse_detail(page("£6.000", "Fiyatı satıcıya sor")) is None  # yalnız benzer ilan kartı fiyatsız
    assert parse_detail(page("", "")) is None  # teklif yok, sayfa da fiyat sormuyor: bilinmiyor


# ---------- 16.11.2026 şablonu (gerçek sayfalardan kısaltılmış; satıcı adı değiştirildi) ----------
V2 = Path(__file__).parent / "fixtures"


def test_v2_archived_page_is_read_as_inactive():
    d = parse_detail((V2 / "kktcar_detail_archived_v2.html").read_text())
    assert (d["brand"], d["model"], d["year"], d["km"]) == ("Mercedes-Benz", "C 200", 2001, 250000)
    assert d["is_active"] is False and d["urgency_signals"] == ["arsiv"]
    assert (d["price_amount"], d["currency"], d["fuel"], d["transmission"], d["location"]) == (3300.0, "GBP", "dizel", "otomatik", "Girne")
    assert "sold_at" not in d and d["archive_dated"] is True  # arşiv (süre dolumu): satış tarihi yazılmaz


def test_v2_sold_page_keeps_sale_date():
    d = parse_detail((V2 / "kktcar_detail_sold_v2.html").read_text())
    assert (d["brand"], d["model"], d["year"], d["km"]) == ("Suzuki", "Swift", 2004, 201000)
    assert d["is_active"] is False and d["urgency_signals"] == ["satildi"]
    assert (d["price_amount"], d["currency"]) == (150000.0, "TRY")
    assert d["sold_at"] is not None and d["archive_dated"] is True


def test_v2_ask_price_active_page():
    d = parse_detail((V2 / "kktcar_detail_ask_price_v2.html").read_text())
    assert (d["brand"], d["model"], d["year"], d["km"], d["engine_l"]) == ("BMW", "X1", 2016, 100760, 1.8)
    assert d["price_amount"] is None and d["urgency_signals"] == ["fiyatsiz"] and d.get("is_active") is None
    assert (d["fuel"], d["transmission"], d["location"]) == ("benzin", "otomatik", "Girne")
    assert d["posted_at"].month == 8 and d["posted_at"].day == 1
    assert "Test Satıcı" not in d["raw_text"]  # satıcı adı okunmaz


def test_v2_active_page_with_price_and_unknown_price_shape():
    html = (V2 / "kktcar_detail_ask_price_v2.html").read_text()
    priced = parse_detail(html.replace("<p>Fiyatı satıcıya sor</p>", "<p>£9.500</p><p>£</p><p>9.500</p>"))
    assert (priced["price_amount"], priced["currency"], priced["urgency_signals"]) == (9500.0, "GBP", None)
    assert parse_detail(html.replace("<p>Fiyatı satıcıya sor</p>", "<p>Bilinmeyen</p>")) is None  # tanımadığımız biçim: okunmadı say


def test_v2_posted_relative_and_future_month():
    from datetime import datetime, timezone
    from infrastructure.collectors.kktcar import _v2_posted
    today = datetime(2026, 11, 16, tzinfo=timezone.utc)
    assert _v2_posted(["Bireysel satıcı · Girne · 3 gün önce eklendi"], today) == datetime(2026, 11, 13, tzinfo=timezone.utc)
    assert _v2_posted(["Galeri · Lefkoşa · 20 Aralık eklendi"], today) == datetime(2025, 12, 20, tzinfo=timezone.utc)
    assert _v2_posted(["Bireysel satıcı · Girne · bugün eklendi"], today) == today
    assert _v2_posted(["eklendi değil"], today) is None
