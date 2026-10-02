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


class _Resp:
    def __init__(self, status, url, text=""):
        self.status_code, self.url, self.text = status, url, text


class _Client:
    def __init__(self, resp):
        self.resp = resp

    def get(self, url, timeout=30):
        return self.resp


def _entry():
    from infrastructure.collectors.kibrisarabaal import Entry
    return Entry("https://kibrisarabaal.com/ilan/12345-mazda-demio", "12345", None)


def test_removed_ad_redirecting_home_is_gone():
    from infrastructure.collectors.kibrisarabaal import fetch_detail
    assert fetch_detail(_Client(_Resp(200, "https://kibrisarabaal.com/", HTML)), _entry())["urgency_signals"] == ["kaldirildi"]
    assert fetch_detail(_Client(_Resp(404, "x")), _entry())["is_active"] is False


def test_template_change_is_unreadable_not_gone():
    from infrastructure.collectors.kibrisarabaal import fetch_detail
    same = "https://kibrisarabaal.com/ilan/12345-mazda-demio"
    assert fetch_detail(_Client(_Resp(200, same, "<html><body>yeni tasarım</body></html>")), _entry()) is None  # tekrar denenir
    assert fetch_detail(_Client(_Resp(200, same, HTML)), _entry())["brand"] == "Mazda"
    assert fetch_detail(_Client(_Resp(503, same)), _entry()) is None


def test_baslik_yili_ile_alan_yili_celisirse_eski_yil_alinir():
    # Sayfada "Yıl: 2022" (plakasız aracın kayıt yılı) ama başlık "2013 Model Otomatik Nissan Juke"
    d = parse_detail((Path(__file__).parent / "fixtures/kibrisarabaal_detail_juke_year.html").read_text())
    assert d["year"] == 2013 and d["brand"] == "Nissan"
