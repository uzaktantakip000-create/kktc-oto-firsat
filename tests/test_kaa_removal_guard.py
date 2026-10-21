"""Adım 2a: KibrisArabaAl'da toplu "satıldı/kaldırıldı" koruması. Site yönlendirmeyi/şablonu değiştirir ya da botu engellerse okunan
ilanların çoğu aynı anda "kapalı" görünür; bu ilanlar pasifleştirilirse "satıldı" işareti emsale girer. Yarısı kapanıyorsa hiçbiri yazılmaz.
(21.10.2026: aktif ilanların yeniden okunması (refresh_active) kaldırıldı; koruma yeni ilanlarda sürüyor. Testler izin kapısı açık, sahte
istemciyle: siteye istek yok.)"""
import pytest

from application import collect_kibrisarabaal as kaa
from application.safeguards import removed_message, removed_rate_suspect
from infrastructure.collectors import kibrisarabaal as site

SOURCE = {"id": "s1", "name": "KibrisArabaAl"}
GONE = {"is_active": False, "urgency_signals": ["kaldirildi"]}
OPEN = {"price_amount": 9000.0, "currency": "GBP"}


class Repo:
    def __init__(self, known=()):
        self.known = set(known)
        self.upserts, self.checked, self.state, self.missing = [], [], {}, []

    def known_item_ids(self, source_id):
        return self.known

    def upsert_listing(self, source_id, item_id, data):
        self.upserts.append((item_id, data.get("is_active")))
        return True

    def mark_checked(self, *a, **k):
        self.checked.append(1)

    def count_recent(self, source_id):
        return 0

    def get_state(self, key, default=None):
        return self.state.get(key, default)

    def set_state(self, key, value):
        self.state[key] = value

    def deactivate_missing(self, source_id, present):
        self.missing.append(set(present))
        return 0


class NoNetwork:
    """Sahte istemci: toplayıcının site fonksiyonları sahte olduğundan hiç çağrılmamalı."""

    def get(self, *a, **k):
        raise AssertionError("siteye istek atılmamalıydı")


@pytest.fixture(autouse=True)
def offline(monkeypatch, kaa_open):
    monkeypatch.setattr(kaa, "gbp_rate", lambda currency: 1.0)


def collect(repo, **kw):
    return kaa.collect_kibrisarabaal(repo, SOURCE, client=NoNetwork(), sleep=lambda s: None, **kw)


def test_thresholds():
    assert not removed_rate_suspect(4, 4)  # az örnek: güvenilmez
    assert not removed_rate_suspect(10, 4) and removed_rate_suspect(10, 5) and removed_rate_suspect(25, 25)
    assert "bu ilanlar pasifleştirilmedi" in removed_message("KAA", 10, 6)


def detail(closed_ids):
    return lambda client, entry: dict(GONE) if entry.item_id in closed_ids else dict(OPEN)


def entries(n):
    return [site.Entry(f"https://kibrisarabaal.com/ilan/{i}-x", str(i), None) for i in range(1, n + 1)]


def test_new_entries_mass_removal_is_not_recorded_and_the_round_fails_after_everything_else(monkeypatch):
    monkeypatch.setattr(site, "fetch_sitemap", lambda client: entries(10))
    monkeypatch.setattr(site, "fetch_detail", detail({"1", "2", "3", "4", "5", "6"}))  # 10 yeni ilanın 6'sı kapalı görünüyor
    repo = Repo()
    with pytest.raises(RuntimeError, match="satıldı/kaldırıldı"):
        collect(repo)
    assert sorted(i for i, active in repo.upserts) == ["10", "7", "8", "9"] and all(a is None for _, a in repo.upserts)  # kapalılar yazılmadı
    assert repo.checked == [1]  # kaynak "kontrol edildi" olarak işaretlendi (bayat alarmı yerine gerçek hata mesajı)


def test_new_entries_few_removals_recorded_as_before(monkeypatch):
    monkeypatch.setattr(site, "fetch_sitemap", lambda client: entries(10))
    monkeypatch.setattr(site, "fetch_detail", detail({"3", "9"}))
    repo = Repo()
    stats = collect(repo)
    assert stats.deactivated == 2 and stats.new == 8 and sorted(a for _, a in repo.upserts if a is False) == [False, False]


def test_removals_come_from_the_sitemap_not_from_rereading_active_listings(monkeypatch):
    """Satılan/kaldırılan ilan: haritadan düşünce deactivate_missing (bilinen ilanlar yeniden İSTENMEZ)."""
    monkeypatch.setattr(site, "fetch_sitemap", lambda client: entries(600))
    asked = []
    monkeypatch.setattr(site, "fetch_detail", lambda client, entry: asked.append(entry.item_id) or dict(OPEN))
    repo = Repo(known={str(i) for i in range(1, 601)})  # hepsi zaten kayıtlı
    stats = collect(repo)
    assert asked == [] and stats.fetched == 0  # aktif ilanlar yeniden okunmuyor (eski refresh_active günde ~2.000 istek)
    assert repo.missing == [{str(i) for i in range(1, 601)}]  # haritadakiler dışındakiler pasifleşir


def test_unreadable_new_listing_is_tried_at_most_twice(monkeypatch):
    """Her ilan bir kez okunur; okunamayan (5xx/şablon) yalnız bir kez daha denenir, sonra bir daha istenmez."""
    monkeypatch.setattr(site, "fetch_sitemap", lambda client: entries(1))
    asked = []
    monkeypatch.setattr(site, "fetch_detail", lambda client, entry: asked.append(entry.item_id))
    repo = Repo()
    for _ in range(4):
        collect(repo)
    assert asked == ["1", "1"]
