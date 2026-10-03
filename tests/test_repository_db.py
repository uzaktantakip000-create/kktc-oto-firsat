"""Gerçek PostgreSQL ile repository testleri (CI'de `db-test` işi; yerelde TEST_DATABASE_URL yoksa atlanır).
Bugüne dek veritabanına bağlanan test yoktu: SQL'ler yalnızca canlıda denenebiliyordu. Kapsam: taşma, hızlı/tam değerlendirme turu,
dar emsal havuzu, mükerrer taraması, saklama politikası ve 'bir kez gider' kuralı (alerts)."""
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from application.evaluate import clamp_pct
from tests.conftest import safe_test_dsn

pytestmark = pytest.mark.db
NOW = datetime.now(timezone.utc)


def ago(**kw):
    return NOW - timedelta(**kw)


def add_source(conn, name="T", platform="web", status="aktif"):
    return conn.execute("INSERT INTO sources (platform, name, url, status) VALUES (%s,%s,%s,%s) RETURNING id",
                        (platform, name, f"https://test.example/{name}/{platform}", status)).fetchone()["id"]


def add_listing(conn, source_id, item_id, **cols):
    row = dict(brand_norm="Toyota", model_norm="vitz", year=2015, km=80_000, price_gbp=6000, price_amount=6000, currency="GBP",
               is_active=True) | cols
    names = ["source_id", "source_item_id", *row]
    return conn.execute(f"INSERT INTO listings ({','.join(names)}) VALUES ({','.join(['%s'] * len(names))}) RETURNING id",
                        [source_id, item_id, *row.values()]).fetchone()["id"]


def add_eval(conn, listing_id, evaluated_at, tier="yok"):
    conn.execute("INSERT INTO evaluations (listing_id, evaluated_at, comparables_n, confidence, tier) VALUES (%s,%s,8,'orta',%s)",
                 (listing_id, evaluated_at, tier))


def items(rows):
    return {r["source_item_id"] for r in rows}


def test_test_database_guard_refuses_real_or_remote_databases():
    assert safe_test_dsn({}) is None
    assert safe_test_dsn({"TEST_DATABASE_URL": "postgresql://u:p@db.abc.supabase.co:5432/postgres"}) is None  # uzak
    assert safe_test_dsn({"TEST_DATABASE_URL": "postgresql://u:p@localhost:5432/postgres"}) is None  # adında 'test' yok
    assert safe_test_dsn({"TEST_DATABASE_URL": "postgresql://u:p@localhost:5432/kktc_test"}) is not None


def test_profit_pct_overflow_is_real_and_the_clamp_prevents_it(db):
    sid = add_source(db.conn)
    lid = add_listing(db.conn, sid, "a")
    with pytest.raises(psycopg.errors.NumericValueOutOfRange):  # DECIMAL(5,2): %6290 sığmaz (bütün tick'i çökertirdi)
        db.save_evaluation(lid, {"comparables_n": 8, "confidence": "orta", "tier": "yok", "profit_pct": 6290.35})
    db.save_evaluation(lid, {"comparables_n": 8, "confidence": "orta", "tier": "yok", "profit_pct": clamp_pct(62.9035)})
    assert db.conn.execute("SELECT profit_pct::float8 AS p FROM evaluations").fetchone()["p"] == 999.99


def test_unevaluated_active_full_and_quick_rounds(db):
    c, sid = db.conn, add_source(db.conn)
    add_listing(c, sid, "a")  # yeni, hiç değerlendirilmedi
    add_listing(c, sid, "b", first_seen_at=ago(days=5))  # eski, hiç değerlendirilmedi (emsal bulunamamış birikim)
    lid_c = add_listing(c, sid, "c", first_seen_at=ago(days=5))
    add_eval(c, lid_c, ago(days=1))  # 1 gün önce değerlendirildi: yeniden bakış yok
    lid_d = add_listing(c, sid, "d", first_seen_at=ago(days=9))
    add_eval(c, lid_d, ago(days=5))  # 5 gün önce: yeniden bakış zamanı (3 gün)
    lid_e = add_listing(c, sid, "e", first_seen_at=ago(days=5))
    add_eval(c, lid_e, ago(days=1))
    c.execute("INSERT INTO listing_history (listing_id, field, old_value, new_value) VALUES (%s,'price_gbp','6000','5000')", (lid_e,))  # fiyat değişti
    lid_a = c.execute("SELECT id FROM listings WHERE source_item_id='a'").fetchone()["id"]
    add_listing(c, sid, "f", duplicate_of=lid_a)  # mükerrer: atlanır
    add_listing(c, sid, "g", is_active=False)  # pasif: atlanır
    add_listing(c, sid, "h", price_gbp=None)  # fiyatsız: atlanır
    add_listing(c, sid, "i", brand_norm=None)  # markasız: atlanır
    add_listing(c, sid, "j", karantina_nedeni="test")  # karantinada: atlanır
    assert items(db.unevaluated_active()) == {"a", "b", "d", "e"}  # tam tur (saatte bir)
    assert items(db.unevaluated_active(recent_hours=3)) == {"a", "e"}  # hızlı tur: yeni + fiyatı değişen


def test_market_pool_keys_return_exactly_the_filtered_pool_including_null_models(db):
    c, sid = db.conn, add_source(db.conn)
    for i in range(3):
        add_listing(c, sid, f"v{i}")
    for i in range(2):
        add_listing(c, sid, f"f{i}", brand_norm="Honda", model_norm="fit")
    for i in range(2):
        add_listing(c, sid, f"n{i}", brand_norm="Mazda", model_norm=None)  # model bilinmiyor
    add_listing(c, sid, "b0", brand_norm="BMW", model_norm="3")
    full = db.market_pool(days=120)
    assert len(full) == 8
    keys = [("Toyota", "vitz"), ("Mazda", None)]
    narrow = db.market_pool(days=120, keys=keys)
    assert {str(r["id"]) for r in narrow} == {str(r["id"]) for r in full if (r["brand_norm"], r["model_norm"]) in set(keys)} and len(narrow) == 5
    assert db.market_pool(days=120, keys=[]) == []


def test_dedupe_candidates_quick_returns_complete_groups_only_where_new_listings_appeared(db):
    c, sid = db.conn, add_source(db.conn)
    add_listing(c, sid, "x_old", first_seen_at=ago(days=30))
    add_listing(c, sid, "x_new")  # Toyota vitz 2015 grubunda yeni ilan
    add_listing(c, sid, "y_old1", brand_norm="Honda", model_norm="fit", year=2014, first_seen_at=ago(days=30))
    add_listing(c, sid, "y_old2", brand_norm="Honda", model_norm="fit", year=2014, first_seen_at=ago(days=20))  # yeni ilan YOK
    add_listing(c, sid, "z_old", brand_norm="Mazda", model_norm=None, first_seen_at=ago(days=30))
    add_listing(c, sid, "z_new", brand_norm="Mazda", model_norm=None)  # model bilinmeyen grup (NULL = NULL aynı grup)
    names = {r["id"]: r["source_item_id"] for r in c.execute("SELECT id, source_item_id FROM listings").fetchall()}  # dedupe_candidates kimlik döndürür

    def found(rows):
        return {names[r["id"]] for r in rows}

    assert found(db.dedupe_candidates(new_hours=3)) == {"x_old", "x_new", "z_old", "z_new"}
    assert found(db.dedupe_candidates()) == {"x_old", "x_new", "y_old1", "y_old2", "z_old", "z_new"}


def test_release_orphan_duplicates_frees_only_active_copies_of_inactive_originals(db):
    c, sid = db.conn, add_source(db.conn)
    dead = add_listing(c, sid, "dead", is_active=False)
    live = add_listing(c, sid, "live")
    add_listing(c, sid, "copy_of_dead_active", duplicate_of=dead)  # serbest kalmalı
    add_listing(c, sid, "copy_of_dead_inactive", duplicate_of=dead, is_active=False)  # pasif kopya: kalır
    add_listing(c, sid, "copy_of_live", duplicate_of=live)  # kanonik aktif: kalır
    assert db.release_orphan_duplicates() == 1
    rows = {r["source_item_id"]: r["duplicate_of"] for r in c.execute("SELECT source_item_id, duplicate_of FROM listings").fetchall()}
    assert rows["copy_of_dead_active"] is None and rows["copy_of_dead_inactive"] == dead and rows["copy_of_live"] == live


def test_deactivate_missing_only_closes_active_listings_absent_from_the_sitemap(db):
    c, sid = db.conn, add_source(db.conn)
    for i in (1, 2, 3):
        add_listing(c, sid, str(i))
    add_listing(c, sid, "4", is_active=False)
    assert db.deactivate_missing(sid, {"1", "2", "4"}) == 1  # yalnız aktif ve haritada olmayan: "3"
    assert {r["source_item_id"] for r in c.execute("SELECT source_item_id FROM listings WHERE is_active").fetchall()} == {"1", "2"}


def test_retention_purges_personal_data_of_inactive_listings_only(db):
    c, sid = db.conn, add_source(db.conn)
    add_listing(c, sid, "old", is_active=False, seller_phone="905550000001", raw_text="metin", last_seen_at=ago(days=200))
    add_listing(c, sid, "mid", is_active=False, seller_phone="905550000002", raw_text="metin", last_seen_at=ago(days=100))
    add_listing(c, sid, "new", is_active=False, seller_phone="905550000003", raw_text="metin", last_seen_at=ago(days=10))
    add_listing(c, sid, "live", is_active=True, seller_phone="905550000004", raw_text="metin", last_seen_at=ago(days=300))
    assert db.purge_personal_data() == (2, 1)  # telefon: 90+ gün (old, mid); metin: 180+ gün (old)
    got = {r["source_item_id"]: (r["seller_phone"], r["raw_text"]) for r in c.execute("SELECT * FROM listings").fetchall()}
    assert got["old"] == (None, None) and got["mid"] == (None, "metin") and got["new"][0] and got["live"] == ("905550000004", "metin")


def test_expire_unverifiable_closes_old_social_but_not_web_listings(db):
    c = db.conn
    ig, web = add_source(c, "ig", "instagram"), add_source(c, "KKTCar", "web")
    add_listing(c, ig, "old_ig", posted_at=ago(days=40))
    add_listing(c, ig, "new_ig", posted_at=ago(days=5))
    add_listing(c, web, "old_web", posted_at=ago(days=40))
    assert db.expire_unverifiable() == 1
    active = {r["source_item_id"] for r in c.execute("SELECT source_item_id FROM listings WHERE is_active").fetchall()}
    assert active == {"new_ig", "old_web"}


def test_a_listing_is_sent_once_across_green_and_orange_but_the_digest_record_spends_no_right(db):
    """'Bir kez gider': 🟢 ve 🟠 aynı ilan için tek hak paylaşır (🟠'den sonra 🟢 ikinci mesaj üretmez); 🟡 özet kaydı hak harcamaz.
    (alerts benzersiz indeksi hâlâ ilan+sohbet+seviye: kural kodda, 019b'de `kind` ile genişleyecek.)"""
    c, sid = db.conn, add_source(db.conn)
    lid = add_listing(c, sid, "a")
    db.save_alert(lid, "c1", "pazarlik", 1)  # günlük özet kaydı
    assert not db.alert_exists(lid, "c1", "guclu") and not db.alert_exists(lid, "c1", "tahmini")
    assert db.alert_exists(lid, "c1", "pazarlik")  # kendi seviyesi için aynen
    db.save_alert(lid, "c1", "tahmini", 2)
    assert db.alert_exists(lid, "c1", "guclu") and db.alert_exists(lid, "c1", "tahmini")  # 🟠 gitti: 🟢 de gitmez
    assert not db.alert_exists(lid, "c2", "guclu")  # başka sohbet etkilenmez
    db.save_alert(lid, "c1", "tahmini", 3)  # aynı seviye: ON CONFLICT DO NOTHING
    assert c.execute("SELECT count(*) AS n FROM alerts").fetchone()["n"] == 2


def test_pending_strong_skips_listings_already_sent_as_green_or_orange_but_not_digest_only(db):
    c, sid = db.conn, add_source(db.conn)
    c.execute("INSERT INTO subscribers (chat_id, status) VALUES ('c1', 'onayli')")
    only_digest, sent_orange, fresh = (add_listing(c, sid, n) for n in ("only_digest", "sent_orange", "fresh"))
    for lid in (only_digest, sent_orange, fresh):
        c.execute("INSERT INTO evaluations (listing_id, comparables_n, market_median_gbp, exit_price_gbp, profit_gbp, profit_pct, confidence, tier, evaluated_at) "
                  "VALUES (%s, 9, 8000, 7600, 1600, 26.7, 'orta', 'guclu', NOW())", (lid,))
    db.save_alert(only_digest, "c1", "pazarlik", 1)
    db.save_alert(sent_orange, "c1", "tahmini", 2)
    ids = {r["id"] for r in db.pending_strong(36, "guclu")}
    assert ids == {only_digest, fresh}  # 🟠 gönderilmiş ilan 🟢 olarak yeniden gelmez; yalnız özet kaydı olan gelir


def test_reset_evaluations_skips_alerted_inactive_and_old_ones(db):
    c, sid = db.conn, add_source(db.conn)
    plain = add_listing(c, sid, "plain")
    alerted = add_listing(c, sid, "alerted")
    inactive = add_listing(c, sid, "inactive", is_active=False)
    old = add_listing(c, sid, "old")
    for lid in (plain, alerted, inactive):
        add_eval(c, lid, ago(days=1))
    add_eval(c, old, ago(days=20))
    db.save_alert(alerted, "c1", "guclu", 1)
    assert db.reset_evaluations(7) == 1  # yalnız bildirimsiz, aktif, son 7 günün değerlendirmesi
    assert c.execute("SELECT count(*) AS n FROM evaluations").fetchone()["n"] == 3
