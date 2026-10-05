import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from application import collect_facebook as cf
from application import collect_instagram as ci
from application import llm_reader
from application import social_ingest as si
from application.social_port import SocialPost, SocialSource
from domain.llm_read import LlmRead

FIX = Path(__file__).parent / "fixtures"
FB = {p["id"]: p["text"] for p in json.loads((FIX / "facebook_group_posts.json").read_text())}
IG = [p["caption"] for p in json.loads((FIX / "kibris_car_captions.json").read_text())]
NOW = datetime(2026, 10, 5, 9, tzinfo=timezone.utc)
NO_STEER = FB["1535038928644885"]  # fiyatlı, km'siz, direksiyon yazmıyor
NO_PRICE = FB["1816662732692186"]  # araç ama fiyat yazıda yok
NOT_CAR = FB["3124656827729983"]


@pytest.fixture(autouse=True)
def no_fx(monkeypatch):  # kur servisi çağrılmasın
    for mod in (cf, ci, llm_reader):
        monkeypatch.setattr(mod, "gbp_rate", lambda c: 1.0)


def fb_source(steering=None, source_id=None):
    return SocialSource("facebook", "111", "https://www.facebook.com/groups/111/", "fb-a", source_id=source_id,
                        slug="ornek-grup", default_steering=steering)


IG_SOURCE = SocialSource("instagram", "galeri_a", "https://www.instagram.com/galeri_a/", "ig-a")


def fb_post(pid, text, image=None):
    return SocialPost("facebook", "111", pid, f"https://www.facebook.com/groups/111/posts/{pid}/", NOW, text, image_url=image)


def ig_post(pid, text, owner=None):
    return SocialPost("instagram", "galeri_a", pid, f"https://www.instagram.com/p/{pid}/", NOW, text,
                      image_url="https://cdn.example/x.jpg", owner=owner)


class Journal:
    """Deneme dosyası gibi: her gönderiyi nedeniyle kaydeder."""

    def __init__(self):
        self.records, self.upserts = [], []

    def record_post(self, alias, post, data, reason):
        new = post.post_id not in {r[1].post_id for r in self.records}
        self.records.append((alias, post, data, reason))
        return new and reason is None

    def upsert_listing(self, source_id, item_id, data):
        raise AssertionError("deneme kipinde record_post kullanılmalı")

    def known_item_ids(self, source_id):
        return {r[1].post_id for r in self.records}


class Repo:
    """Veritabanı kipi gibi: yalnız upsert_listing (record_post yok)."""

    def __init__(self):
        self.rows = {}

    def upsert_listing(self, source_id, item_id, data):
        new = (source_id, item_id) not in self.rows
        self.rows.setdefault((source_id, item_id), data)
        return new

    def known_item_ids(self, source_id):
        return {i for (s, i) in self.rows if s == source_id}


class Reader:
    def __init__(self, read=None, image_text=None):
        self._read, self._image_text, self.calls = read, image_text, []

    def read(self, text):
        self.calls.append(("read", text))
        return self._read

    def read_image(self, image, mime="image/jpeg"):
        self.calls.append(("image", mime))
        return self._image_text


def test_facebook_every_post_recorded_with_reason_and_listing_counted():
    j = Journal()
    posts = [fb_post("1", FB["x1"]), fb_post("2", FB["x2"]), fb_post("3", NOT_CAR), fb_post("4", NO_PRICE, image="https://cdn.example/a.jpg")]
    st = si.ingest("facebook", posts, fb_source(), j)
    assert (st.fetched, st.new, st.skipped, st.parsed) == (4, 1, 3, 1)
    assert [r[3] for r in j.records] == [None, "arac_degil", "arac_degil", "fiyat_yok"]
    assert {r[0] for r in j.records} == {"fb-a"}  # yalnız takma ad
    row = j.records[0][2]
    assert (row["brand"], row["year"], row["price_gbp"], row["extraction_by"]) == ("Toyota", 2013, 7250, "parser_serbest")
    assert st.photo_candidates == 1 and st.photo_read == 0  # okuyucu yok: fotoğraf okunmaz, aday sayılır
    assert st.reasons == {"arac_degil": 2, "fiyat_yok": 1}


def test_facebook_no_image_counts_foto_url_yok():
    st = si.ingest("facebook", [fb_post("1", NO_PRICE)], fb_source(), Journal())
    assert st.reasons == {"foto_url_yok": 1, "fiyat_yok": 1} and st.photo_candidates == 0


def test_steering_hint_never_carries_real_group_name(monkeypatch):
    seen = []
    real = si.listing_data
    monkeypatch.setattr(si, "listing_data", lambda post, source: seen.append(dict(source)) or real(post, source))
    lhd = si.ingest("facebook", [fb_post("1", NO_STEER)], fb_source("LHD"), j := Journal())
    assert lhd.new == 1 and j.records[0][2]["steering"] == "LHD"
    si.ingest("facebook", [fb_post("2", NO_STEER)], fb_source(None), j2 := Journal())
    assert j2.records[0][2]["steering"] is None
    assert seen == [{"name": "sol direksiyon"}, {"name": ""}]
    raw = si.raw_group_post(fb_post("1", "x"), fb_source())
    assert raw.group_url == "https://www.facebook.com/groups/111/" and raw.post_id == "1"


def test_database_sink_gets_only_listings_with_source_id():
    repo = Repo()
    si.ingest("facebook", [fb_post("1", FB["x1"]), fb_post("2", NOT_CAR)], fb_source(source_id="uuid-1"), repo)
    assert list(repo.rows) == [("uuid-1", "1")]
    repo2 = Repo()
    si.ingest("facebook", [fb_post("1", FB["x1"])], fb_source(), repo2)
    assert list(repo2.rows) == [("111", "1")]  # deneme kimliği yoksa kaynak anahtarı


def test_reader_fills_km_retries_and_reads_photo_with_shared_caps():
    reader = Reader(read=LlmRead(is_car=True, brand="Honda", year=2021, km=63000, price=9000.0, currency="GBP"),
                    image_text="9.000 STG")
    images = []
    budget = si.ReadBudget(km_left=1, photos_left=1)
    j = Journal()
    posts = [fb_post("1", NO_STEER), fb_post("2", NO_STEER + " "), fb_post("3", NO_PRICE, image="https://cdn.example/a.jpg")]
    st = si.ingest("facebook", posts, fb_source(), j, reader=reader, budget=budget,
                   fetch_image=lambda url: images.append(url) or (b"img", "image/jpeg"))
    assert st.km_read == 1 and budget.km_left == 0  # tavan: ikinci km okuması yapılmaz
    assert j.records[0][2]["km"] == 63000 and j.records[1][2]["km"] is None
    rec = j.records[2]
    assert rec[3] is None and rec[2]["extraction_by"] == "llm"  # fiyat yazıda yok: yapay zekâ (ya da fotoğraf) okudu, en fazla 🟡
    assert st.llm_read + st.photo_read == 1


def test_photo_read_when_text_retry_fails():
    class TextFails(Reader):
        def read(self, text):
            self.calls.append(("read", text))
            return None if "9.000 STG" not in text else LlmRead(is_car=True, brand="Honda", year=2021, price=9000.0, currency="GBP")

    reader = TextFails(image_text="Fiyat 9.000 STG")
    budget = si.ReadBudget(photos_left=1)
    j = Journal()
    posts = [fb_post("1", NO_PRICE, image="https://cdn.example/a.jpg"), fb_post("2", NO_PRICE + " ", image="https://cdn.example/b.jpg")]
    st = si.ingest("facebook", posts, fb_source(), j, reader=reader, budget=budget, fetch_image=lambda url: (b"img", "image/jpeg"))
    assert st.photo_read == 1 and st.reasons.get("foto_fiyat") == 1 and budget.photos_left == 0
    assert j.records[0][2]["extraction_by"] == "llm" and j.records[1][3] == "fiyat_yok"
    assert st.photo_candidates == 2


def test_llm_time_budget_stops_reader_but_rules_still_run():
    ticks = iter([0.0, 1000.0, 1000.0, 1000.0, 1000.0, 1000.0])
    budget = si.ReadBudget(seconds_left=100, clock=lambda: next(ticks))
    reader = Reader(read=LlmRead(is_car=True, km=1))
    j = Journal()
    st = si.ingest("facebook", [fb_post("1", NO_STEER)], fb_source(), j, reader=reader, budget=budget)
    assert reader.calls == [] and st.new == 1 and budget.seconds_left < 0


def test_known_post_is_not_sent_to_reader_again():
    j = Journal()
    j.records.append(("fb-a", fb_post("1", NO_STEER), None, "x"))
    reader = Reader(read=LlmRead(is_car=True, km=5))
    si.ingest("facebook", [fb_post("1", NO_STEER)], fb_source(), j, reader=reader)
    assert reader.calls == []


def test_instagram_template_sold_and_unreadable():
    j = Journal()
    sold = []
    posts = [ig_post("A1", IG[0], owner="Galeri_A"), ig_post("A2", "SATILDI ✅ İlan No: 4521"), ig_post("A3", "Merhaba, yeni haftaya hazırız"),
             ig_post("A4", "")]
    st = si.ingest("instagram", posts, IG_SOURCE, j, on_sold=lambda sid, no: sold.append((sid, no)) or 1)
    assert (st.fetched, st.new, st.parsed, st.sold, st.skipped) == (4, 1, 1, 1, 3)
    assert [r[3] for r in j.records] == [None, "satildi", "sablon_yok", "metin_yok"]
    assert j.records[0][2]["brand"] == "Mazda" and j.records[0][2]["seller_handle"] == "galeri_a"
    assert j.records[1][2]["is_active"] is False and sold == [("galeri_a", "4521")]
    raw = si.raw_instagram_post(ig_post("A9", "x"), IG_SOURCE)
    assert raw.owner == "galeri_a" and raw.photo_url == "https://cdn.example/x.jpg"  # sahibi yoksa kaynak hesabı


def test_instagram_database_sink_keeps_every_post_like_apify_collector():
    repo = Repo()
    st = si.ingest("instagram", [ig_post("A1", IG[1]), ig_post("A3", "Merhaba")], IG_SOURCE, repo)
    assert set(repo.rows) == {("galeri_a", "A1"), ("galeri_a", "A3")} and st.new == 2
    assert repo.rows[("galeri_a", "A3")]["extraction_by"] is None


def test_instagram_reader_reads_unparsed_caption_once():
    reader = Reader(read=LlmRead(is_car=True, brand="Toyota", year=2015, price=6900.0, currency="GBP"))
    j = Journal()
    st = si.ingest("instagram", [ig_post("A3", "Toyota Vitz 2015 temiz 6900 sterlin")], IG_SOURCE, j, reader=reader)
    assert st.llm_read == 1 and j.records[0][3] is None and j.records[0][2]["extraction_by"] == "llm"


def test_unknown_platform_rejected():
    with pytest.raises(ValueError):
        si.ingest("x", [], IG_SOURCE, Journal())
