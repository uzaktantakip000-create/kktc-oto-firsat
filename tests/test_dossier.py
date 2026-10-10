"""İlan dosyası (application/dossier, 10.10.2026): bota atılan taranan-site linki → veritabanındaki ilanın dosyası. Sahte veritabanı; ağ yok.
Kimlikler/adresler uydurma."""
from datetime import timedelta

import pytest

from application import dossier
from domain.settings import Settings
from tests.test_evaluate import NOW, POOL, car

LINK = "https://www.kktcarabam.com/259593-toyota-vitz-girne-benzin-otomatik"


def listing(price=5000, **kw):
    base = car("t", price, brand="Toyota", model="Vitz", source_id=7, source_name="KKTCarabam", platform="web", location="Girne",
               price_amount=price, currency="GBP", url=LINK, posted_at=NOW - timedelta(days=3), seller_phone=None, seller_handle=None,
               seller_type="bilinmiyor", inactive_at=None, last_seen_at=NOW)
    return base | kw


class Repo:
    def __init__(self, found=None, history=(), others=None, twins=(), fb=0, sites=("kktcarabam.com",), checked=NOW):
        self.found, self.history, self.others, self._twins, self.fb, self.sites = found, list(history), others, list(twins), fb, sites
        self.checked = checked
        self.state, self.asked = {}, []

    def listing_by_link(self, canon, host, ids):
        self.asked.append((canon, host, ids))
        return self.found

    def scanned_site(self, host):
        return {"name": "KKTCarabam", "last_checked_at": self.checked} if host in self.sites else None

    def market_pool(self, days, keys=None):
        return POOL

    def price_history(self, listing_id):
        return self.history

    def seller_active_count(self, l):
        return self.others

    def twins(self, l):
        return self._twins

    def facebook_similar_count(self, brand, model, year):
        return self.fb

    def get_state(self, k, default=None):
        return self.state.get(k, default)

    def set_state(self, k, v):
        self.state[k] = v


@pytest.fixture(autouse=True)
def owner_settings(monkeypatch):
    import application.settings_store as st
    monkeypatch.setattr(st, "load_settings", lambda repo: Settings())


# --- link tanıma ---
@pytest.mark.parametrize("url,canon,ids", [
    (LINK, "kktcarabam.com/259593-toyota-vitz-girne-benzin-otomatik", ["259593"]),
    ("https://kibrisarabaal.com/ilan/3534-2019-model-otomatik-land-rover/?utm=x", "kibrisarabaal.com/ilan/3534-2019-model-otomatik-land-rover", ["3534"]),
    ("https://m.facebook.com/groups/405189333280604/permalink/2547920612340788/?mibextid=abc",
     "facebook.com/groups/405189333280604/permalink/2547920612340788", ["2547920612340788"]),
    ("https://kktcar.com/listing/2013-mercedes-benz-e-220d-magusa-4dscu).", "kktcar.com/listing/2013-mercedes-benz-e-220d-magusa-4dscu",
     ["2013-mercedes-benz-e-220d-magusa-4dscu"]),
])
def test_links_are_reduced_to_site_path_and_listing_number(url, canon, ids):
    assert dossier.parse_link(url) == (canon, canon.split("/")[0], ids)


def test_a_link_without_a_path_is_not_a_listing():
    assert dossier.parse_link("https://kktcar.com/") is None


# --- dosya içeriği ---
def test_cheap_listing_dossier_has_verdict_target_age_seller_and_comparables():
    out = dossier.handle(Repo(found=listing(), others=0), LINK, now=NOW)
    assert out.startswith("📂 İLAN DOSYASI\n2015 Toyota Vitz · £5.000")
    assert "🟢 FIRSAT" in out and "📊 Piyasa ortası" in out and "8 emsal" in out
    assert "🎯 %20 kâr sınırı" in out and "ilan fiyatı zaten altında" in out
    assert "🗓 İlan tarihi" in out and "(3 gündür yayında)" in out
    assert "bireysel görünüyor" in out and "Benzerleri (piyasayı kuranlardan en yakın 3)" in out


def test_expensive_listing_gets_the_price_it_should_be_bought_at():
    out = dossier.handle(Repo(found=listing(8400)), LINK, now=NOW)
    assert "➖ Fırsat değil" in out and "🎯 %20 kâr için en çok £" in out and "indirim gerekir" in out


def test_old_ad_price_history_dealer_and_facebook_count():
    hist = [{"changed_at": NOW - timedelta(days=5), "old_value": "6200.0", "new_value": "5800.0"},
            {"changed_at": NOW - timedelta(days=1), "old_value": "5800.0", "new_value": "5000.0"}]
    l = listing(posted_at=NOW - timedelta(days=90), seller_phone="905330000001")
    out = dossier.handle(Repo(found=l, history=hist, others=6, fb=3), LINK, now=NOW)
    assert "(90 gün): çok eski ilan, araç satılmış olabilir" in out
    assert "💸 Fiyat geçmişi: £6.200 → £5.800" in out and "→ £5.000" in out
    assert "aynı satıcının 6 aktif ilanı daha var → galeri/ticari olabilir" in out
    assert "Facebook gruplarında da 3 benzer ilan var" in out
    assert "905330000001" not in out  # telefon hiçbir zaman yazılmaz


def test_inactive_listing_says_so_at_the_top():
    l = listing(is_active=False, inactive_at=NOW - timedelta(days=2))
    lines = dossier.handle(Repo(found=l), LINK, now=NOW).split("\n")
    assert lines[3].startswith("⛔ Bu ilan") and "yayında görünmüyor" in lines[3]


def test_usd_listing_shows_the_original_price_and_unknown_market_is_honest():
    l = listing(currency="USD", price_amount=6500, model_norm="nadir", model="Nadir")
    out = dossier.handle(Repo(found=l), LINK, now=NOW)
    assert "(ilanda 6.500 USD)" in out and "Yeterli emsal yok" in out


def test_absurd_price_is_not_valued():
    out = dossier.handle(Repo(found=listing(1)), LINK, now=NOW)
    assert "Fiyat mantıksız" in out and "Piyasa ortası" not in out


def test_extra_queries_failing_do_not_stop_the_dossier():
    class Broken(Repo):
        def price_history(self, listing_id):
            raise RuntimeError("db")

        def twins(self, l):
            raise RuntimeError("db")

    assert "🟢 FIRSAT" in dossier.handle(Broken(found=listing()), LINK, now=NOW)


# --- bulunamayan link ---
def test_unknown_listing_on_a_scanned_site_says_not_seen_yet():
    assert dossier.handle(Repo(), LINK, now=NOW) == dossier.NOT_SEEN


def test_link_of_a_site_we_do_not_scan_falls_back_to_old_flow():
    assert dossier.handle(Repo(), "https://www.example.com/ilan/123", now=NOW) is None
    assert dossier.handle(Repo(), "merhaba, nasılsın", now=NOW) is None


def test_link_with_ad_text_falls_back_to_ad_check_when_not_found():
    assert dossier.handle(Repo(), f"2015 Toyota Vitz 80.000 km 5.000£ {LINK}", now=NOW) is None


def test_quota_is_shared_with_ad_check():
    repo = Repo(found=listing())
    repo.state[f"adcheck:{NOW:%Y-%m-%d}"] = "30"
    assert "sınırına ulaşıldı" in dossier.handle(repo, LINK, now=NOW)


def test_a_source_that_cannot_be_read_makes_the_listing_status_unknown():
    out = dossier.handle(Repo(found=listing(source_checked_at=NOW - timedelta(hours=30))), LINK, now=NOW)
    assert "ℹ️ KKTCarabam 30 saattir okunamıyor: ilanın hâlâ yayında olup olmadığını bilmiyorum" in out
    assert "okunamıyor" not in dossier.handle(Repo(found=listing(source_checked_at=NOW - timedelta(hours=1))), LINK, now=NOW)


def test_not_seen_on_an_unreadable_site_does_not_promise_two_hours():
    out = dossier.handle(Repo(checked=NOW - timedelta(hours=30)), LINK, now=NOW)
    assert "KKTCarabam 30 saattir okunamıyor" in out and "2 saat" not in out


@pytest.mark.parametrize("link", ["https://www.facebook.com/groups/123456789/", "https://www.instagram.com/kibrisoto/",
                                  "https://kibrisarabaal.com/", "https://www.kktcarabam.com/ikinci-el-araba",
                                  "https://www.facebook.com/groups/123456789/permalink/987654321/", "https://www.instagram.com/p/Cabc123/"])
def test_source_links_are_not_answered_with_not_seen(link):
    """Sahibin kaynak ekleme linki (grup, hesap, site; okumadığımız gruptaki gönderi) dosyaya takılmaz: eski akış (kaynak önerisi) cevaplar."""
    assert dossier.handle(Repo(sites=("facebook.com", "instagram.com", "kibrisarabaal.com", "kktcarabam.com")), link, now=NOW) is None


def test_quarantined_listing_is_flagged_above_the_verdict():
    out = dossier.handle(Repo(found=listing(karantina_nedeni="km_supheli")), LINK, now=NOW)
    assert "⚠️ Veri kontrolü bu ilanı şüpheli buldu (km makul değil)" in out
    assert out.index("şüpheli buldu") < out.index("🟢 FIRSAT")


def test_owner_personal_filters_do_not_turn_a_cheap_car_into_not_a_deal(monkeypatch):
    """/istemiyorum, /butce ve engellenen satıcı: bildirim filtresidir; bakılan ilanın fiyat yorumu aynı kalır, filtre ayrıca yazılır."""
    import application.settings_store as st
    monkeypatch.setattr(st, "load_settings", lambda repo: Settings(blocked_brands=["Toyota"], max_buy_gbp=4000, blocked_phones=["905330000009"]))
    out = dossier.handle(Repo(found=listing(seller_phone="905330000009")), LINK, now=NOW)
    assert "🟢 FIRSAT" in out and "Fırsat değil" not in out
    assert "/istemiyorum listende" in out and "Bütçenin (£4.000) üstünde" in out and "engellemiştin" in out
    assert "905330000009" not in out


def test_unreadable_price_history_value_does_not_stop_the_dossier():
    hist = [{"changed_at": NOW, "old_value": "", "new_value": "5000.0"}, {"changed_at": NOW, "old_value": "5000.0", "new_value": "x"}]
    assert "🟢 FIRSAT" in dossier.handle(Repo(found=listing(), history=hist), LINK, now=NOW)
