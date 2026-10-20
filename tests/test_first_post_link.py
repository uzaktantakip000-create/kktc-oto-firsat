"""Facebook devir "ilk_gonderi" (sosyal oturum 8407886, 20.10.2026): okuyucu aynı metnin son 14 günde gördüğü ilk gönderisini yazar; aktarıcı
(application/social_import) yeni ilanı o gönderinin ilanının kopyası yapar (Repository.link_first_post). Sebep: 17–19.10 aynı 2021 Suzuki Swift
birçok grupta defalarca paylaşıldı, her paylaşım ayrı ilan oldu (km ve telefon yok: same_car bağlayamaz) ve sahibe 11 ⚡ gitti.
Uydurma grup/gönderi kimlikleri."""
from datetime import timedelta

import pytest

from application import social_import as si
from tests.test_repository_db import add_listing
from tests.test_social_import import NOW, FakeRepo, _file, _line, _rows

FIRST = "1000000000000099"


# --- satır ayrıştırma ve aktarım (sahte veritabanı) ------------------------------------------------------------------------------
def test_the_field_is_read_only_when_it_is_a_post_id_other_than_the_line_itself():
    assert si.parse_line(_line(ilk_gonderi=FIRST))[1].first_post == FIRST
    for bad in (None, "", "abc", 123, "1000000001"):  # yok, boş, kimlik değil, sayı, satırın kendisi
        assert si.parse_line(_line(ilk_gonderi=bad))[1].first_post is None
    assert si.parse_line(_line())[1].first_post is None  # alan yok (eski satır)


class LinkRepo(FakeRepo):
    def __init__(self, rows, fail=False):
        super().__init__(rows)
        self.links, self.fail = [], fail

    def link_first_post(self, source_id, item_id, first_item_id):
        if self.fail:
            raise RuntimeError("bağlantı koptu")
        self.links.append((source_id, item_id, first_item_id))
        return True


def test_import_links_only_lines_that_carry_the_field_and_reports_the_count(tmp_path):
    _file(tmp_path, NOW, _line("1000000001"), _line("1000000002", ilk_gonderi="1000000001"))
    repo = LinkRepo(_rows())
    rep = si.import_facebook(repo, tmp_path)
    assert repo.links == [("src-1", "1000000002", "1000000001")] and rep.linked == 1
    assert "(1 ilk gönderiye bağlandı)" in rep.summary()[0]


def test_a_failing_link_never_stops_the_import(tmp_path):
    _file(tmp_path, NOW, _line("1000000001"), _line("1000000002", ilk_gonderi="1000000001"))
    repo, logs = LinkRepo(_rows(), fail=True), []
    rep = si.import_facebook(repo, tmp_path, log=logs.append)
    assert rep.new_listings == 2 and rep.linked == 0 and repo.state[si.STATE_KEY]  # imleç ilerledi
    assert "sosyal devir: ilk gönderi bağı kurulamadı: RuntimeError" in logs and "bağlantı koptu" not in " ".join(logs)


# --- Repository.link_first_post (gerçek PostgreSQL) -------------------------------------------------------------------------------
def fb_source(conn, group, level="yesil"):
    return conn.execute("INSERT INTO sources (platform, name, url, status, alert_level) VALUES ('facebook', %s, %s, 'aktif', %s) RETURNING id",
                        (f"Grup {group}", f"https://www.facebook.com/groups/{group}/", level)).fetchone()["id"]


def dup_of(conn, lid):
    return conn.execute("SELECT duplicate_of FROM listings WHERE id=%s", (lid,)).fetchone()["duplicate_of"]


@pytest.fixture
def two(db):
    """İki grup, birinde ilk gönderinin ilanı (önce görülmüş)."""
    a, b = fb_source(db.conn, "111"), fb_source(db.conn, "222")
    first = add_listing(db.conn, a, FIRST, km=None, first_seen_at=NOW - timedelta(days=1))
    return db, a, b, first


@pytest.mark.db
def test_a_repost_in_another_group_becomes_a_copy_of_the_first_post(two):
    db, a, b, first = two
    lid = add_listing(db.conn, b, "2000000000000001", km=None)
    assert db.link_first_post(b, "2000000000000001", FIRST) is True and dup_of(db.conn, lid) == first
    assert db.link_first_post(b, "2000000000000001", FIRST) is False  # zaten kopya: ikinci kez dokunulmaz


@pytest.mark.db
def test_a_copy_of_a_copy_points_at_the_canonical(two):
    db, a, b, first = two
    mid = add_listing(db.conn, b, "2000000000000001", km=None, duplicate_of=first)
    lid = add_listing(db.conn, a, "2000000000000002", km=None)
    assert db.link_first_post(a, "2000000000000002", "2000000000000001") is True and dup_of(db.conn, lid) == first and mid != first


@pytest.mark.db
@pytest.mark.parametrize("cols", [dict(price_amount=5500, price_gbp=5500),  # fiyat değişti: yeni sinyal, ayrı değerlendirilir
                                  dict(currency="TRY"),
                                  dict(year=2016), dict(model_norm="yaris"),
                                  dict(price_amount=None, price_gbp=None, currency=None)])  # biri fiyatlı, öbürü fiyatsız
def test_a_different_car_or_price_is_never_linked(two, cols):
    db, a, b, first = two
    lid = add_listing(db.conn, b, "2000000000000001", km=None, **cols)
    assert db.link_first_post(b, "2000000000000001", FIRST) is False and dup_of(db.conn, lid) is None


@pytest.mark.db
def test_an_unknown_model_is_never_linked_even_with_the_same_text(db):
    a, b = fb_source(db.conn, "111"), fb_source(db.conn, "222")
    add_listing(db.conn, a, FIRST, model_norm=None)
    lid = add_listing(db.conn, b, "2000000000000001", model_norm=None)
    assert db.link_first_post(b, "2000000000000001", FIRST) is False and dup_of(db.conn, lid) is None


@pytest.mark.db
def test_both_priceless_reposts_are_linked(db):
    a, b = fb_source(db.conn, "111"), fb_source(db.conn, "222")
    first = add_listing(db.conn, a, FIRST, price_amount=None, price_gbp=None, currency=None)
    lid = add_listing(db.conn, b, "2000000000000001", price_amount=None, price_gbp=None, currency=None)
    assert db.link_first_post(b, "2000000000000001", FIRST) is True and dup_of(db.conn, lid) == first


@pytest.mark.db
def test_an_active_listing_is_not_a_copy_of_an_inactive_one_and_a_shadow_listing_is_not_a_canonical(db):
    a, b, g = fb_source(db.conn, "111"), fb_source(db.conn, "222"), fb_source(db.conn, "333", level="golge")
    add_listing(db.conn, a, FIRST, is_active=False)
    lid = add_listing(db.conn, b, "2000000000000001")
    assert db.link_first_post(b, "2000000000000001", FIRST) is False and dup_of(db.conn, lid) is None
    add_listing(db.conn, g, "3000000000000001")  # ilk gönderi gölge grupta
    lid2 = add_listing(db.conn, b, "2000000000000002")
    assert db.link_first_post(b, "2000000000000002", "3000000000000001") is False and dup_of(db.conn, lid2) is None
    lid3 = add_listing(db.conn, g, "3000000000000002")  # gölge ilan gölge olmayanın kopyası olabilir
    assert db.link_first_post(g, "3000000000000002", "2000000000000001") is True and dup_of(db.conn, lid3) == lid


@pytest.mark.db
def test_a_missing_first_post_or_listing_changes_nothing(two):
    db, a, b, first = two
    lid = add_listing(db.conn, b, "2000000000000001", km=None)
    assert db.link_first_post(b, "2000000000000001", "9999999999999999") is False  # ilk gönderinin ilanı yok (ilan değildi)
    assert db.link_first_post(b, "2000000000000777", FIRST) is False  # yeni ilan yok (araç değildi)
    assert dup_of(db.conn, lid) is None


@pytest.mark.db
def test_only_facebook_listings_can_be_the_first_post(db):
    web = db.conn.execute("INSERT INTO sources (platform, name, url, status) VALUES ('web','Site','https://test.example/x','aktif') RETURNING id"
                          ).fetchone()["id"]
    add_listing(db.conn, web, FIRST)
    b = fb_source(db.conn, "222")
    lid = add_listing(db.conn, b, "2000000000000001")
    assert db.link_first_post(b, "2000000000000001", FIRST) is False and dup_of(db.conn, lid) is None


@pytest.mark.db
def test_the_import_links_a_repost_end_to_end(db, tmp_path):
    """Gerçek aktarım: iki gruptaki aynı metin; ikinci satır "ilk_gonderi" taşır → kopya; ilan değerlendirmeye girmez (duplicate_of dolu)."""
    fb_source(db.conn, "1234567890")
    other = fb_source(db.conn, "5550001112")
    text = "2015 Toyota Vitz\n120.000 km\nFiyat 6.500 STG"
    _file(tmp_path, NOW, _line("1000000001", text=text), _line("1000000002", group="5550001112", text=text, ilk_gonderi="1000000001"))
    rep = si.import_facebook(db, tmp_path)
    assert rep.new_listings == 2 and rep.linked == 1
    rows = {r["source_item_id"]: r for r in db.conn.execute("SELECT id, source_id, source_item_id, duplicate_of FROM listings").fetchall()}
    assert rows["1000000002"]["source_id"] == other and rows["1000000002"]["duplicate_of"] == rows["1000000001"]["id"]
