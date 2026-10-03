"""Adım 2a: KibrisArabaAl'da toplu "satıldı/kaldırıldı" koruması. Site yönlendirmeyi/şablonu değiştirir ya da botu engellerse okunan
ilanların çoğu aynı anda "kapalı" görünür; bu ilanlar pasifleştirilirse "satıldı" işareti emsale girer. Yarısı kapanıyorsa hiçbiri yazılmaz."""
from contextlib import nullcontext

import pytest

from application import collect_kibrisarabaal as kaa
from application.safeguards import removed_message, removed_rate_suspect
from infrastructure.collectors import kibrisarabaal as site

SOURCE = {"id": "s1", "name": "KibrisArabaAl"}
GONE = {"is_active": False, "urgency_signals": ["kaldirildi"]}
OPEN = {"price_amount": 9000.0, "currency": "GBP"}


class Repo:
    def __init__(self, stale=(), known=()):
        self.stale, self.known = list(stale), set(known)
        self.applied, self.touched, self.upserts, self.checked, self.state = [], [], [], [], {}

    def stale_active(self, source_id, hours, limit):
        return self.stale

    def apply_refresh(self, listing_id, old, data):
        self.applied.append(listing_id)
        return "pasif" if data.get("is_active") is False else None

    def touch(self, listing_id):
        self.touched.append(listing_id)

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
        return 0


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(site, "polite_sleep", lambda: None)
    monkeypatch.setattr(kaa, "gbp_rate", lambda currency: 1.0)
    monkeypatch.setattr(site, "new_client", lambda: nullcontext())


def test_thresholds():
    assert not removed_rate_suspect(4, 4)  # az örnek: güvenilmez
    assert not removed_rate_suspect(10, 4) and removed_rate_suspect(10, 5) and removed_rate_suspect(25, 25)
    assert "bu ilanlar pasifleştirilmedi" in removed_message("KAA", 10, 6)


def rows(n):
    return [dict(id=f"r{i}", url=f"https://x/{i}", source_item_id=str(i)) for i in range(n)]


def detail(closed_ids):
    return lambda client, entry: dict(GONE) if entry.item_id in closed_ids else dict(OPEN)


def test_refresh_mass_removal_writes_nothing_for_the_closed_ones(monkeypatch):
    monkeypatch.setattr(site, "fetch_detail", detail({"0", "1", "2", "3", "4", "5"}))  # 10 okunan ilanın 6'sı "kapalı"
    repo, stats = Repo(rows(10)), kaa.KaaStats()
    kaa.refresh_active(repo, SOURCE, None, stats)
    assert repo.applied == ["r6", "r7", "r8", "r9"]  # yalnız açık görünenler işlendi; kapalı görünenlere dokunulmadı
    assert stats.went_inactive == 0 and len(stats.suspect) == 1 and "10 ilandan 6'i" in stats.suspect[0]


def test_refresh_few_removals_are_applied_as_before(monkeypatch):
    monkeypatch.setattr(site, "fetch_detail", detail({"2", "7"}))  # 10 ilanın 2'si gerçekten kapanmış
    repo, stats = Repo(rows(10)), kaa.KaaStats()
    kaa.refresh_active(repo, SOURCE, None, stats)
    assert len(repo.applied) == 10 and stats.went_inactive == 2 and stats.suspect == []


def test_refresh_too_few_samples_are_not_blocked(monkeypatch):
    monkeypatch.setattr(site, "fetch_detail", detail({"0", "1", "2", "3"}))  # 4 ilan, hepsi kapalı: örnek küçük
    repo, stats = Repo(rows(4)), kaa.KaaStats()
    kaa.refresh_active(repo, SOURCE, None, stats)
    assert stats.went_inactive == 4 and stats.suspect == []


def entries(n):
    return [site.Entry(f"https://x/{i}", str(i), None) for i in range(1, n + 1)]


def test_new_entries_mass_removal_is_not_recorded_and_the_round_fails_after_everything_else(monkeypatch):
    monkeypatch.setattr(site, "fetch_sitemap", lambda client: entries(10))
    monkeypatch.setattr(site, "fetch_detail", detail({"1", "2", "3", "4", "5", "6"}))  # 10 yeni ilanın 6'sı kapalı görünüyor
    repo = Repo()
    with pytest.raises(RuntimeError, match="satıldı/kaldırıldı"):
        kaa.collect_kibrisarabaal(repo, SOURCE)
    assert sorted(i for i, active in repo.upserts) == ["10", "7", "8", "9"] and all(a is None for _, a in repo.upserts)  # kapalılar yazılmadı
    assert repo.checked == [1]  # kaynak "kontrol edildi" olarak işaretlendi (bayat alarmı yerine gerçek hata mesajı)


def test_new_entries_few_removals_recorded_as_before(monkeypatch):
    monkeypatch.setattr(site, "fetch_sitemap", lambda client: entries(10))
    monkeypatch.setattr(site, "fetch_detail", detail({"3", "9"}))
    repo = Repo()
    stats = kaa.collect_kibrisarabaal(repo, SOURCE)
    assert stats.deactivated == 2 and stats.new == 8 and sorted(a for _, a in repo.upserts if a is False) == [False, False]
