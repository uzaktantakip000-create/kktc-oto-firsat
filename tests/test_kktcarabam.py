from pathlib import Path

from infrastructure.collectors.kktcarabam import card_to_listing, parse_detail, parse_list

FIX = Path(__file__).parent / "fixtures"


def test_list_cards():
    cards = parse_list((FIX / "kktcarabam_list.html").read_text())
    assert len(cards) >= 10
    by_id = {c.item_id: c for c in cards}
    assert "263799" in by_id and by_id["263799"].price_text == "0 TL"
    assert any(c.price_text.endswith("GBP") for c in cards)


def test_detail_swift_without_price():
    cards = {c.item_id: c for c in parse_list((FIX / "kktcarabam_list.html").read_text())}
    d = parse_detail((FIX / "kktcarabam_detail_swift.html").read_text(), cards["263799"])
    assert (d["brand"], d["model"], d["year"], d["km"]) == ("Suzuki", "Swift", 2024, 69)
    assert d["fuel"] == "benzin" and d["transmission"] == "otomatik" and d["steering"] == "RHD"
    assert d["price_amount"] is None and "fiyatsiz" in d["urgency_signals"]
    assert d["posted_at"].month == 10 and d["posted_at"].day == 1
    assert d["seller_handle"] == "GÖKHAN TÜRK MOTORS" and d["seller_type"] == "galeri"
    assert d["location"] == "Lefkoşa / Küçük Kaymaklı"


def test_card_only_listing():
    cards = {c.item_id: c for c in parse_list((FIX / "kktcarabam_list.html").read_text())}
    d = card_to_listing(cards["263800"])
    assert (d["brand"], d["model"], d["year"], d["fuel"], d["location"]) == ("Mercedes-Benz", "E Serisi", 2013, "dizel", "girne")
    assert (d["price_amount"], d["currency"], d["transmission"]) == (14999, "GBP", "otomatik")
    assert "fiyatsiz" in card_to_listing(cards["263799"])["urgency_signals"]
