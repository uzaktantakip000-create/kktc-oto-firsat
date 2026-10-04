"""Adım 2b: KKTCar ve Mezunum toplayıcılarında süre bütçesi. Site yavaşlarsa tur 13 dk bütçesini/iş akışı 20 dk sınırını aşmasın
(değerlendirme hiç çalışmasın diye); süre yüzünden atlanan ilan "okunamadı" SAYILMAZ, kaynak yine "kontrol edildi" işaretlenir."""
from contextlib import nullcontext
from datetime import datetime, timezone

import pytest

from application import collect_kktcar as kk
from application import collect_mezunum as mz
from infrastructure.collectors import kktcar, mezunum


class Clock:
    """Sahte saat: her polite_sleep 60 sn ilerletir (yavaş site)."""
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Repo:
    def __init__(self, stale=()):
        self.stale, self.touched, self.applied, self.upserts, self.checked, self.state = list(stale), [], [], [], [], {}

    def stale_active(self, source_id, hours, limit):
        return self.stale

    def touch(self, i):
        self.touched.append(i)

    def apply_refresh(self, i, old, data):
        self.applied.append(i)

    def known_item_ids(self, source_id):
        return set()

    def mark_alive(self, source_id, item_ids):
        return 0

    def upsert_listing(self, source_id, item_id, data):
        self.upserts.append(item_id)
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


SOURCE = {"id": "s1", "name": "KKTCar"}
OPEN = {"price_amount": 9000.0, "currency": "GBP", "swap": None}


def sitemap(n):
    return [kktcar.SitemapEntry(f"https://x/{i}", f"slug{i}", datetime(2026, 10, 1, tzinfo=timezone.utc)) for i in range(n)]


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(kk, "gbp_rate", lambda currency: 1.0)
    monkeypatch.setattr(kktcar, "new_client", lambda: nullcontext())
    monkeypatch.setattr(kktcar, "polite_sleep", lambda: setattr(c, "now", c.now + 60))
    return c


def test_kktcar_new_listing_loop_stops_at_the_time_budget(monkeypatch, clock):
    monkeypatch.setattr(kktcar, "fetch_sitemap", lambda client: sitemap(20))
    monkeypatch.setattr(kktcar, "fetch_detail", lambda client, entry: dict(OPEN))
    repo = Repo()
    stats = kk.collect_kktcar(repo, SOURCE, clock=clock)
    assert stats.fetched == 3 and len(repo.upserts) == 3 and stats.time_limited  # 60 sn/ilan, bütçe 150 sn
    assert stats.failed == 0  # süre yüzünden atlananlar "okunamadı" sayılmadı (okuma oranı alarmı yok)
    assert repo.checked == [1]  # kaynak yine "kontrol edildi" işaretlendi


def test_kktcar_refresh_stops_at_the_budget_and_leaves_the_rest_untouched(monkeypatch, clock):
    monkeypatch.setattr(kktcar, "fetch_sitemap", lambda client: [])
    monkeypatch.setattr(kktcar, "fetch_detail", lambda client, entry: dict(OPEN))
    rows = [dict(id=f"r{i}", url=f"https://x/{i}", source_item_id=f"s{i}") for i in range(10)]
    repo = Repo(rows)
    stats = kk.collect_kktcar(repo, SOURCE, clock=clock)
    assert len(repo.applied) == 2 and stats.time_limited  # bütçe 90 sn: iki ilan (60 sn/ilan)
    assert repo.touched == []  # kalanlar sırada bekliyor, "okunamadı" gibi geriye atılmadı


def test_kktcar_refresh_read_rate_raises_when_template_changes(monkeypatch, clock):
    monkeypatch.setattr(kk, "REFRESH_SECONDS", 10_000)
    monkeypatch.setattr(kktcar, "fetch_sitemap", lambda client: [])
    monkeypatch.setattr(kktcar, "fetch_detail", lambda client, entry: None if int(entry.slug.removeprefix("s")) < 6 else dict(OPEN))
    rows = [dict(id=f"r{i}", url=f"https://x/{i}", source_item_id=f"s{i}") for i in range(10)]
    repo = Repo(rows)
    monkeypatch.setattr(kk.kktcar, "polite_sleep", lambda: None)  # süre sınırı bu testte devrede değil
    with pytest.raises(RuntimeError, match="yenileme"):
        kk.collect_kktcar(repo, SOURCE, clock=clock)
    assert repo.checked == [1] and len(repo.touched) == 6  # okunamayanlar sıra için touch edildi, kaynak işaretlendi


def test_mezunum_stops_at_the_time_budget(monkeypatch):
    c = Clock()

    class Resp:
        status_code = 200
        text = "<html></html>"

        def raise_for_status(self):
            pass

    class Client:
        def get(self, url, params=None):
            return Resp()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(mezunum, "new_client", lambda: Client())
    monkeypatch.setattr(mezunum, "polite_sleep", lambda: setattr(c, "now", c.now + 60))
    monkeypatch.setattr(mezunum, "parse_list", lambda html: [mezunum.Entry(f"araba-{i}", f"https://x/{i}") for i in range(12)])
    monkeypatch.setattr(mezunum, "parse_detail", lambda html: None)  # okunamayan sayfa: failed sayılır
    repo = Repo()
    stats = mz.collect_mezunum(repo, {"id": "m1", "name": "Mezunum"}, clock=c)
    assert stats.time_limited and stats.fetched < 12  # bütçe dolunca kalanlar sonraki tura bırakıldı
    assert repo.checked == [1]
