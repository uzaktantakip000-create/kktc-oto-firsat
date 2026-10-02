from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from application import collect_pazarkibris as cp
from application import collect_sitemap_site as css
from domain.duplicates import same_car
from infrastructure.collectors import kibriscars, pazarkibris, sahibindenarabakibris

FX = Path(__file__).parent / "fixtures"
KC = (FX / "kibriscars_detail_bmw1.html").read_text()
SA = (FX / "sahibindenarabakibris_detail_fit.html").read_text()
SA2 = (FX / "sahibindenarabakibris_detail_auris_aciklamada.html").read_text()
PK = (FX / "pazarkibris_list_cars.html").read_text()
NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


# --- kibriscars ---
def test_kibriscars_detail_fields():
    d = kibriscars.parse_detail(KC)
    assert (d["brand"], d["model"], d["year"], d["km"]) == ("BMW", "1 Serisi", 2019, 76000)
    assert (d["price_amount"], d["currency"], d["currency_guess"]) == (15250.0, "GBP", False)  # indirimli (güncel) fiyat, eski fiyat değil
    assert d["fuel"] == "benzin" and d["transmission"] == "otomatik" and d["engine_l"] == 1.6
    assert d["seller_phone"] == "905550000000" and d["posted_at"].date().isoformat() == "2026-09-29"
    assert d["negotiable"] is True and "APPLE CAR PLAY" in d["raw_text"] and "Benzer" not in d["raw_text"]
    assert d["steering"] is None  # sitede direksiyon alanı yok


def test_kibriscars_not_a_listing_and_no_price():
    assert kibriscars.parse_detail("<html><body>Ana sayfa</body></html>") is None
    d = kibriscars.parse_detail(KC.replace('class="main-price"', 'class="x"'))
    assert d["price_amount"] is None and d["urgency_signals"] == ["fiyatsiz"]


def test_kibriscars_model_cleanup():
    assert kibriscars._model("2017 Honda Fit Otomatik", "Honda", 2017) == "Fit"
    assert kibriscars._model("1999 Mercedes Benz C Serisi Otomatik", "Mercedes-Benz", 1999) == "C Serisi"
    assert kibriscars._model("2015 Mazda Demio 2", "Mazda", 2015) == "Demio"


def test_kibriscars_sitemap():
    class R:
        text = ("<urlset><url><loc>https://kibriscars.com/satilik-araba/</loc><lastmod>2026-09-29T11:08:58+00:00</lastmod></url>"
                "<url><loc>https://kibriscars.com/araba-ilani/2017-honda-fit-otomatik/</loc><lastmod>2026-07-08T07:14:59+00:00</lastmod></url></urlset>")

        def raise_for_status(self):
            pass

    class C:
        def get(self, url, timeout=0):
            return R()

    es = kibriscars.fetch_sitemap(C())
    assert [e.item_id for e in es] == ["2017-honda-fit-otomatik"] and es[0].lastmod.year == 2026


# --- sahibindenarabakibris ---
def test_sahibinden_detail_fields_and_unicode():
    d = sahibindenarabakibris.parse_detail(SA)
    assert (d["brand"], d["model"], d["year"], d["km"]) == ("Honda", "Fit", 2017, 69000)
    assert (d["price_amount"], d["currency"]) == (8000.0, "GBP")
    assert d["fuel"] == "benzin" and d["transmission"] == "otomatik" and d["engine_l"] == 1.3
    assert d["steering"] == "RHD" and d["location"] == "Lefkoşa" and d["seller_type"] == "bireysel"
    assert d["seller_phone"] == "905550000000" and d["posted_at"].year == 2026 and d["posted_at"].month == 5
    nfd = SA.replace("Dümen", "Dümen").replace("Sağ", "Sağ")  # sitedeki bazı ilanlar ayrışık Unicode
    assert sahibindenarabakibris.parse_detail(nfd)["steering"] == "RHD"


def test_sahibinden_year_and_price_from_description_marks_freetext():
    d = sahibindenarabakibris.parse_detail(SA2)
    assert (d["year"], d["price_amount"], d["currency"]) == (2012, 7000.0, "GBP")
    assert d["extraction_by"] == "parser_serbest" and d["seller_phone"] == "905550000000" and d["location"] is None


def test_sahibinden_price_on_request_is_listing_without_price():
    d = sahibindenarabakibris.parse_detail(SA2.replace("Fiyat : 7.000 STG", ""))
    assert d["price_amount"] is None and d["urgency_signals"] == ["fiyatsiz"] and d["year"] == 2012
    assert sahibindenarabakibris.parse_detail("<html><body>404</body></html>") is None


class _Resp:
    def __init__(self, status, url, text=""):
        self.status_code, self.url, self.text = status, url, text


class _Client:
    def __init__(self, resp):
        self.resp = resp

    def get(self, url, timeout=30):
        return self.resp


@pytest.mark.parametrize("mod,html,base,seg", [(kibriscars, KC, "https://kibriscars.com", "araba-ilani"),
                                                (sahibindenarabakibris, SA, "https://sahibindenarabakibris.com", "vehicle")])
def test_gone_only_on_404_or_redirect_away(mod, html, base, seg):
    e = mod.Entry(f"{base}/{seg}/x-y/", "x-y", None)
    assert mod.fetch_detail(_Client(_Resp(404, "x")), e)["urgency_signals"] == ["kaldirildi"]
    assert mod.fetch_detail(_Client(_Resp(200, base + "/", html)), e)["is_active"] is False  # ana sayfaya yönlendi
    assert mod.fetch_detail(_Client(_Resp(503, e.url)), e) is None  # geçici hata: tekrar denenir
    assert mod.fetch_detail(_Client(_Resp(200, e.url, "<html><body>yeni tasarım</body></html>")), e) is None  # şablon değişti: kaldırıldı sayılmaz


# --- ortak toplayıcı (site haritalı siteler) ---
class FakeRepo:
    def __init__(self, known=()):
        self.known, self.saved, self.state, self.deactivated = set(known), {}, {}, None

    def known_item_ids(self, sid):
        return set(self.known)

    def upsert_listing(self, sid, item_id, data):
        if item_id in self.saved or item_id in self.known:
            return False
        self.saved[item_id] = data
        return True

    def get_state(self, k, d=None):
        return self.state.get(k, d)

    def set_state(self, k, v):
        self.state[k] = v

    def mark_checked(self, *a, **k):
        pass

    def count_recent(self, sid):
        return 0

    def deactivate_missing(self, sid, present):
        self.deactivated = present
        return 3


class FakeSite:
    def __init__(self, entries, details):
        self.entries, self.details, self.fetched = entries, details, []

    def new_client(self):
        import contextlib
        return contextlib.nullcontext(self)

    def fetch_sitemap(self, c):
        return self.entries

    def fetch_detail(self, c, e):
        self.fetched.append(e.item_id)
        return self.details.get(e.item_id)

    def polite_sleep(self):
        pass


def _entries(n, old=0):
    es = [kibriscars.Entry(f"u{i}", f"new{i}", NOW - timedelta(days=i)) for i in range(n)]
    return es + [kibriscars.Entry(f"o{i}", f"old{i}", NOW - timedelta(days=400)) for i in range(old)]


def _detail(**kw):
    return {"brand": "Honda", "model": "Fit", "year": 2017, "km": 58000, "price_amount": 8900.0, "currency": "GBP", "posted_at": NOW - timedelta(days=2), **kw}


@pytest.fixture(autouse=True)
def _rate(monkeypatch):
    monkeypatch.setattr(css, "gbp_rate", lambda c: 1.0)


def test_collect_skips_old_sitemap_entries_and_stores_new():
    repo = FakeRepo()
    site = FakeSite(_entries(3, old=5), {f"new{i}": _detail() for i in range(3)})
    st = css.collect_sitemap_site(repo, {"id": "s", "name": "X"}, site, now=NOW)
    assert st.new == 3 and st.skipped_old == 5 and not any(f.startswith("old") for f in site.fetched)  # eski ilanlar hiç çekilmez
    d = repo.saved["new0"]
    assert d["price_gbp"] == 8900.0 and d["extraction_by"] == "parser" and d["photo_urls"] == [] and d["url"] == "u0"


def test_collect_old_posted_date_is_recorded_inactive_and_gone_is_inactive():
    repo = FakeRepo()
    site = FakeSite(_entries(2), {"new0": _detail(posted_at=NOW - timedelta(days=200)), "new1": {"is_active": False, "urgency_signals": ["kaldirildi"]}})
    st = css.collect_sitemap_site(repo, {"id": "s", "name": "X"}, site, now=NOW)
    assert st.new == 0 and st.skipped_old == 1 and st.deactivated == 1
    assert repo.saved["new0"]["is_active"] is False and repo.saved["new1"]["urgency_signals"] == ["kaldirildi"]


def test_collect_freetext_extraction_is_kept():
    repo = FakeRepo()
    css.collect_sitemap_site(repo, {"id": "s", "name": "X"}, FakeSite(_entries(1), {"new0": _detail(extraction_by="parser_serbest")}), now=NOW)
    assert repo.saved["new0"]["extraction_by"] == "parser_serbest"


def test_collect_read_rate_alarm_and_sitemap_shrink():
    site = FakeSite(_entries(8), {})  # hiçbiri okunamıyor (şablon değişti)
    with pytest.raises(RuntimeError, match="okunamadı"):
        css.collect_sitemap_site(FakeRepo(), {"id": "s", "name": "X"}, site, max_new=8, now=NOW)
    repo = FakeRepo()
    repo.state["sitemap_n:s"] = "1000"
    with pytest.raises(RuntimeError, match="küçüldü"):
        css.collect_sitemap_site(repo, {"id": "s", "name": "X"}, FakeSite(_entries(5), {}), now=NOW)
    assert repo.deactivated is None  # toplu pasifleştirme yapılmadı


def test_collect_deactivates_missing_only_for_big_sitemaps():
    repo = FakeRepo()
    st = css.collect_sitemap_site(repo, {"id": "s", "name": "X"}, FakeSite(_entries(150), {}), max_new=0, now=NOW)
    assert st.deactivated == 3 and len(repo.deactivated) == 150
    repo2 = FakeRepo()
    css.collect_sitemap_site(repo2, {"id": "s", "name": "X"}, FakeSite(_entries(20), {}), max_new=0, now=NOW)
    assert repo2.deactivated is None


# --- pazarkibris ---
def test_pazarkibris_list_parse():
    items, total = pazarkibris.parse_list_page(PK)
    assert total == 651 and [i["item_id"] for i in items] == ["00tn", "00t6", "00ax"]
    a, b, _ = items
    assert (a["price_amount"], a["currency"]) == (19000.0, "GBP") and a["seller_phone"] == "905550000000"
    assert a["url"] == "https://pazarkibris.com/v/bmw-520d-2019-dizel-otomatik-194000-km/00tn" and a["active"] and a["photo_urls"]
    assert b["price_amount"] is None and b["currency"] is None  # fiyatlı ilan ≈%5
    assert pazarkibris.parse_list_page("<html>engellendi</html>") == ([], 0)


def test_pazarkibris_collect(monkeypatch):
    monkeypatch.setattr(pazarkibris, "polite_sleep", lambda: None)
    monkeypatch.setattr("application.collect_mezunum.gbp_rate", lambda c: 1.0)

    class C:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def get(self, url, params=None):
            return _Resp(200, url, PK if (params or {}).get("page") is None else "<html>bos</html>")

    monkeypatch.setattr(pazarkibris, "new_client", lambda: C())
    repo = FakeRepo()
    st = cp.collect_pazarkibris(repo, {"id": "p", "name": "PazarKibris"}, None, now=NOW)
    assert st.new == 1 and st.no_price == 1 and st.stale == 1 and st.failed == 1
    d = repo.saved["00tn"]
    assert (d["brand"], d["model"], d["year"], d["km"], d["price_gbp"]) == ("BMW", "520d", 2019, 194000, 19000.0)
    assert d["extraction_by"] == "parser_serbest" and d["url"].endswith("/00tn") and d["seller_phone"] == "905550000000"
    assert repo.saved["00t6"]["urgency_signals"] == ["fiyatsiz"] and repo.saved["00ax"]["urgency_signals"] == ["eski"]
    assert repo.saved["00t6"]["is_active"] is False


def test_pazarkibris_all_pages_unreadable_raises(monkeypatch):
    monkeypatch.setattr(pazarkibris, "polite_sleep", lambda: None)

    class C:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def get(self, url, params=None):
            return _Resp(403, url, "")

    monkeypatch.setattr(pazarkibris, "new_client", lambda: C())
    with pytest.raises(RuntimeError, match="okunamadı"):
        cp.collect_pazarkibris(FakeRepo(), {"id": "p", "name": "PazarKibris"}, None, now=NOW)


# --- siteler arası mükerrer ---
def test_same_car_across_sites_by_phone_km_price():
    kc = kibriscars.parse_detail(KC)
    other = {"brand_norm": "BMW", "model_norm": "1", "year": 2019, "km": 76000, "seller_phone": kc["seller_phone"], "price_gbp": 15500.0}
    me = {"brand_norm": "BMW", "model_norm": "1", "year": 2019, "km": kc["km"], "seller_phone": kc["seller_phone"], "price_gbp": kc["price_amount"]}
    assert same_car(me, other)  # aynı telefon + aynı km: fiyat farkı olsa da aynı araç
    assert not same_car(me, {**other, "km": 87000, "seller_phone": None})  # farklı km: farklı araç
