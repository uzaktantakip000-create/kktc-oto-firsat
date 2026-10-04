"""Adım 5c (kısım 1): ilanın NE ZAMAN ve NEDEN pasifleştiği kaydedilir (listings.inactive_at / inactive_reason, migration 019).
'satildi' yalnız KKTCar/KibrisArabaAl sayfasının kendi söylediği ya da sahibin beyanı; arşiv/site haritasından düşme/süre dolumu 'belirsiz'.
KKTCar yenilemesine KibrisArabaAl'daki toplu "satıldı" freni de eklendi (Opus K6)."""
from contextlib import nullcontext

import pytest

from application import collect_kktcar as kk
from domain.lifecycle import inactive_reason
from infrastructure.collectors import kktcar as site
from infrastructure.db.repository import Repository


def test_reason_from_the_pages_own_signals():
    assert inactive_reason(["satildi"]) == "satildi"
    assert inactive_reason(["satildi", "fiyatsiz"]) == "satildi"
    assert inactive_reason(["kaldirildi"]) == "kaldirildi"
    assert inactive_reason(["arsiv"]) == "belirsiz" and inactive_reason(["arsiv", "fiyatsiz"]) == "belirsiz"
    assert inactive_reason(None) == "belirsiz" and inactive_reason([]) == "belirsiz"


class Conn:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))
        return self

    rowcount = 0


def repo():
    r = Repository.__new__(Repository)
    r.conn = Conn()
    return r


def test_apply_refresh_records_inactive_time_and_reason_without_overwriting():
    r = repo()
    r.apply_refresh("L1", {"price_amount": 1, "currency": "GBP", "price_gbp": 1}, {"is_active": False, "urgency_signals": ["arsiv"]})
    sql, params = r.conn.calls[0]
    assert "inactive_at=COALESCE(inactive_at, NOW())" in sql and "inactive_reason=COALESCE(inactive_reason, %s)" in sql
    assert params == (["arsiv"], "belirsiz", "L1")


def test_every_deactivation_path_records_a_reason_and_only_the_owner_or_the_page_can_say_sold():
    r = repo()
    r.deactivate_missing("S", {"a"})
    r.expire_unverifiable()
    r.deactivate_by_ilan_no("S", "123")
    r.mark_sold("L1")
    by = {name: sql for name, (sql, _) in zip(["missing", "expire", "ilan_no", "sold"], r.conn.calls)}
    for name in ("missing", "expire", "ilan_no", "sold"):
        assert "inactive_at" in by[name] and "inactive_reason" in by[name], name
    assert r.conn.calls[0][1][0] == "belirsiz" and r.conn.calls[1][1][0] == "belirsiz"  # sitemap'ten düşme / süre dolumu: belirsiz
    assert "inactive_reason='belirsiz'" in by["ilan_no"]  # Instagram "satıldı" yazısı bile 'satildi' sayılmaz
    assert "inactive_reason='satildi'" in by["sold"]


# --- KKTCar yenileme: toplu "satıldı/arşiv" freni (KibrisArabaal ile aynı kural) ---
SOURCE = {"id": "s1", "name": "KKTCar"}
CLOSED = {"is_active": False, "urgency_signals": ["satildi"]}
OPEN = {"price_amount": 9000.0, "currency": "GBP"}


class Repo:
    def __init__(self, stale=()):
        self.stale, self.applied, self.touched = list(stale), [], []

    def stale_active(self, source_id, hours, limit):
        return self.stale

    def apply_refresh(self, listing_id, old, data):
        self.applied.append(listing_id)
        return "pasif" if data.get("is_active") is False else None

    def touch(self, listing_id):
        self.touched.append(listing_id)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(site, "polite_sleep", lambda: None)
    monkeypatch.setattr(kk, "gbp_rate", lambda currency: 1.0)
    monkeypatch.setattr(site, "new_client", lambda: nullcontext())


def rows(n):
    return [dict(id=f"r{i}", url=f"https://kktcar.com/{i}", source_item_id=str(i)) for i in range(n)]


def detail(closed):
    return lambda client, entry: dict(CLOSED) if entry.slug in closed else dict(OPEN)


def test_kktcar_refresh_mass_removal_writes_nothing_for_the_closed_ones(monkeypatch):
    monkeypatch.setattr(site, "fetch_detail", detail({"0", "1", "2", "3", "4", "5"}))  # 10 okunanın 6'sı "satıldı"
    r, stats = Repo(rows(10)), kk.KktcarStats()
    kk.refresh_active(r, SOURCE, None, stats)
    assert r.applied == ["r6", "r7", "r8", "r9"] and stats.went_inactive == 0
    assert len(stats.suspect) == 1 and "10 ilandan 6'i" in stats.suspect[0]


def test_kktcar_refresh_few_removals_are_applied_as_before(monkeypatch):
    monkeypatch.setattr(site, "fetch_detail", detail({"2", "7"}))
    r, stats = Repo(rows(10)), kk.KktcarStats()
    kk.refresh_active(r, SOURCE, None, stats)
    assert len(r.applied) == 10 and stats.went_inactive == 2 and stats.suspect == [] and stats.refreshed == 10


def test_kktcar_refresh_too_few_samples_are_not_blocked_and_unreadable_pages_are_not_counted_as_closed(monkeypatch):
    monkeypatch.setattr(site, "fetch_detail", detail({"0", "1", "2", "3"}))  # 4 okunan, hepsi kapalı: örnek küçük
    r, stats = Repo(rows(4)), kk.KktcarStats()
    kk.refresh_active(r, SOURCE, None, stats)
    assert stats.went_inactive == 4 and stats.suspect == []
    monkeypatch.setattr(site, "fetch_detail", lambda client, entry: None)  # hiçbiri okunamadı: kapalı SAYILMAZ
    r, stats = Repo(rows(10)), kk.KktcarStats()
    kk.refresh_active(r, SOURCE, None, stats)
    assert r.applied == [] and r.touched == [f"r{i}" for i in range(10)] and stats.suspect == []


class CollectRepo(Repo):
    def __init__(self, stale=()):
        super().__init__(stale)
        self.upserts, self.checked = [], []

    def known_item_ids(self, source_id):
        return set()

    def upsert_listing(self, source_id, item_id, data):
        self.upserts.append((item_id, data))
        return True

    def get_state(self, key, default=None):
        return default

    def set_state(self, key, value):
        pass

    def mark_checked(self, *a, **k):
        self.checked.append(1)

    def count_recent(self, source_id):
        return 0

    def deactivate_missing(self, source_id, present):
        return 0


def test_suspect_removals_fail_the_round_after_everything_else_ran(monkeypatch):
    monkeypatch.setattr(site, "fetch_sitemap", lambda client: [])
    monkeypatch.setattr(site, "fetch_detail", detail({str(i) for i in range(6)}))
    r = CollectRepo(rows(10))
    with pytest.raises(RuntimeError, match="satıldı/kaldırıldı"):
        kk.collect_kktcar(r, SOURCE)
    assert r.checked == [1]  # kaynak "kontrol edildi" işaretlendi (bayat alarmı yerine gerçek hata mesajı)


def test_listing_born_closed_gets_a_reason_but_no_inactive_time(monkeypatch):
    entry = site.SitemapEntry("https://kktcar.com/x", "x", None)
    monkeypatch.setattr(site, "fetch_sitemap", lambda client: [entry])
    monkeypatch.setattr(site, "fetch_detail", lambda client, e: {"is_active": False, "urgency_signals": ["arsiv"], "price_amount": 5000.0, "currency": "GBP"})
    r = CollectRepo()
    kk.collect_kktcar(r, SOURCE)
    (item, data), = r.upserts
    assert item == "x" and data["inactive_reason"] == "belirsiz" and "inactive_at" not in data  # pasifleşme AN'ı bilinmiyor: boş kalır


# --- last_alive_at (Adım 5c kısım 2): kaynakta AKTİF görüldüğü son an ---

def test_active_page_read_marks_alive_but_a_failed_read_does_not():
    r = repo()
    r.apply_refresh("L1", {"price_amount": 6000, "currency": "GBP", "price_gbp": 6000},
                    {"price_amount": 6000, "currency": "GBP", "price_gbp": 6000, "price_raw": "6000", "currency_guess": False, "is_active": True})
    assert any("last_alive_at=NOW()" in sql for sql, _ in r.conn.calls)
    r = repo()
    r.touch("L1")  # okunamayan sayfa (toplayıcılar bunu çağırır): canlı görüldü SAYILMAZ
    assert not any("last_alive_at" in sql for sql, _ in r.conn.calls)
    r = repo()
    r.apply_refresh("L1", {"price_amount": 1, "currency": "GBP", "price_gbp": 1}, {"is_active": False, "urgency_signals": ["satildi"]})
    assert not any("last_alive_at" in sql for sql, _ in r.conn.calls)  # kapalı sayfa canlı değil


def test_list_collectors_mark_known_listings_seen_in_the_list_as_alive(monkeypatch):
    from application import collect_kktcarabam as kka
    from infrastructure.collectors import kktcarabam as kk_site

    class Card:
        def __init__(self, i):
            self.item_id = i

    class FeedRepo:
        def __init__(self):
            self.alive, self.upserts = [], []

        def known_item_ids(self, source_id):
            return {"a", "b"}

        def mark_alive(self, source_id, item_ids):
            self.alive.append((source_id, sorted(item_ids)))

        def upsert_listing(self, *a):
            self.upserts.append(a)
            return True

        def mark_checked(self, *a, **k):
            pass

        def count_recent(self, source_id):
            return 0

    monkeypatch.setattr(kk_site, "open_session", lambda: nullcontext())
    monkeypatch.setattr(kk_site, "fetch_html", lambda session, url: "<html/>")
    monkeypatch.setattr(kk_site, "parse_list", lambda html: [Card("a"), Card("c")])  # a bilinen, c yeni
    monkeypatch.setattr(kk_site, "card_to_listing", lambda card: None)
    r = FeedRepo()
    kka.collect_kktcarabam(r, {"id": "k1", "name": "KKTCarabam"})
    assert r.alive == [("k1", ["a"])]  # yalnız listede görülen BİLİNEN ilan; "b" listede yok → canlı sayılmaz
