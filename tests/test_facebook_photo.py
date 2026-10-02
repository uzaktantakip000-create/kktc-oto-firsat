"""Facebook: fiyatı yazıda olmayan araç gönderisinin ilk fotoğrafından fiyat okuma."""
import json
from datetime import datetime, timezone

import httpx

from application import collect_facebook as cf
from application import collect_instagram as ci
from infrastructure.collectors import facebook_groups as fg
from infrastructure.collectors.facebook_groups import RawGroupPost, parse_item

GROUP = "https://www.facebook.com/groups/1"
NOW = datetime(2026, 10, 1, 20, tzinfo=timezone.utc)
SOURCES = [dict(id="G1", name="Grup", url=GROUP + "/", last_checked_at=None)]
NO_PRICE = "Satılık 2015 Toyota Vitz otomatik 90.000 km Girne temiz araç"


class FakeRepo:
    def __init__(self):
        self.state, self.rows = {}, {}

    def get_state(self, key, default=None):
        return self.state.get(key, default)

    def set_state(self, key, value):
        self.state[key] = value

    def upsert_listing(self, source_id, item_id, data):
        new = (source_id, item_id) not in self.rows
        self.rows.setdefault((source_id, item_id), data)
        return new

    def mark_checked(self, *a, **k):
        pass

    def count_recent(self, source_id):
        return 0

    def known_item_ids(self, source_id):
        return {i for (sid, i) in self.rows if sid == source_id}


class FakeReader:
    last_error = None

    def __init__(self, image_text="Fiyat: 5.500 STG", text_fields=None):
        self.image_text, self.text_fields, self.image_calls, self.text_calls = image_text, text_fields, 0, 0

    def read_image(self, image, mime="image/jpeg"):
        self.image_calls += 1
        return self.image_text

    def read(self, text):
        self.text_calls += 1
        return self.text_fields if "five thousand" in text else None  # yalnızca fotoğraf metni eklenince okunabilir


def post(i, text=NO_PRICE, image="https://scontent.example/a.jpg"):
    return RawGroupPost(i, f"https://www.facebook.com/groups/1/permalink/{i}/", NOW, text, GROUP, image)


def img_fetch(url):
    return b"\xff\xd8img", "image/jpeg"


def run(monkeypatch, posts, reader, fetch_image=img_fetch, repo=None):
    monkeypatch.setattr(cf, "gbp_rate", lambda c: 1.0)
    repo = repo or FakeRepo()

    def fetch(token, urls, hours, max_items):
        return posts, 0.05, len(posts)

    res = cf.collect_facebook_groups(repo, "tok", SOURCES, fetch=fetch, now=NOW, reader=reader, fetch_image=fetch_image)
    return repo, res["Grup"]


def test_price_read_from_photo_makes_llm_listing(monkeypatch):
    reader = FakeReader()
    repo, st = run(monkeypatch, [post("1")], reader)
    row = repo.rows[("G1", "1")]
    assert (row["brand"], row["year"], row["price_amount"], row["currency"], row["price_gbp"]) == ("Toyota", 2015, 5500.0, "GBP", 5500.0)
    assert row["extraction_by"] == "llm"  # asla 🟢 olmaz, emsale girmez
    assert "5.500 STG" in row["raw_text"] and st.photo_read == 1 and st.reasons["foto_fiyat"] == 1 and st.new == 1
    assert reader.image_calls == 1
    assert json.loads(repo.state["fb_funnel:2026-10"])["foto_fiyat"] == 1


def test_llm_text_path_after_photo_text(monkeypatch):
    # kural okuyamazsa (ör. fiyat yazım biçimi) birleşik metin yapay zekâ okuyucuya gider
    from domain.llm_read import LlmRead
    import application.llm_reader as lr
    monkeypatch.setattr(lr, "gbp_rate", lambda c: 1.0)
    read = LlmRead(is_car=True, brand="Toyota", model="Vitz", year=2015, km=90000, price=5500.0, currency="GBP",
                   steering=None)
    reader = FakeReader(image_text="five thousand five hundred pounds", text_fields=read)
    repo, st = run(monkeypatch, [post("1")], reader)
    assert repo.rows[("G1", "1")]["extraction_by"] == "llm" and st.photo_read == 1


def test_photo_without_price_or_download_failure_stores_nothing(monkeypatch):
    repo, st = run(monkeypatch, [post("1")], FakeReader(image_text="Hasarsız temiz araç"))
    assert not repo.rows and st.skipped == 1 and st.reasons["foto_okunamadi"] == 1
    repo, st = run(monkeypatch, [post("2")], FakeReader(), fetch_image=lambda u: None)
    assert not repo.rows and st.photo_read == 0


def test_photo_not_read_without_image_or_for_other_reasons(monkeypatch):
    reader = FakeReader()
    repo, st = run(monkeypatch, [post("1", image=None), post("2", text="kiralık 2015 Toyota Vitz 500 STG"),
                                 post("3", text="hasarsız temiz araç satılık")], reader)
    assert reader.image_calls == 0 and not repo.rows and st.reasons["foto_url_yok"] == 1


def test_photo_reads_capped_per_run_and_not_repeated(monkeypatch):
    reader = FakeReader(image_text="boş")
    posts = [post(str(i)) for i in range(cf.MAX_PHOTO_READS + 5)]
    repo, st = run(monkeypatch, posts, reader)
    assert reader.image_calls == cf.MAX_PHOTO_READS
    run(monkeypatch, posts[:3], reader, repo=repo)  # aynı gönderiler sonraki turda tekrar okunmaz
    assert reader.image_calls == cf.MAX_PHOTO_READS


def test_no_reader_means_no_photo_read(monkeypatch):
    repo, st = run(monkeypatch, [post("1")], None)
    assert not repo.rows and st.photo_read == 0


def test_budgets_raised():
    assert cf.MONTHLY_BUDGET_USD == 60.0 and ci.MONTHLY_BUDGET_USD == 10.0


def test_apify_item_image_field_variants():
    base = {"legacyId": "9", "url": "https://www.facebook.com/groups/1/permalink/9/", "text": "x", "inputUrl": GROUP}
    assert parse_item({**base, "image": "https://cdn/a.jpg"}).image_url == "https://cdn/a.jpg"
    assert parse_item({**base, "media": [{"photo_image": {"uri": "https://cdn/b.jpg"}}]}).image_url == "https://cdn/b.jpg"
    assert parse_item({**base, "attachments": [{"thumbnail": "https://cdn/c.jpg"}]}).image_url == "https://cdn/c.jpg"
    assert parse_item(base).image_url is None


def test_fetch_image_limits():
    def client(resp):
        return httpx.Client(transport=httpx.MockTransport(lambda req: resp))

    ok = httpx.Response(200, content=b"\xff\xd8abc", headers={"content-type": "image/jpeg"})
    assert fg.fetch_image("https://x/a.jpg", client(ok)) == (b"\xff\xd8abc", "image/jpeg")
    assert fg.fetch_image("https://x/a", client(httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"}))) is None
    assert fg.fetch_image("https://x/a", client(httpx.Response(404, content=b"", headers={"content-type": "image/jpeg"}))) is None
    big = httpx.Response(200, content=b"0" * (fg.MAX_IMAGE_BYTES + 1), headers={"content-type": "image/png"})
    assert fg.fetch_image("https://x/a.png", client(big)) is None
