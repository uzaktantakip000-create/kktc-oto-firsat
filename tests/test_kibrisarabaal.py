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
    def __init__(self, status, url, text="", headers=None):
        self.status_code, self.url, self.text, self.headers = status, url, text, headers or {}


class _Client:
    def __init__(self, *resps):
        self.resps, self.urls = list(resps), []

    def get(self, url, timeout=30):
        self.urls.append(url)
        return self.resps[0] if len(self.resps) == 1 else self.resps.pop(0)


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
    assert fetch_detail(_Client(_Resp(500, same)), _entry()) is None  # sunucu hatası: okunamadı (21.10.2026'ya kadar 503 de buradaydı)


def test_block_codes_raise_blocked_with_retry_after():
    """21.10.2026: 403/429/503 artık "okunamadı" değil Blocked: tur durur, 24 saat istek yok (application/kaa_gate)."""
    from datetime import timedelta

    import pytest

    from infrastructure.collectors.kibrisarabaal import Blocked, fetch_detail
    same = "https://kibrisarabaal.com/ilan/12345-mazda-demio"
    for code in (403, 429, 503):
        with pytest.raises(Blocked) as e:
            fetch_detail(_Client(_Resp(code, same, headers={"Retry-After": "7200"})), _entry())
        assert e.value.status == code and e.value.retry_after == timedelta(hours=2)


def test_redirect_away_from_the_listing_is_gone_without_requesting_the_target():
    """Kaldırılan ilan ana sayfaya yönlenir: hedef (ana sayfa) hiç istenmez (her istek sayılır ve aralıklanır)."""
    from infrastructure.collectors.kibrisarabaal import fetch_detail
    c = _Client(_Resp(301, _entry().url, headers={"location": "/"}))
    assert fetch_detail(c, _entry())["urgency_signals"] == ["kaldirildi"] and len(c.urls) == 1
    own = _entry().url + "/"  # ilanın kendi adresine (sondaki /) yönlenme izlenir
    c = _Client(_Resp(301, _entry().url, headers={"location": own}), _Resp(200, own, HTML))
    assert fetch_detail(c, _entry())["brand"] == "Mazda" and c.urls == [_entry().url, own]
    c = _Client(_Resp(302, _entry().url, headers={"location": "https://baska-site.example/ilan/12345-x"}))
    assert fetch_detail(c, _entry())["is_active"] is False and len(c.urls) == 1  # başka siteye: izlenmez


def test_baslik_yili_ile_alan_yili_celisirse_eski_yil_alinir():
    # Sayfada "Yıl: 2022" (plakasız aracın kayıt yılı) ama başlık "2013 Model Otomatik Nissan Juke"
    d = parse_detail((Path(__file__).parent / "fixtures/kibrisarabaal_detail_juke_year.html").read_text())
    assert d["year"] == 2013 and d["brand"] == "Nissan"


def test_location_is_only_the_part_after_the_long_dash():
    """Şehir yazmayan ilanda marka/modeldeki kısa tire şehir sanılmaz ("Mercedes - Benz SLK 200" → şehir "Benz SLK" değil)."""
    desc = "2015 Mazda Demio – Güzelyurt. KKTC"
    for text, want in [("2019 Mercedes - Benz SLK 200. KKTC", None), ("2020 Toyota C-HR Hybrid. KKTC", None), ("2021 Volkswagen T-Roc. KKTC", None),
                       ("2018 Mercedes - Benz C 200 – Girne. KKTC", "Girne"), ("2020 Toyota C-HR Hybrid – Lefkoşa. KKTC", "Lefkoşa")]:
        assert desc in HTML
        assert parse_detail(HTML.replace(desc, text))["location"] == want, text
