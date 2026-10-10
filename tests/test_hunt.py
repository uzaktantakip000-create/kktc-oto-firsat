"""🎯 Av modu (application/hunt, 11.10.2026): kalıcı arama kur/listele/sil; turda yalnız kurulduktan ve son turdan SONRA ilk kez görülen,
taze, avın filtresine uyan ilanlar bir kez yazılır. Sahte veritabanı; ağ yok."""
import json
from datetime import timedelta

import pytest

from application import hunt, search_cmd
from domain.settings import Settings
from tests.test_evaluate import NOW, POOL
from tests.test_search_cmd import BOOK, listing

OWNER = "1"


class Repo:
    """search_active: marka/model/fiyat/görülme aralığı süzgeci gerçek sorgu gibi (yıl/km/vites bu testlerde kullanılmıyor)."""

    def __init__(self, rows=()):
        self.rows, self.state, self.asked = list(rows), {}, []

    def search_active(self, brand, model, y1, y2, p1, p2, km, t, limit, seen_after=None, seen_until=None):
        self.asked.append((brand, model, p2, seen_after, seen_until))
        return [r for r in self.rows if r["brand_norm"] == brand and (model is None or r["model_norm"] == model)
                and (p2 is None or r["price_gbp"] <= p2) and (seen_after is None or r["first_seen_at"] > seen_after)
                and (seen_until is None or r["first_seen_at"] <= seen_until)][:limit]

    def market_pool(self, days, keys=None):
        return POOL

    def resurfaced_kktcarabam(self, ids, gap_minutes=None):
        return set()

    def get_state(self, k, default=None):
        return self.state.get(k, default)

    def set_state(self, k, v):
        self.state[k] = v


@pytest.fixture(autouse=True)
def book(monkeypatch):
    import application.evaluate as ev
    monkeypatch.setattr(search_cmd, "load_book", lambda repo: BOOK)
    monkeypatch.setattr(ev, "load_book", lambda repo: BOOK)
    monkeypatch.setattr(search_cmd, "gbp_rate", lambda c: 1.0)


def new(i, price, minutes_ago=5, **kw):
    return listing(i, price, first_seen_at=NOW - timedelta(minutes=minutes_ago)) | {"posted_at": None} | kw


def test_setting_up_a_hunt_saves_the_filters_and_shows_what_matches_now():
    repo = Repo([new("a", 5000, minutes_ago=600)])
    out = hunt.handle(repo, OWNER, " vitz 2015 7000", Settings(), NOW - timedelta(minutes=30))
    assert out.startswith("🎯 Av #1 kuruldu: Toyota Vitz · 2015 · en çok £7.000") and "Şu an uyanlar:" in out and "🟢 2015 Vitz" in out
    (h,) = json.loads(repo.state["avlar:1"])
    assert (h["brand"], h["model"], h["y1"], h["y2"], h["p2"]) == ("Toyota", "vitz", 2015, 2015, 7000)
    assert "1) Toyota Vitz · 2015 · en çok £7.000" in hunt.handle(repo, OWNER, "liste", Settings(), NOW)
    assert "diye bir araç bulamadım" in hunt.handle(repo, OWNER, "zzqx 2015", Settings(), NOW)
    assert hunt.handle(repo, OWNER, "", Settings(), NOW).startswith("🎯 Av modu")


def test_delete_one_or_all_and_the_limit():
    repo = Repo()
    for _ in range(hunt.MAX_HUNTS):
        hunt.handle(repo, OWNER, "vitz", Settings(), NOW)
    assert "En çok 10 av" in hunt.handle(repo, OWNER, "fit", Settings(), NOW)
    assert "Silindi" in hunt.handle(repo, OWNER, "sil 2 3", Settings(), NOW) and len(hunt.load(repo, OWNER)) == 8
    assert "Hangi av?" in hunt.handle(repo, OWNER, "sil 99", Settings(), NOW)
    assert hunt.handle(repo, OWNER, "sil hepsi", Settings(), NOW) == "🎯 8 av silindi." and hunt.load(repo, OWNER) == []


def test_turn_sends_only_new_matching_listings_once():
    repo = Repo([new("eski", 5000, minutes_ago=90), new("a", 5000), new("pahali", 9000), new("fit", 5000, brand_norm="Honda", model_norm="fit")])
    hunt.handle(repo, OWNER, "vitz 7000", Settings(), NOW - timedelta(hours=2))  # 2 saat önce kuruldu
    repo.state[hunt.LAST_KEY] = (NOW - timedelta(minutes=15)).isoformat()
    sent = []
    assert hunt.run(repo, OWNER, sent.append, Settings(), NOW) == 1
    (msg,) = sent
    assert msg.startswith("🎯 AV #1 · Toyota Vitz · en çok £7.000: yeni ilan\n🟢 2015 Vitz · 80.000 km · £5.000")
    assert "kibrisarabaal.com/ilan/a-x/" in msg and "eski" not in msg and "pahali" not in msg
    assert repo.state[hunt.LAST_KEY] == NOW.isoformat()
    assert hunt.run(repo, OWNER, sent.append, Settings(), NOW + timedelta(minutes=15)) == 0 and len(sent) == 1  # aynı ilan ikinci kez gitmez


def test_listings_seen_before_the_hunt_was_set_up_are_not_sent():
    repo = Repo([new("a", 5000, minutes_ago=10)])
    hunt.handle(repo, OWNER, "vitz", Settings(), NOW - timedelta(minutes=5))  # kurulurken zaten listelendi
    repo.state[hunt.LAST_KEY] = (NOW - timedelta(minutes=15)).isoformat()
    sent = []
    assert hunt.run(repo, OWNER, sent.append, Settings(), NOW) == 0 and sent == []


def test_stale_listing_and_blocked_seller_are_skipped_and_no_hunts_costs_one_write():
    repo = Repo([new("eski_ilan", 5000, posted_at=NOW - timedelta(days=30)), new("engelli", 5000, seller_phone="905330000009")])
    hunt.handle(repo, OWNER, "vitz", Settings(), NOW - timedelta(hours=1))
    repo.state[hunt.LAST_KEY] = (NOW - timedelta(minutes=15)).isoformat()
    sent = []
    assert hunt.run(repo, OWNER, sent.append, Settings(blocked_phones=["905330000009"]), NOW) == 0
    empty = Repo([new("a", 5000)])
    assert hunt.run(empty, OWNER, sent.append, Settings(), NOW) == 0 and empty.asked == [] and hunt.LAST_KEY in empty.state


def test_many_new_listings_in_one_turn_are_capped():
    repo = Repo([new(f"x{i}", 5000 + i * 10) for i in range(8)])
    hunt.handle(repo, OWNER, "vitz", Settings(), NOW - timedelta(hours=1))
    repo.state[hunt.LAST_KEY] = (NOW - timedelta(minutes=15)).isoformat()
    sent = []
    hunt.run(repo, OWNER, sent.append, Settings(), NOW)
    assert "8 yeni ilan, en iyi 5'i" in sent[0] and sent[0].count("kibrisarabaal.com/ilan/") == 5
