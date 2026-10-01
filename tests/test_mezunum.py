from pathlib import Path

from domain.freetext_parser import parse_freetext
from infrastructure.collectors.mezunum import parse_detail, parse_list

HTML = (Path(__file__).parent / "fixtures/mezunum_detail_citroen.html").read_text()


def test_detail_structured_fields_and_no_real_phone_in_fixture():
    d = parse_detail(HTML)
    assert (d["price_amount"], d["currency"], d["location"]) == (2000.0, "GBP", "İskele")
    assert d["posted_at"].year == 2026 and d["posted_at"].month == 8 and d["posted_at"].day == 21
    assert d["seller_phone"] == "905550000000"  # fixture'daki telefon sahte
    assert "2004 model 206bin km" in d["description"] and "£2.000" not in d["description"]


def test_free_text_plus_known_price_gives_a_listing():
    d = parse_detail(HTML)
    p = parse_freetext(d["title"] + "\n" + d["description"], known_price=(d["price_amount"], d["currency"]))
    assert (p.brand, p.model, p.year, p.price_amount, p.currency) == ("Citroen", "C5", 2004, 2000.0, "GBP")


def test_not_a_listing_page_and_list_parsing():
    assert parse_detail("<html><body>404</body></html>") is None
    html = ('<a href="https://mezunumsatiyorumkibris.com.tr/ilan/temiz-honda-fit">x</a>'
            '<a href="https://mezunumsatiyorumkibris.com.tr/ilan/temiz-honda-fit">y</a>'
            '<a href="https://mezunumsatiyorumkibris.com.tr/ilan-ver">z</a>')
    assert [e.slug for e in parse_list(html)] == ["temiz-honda-fit"]


def test_listing_data_uses_site_price_and_llm_fallback_only_when_price_matches(monkeypatch):
    from application import collect_mezunum as cm
    monkeypatch.setattr(cm, "gbp_rate", lambda c: 1.0)
    d = parse_detail(HTML)
    data, by_llm = cm.listing_data(d)
    assert by_llm is False and data["brand"] == "Citroen" and data["price_gbp"] == 2000.0 and data["extraction_by"] == "parser_serbest"
    assert data["seller_phone"] == "905550000000" and data["posted_at"].day == 21
    odd = {**d, "title": "Acil satılık araba", "description": "Temiz kullanılmış"}  # marka yok: kural okuyamaz
    assert cm.listing_data(odd) == (None, False)

    class R:
        last_error = None

        def __init__(self, fields):
            self.fields = fields

        def read(self, text):
            return self.fields

    import application.llm_reader as lr
    monkeypatch.setattr(lr, "gbp_rate", lambda c: 1.0)
    monkeypatch.setattr(cm, "listing_fields", lambda read: read)
    good = {"brand": "Honda", "model": "Fit", "year": 2012, "km": None, "steering": None, "price_amount": 2000.0, "currency": "GBP",
            "currency_guess": False, "price_gbp": 2000.0, "extraction_by": "llm"}
    data, by_llm = cm.listing_data(odd, R(good))
    assert by_llm is True and data["extraction_by"] == "llm"
    assert cm.listing_data(odd, R({**good, "price_amount": 999.0}))[0] is None  # fiyat sitenin kesin alanıyla uyuşmuyor: reddedilir
