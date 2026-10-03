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


# --- Adım 2d: geçici okuma hatası kalıcı "araç değil" damgası olmasın ---

ODD = {"title": "Acil satılık araba", "description": "Temiz kullanılmış", "price_amount": 2000.0, "currency": "GBP",
       "posted_at": None, "location": None, "seller_phone": None}


class Reader:
    def __init__(self, read=None, last_error=None):
        self._read, self.last_error = read, last_error

    def read(self, text):
        return self._read


def test_unreadable_without_llm_or_on_transient_error_is_retried_not_marked(monkeypatch):
    from application import collect_mezunum as cm
    assert cm.read_listing(ODD, None)[2] == "retry"  # yapay zekâ anahtarı yok
    assert cm.read_listing(ODD, Reader(None, "günlük yapay zekâ bütçesi doldu"))[2] == "retry"
    assert cm.read_listing(ODD, Reader(None, "HTTP 503"))[2] == "retry"  # ağ/API hatası


def test_llm_answered_but_unusable_is_marked_with_the_right_reason(monkeypatch):
    from application import collect_mezunum as cm
    assert cm.read_listing(ODD, Reader(None, "çıktı geçersiz"))[2] == "okunamadi"  # cevap geldi ama okunamadı
    monkeypatch.setattr(cm, "listing_fields", lambda read: None)
    assert cm.read_listing(ODD, Reader(object()))[2] == "arac_degil"  # yapay zekâ okudu: araç değil/satılmış/kredi-peşinat
    monkeypatch.setattr(cm, "listing_fields", lambda read: {"price_amount": 999.0, "currency": "GBP"})
    assert cm.read_listing(ODD, Reader(object()))[2] == "okunamadi"  # fiyat sitenin kesin alanıyla uyuşmuyor


def test_collect_marks_only_final_outcomes(monkeypatch):
    from application import collect_mezunum as cm
    from infrastructure.collectors import mezunum as site

    class Repo:
        def __init__(self):
            self.upserts, self.checked = {}, []

        def known_item_ids(self, source_id):
            return set()

        def upsert_listing(self, source_id, item_id, data):
            self.upserts[item_id] = data.get("urgency_signals")
            return True

        def mark_checked(self, *a, **k):
            self.checked.append(1)

        def count_recent(self, source_id):
            return 0

    class Resp:
        status_code, text = 200, "x"

        def raise_for_status(self):
            pass

    class Client:
        def get(self, url, params=None):
            return Resp()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    statuses = iter(["retry", "arac_degil", "okunamadi"])  # sırayla: a, b, c
    pages = iter([[site.Entry(s, f"https://x/{s}") for s in "abc"], []])  # 1. sayfa üç ilan, 2. sayfa boş
    monkeypatch.setattr(site, "new_client", lambda: Client())
    monkeypatch.setattr(site, "polite_sleep", lambda: None)
    monkeypatch.setattr(site, "parse_list", lambda html: next(pages))
    monkeypatch.setattr(site, "parse_detail", lambda html: dict(ODD))
    monkeypatch.setattr(cm, "read_listing", lambda detail, reader=None: (None, False, next(statuses)))
    repo = Repo()
    stats = cm.collect_mezunum(repo, {"id": "m", "name": "Mezunum"})
    assert repo.upserts == {"b": ["arac_degil"], "c": ["okunamadi"]}  # geçici olan ('a') hiç işaretlenmedi
    assert stats.unread == 1 and stats.not_car == 2
