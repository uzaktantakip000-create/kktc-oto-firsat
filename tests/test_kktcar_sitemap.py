"""KKTCar site haritası (08.10.2026 değişikliği): sitemap.xml artık dizin; ilanlar /sitemaps/sitemap/listings.xml'de, yalnız yayındakiler.
Örnekler gerçek siteden (tests/fixtures/kktcar_sitemap_index.xml, kktcar_sitemap_listings.xml: ilk 3 ilan)."""
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from application import collect_kktcar as kk
from infrastructure.collectors import kktcar

FX = Path(__file__).parent / "fixtures"
INDEX = (FX / "kktcar_sitemap_index.xml").read_text()
LISTINGS = (FX / "kktcar_sitemap_listings.xml").read_text()
OLD_FLAT = ('<?xml version="1.0" encoding="UTF-8"?><urlset><url><loc>https://kktcar.com/listing/2014-mazda-demio-girne-ab1cd</loc>'
            '<lastmod>2026-10-01T10:00:00+00:00</lastmod></url><url><loc>https://kktcar.com/hakkimizda</loc></url></urlset>')


def client(pages: dict[str, str], seen: list[str]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        body = pages.get(str(request.url))
        return httpx.Response(200, text=body) if body is not None else httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_index_is_followed_to_the_listings_sitemap_only():
    seen = []
    entries = kktcar.fetch_sitemap(client({"https://kktcar.com/sitemap.xml": INDEX,
                                           "https://kktcar.com/sitemaps/sitemap/listings.xml": LISTINGS}, seen))
    assert seen == ["https://kktcar.com/sitemap.xml", "https://kktcar.com/sitemaps/sitemap/listings.xml"]  # hubs/price-guides istenmez
    assert [e.slug for e in entries] == ["2020-toyota-raize-lefkosa-924zp", "2005-bmw-530i-lefkosa-k2edf", "2015-bmw-316i-girne-wmmd7"]
    assert entries[0].url == "https://kktcar.com/listing/2020-toyota-raize-lefkosa-924zp"  # dil seçenekleri ve resim adresleri ilan sayılmaz
    assert entries[0].lastmod == datetime(2026, 9, 28, 13, 19, 16, 966992, tzinfo=timezone.utc)


def test_old_single_file_sitemap_still_works():
    entries = kktcar.fetch_sitemap(client({"https://kktcar.com/sitemap.xml": OLD_FLAT}, []))
    assert [e.slug for e in entries] == ["2014-mazda-demio-girne-ab1cd"]


def test_index_without_a_listings_sitemap_fails_loudly():
    index = INDEX.replace("listings.xml", "cars.xml")
    with pytest.raises(RuntimeError, match="ilan haritası yok"):
        kktcar.fetch_sitemap(client({"https://kktcar.com/sitemap.xml": index}, []))


def test_listings_sitemap_error_is_not_an_empty_sitemap():
    with pytest.raises(httpx.HTTPStatusError):  # 404: "0 ilan" sayılıp herkes pasifleştirilmez, tur hata verir
        kktcar.fetch_sitemap(client({"https://kktcar.com/sitemap.xml": INDEX}, []))


class Repo:
    def __init__(self, state):
        self.state, self.deactivated_with = dict(state), None

    def known_item_ids(self, source_id):
        return {f"s{i}" for i in range(510)}

    def get_state(self, k, default=None):
        return self.state.get(k, default)

    def set_state(self, k, v):
        self.state[k] = v

    def deactivate_missing(self, source_id, present):
        self.deactivated_with = present
        return 3

    def stale_active(self, *a):
        return []

    def mark_checked(self, *a, **k):
        pass

    def count_recent(self, source_id):
        return 0


def test_switch_to_published_only_sitemap_does_not_trip_the_shrink_guard(monkeypatch):
    """Eski haritanın son boyutu 3.396 (satılmışlar dahil); yeni harita 510: yeni anahtarla ölçülür, kaybolanlar pasifleşir."""
    entries = [kktcar.SitemapEntry(f"https://kktcar.com/listing/s{i}", f"s{i}", None) for i in range(510)]
    monkeypatch.setattr(kktcar, "fetch_sitemap", lambda c: entries)
    repo = Repo({"sitemap_n:S": "3396"})
    stats = kk.collect_kktcar(repo, {"id": "S", "name": "KKTCar"})
    assert stats.in_sitemap == 510 and stats.deactivated == 3 and len(repo.deactivated_with) == 510
    assert repo.state["sitemap_n:S:yayinda"] == "510"


def test_published_only_sitemap_still_guards_against_a_half_response(monkeypatch):
    entries = [kktcar.SitemapEntry(f"https://kktcar.com/listing/s{i}", f"s{i}", None) for i in range(250)]
    monkeypatch.setattr(kktcar, "fetch_sitemap", lambda c: entries)
    repo = Repo({"sitemap_n:S:yayinda": "510"})
    with pytest.raises(RuntimeError, match="şüpheli biçimde küçüldü"):
        kk.collect_kktcar(repo, {"id": "S", "name": "KKTCar"})
    assert repo.deactivated_with is None and repo.state["sitemap_n:S:yayinda"] == "510"
