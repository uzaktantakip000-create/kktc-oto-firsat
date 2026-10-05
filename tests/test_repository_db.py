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


def add_site(conn, name, url):
    """Gerçek adresli kaynak (KKTCarabam ve KibrisArabaAl tohum migration'larında zaten var: onlar kullanılır)."""
    row = conn.execute("SELECT id FROM sources WHERE url=%s", (url,)).fetchone()
    return row["id"] if row else conn.execute("INSERT INTO sources (platform, name, url, status) VALUES ('web',%s,%s,'aktif') RETURNING id",
                                              (name, url)).fetchone()["id"]


def test_twin_candidates_returns_only_cross_site_exact_price_rows_within_the_window(db):
    c = db.conn
    arabam = add_site(c, "KKTCarabam", "https://www.kktcarabam.com/kategori/ikinci-el-araclar")
    kaa = add_site(c, "KibrisArabaAl", "https://kibrisarabaal.com/")
    other = add_site(c, "KKTCar", "https://kktcar.com/")
    bmw = dict(brand_norm="BMW", model_norm="3", year=2007, price_gbp=5400, price_amount=5400, currency="GBP", km=None)
    add_listing(c, arabam, "a_bmw", **bmw, location="girne", first_seen_at=ago(minutes=10))
    add_listing(c, kaa, "k_bmw", **(bmw | {"km": 145_000}), first_seen_at=ago(minutes=26))  # eş
    add_listing(c, kaa, "k_bmw_late", **bmw, first_seen_at=ago(hours=4))  # 3 saatten uzak: gelmez
    add_listing(c, kaa, "k_bmw_price", **(bmw | {"price_amount": 5450, "price_gbp": 5450}), first_seen_at=ago(minutes=20))  # tutar farklı
    add_listing(c, kaa, "k_bmw_try", **(bmw | {"currency": "TRY"}), first_seen_at=ago(minutes=20))  # para birimi farklı
    add_listing(c, other, "x_bmw", **bmw, first_seen_at=ago(minutes=20))  # başka site: gelmez
    add_listing(c, arabam, "a_bmw2", **bmw, first_seen_at=ago(minutes=15))  # aynı sitede eşi var ama öbür sitede de var: gelir (rakip)
    old = dict(brand_norm="Honda", model_norm="fit", year=2012, price_gbp=5450, price_amount=5450, currency="GBP", km=None)
    add_listing(c, arabam, "a_fit_old", **old, first_seen_at=ago(days=4, hours=1))  # eski grup: yalnız tam turda
    add_listing(c, kaa, "k_fit_old", **old, first_seen_at=ago(days=4))
    add_listing(c, arabam, "a_nomodel", **(bmw | {"model_norm": None}), first_seen_at=ago(minutes=10))  # model bilinmiyor: gelmez
    add_listing(c, kaa, "k_nomodel", **(bmw | {"model_norm": None}), first_seen_at=ago(minutes=10))
    names = {r["id"]: r["source_item_id"] for r in c.execute("SELECT id, source_item_id FROM listings").fetchall()}

    def found(rows):
        return {(names[r["id"]], r["site"]) for r in rows}

    full = db.twin_candidates(window_hours=3)
    assert found(full) == {("a_bmw", "kktcarabam"), ("k_bmw", "kibrisarabaal"), ("a_bmw2", "kktcarabam"),
                           ("a_fit_old", "kktcarabam"), ("k_fit_old", "kibrisarabaal")}
    assert found(db.twin_candidates(window_hours=3, new_hours=3)) == {("a_bmw", "kktcarabam"), ("k_bmw", "kibrisarabaal"),
                                                                       ("a_bmw2", "kktcarabam")}
    r = next(r for r in full if names[r["id"]] == "a_bmw")
    assert r["price_amount"] == 5400.0 and r["currency"] == "GBP" and r["location"] == "girne" and r["is_active"] and r["duplicate_of"] is None


def test_mark_duplicates_links_kktcarabam_twin_to_kaa_on_real_db_and_quick_equals_full(db):
    from application.dedupe import mark_duplicates
    c = db.conn
    arabam = add_site(c, "KKTCarabam", "https://www.kktcarabam.com/kategori/ikinci-el-araclar")
    kaa = add_site(c, "KibrisArabaAl", "https://kibrisarabaal.com/")
    bmw = dict(brand_norm="BMW", model_norm="3", year=2007, price_gbp=5400, price_amount=5400, currency="GBP", km=None, seller_phone=None)
    a = add_listing(c, arabam, "a_bmw", **bmw, location="girne", first_seen_at=ago(hours=2))  # KKTCarabam ÖNCE görülmüş
    k = add_listing(c, kaa, "k_bmw", **(bmw | {"km": 145_000, "seller_phone": "905330000001"}), first_seen_at=ago(minutes=30))
    fit = dict(brand_norm="Honda", model_norm="fit", year=2012, price_gbp=5450, price_amount=5450, currency="GBP", km=None, seller_phone=None)
    a_amb = add_listing(c, arabam, "a_fit", **fit, first_seen_at=ago(minutes=40))  # iki KAA adayı: bağ yok
    add_listing(c, kaa, "k_fit1", **(fit | {"km": 200_000, "seller_phone": "905330000002"}), first_seen_at=ago(minutes=30))
    add_listing(c, kaa, "k_fit2", **(fit | {"km": 90_000, "seller_phone": "905330000003"}), first_seen_at=ago(minutes=50))
    assert mark_duplicates(db, quick=True) == 1
    dup = {r["id"]: r["duplicate_of"] for r in c.execute("SELECT id, duplicate_of FROM listings").fetchall()}
    assert dup[a] == k and dup[k] is None and dup[a_amb] is None
    assert mark_duplicates(db) == 0  # tam tur aynı sonucu verir (yeni bağ yok)
    assert db.unevaluated_active() and a not in {r["id"] for r in db.unevaluated_active()}  # kopya değerlendirilmez
    c.execute("UPDATE listings SET is_active=FALSE WHERE id=%s", (k,))  # KAA ilanı satıldı/kalktı
    assert db.release_orphan_duplicates() == 1  # mevcut kural: aktif kopya serbest kalır
    assert mark_duplicates(db) == 0  # ve pasif KAA ilanına yeniden bağlanmaz


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


def test_release_orphan_duplicates_also_frees_copies_linked_to_a_different_model_key(db):
    """Model anahtarı düzeltmesinden (renormalize) sonra eski karışık anahtarda kurulmuş yanlış kopya bağı (CX-5 ↔ CX-30) çözülür; aynı modelin
    gerçek kopyası ve yıl farkı olan aynı model kalır. Aktif/pasif fark etmez (yanlış bağ her iki durumda yanlış)."""
    c, sid = db.conn, add_source(db.conn)
    canon = add_listing(c, sid, "canon", brand_norm="Mazda", model_norm="cx-5")
    add_listing(c, sid, "wrong_model", brand_norm="Mazda", model_norm="cx-30", duplicate_of=canon)  # serbest kalmalı
    add_listing(c, sid, "wrong_model_inactive", brand_norm="Mazda", model_norm="cx-8", duplicate_of=canon, is_active=False)  # serbest kalmalı
    add_listing(c, sid, "real_copy", brand_norm="Mazda", model_norm="cx-5", duplicate_of=canon)  # kalır
    add_listing(c, sid, "other_year", brand_norm="Mazda", model_norm="cx-5", year=2016, duplicate_of=canon)  # yıl farkı: bu adımın işi değil, kalır
    null_canon = add_listing(c, sid, "null_canon", brand_norm="Mazda", model_norm=None)
    add_listing(c, sid, "null_copy", brand_norm="Mazda", model_norm=None, duplicate_of=null_canon)  # NULL = NULL: aynı anahtar, kalır
    assert db.release_orphan_duplicates() == 2
    rows = {r["source_item_id"]: r["duplicate_of"] for r in c.execute("SELECT source_item_id, duplicate_of FROM listings").fetchall()}
    assert rows["wrong_model"] is None and rows["wrong_model_inactive"] is None
    assert rows["real_copy"] == canon and rows["other_year"] == canon and rows["null_copy"] == null_canon


def test_pending_strong_skips_a_listing_whose_alert_was_sent_but_not_recorded_for_that_subscriber(db):
    """Gönderilmiş ama `alerts` kaydı yazılamamış 🟢 (bot_state yedek izi) sonraki turlarda aday olmaz; izi olmayan başka abone için aday kalır."""
    from infrastructure.db.repository import unsaved_alert_key
    c, sid = db.conn, add_source(db.conn)
    c.execute("INSERT INTO subscribers (chat_id, status) VALUES ('c1', 'onayli')")
    traced, plain = add_listing(c, sid, "traced"), add_listing(c, sid, "plain")
    for lid in (traced, plain):
        c.execute("INSERT INTO evaluations (listing_id, comparables_n, market_median_gbp, exit_price_gbp, profit_gbp, profit_pct, confidence, tier, evaluated_at) "
                  "VALUES (%s, 9, 8000, 7600, 1600, 26.7, 'orta', 'guclu', NOW())", (lid,))
    db.set_state(unsaved_alert_key(traced, "c1"), "42")
    assert {r["id"] for r in db.pending_strong(36, "guclu")} == {plain}
    c.execute("INSERT INTO subscribers (chat_id, status) VALUES ('c2', 'onayli')")  # yeni abone: iz yalnız c1 içindi
    assert {r["id"] for r in db.pending_strong(36, "guclu")} == {plain, traced}


def test_deactivate_missing_only_closes_active_listings_absent_from_the_sitemap(db):
    c, sid = db.conn, add_source(db.conn)
    for i in (1, 2, 3):
        add_listing(c, sid, str(i))
    add_listing(c, sid, "4", is_active=False)
    assert db.deactivate_missing(sid, {"1", "2", "4"}) == 1  # yalnız aktif ve haritada olmayan: "3"
    assert {r["source_item_id"] for r in c.execute("SELECT source_item_id FROM listings WHERE is_active").fetchall()} == {"1", "2"}


def test_retention_purges_personal_data_of_inactive_listings_only(db):
    c, sid = db.conn, add_source(db.conn)
    kw = dict(raw_text="metin", seller_handle="Ad Soyad")
    add_listing(c, sid, "old", is_active=False, seller_phone="905550000001", last_seen_at=ago(days=200), **kw)
    add_listing(c, sid, "mid", is_active=False, seller_phone="905550000002", last_seen_at=ago(days=100), **kw)
    add_listing(c, sid, "m160", is_active=False, seller_phone="905550000005", last_seen_at=ago(days=160), **kw)
    add_listing(c, sid, "new", is_active=False, seller_phone="905550000003", last_seen_at=ago(days=10), **kw)
    add_listing(c, sid, "live", is_active=True, seller_phone="905550000004", last_seen_at=ago(days=300), **kw)
    # telefon: 90+ gün (old, m160, mid); satıcı adı: 150+ gün (old, m160); metin: 180+ gün (old)
    assert db.purge_personal_data() == (3, 2, 1)
    got = {r["source_item_id"]: (r["seller_phone"], r["seller_handle"], r["raw_text"]) for r in c.execute("SELECT * FROM listings").fetchall()}
    assert got["old"] == (None, None, None) and got["m160"] == (None, None, "metin") and got["mid"] == (None, "Ad Soyad", "metin")
    assert got["new"][0] and got["new"][1] and got["live"] == ("905550000004", "Ad Soyad", "metin")


def test_expire_unverifiable_closes_old_social_but_not_web_listings(db):
    c = db.conn
    ig, fb, web = add_source(c, "ig", "instagram"), add_source(c, "fb", "facebook"), add_source(c, "KKTCar", "web")
    add_listing(c, ig, "old_ig", posted_at=ago(days=40))
    add_listing(c, ig, "new_ig", posted_at=ago(days=5))
    add_listing(c, fb, "old_fb", posted_at=ago(days=40), seller_phone="905550000009")  # Facebook da 30 günde pasifleşir (telefon temizliğine girsin)
    add_listing(c, fb, "new_fb", posted_at=ago(days=5))
    add_listing(c, web, "old_web", posted_at=ago(days=40))
    assert db.expire_unverifiable() == 2
    active = {r["source_item_id"] for r in c.execute("SELECT source_item_id FROM listings WHERE is_active").fetchall()}
    assert active == {"new_ig", "new_fb", "old_web"}
    assert c.execute("SELECT inactive_reason FROM listings WHERE source_item_id='old_fb'").fetchone()["inactive_reason"] == "belirsiz"


def test_a_listing_is_sent_once_across_green_and_orange_but_the_digest_record_spends_no_right(db):
    """'Bir kez gider': 🟢 ve 🟠 aynı ilan için tek hak paylaşır (🟠'den sonra 🟢 ikinci mesaj üretmez); 🟡 özet kaydı hak harcamaz.
    (alerts benzersiz indeksi hâlâ ilan+sohbet+seviye: kural kodda, 019b'de `kind` ile genişleyecek.)"""
    c, sid = db.conn, add_source(db.conn)
    lid = add_listing(c, sid, "a")
    db.save_alert(lid, "c1", "pazarlik", 1, evaluation_id=None, price_gbp=None)  # günlük özet kaydı
    assert not db.alert_exists(lid, "c1", "guclu") and not db.alert_exists(lid, "c1", "tahmini")
    assert db.alert_exists(lid, "c1", "pazarlik")  # kendi seviyesi için aynen
    db.save_alert(lid, "c1", "tahmini", 2, evaluation_id=None, price_gbp=None)
    assert db.alert_exists(lid, "c1", "guclu") and db.alert_exists(lid, "c1", "tahmini")  # 🟠 gitti: 🟢 de gitmez
    assert not db.alert_exists(lid, "c2", "guclu")  # başka sohbet etkilenmez
    db.save_alert(lid, "c1", "tahmini", 3, evaluation_id=None, price_gbp=None)  # aynı seviye: ON CONFLICT DO NOTHING
    assert c.execute("SELECT count(*) AS n FROM alerts").fetchone()["n"] == 2


def test_pending_strong_skips_listings_already_sent_as_green_or_orange_but_not_digest_only(db):
    c, sid = db.conn, add_source(db.conn)
    c.execute("INSERT INTO subscribers (chat_id, status) VALUES ('c1', 'onayli')")
    only_digest, sent_orange, fresh = (add_listing(c, sid, n) for n in ("only_digest", "sent_orange", "fresh"))
    for lid in (only_digest, sent_orange, fresh):
        c.execute("INSERT INTO evaluations (listing_id, comparables_n, market_median_gbp, exit_price_gbp, profit_gbp, profit_pct, confidence, tier, evaluated_at) "
                  "VALUES (%s, 9, 8000, 7600, 1600, 26.7, 'orta', 'guclu', NOW())", (lid,))
    db.save_alert(only_digest, "c1", "pazarlik", 1, evaluation_id=None, price_gbp=None)
    db.save_alert(sent_orange, "c1", "tahmini", 2, evaluation_id=None, price_gbp=None)
    ids = {r["id"] for r in db.pending_strong(36, "guclu")}
    assert ids == {only_digest, fresh}  # 🟠 gönderilmiş ilan 🟢 olarak yeniden gelmez; yalnız özet kaydı olan gelir


def test_the_same_car_is_not_sent_twice_when_its_copy_was_already_sent(db):
    """05.10 Mazda Demio 2014 £4.500: km'siz KKTCarabam ilanı önce 🟢 gitti, 18 dk sonra KibrisArabaAl ikizi de gitti. Kaynaklar arası ikizde
    kopya önce gelen ilan olabilir (kopya = KKTCarabam): kopyası bu sohbete gitmiş ilan, o sohbet için gitmiş sayılır (alert_exists,
    pending_strong); haftalık rapor da onu 'bildirilmemiş' ya da 'yakın kaçan' diye göstermez. Kopyası gitmemiş ilan etkilenmez."""
    c, sid = db.conn, add_source(db.conn)
    for chat in ("c1", "c2"):
        c.execute("INSERT INTO subscribers (chat_id, status) VALUES (%s, 'onayli')", (chat,))
    rich, other = add_listing(c, sid, "kaa_twin"), add_listing(c, sid, "other")
    lean = add_listing(c, sid, "kktcarabam", km=None, duplicate_of=rich)
    for lid in (rich, other):
        add_report_eval(c, lid, "guclu", pct=30)
    db.save_alert(lean, "c1", "guclu", 1, evaluation_id=None, price_gbp=None)  # kopya YALNIZ c1'e gitti
    assert db.alert_exists(rich, "c1", "guclu") and db.alert_exists(rich, "c1", "tahmini")
    assert not db.alert_exists(rich, "c2", "guclu") and not db.alert_exists(other, "c1", "guclu")
    assert {r["id"] for r in db.pending_strong(36, "guclu")} == {rich, other}  # c2 için hâlâ aday
    db.save_alert(lean, "c2", "guclu", 2, evaluation_id=None, price_gbp=None)
    assert {r["id"] for r in db.pending_strong(36, "guclu")} == {other}  # iki sohbete de gitti: aynı araç yeniden gelmez
    assert item_names(c, db.unnotified_strong("v1", days=14)) == ["other"]
    c.execute("UPDATE evaluations SET tier='pazarlik', profit_pct=18")
    assert item_names(c, db.near_misses("v1", days=7, limit=10)) == ["other"]


def add_versioned_eval(conn, listing_id, evaluated_at, version, tier="yok"):
    conn.execute("INSERT INTO evaluations (listing_id, evaluated_at, comparables_n, confidence, tier, rules_version, saticilar_n, evidence, nedenler, "
                 "red_flags, market_median_gbp, profit_pct) VALUES (%s,%s,8,'orta',%s,%s,5,'{\"yontem\": \"A\"}', ARRAY['km_yuksek'], ARRAY['km_yuksek'], 9000, 40)",
                 (listing_id, evaluated_at, tier, version))


def test_unevaluated_active_rules_version_branch_only_picks_notification_candidates(db):
    """Adım 5b dilim 2: sürüm dalı (yalnız tam tur): son satırı başka sürümlü VE bildirime aday (taze / fiyatı yeni değişmiş / taze 🟢-🟠) ilanlar;
    NULL sürüm dahil (IS DISTINCT FROM); bildirimi olan ilan da dahil (kısmen gönderilmiş 🟢 kaybolmasın); eski ilan dahil DEĞİL (döngü yok)."""
    c, sid = db.conn, add_source(db.conn)
    fresh_old_ver = add_listing(c, sid, "fresh_old")  # taze, eski sürüm
    add_versioned_eval(c, fresh_old_ver, ago(hours=2), "eski")
    fresh_null = add_listing(c, sid, "fresh_null")  # taze, sürümsüz satır (NULL)
    add_eval(c, fresh_null, ago(hours=2))
    fresh_current = add_listing(c, sid, "fresh_cur")  # taze, güncel sürüm: dahil değil
    add_versioned_eval(c, fresh_current, ago(hours=2), "yeni")
    old_listing = add_listing(c, sid, "old", first_seen_at=ago(days=10))  # eski ilan, eski sürüm, 🟡: dahil değil (bildirim üretemez)
    add_versioned_eval(c, old_listing, ago(hours=2), "eski")
    repriced = add_listing(c, sid, "repriced", first_seen_at=ago(days=10))  # eski ilan ama son 48 saatte fiyatı değişmiş
    add_versioned_eval(c, repriced, ago(hours=30), "eski")
    c.execute("INSERT INTO listing_history (listing_id, field, old_value, new_value, changed_at) VALUES (%s,'price_gbp','6000','5000', now() - interval '3 hours')", (repriced,))
    old_green = add_listing(c, sid, "old_green", first_seen_at=ago(days=10))  # eski ilan ama son satırı 🟢 ve 36 saatten yeni
    add_versioned_eval(c, old_green, ago(hours=5), "eski", tier="guclu")
    alerted = add_listing(c, sid, "alerted")  # taze, eski sürüm, bildirimi VAR (kısmen gönderilmiş): dahil
    add_versioned_eval(c, alerted, ago(hours=3), "eski", tier="guclu")
    db.save_alert(alerted, "c1", "guclu", 1, evaluation_id=None, price_gbp=None)
    assert items(db.unevaluated_active(rules_version="yeni")) == {"fresh_old", "fresh_null", "repriced", "old_green", "alerted"}
    assert items(db.unevaluated_active(recent_hours=3, rules_version="yeni")) == {"repriced"}  # hızlı tur sürüm dalına bakmaz (yalnız fiyatı değişen gelir)
    assert db.count_stale_rules("yeni") == 6  # bilgi sayacı: fresh_old, fresh_null, old, repriced, old_green, alerted
    assert db.conn.execute("SELECT count(*) AS n FROM evaluations").fetchone()["n"] == 7  # HİÇBİR satır silinmedi


def test_hourly_full_round_skips_only_the_old_unevaluated_backlog_and_keeps_every_alertable_listing(db):
    """Çıkış kotası (Adım 2h devamı): saatlik tam tur (unevaluated_hours=72) hiç değerlendirilmemiş ESKİ birikimi (ilk görülme VE son fiyat
    değişikliği >72 saat) okumaz; günlük tur (unevaluated_hours yok) bugünkü tam turla birebir aynıdır. Bildirim üretebilecek (notify.is_fresh)
    her ilan saatlik turda da vardır; diğer dallar (3 günlük yeniden bakış, değerlendirmeden sonra fiyat değişimi, kural sürümü) değişmez."""
    from application.evaluate import BACKLOG_AFTER_HOURS
    from application.notify import is_fresh

    c = db.conn
    web, ig = add_source(c, "W"), add_source(c, "I", platform="instagram")

    def repriced(lid, hours):
        c.execute("INSERT INTO listing_history (listing_id, field, old_value, new_value, changed_at) VALUES (%s,'price_gbp','6000','5000',%s)",
                  (lid, ago(hours=hours)))

    # hiç değerlendirilmemiş (emsalsiz kalmış) ilanlar
    add_listing(c, web, "new_1h", first_seen_at=ago(hours=1))
    add_listing(c, web, "new_30h", first_seen_at=ago(hours=30))
    add_listing(c, web, "new_30h_old_post", first_seen_at=ago(hours=30), posted_at=ago(days=10))  # taze değil ama 72 saat içinde: yine her saat
    add_listing(c, ig, "ig_30h", first_seen_at=ago(hours=30), posted_at=ago(hours=30))
    add_listing(c, web, "new_70h", first_seen_at=ago(hours=70))
    add_listing(c, web, "old_80h", first_seen_at=ago(hours=80))  # birikim: günde bir
    add_listing(c, web, "old_10d", first_seen_at=ago(days=10))  # birikim: günde bir
    repriced(add_listing(c, web, "old_repriced_5h", first_seen_at=ago(days=10)), 5)  # eski ilan, fiyatı yeni değişti: TAZE, her saat
    repriced(add_listing(c, web, "old_repriced_50h", first_seen_at=ago(days=10)), 50)  # 72 saat içinde: her saat
    old_twice = add_listing(c, web, "old_repriced_100h", first_seen_at=ago(days=10))
    repriced(old_twice, 200)
    repriced(old_twice, 100)  # son değişiklik 100 saat önce: birikim
    # değerlendirilmiş ilanlar: dalları değişmez
    lid = add_listing(c, web, "stale", first_seen_at=ago(days=9))
    add_eval(c, lid, ago(days=5))  # 3 günlük yeniden bakış
    lid = add_listing(c, web, "repriced_after_eval", first_seen_at=ago(days=9))
    add_eval(c, lid, ago(days=1))
    repriced(lid, 2)
    lid = add_listing(c, web, "fresh_old_version")
    add_versioned_eval(c, lid, ago(hours=2), "eski")  # kural sürümü dalı
    lid = add_listing(c, web, "done", first_seen_at=ago(days=5))
    add_eval(c, lid, ago(days=1))  # hiçbir dala girmez

    daily = items(db.unevaluated_active(rules_version="yeni"))  # = bugünkü tam tur
    hourly = items(db.unevaluated_active(rules_version="yeni", unevaluated_hours=BACKLOG_AFTER_HOURS))
    assert daily == {"new_1h", "new_30h", "new_30h_old_post", "ig_30h", "new_70h", "old_80h", "old_10d", "old_repriced_5h",
                     "old_repriced_50h", "old_repriced_100h", "stale", "repriced_after_eval", "fresh_old_version"}
    assert daily - hourly == {"old_80h", "old_10d", "old_repriced_100h"} and hourly <= daily  # yalnız eski birikim çıktı
    rows = c.execute("""SELECT l.source_item_id, l.first_seen_at, l.posted_at, s.platform,
                               (SELECT MAX(h.changed_at) FROM listing_history h WHERE h.listing_id=l.id AND h.field='price_gbp') AS pc
                        FROM listings l JOIN sources s ON s.id=l.source_id""").fetchall()
    alertable = {r["source_item_id"] for r in rows if is_fresh(r["first_seen_at"], r["posted_at"], price_changed_at=r["pc"], platform=r["platform"])}
    assert {"new_1h", "new_30h", "ig_30h", "old_repriced_5h", "repriced_after_eval", "fresh_old_version"} <= alertable  # test gerçekten taze ilan içeriyor
    assert alertable & daily <= hourly  # bildirim üretebilecek her aday saatlik turda da var
    assert items(db.unevaluated_active(recent_hours=3, unevaluated_hours=BACKLOG_AFTER_HOURS)) == items(db.unevaluated_active(recent_hours=3))  # hızlı tur aynı


def test_downgrade_evaluation_inserts_a_copy_and_never_changes_the_original(db):
    """Adım 5b dilim 2: düşürme UPDATE değil EKLEME: doğrulanan satırın kopyası (tüm sütunlar), tier 🟡, nedenler eklenir, 1 µs sonra."""
    c, sid = db.conn, add_source(db.conn)
    lid = add_listing(c, sid, "a")
    add_versioned_eval(c, lid, ago(hours=1), "yeni", tier="guclu")
    orig = c.execute("SELECT * FROM evaluations").fetchone()
    db.downgrade_evaluation(lid, ["okuma_fiyat", "llm_okudu"], evaluation_id=orig["id"])
    rows = c.execute("SELECT * FROM evaluations ORDER BY evaluated_at").fetchall()
    assert len(rows) == 2
    old, new = rows
    assert old == orig  # eski satır DEĞİŞMEDİ
    assert new["id"] != old["id"] and new["tier"] == "pazarlik" and new["listing_id"] == lid
    assert (new["evaluated_at"] - old["evaluated_at"]).total_seconds() == pytest.approx(1e-6, abs=1e-7)
    assert new["red_flags"] == ["km_yuksek", "okuma_fiyat", "llm_okudu"] and new["nedenler"] == ["km_yuksek", "okuma_fiyat", "llm_okudu"]
    for col in ("rules_version", "saticilar_n", "comparables_n", "market_median_gbp", "profit_pct", "confidence"):
        assert new[col] == old[col], col  # kopya tüm sütunları taşır
    assert new["evidence"]["yontem"] == "A" and "dusuruldu_an" in new["evidence"]
    # evaluation_id verilmezse ilanın son satırı kopyalanır; boş neden listesi nedenler'e dokunmaz
    db.downgrade_evaluation(lid, [])
    last = c.execute("SELECT * FROM evaluations ORDER BY evaluated_at DESC LIMIT 1").fetchone()
    assert last["tier"] == "pazarlik" and last["nedenler"] == new["nedenler"] and c.execute("SELECT count(*) AS n FROM evaluations").fetchone()["n"] == 3
    # başka ilanın değerlendirme kimliği bu ilan için kopyalanamaz
    other = add_listing(c, sid, "b")
    add_versioned_eval(c, other, ago(hours=1), "yeni")
    other_eval = c.execute("SELECT id FROM evaluations WHERE listing_id=%s", (other,)).fetchone()["id"]
    before = c.execute("SELECT count(*) AS n FROM evaluations").fetchone()["n"]
    db.downgrade_evaluation(lid, ["x"], evaluation_id=other_eval)
    assert c.execute("SELECT count(*) AS n FROM evaluations").fetchone()["n"] == before  # eşleşmedi: kopya yok


def test_pending_strong_rules_version_filter_is_applied_to_the_latest_row_only(db):
    """Opus K2b: süzgeç DISTINCT ON'un dışında. Daha yeni bir 🟡 satırı varken eski 🟢 satırı (hatta güncel sürümlü) gönderilemez; eski sürümün son 🟢'si de."""
    c, sid = db.conn, add_source(db.conn)
    c.execute("UPDATE sources SET alert_level='yesil' WHERE id=%s", (sid,))
    c.execute("INSERT INTO subscribers (chat_id, status) VALUES ('c1','onayli')")
    current = add_listing(c, sid, "current")
    add_versioned_eval(c, current, ago(hours=2), "yeni", tier="guclu")
    old_ver = add_listing(c, sid, "old_ver")
    add_versioned_eval(c, old_ver, ago(hours=2), "eski", tier="guclu")
    downgraded = add_listing(c, sid, "downgraded")
    add_versioned_eval(c, downgraded, ago(hours=2), "yeni", tier="guclu")
    db.downgrade_evaluation(downgraded, ["llm_okudu"])  # en son satır artık 🟡
    assert items(db.pending_strong(36, "guclu", rules_version="yeni")) == {"current"}
    assert items(db.pending_strong(36, "guclu")) == {"current", "old_ver"}  # süzgeçsiz çağrı eskisi gibi (🟡 son satırlı olan yine yok)


def test_migration_019_only_adds_nullable_columns_and_changes_nothing_else(db):
    """019 yalnız ekleme: sütunlar NULL'lanabilir, varsayılan değersiz; eski yazma yolları (değerlendirme/ilan) sütunlardan habersiz çalışır."""
    expected = {"evaluations": {"rules_version", "saticilar_n", "alt_ceyrek_gbp", "tablo_degeri_gbp", "nedenler", "evidence"},
                "alerts": {"evaluation_id", "kind", "fiyat_gonderimde"},
                "listings": {"sold_at", "inactive_at", "inactive_reason", "last_alive_at"}}
    for table, cols in expected.items():
        rows = db.conn.execute("SELECT column_name, is_nullable, column_default FROM information_schema.columns "
                               "WHERE table_name=%s AND column_name = ANY(%s)", (table, sorted(cols))).fetchall()
        assert {r["column_name"] for r in rows} == cols, table
        assert all(r["is_nullable"] == "YES" and r["column_default"] is None for r in rows), table
    sid = add_source(db.conn)
    lid = add_listing(db.conn, sid, "a")
    db.save_evaluation(lid, {"comparables_n": 8, "confidence": "orta", "tier": "yok"})  # eski biçimli kayıt hâlâ yazılır
    db.save_alert(lid, "c1", "guclu", 1, evaluation_id=None, price_gbp=None)
    row = db.conn.execute("SELECT rules_version, evidence FROM evaluations").fetchone()
    assert row["rules_version"] is None and row["evidence"] is None


def test_apply_refresh_fills_seller_handle_without_overwriting(db):
    c, sid = db.conn, add_source(db.conn)
    empty = add_listing(c, sid, "e", seller_handle=None)
    kept = add_listing(c, sid, "k", seller_handle="Ahmet Galeri")
    old = {"price_amount": 6000, "currency": "GBP", "price_gbp": 6000}
    new = {"price_amount": 6000, "currency": "GBP", "price_gbp": 6000, "price_raw": "6000", "currency_guess": False,
           "seller_handle": "kktcar:abc-123", "is_active": True}
    db.apply_refresh(empty, old, new)
    db.apply_refresh(kept, old, new)
    got = {r["source_item_id"]: r["seller_handle"] for r in c.execute("SELECT source_item_id, seller_handle FROM listings").fetchall()}
    assert got == {"e": "kktcar:abc-123", "k": "Ahmet Galeri"}


def test_market_pool_carries_currency_so_the_tl_rule_can_work(db):
    """Unutulursa TL emsal kuralı SESSİZCE kapanır (muhafazakâr yön ama kimse fark etmez): havuz satırında currency olmalı."""
    c, sid = db.conn, add_source(db.conn)
    add_listing(c, sid, "tl", currency="TRY", price_gbp=5000)
    add_listing(c, sid, "gbp", currency="GBP", price_gbp=7000)
    got = {r["id"]: r["currency"] for r in db.market_pool(days=120)}
    assert sorted(got.values()) == ["GBP", "TRY"]


def test_renormalize_dry_candidates_apply_and_undo(db):
    c, sid = db.conn, add_source(db.conn)
    cx5 = add_listing(c, sid, "a", brand="Mazda", model="CX-5 2.0 Skyactiv-G", brand_norm="Mazda", model_norm="cx")  # eski anahtar
    add_listing(c, sid, "b", brand="Toyota", model="Vitz 1.3", brand_norm="Toyota", model_norm="vitz")  # değişmez
    add_listing(c, sid, "c", brand=None, model=None, brand_norm="Toyota", model_norm="vitz")  # ham ad yok: dokunulmaz
    since = c.execute("SELECT now() AS t").fetchone()["t"]
    ch = db.renormalize_candidates()
    assert [(x["model_norm"], x["new_model_norm"]) for x in ch] == [("cx", "cx-5")]  # kuru deneme: hiçbir şey yazılmadı
    assert c.execute("SELECT model_norm FROM listings WHERE id=%s", (cx5,)).fetchone()["model_norm"] == "cx"
    assert db.apply_renormalize(ch) == 1
    assert c.execute("SELECT model_norm FROM listings WHERE id=%s", (cx5,)).fetchone()["model_norm"] == "cx-5"
    hist = c.execute("SELECT field, old_value, new_value FROM listing_history WHERE listing_id=%s", (cx5,)).fetchall()
    assert [(h["field"], h["old_value"], h["new_value"]) for h in hist] == [("model_norm", "cx", "cx-5")]
    assert db.renormalize_candidates() == []  # tekrar çalıştırınca değişecek bir şey kalmaz
    assert db.undo_renormalize(since) == 1  # geri alma: eski anahtar döner, geri alma kayıtları temizlenir
    assert c.execute("SELECT model_norm FROM listings WHERE id=%s", (cx5,)).fetchone()["model_norm"] == "cx"
    assert c.execute("SELECT count(*) AS n FROM listing_history WHERE field IN ('brand_norm','model_norm')").fetchone()["n"] == 0


def test_market_pool_carries_seller_handle_for_the_seller_key_shadow(db):
    """Unutulursa (Adım 7) satıcı anahtarı CANLIDA sessizce ilan kimliğine düşer; testler geçse de çeşitlilik abartılır."""
    c, sid = db.conn, add_source(db.conn)
    add_listing(c, sid, "h", seller_handle="kktcar:abc")
    assert [r["seller_handle"] for r in db.market_pool(days=120)] == ["kktcar:abc"]


def test_save_evaluation_stores_the_decision_record_columns(db):
    """Adım 5b-1: karar kaydı sütunları gerçek Postgres'te yazılır/okunur (JSONB, TEXT[], sayılar); UUID kanıt içinde yazı olur."""
    import uuid
    sid = add_source(db.conn)
    lid = add_listing(db.conn, sid, "a")
    other = uuid.uuid4()
    db.save_evaluation(lid, {"comparables_n": 8, "confidence": "orta", "tier": "guclu", "rules_version": "2026-10-04c", "saticilar_n": 6,
                             "alt_ceyrek_gbp": 7000.5, "tablo_degeri_gbp": 8700, "nedenler": ["km_yuksek", "plaka_uyari"],
                             "evidence": {"yontem": "A", "emsal_ids": [other], "gbp_only": False}})
    row = db.conn.execute("SELECT rules_version, saticilar_n, alt_ceyrek_gbp::float8 AS alt, tablo_degeri_gbp::float8 AS tablo, nedenler, evidence "
                          "FROM evaluations").fetchone()
    assert row["rules_version"] == "2026-10-04c" and row["saticilar_n"] == 6 and row["alt"] == 7000.5 and row["tablo"] == 8700
    assert row["nedenler"] == ["km_yuksek", "plaka_uyari"]
    assert row["evidence"] == {"yontem": "A", "emsal_ids": [str(other)], "gbp_only": False}
    db.save_evaluation(lid, {"comparables_n": 8, "confidence": "orta", "tier": "yok", "evidence": None, "nedenler": None})  # boş kayıt da yazılır


def test_pending_strong_returns_the_evaluation_id_and_save_alert_stores_it_with_the_price(db):
    """Adım 5b dilim 3: bildirim kaydı, onu doğuran değerlendirme satırına ve gönderildiği andaki fiyata bağlanır (l.id ezilmez)."""
    c, sid = db.conn, add_source(db.conn, platform="web")
    c.execute("UPDATE sources SET alert_level='yesil' WHERE id=%s", (sid,))
    c.execute("INSERT INTO subscribers (chat_id, status) VALUES ('c1','onayli')")
    lid = add_listing(c, sid, "a", price_gbp=5000)
    c.execute("INSERT INTO evaluations (listing_id, comparables_n, market_median_gbp, exit_price_gbp, profit_gbp, profit_pct, confidence, tier, evaluated_at) "
              "VALUES (%s, 8, 9000, 8550, 3250, 65, 'orta', 'guclu', now())", (lid,))
    (row,) = db.pending_strong(36, "guclu")
    eval_id = c.execute("SELECT id FROM evaluations").fetchone()["id"]
    assert row["id"] == lid and row["evaluation_id"] == eval_id
    db.save_alert(lid, "c1", "guclu", 7, evaluation_id=row["evaluation_id"], price_gbp=row["price_gbp"])
    got = c.execute("SELECT evaluation_id, fiyat_gonderimde::float8 AS p, kind FROM alerts").fetchone()
    assert got["evaluation_id"] == eval_id and got["p"] == 5000 and got["kind"] is None


def lifecycle(conn, lid):
    return conn.execute("SELECT is_active, inactive_at, inactive_reason, last_alive_at FROM listings WHERE id=%s", (lid,)).fetchone()


def test_every_deactivation_path_records_inactive_time_and_reason(db):
    """Adım 5c: pasifleşme AN'ı ve NEDENİ yazılır; 'satildi' yalnız sayfanın kendi sinyali ya da sahibin beyanı, gerisi 'belirsiz'."""
    c = db.conn
    web, ig = add_source(c, "W", "web"), add_source(c, "I", "instagram")
    old = {"price_amount": 6000, "currency": "GBP", "price_gbp": 6000}
    sold, gone, archived = (add_listing(c, web, n) for n in ("sold", "gone", "archived"))
    db.apply_refresh(sold, old, {"is_active": False, "urgency_signals": ["satildi"]})
    db.apply_refresh(gone, old, {"is_active": False, "urgency_signals": ["kaldirildi"]})
    db.apply_refresh(archived, old, {"is_active": False, "urgency_signals": ["arsiv"]})
    assert [lifecycle(c, i)["inactive_reason"] for i in (sold, gone, archived)] == ["satildi", "kaldirildi", "belirsiz"]
    assert all(lifecycle(c, i)["inactive_at"] is not None for i in (sold, gone, archived))
    first = lifecycle(c, sold)["inactive_at"]
    db.apply_refresh(sold, old, {"is_active": False, "urgency_signals": ["kaldirildi"]})  # ikinci pasifleştirme ilk kaydı ezmez
    assert lifecycle(c, sold)["inactive_reason"] == "satildi" and lifecycle(c, sold)["inactive_at"] == first
    # site haritasından düşme: belirsiz; haritada olan dokunulmaz
    missing, present = add_listing(c, web, "missing"), add_listing(c, web, "present")
    assert db.deactivate_missing(web, {"present", "sold", "gone", "archived"}) == 1
    assert lifecycle(c, missing)["inactive_reason"] == "belirsiz" and lifecycle(c, missing)["inactive_at"] is not None
    assert lifecycle(c, present)["is_active"] and lifecycle(c, present)["inactive_reason"] is None
    # Instagram: 30 günlük süre dolumu ve "ilan no" ile kapatma ASLA 'satildi' olmaz
    stale_ig = add_listing(c, ig, "stale", posted_at=ago(days=40))
    numbered = add_listing(c, ig, "numbered", raw_text="Toyota Vitz İlan No: 4455 temiz araç")
    assert db.expire_unverifiable(30) == 1 and db.deactivate_by_ilan_no(ig, "4455") == 1
    assert lifecycle(c, stale_ig)["inactive_reason"] == "belirsiz" and lifecycle(c, numbered)["inactive_reason"] == "belirsiz"
    # sahibin düğmesi: 'satildi'e yükseltir, ilk pasifleşme zamanı korunur
    first_missing = lifecycle(c, missing)["inactive_at"]
    db.mark_sold(missing)
    assert lifecycle(c, missing)["inactive_reason"] == "satildi" and lifecycle(c, missing)["inactive_at"] == first_missing
    live = add_listing(c, web, "live")
    db.mark_sold(live)
    assert lifecycle(c, live)["inactive_reason"] == "satildi" and lifecycle(c, live)["inactive_at"] is not None


def test_listing_born_inactive_keeps_the_reason_but_not_a_fake_inactive_time(db):
    c, web = db.conn, add_source(db.conn, "W", "web")
    db.upsert_listing(web, "born", {"brand_norm": "Toyota", "model_norm": "vitz", "is_active": False, "urgency_signals": ["arsiv"],
                                    "inactive_reason": "belirsiz"})
    got = lifecycle(c, c.execute("SELECT id FROM listings WHERE source_item_id='born'").fetchone()["id"])
    assert got["inactive_reason"] == "belirsiz" and got["inactive_at"] is None and got["last_alive_at"] is None


def test_last_alive_at_is_written_only_when_the_source_really_showed_the_listing_active(db):
    """Adım 5c kısım 2: aktif sayfa okuması / yeni aktif ilan / listede görülen bilinen ilan → last_alive_at. Okunamayan sayfa (touch),
    kapalı sayfa ve pasif doğan ilan → DEĞİL."""
    c, web = db.conn, add_source(db.conn, "W", "web")
    other = add_source(c, "O", "web")
    old = {"price_amount": 6000, "currency": "GBP", "price_gbp": 6000}
    new = {"price_amount": 6000, "currency": "GBP", "price_gbp": 6000, "price_raw": "6000", "currency_guess": False, "is_active": True}
    a, b, closed = (add_listing(c, web, n) for n in ("a", "b", "closed"))
    db.touch(a)
    assert lifecycle(c, a)["last_alive_at"] is None  # okunamadı: sıra kaydı, canlı değil
    db.apply_refresh(a, old, new)
    assert lifecycle(c, a)["last_alive_at"] is not None
    db.apply_refresh(closed, old, {"is_active": False, "urgency_signals": ["satildi"]})
    assert lifecycle(c, closed)["last_alive_at"] is None
    # yeni ilan: aktifse damga, pasif doğduysa yok
    db.upsert_listing(web, "fresh", {"brand_norm": "Toyota", "model_norm": "vitz"})
    db.upsert_listing(web, "born", {"brand_norm": "Toyota", "model_norm": "vitz", "is_active": False})
    ids = {r["source_item_id"]: r for r in c.execute("SELECT source_item_id, last_alive_at FROM listings").fetchall()}
    assert ids["fresh"]["last_alive_at"] is not None and ids["born"]["last_alive_at"] is None
    # liste kaynağı: yalnız aynı kaynağın, aktif ve listedeki ilanlar
    stray = add_listing(c, other, "a")  # başka kaynakta aynı kimlik
    assert db.mark_alive(web, ["b", "closed", "nope"]) == 1  # b aktif ve listede; closed pasif; nope yok
    assert lifecycle(c, b)["last_alive_at"] is not None and lifecycle(c, closed)["last_alive_at"] is None and lifecycle(c, stray)["last_alive_at"] is None
    assert db.mark_alive(web, []) == 0


def test_sold_at_is_stored_once_from_the_page_report_and_born_sold_listings_keep_it(db):
    """Adım 6b kısım 1: kaynağın bildirdiği satış zamanı sold_at'e yazılır (ilk değer korunur); pasif doğan satılmış ilan da taşır."""
    c, web = db.conn, add_source(db.conn, "W", "web")
    old = {"price_amount": 6000, "currency": "GBP", "price_gbp": 6000}
    lid = add_listing(c, web, "a")
    first, later = ago(days=3), ago(days=1)
    db.apply_refresh(lid, old, {"is_active": False, "urgency_signals": ["satildi"], "sold_at": first})
    db.apply_refresh(lid, old, {"is_active": False, "urgency_signals": ["satildi"], "sold_at": later})
    assert c.execute("SELECT sold_at FROM listings WHERE id=%s", (lid,)).fetchone()["sold_at"] == first
    other = add_listing(c, web, "b")
    db.apply_refresh(other, old, {"is_active": False, "urgency_signals": ["arsiv"]})  # süre dolumu: sold_at yok
    assert c.execute("SELECT sold_at FROM listings WHERE id=%s", (other,)).fetchone()["sold_at"] is None
    db.upsert_listing(web, "born", {"brand_norm": "Toyota", "model_norm": "vitz", "is_active": False, "urgency_signals": ["satildi"],
                                    "inactive_reason": "satildi", "sold_at": later})
    assert c.execute("SELECT sold_at FROM listings WHERE source_item_id='born'").fetchone()["sold_at"] == later


def test_feedback_votes_counts_only_button_votes_on_opportunity_messages(db):
    """Öğrenme kapısı (application/learning.py): fırsat mesajı oyları sayılır; denetim ve "satılmış" bildirimi sayılmaz."""
    c, sid = db.conn, add_source(db.conn)
    lid = add_listing(c, sid, "a")
    for action in ("ilgilendim", "pas", "yanlis_fiyat", "kusurlu", "audit_dogru", "audit_yanlis", "satilmis"):
        c.execute("INSERT INTO feedback (listing_id, action) VALUES (%s,%s)", (lid, action))
    assert db.feedback_votes() == 4


def test_alerts_sent_since_counts_distinct_listings_of_the_tier_in_the_window(db):
    c, sid = db.conn, add_source(db.conn)
    a, b, old = (add_listing(c, sid, n) for n in "abo")
    db.save_alert(a, "c1", "tahmini", 1, evaluation_id=None, price_gbp=None)
    db.save_alert(a, "c2", "tahmini", 2, evaluation_id=None, price_gbp=None)  # aynı ilan iki aboneye: 1 sayılır
    db.save_alert(b, "c1", "guclu", 3, evaluation_id=None, price_gbp=None)
    db.save_alert(old, "c1", "tahmini", 4, evaluation_id=None, price_gbp=None)
    c.execute("UPDATE alerts SET sent_at = now() - interval '30 hours' WHERE listing_id=%s", (old,))
    assert db.alerts_sent_since("tahmini", 24) == 1 and db.alerts_sent_since("guclu", 24) == 1 and db.alerts_sent_since("tahmini", 48) == 2


def test_alert_exists_counts_the_fallback_trace_of_an_unsaved_green_or_orange_alert(db):
    """`alerts` kaydı yazılamayan ama Telegram'a gitmiş 🟢/🟠 için `bot_state` yedek izi `alert_exists`te sayılır (tekrar mesaj yok);
    başka sohbet ve 🟡 özet seviyesi etkilenmez."""
    from infrastructure.db.repository import unsaved_alert_key
    c, sid = db.conn, add_source(db.conn)
    lid = add_listing(c, sid, "a")
    assert not db.alert_exists(lid, "c1", "guclu")
    db.set_state(unsaved_alert_key(lid, "c1"), "42")
    assert db.alert_exists(lid, "c1", "guclu") and db.alert_exists(lid, "c1", "tahmini")
    assert not db.alert_exists(lid, "c2", "guclu") and not db.alert_exists(lid, "c1", "pazarlik")



def test_market_pool_drops_listings_the_owner_marked_wrong_but_ignores_a_subscribers_tap(db):
    """Tek abonenin yanlış basışı herkesin emsalini bozmasın (bot_poll: kararlar yalnızca sahipten sisteme döner). Sahip oyu ya da
    sahibi belli olmayan kayıt (denetim, eski satır) emsali dışlamaya devam eder."""
    c, sid = db.conn, add_source(db.conn)
    c.execute("INSERT INTO subscribers (chat_id, name, status, is_owner) VALUES ('o1','sahip','onayli',TRUE), ('s1','abone','onayli',FALSE)")
    keep, owner_bad, sub_bad, no_note = (add_listing(c, sid, n) for n in ("keep", "ownerbad", "subbad", "nonote"))
    for lid, note in ((owner_bad, "chat:o1"), (sub_bad, "chat:s1"), (no_note, None)):
        c.execute("INSERT INTO feedback (listing_id, action, note) VALUES (%s,'yanlis_fiyat',%s)", (lid, note))
    assert {str(r["id"]) for r in db.market_pool(days=120)} == {str(keep), str(sub_bad)}


# --- abone oyu otomatik davranışı yönlendirmez (repository.OWNER_VOTE_SQL): her okuma için abone oyu sayılmaz, sahibinki sayılır ---
def add_people(conn):
    conn.execute("INSERT INTO subscribers (chat_id, name, status, is_owner) VALUES ('o1','sahip','onayli',TRUE), ('s1','abone','onayli',FALSE)")


def vote(conn, listing_id, action, note):
    conn.execute("INSERT INTO feedback (listing_id, action, note) VALUES (%s,%s,%s)", (listing_id, action, note))


def test_every_automatic_feedback_read_uses_the_owner_vote_filter():
    """Veritabanısız bekçi (yerelde de çalışır): otomatik davranışı besleyen her geri bildirim okuması aynı süzgeci kullanır."""
    import inspect

    from infrastructure.db.repository import Repository
    for fn in (Repository.market_pool, Repository.feedback_votes, Repository.pas_count, Repository.est_feedback_by_model,
               Repository.est_feedback_recent, Repository.sources_failing_feedback, Repository.recent_opportunities):
        assert "OWNER_VOTE_SQL" in inspect.getsource(fn), fn.__name__
    from application import status
    assert "OWNER_VOTE_SQL" in inspect.getsource(status.build_status)  # /durum "Senin düğme basışların"


def test_learning_gate_counts_owner_votes_only(db):
    c, sid = db.conn, add_source(db.conn)
    add_people(c)
    lid = add_listing(c, sid, "a")
    for action in ("ilgilendim", "pas", "yanlis_fiyat", "kusurlu"):
        vote(c, lid, action, "chat:s1")
    assert db.feedback_votes() == 0  # abonenin 4 oyu öğrenme kapısını açmaya saymaz
    vote(c, lid, "pas", "chat:o1")
    vote(c, lid, "ilgilendim", None)  # sahibi belli olmayan eski kayıt sahibin sayılır
    assert db.feedback_votes() == 2


def test_pas_count_counts_owner_passes_only(db):
    c, sid = db.conn, add_source(db.conn)
    add_people(c)
    a, b, other_model = add_listing(c, sid, "a"), add_listing(c, sid, "b"), add_listing(c, sid, "c", model_norm="yaris")
    vote(c, a, "pas", "chat:s1")
    vote(c, b, "pas", "chat:s1")
    assert db.pas_count(a) == ("Toyota", "vitz", 0)
    vote(c, b, "pas", "chat:o1")
    vote(c, other_model, "pas", "chat:o1")  # başka model sayılmaz
    assert db.pas_count(a) == ("Toyota", "vitz", 1)


def test_estimate_guard_by_model_counts_owner_wrong_votes_only(db):
    c, sid = db.conn, add_source(db.conn)
    add_people(c)
    a, b = add_listing(c, sid, "a"), add_listing(c, sid, "b")
    for lid in (a, b):
        db.save_alert(lid, "o1", "tahmini", 1, evaluation_id=None, price_gbp=None)
    vote(c, a, "yanlis_fiyat", "chat:s1")
    vote(c, b, "kusurlu", "chat:s1")
    assert db.est_feedback_by_model(30, 1) == []  # yalnız abone "yanlış" dedi: model 🟠'den çıkmaz
    vote(c, a, "yanlis_fiyat", "chat:o1")
    assert db.est_feedback_by_model(30, 2) == []  # b'nin tek "yanlış"ı abonenin: sayılmaz (süzgeçsiz 2 olurdu)
    assert db.est_feedback_by_model(30, 1) == [{"brand_norm": "Toyota", "model_norm": "vitz", "bad_n": 1}]
    vote(c, b, "kusurlu", None)
    assert db.est_feedback_by_model(30, 2) == [{"brand_norm": "Toyota", "model_norm": "vitz", "bad_n": 2}]


def test_estimate_guard_recent_window_counts_owner_feedback_only(db):
    c, sid = db.conn, add_source(db.conn)
    add_people(c)
    a, b = add_listing(c, sid, "a"), add_listing(c, sid, "b")
    for lid in (a, b):
        db.save_alert(lid, "o1", "tahmini", 1, evaluation_id=None, price_gbp=None)
    vote(c, a, "yanlis_fiyat", "chat:s1")
    assert db.est_feedback_recent(10) == []  # yalnız abone oyu almış 🟠 pencereye hiç girmez
    vote(c, b, "ilgilendim", "chat:o1")
    vote(c, b, "yanlis_fiyat", "chat:s1")
    assert db.est_feedback_recent(10) == [False]  # sahip "ilgilendim" dedi; abonenin "yanlış"ı ilanı kötü yapmaz
    vote(c, a, "kusurlu", "chat:o1")
    assert sorted(db.est_feedback_recent(10)) == [False, True]


def test_source_guard_counts_owner_wrong_votes_only(db):
    c, sid = db.conn, add_source(db.conn)
    c.execute("UPDATE sources SET alert_level='yesil' WHERE id=%s", (sid,))
    add_people(c)
    lids = [add_listing(c, sid, f"g{i}") for i in range(3)]
    for lid in lids:
        db.save_alert(lid, "o1", "guclu", 1, evaluation_id=None, price_gbp=None)
        vote(c, lid, "yanlis_fiyat", "chat:s1")
    assert db.sources_failing_feedback(10, 3) == []  # abonenin 3 "yanlış"ı kaynağı düşürmez
    vote(c, lids[0], "yanlis_fiyat", "chat:o1")
    vote(c, lids[1], "kusurlu", "chat:o1")
    assert db.sources_failing_feedback(10, 3) == []  # sahipten yalnız 2
    vote(c, lids[2], "yanlis_fiyat", None)  # sahibi belli olmayan eski kayıt sahibin sayılır
    (row,) = db.sources_failing_feedback(10, 3)
    assert row["id"] == sid and row["n"] == 3 and row["bad_n"] == 3


def test_owner_screens_show_only_the_owners_taps(db):
    """/son "…dedin" ve /durum "Senin düğme basışların" sahibin ekranıdır: abonenin basışı orada sahibinmiş gibi görünmez."""
    from application import status
    c, sid = db.conn, add_source(db.conn)
    add_people(c)
    a, b = add_listing(c, sid, "a"), add_listing(c, sid, "b")
    for lid in (a, b):
        db.save_alert(lid, "o1", "guclu", 1, evaluation_id=None, price_gbp=None)
    vote(c, a, "ilgilendim", "chat:o1")
    vote(c, a, "yanlis_fiyat", "chat:s1")  # abone sonradan bastı: sahibin cevabı değişmez
    vote(c, b, "yanlis_fiyat", "chat:s1")
    got = {r["id"]: r["feedback"] for r in db.recent_opportunities()}
    assert got == {a: "ilgilendim", b: None}
    assert "Senin düğme basışların: 1 " in status.build_status(db, datetime.now(timezone.utc))


def test_current_decisions_reads_the_latest_evaluation_of_active_listings_for_fiyat(db):
    """2.6: /fiyat'taki "piyasa ortası" bildirim mesajıyla AYNI kayıttan (son değerlendirmenin market_median_gbp'si) okunur.
    Yalnız aynı marka-model-yıl, aktif, mükerrer/karantina olmayan ilan; medyansız kayıt (fiyat geçersiz) atlanır; önce 🟢/🟡."""
    c, sid = db.conn, add_source(db.conn)

    def ev(lid, median, tier="yok", hours=1, method=None):
        c.execute("INSERT INTO evaluations (listing_id, evaluated_at, comparables_n, market_median_gbp, confidence, tier, method) "
                  "VALUES (%s, now() - make_interval(hours => %s), 9, %s, 'orta', %s, %s)", (lid, hours, median, tier, method))

    plain = add_listing(c, sid, "plain", model_norm="corolla", year=2014, price_gbp=6000)
    ev(plain, 7000)
    green = add_listing(c, sid, "green", model_norm="corolla", year=2014, price_gbp=5000, km=None)
    ev(green, 9000, "yok", hours=5)
    ev(green, 8600, "guclu", hours=1)  # son kayıt bu
    est = add_listing(c, sid, "est", model_norm="corolla", year=2014, price_gbp=4000)
    ev(est, 9900, "tahmini", method="B")
    for item, cols in (("inactive", {"is_active": False}), ("dup", {"duplicate_of": plain}), ("quar", {"karantina_nedeni": "test"}),
                       ("year", {"year": 2015}), ("model", {"model_norm": "yaris"})):
        ev(add_listing(c, sid, item, **{"model_norm": "corolla", "year": 2014, **cols}), 7500)
    ev(add_listing(c, sid, "nomedian", model_norm="corolla", year=2014), None)
    rows = db.current_decisions("Toyota", "corolla", 2014)
    assert rows[0]["id"] == green and {r["id"] for r in rows} == {green, plain, est}  # 🟢 önce
    by = {r["id"]: r for r in rows}
    assert by[green]["market_median_gbp"] == 8600 and by[green]["km"] is None and by[green]["price_gbp"] == 5000
    assert by[green]["comparables_n"] == 9 and by[green]["total"] == 3
    assert by[est]["method"] == "B" and by[plain]["method"] == "A"  # NULL yöntem = A (kolon varsayılanı)
    assert len(db.current_decisions("Toyota", "corolla", 2014, limit=1)) == 1


# --- haftalık rapor sorguları (application/report.py; yalnız okur) ---
def add_report_eval(conn, listing_id, tier, *, version="v1", hours=1, pct=25.0, n=10, nedenler=None):
    conn.execute("INSERT INTO evaluations (listing_id, evaluated_at, comparables_n, confidence, tier, rules_version, market_median_gbp, "
                 "profit_gbp, profit_pct, nedenler) VALUES (%s, now() - make_interval(hours => %s), %s, 'orta', %s, %s, 9000, 1500, %s, %s::text[])",
                 (listing_id, hours, n, tier, version, pct, nedenler))


def item_names(conn, rows):
    names = {r["id"]: r["source_item_id"] for r in conn.execute("SELECT id, source_item_id FROM listings").fetchall()}
    return [names[r["id"]] for r in rows]


def test_unnotified_strong_lists_only_never_sent_current_green_listings_best_first(db):
    """'Bildirmediğim fırsatlar': son satırı BU sürümle 🟢 (son 14 gün), aktif, kopya/karantina değil, 'yesil' kaynak, hiçbir sohbete 🟢/🟠
    gitmemiş (yazılamamış gönderimin yedek izi de gitmiş sayılır). 🟡 özet kaydı bildirim sayılmaz."""
    from infrastructure.db.repository import unsaved_alert_key
    c, sid = db.conn, add_source(db.conn)
    shadow = add_source(c, "S")
    c.execute("UPDATE sources SET alert_level='sari' WHERE id=%s", (shadow,))
    best, good, digest_only = (add_listing(c, sid, n) for n in ("best", "good", "digest_only"))
    add_report_eval(c, best, "guclu", pct=40)
    add_report_eval(c, good, "guclu", pct=25)
    add_report_eval(c, digest_only, "guclu", pct=22)
    db.save_alert(digest_only, "c1", "pazarlik", 1, evaluation_id=None, price_gbp=None)
    sent_green = add_listing(c, sid, "sent_green")
    add_report_eval(c, sent_green, "guclu", pct=50)
    db.save_alert(sent_green, "c1", "guclu", 2, evaluation_id=None, price_gbp=None)
    sent_orange = add_listing(c, sid, "sent_orange")
    add_report_eval(c, sent_orange, "guclu", pct=50)
    db.save_alert(sent_orange, "c2", "tahmini", 3, evaluation_id=None, price_gbp=None)
    traced = add_listing(c, sid, "traced")
    add_report_eval(c, traced, "guclu", pct=50)
    db.set_state(unsaved_alert_key(traced, "c1"), "42")
    old_version = add_listing(c, sid, "old_version")
    add_report_eval(c, old_version, "guclu", version="v0", pct=50)
    downgraded = add_listing(c, sid, "downgraded")
    add_report_eval(c, downgraded, "guclu", hours=2, pct=50)
    add_report_eval(c, downgraded, "pazarlik", hours=1, pct=50)  # en son satır 🟡
    stale = add_listing(c, sid, "stale")
    add_report_eval(c, stale, "guclu", hours=24 * 20, pct=50)  # 14 günden eski değerlendirme
    for name, cols in (("inactive", {"is_active": False}), ("dup", {"duplicate_of": best}), ("quarantine", {"karantina_nedeni": "test"})):
        add_report_eval(c, add_listing(c, sid, name, **cols), "guclu", pct=50)
    add_report_eval(c, add_listing(c, shadow, "shadow_src"), "guclu", pct=50)  # anlık bildirim vermeyen kaynak
    rows = db.unnotified_strong("v1", days=14)
    assert item_names(c, rows) == ["best", "good", "digest_only"]
    assert item_names(c, db.unnotified_strong("v1", days=14, limit=1)) == ["best"]
    r = rows[0]
    assert r["method"] == "A" and r["price_changed_at"] is None and r["price_amount"] == 6000 and r["currency"] == "GBP"
    assert (r["comparables_n"], r["market_median_gbp"], r["profit_pct"], r["source_name"], r["platform"]) == (10, 9000, 40, "T", "web")


def test_near_misses_are_this_weeks_well_compared_yellow_listings_that_were_never_sent(db):
    """Yakın kaçanlar: bu hafta ilk görülen ya da fiyatı değişen, son satırı BU sürümle 🟡, ≥8 emsal, hiç 🟢/🟠 gitmemiş; süzülen nedenler atlanır."""
    c, sid = db.conn, add_source(db.conn)

    def make(name, tier="pazarlik", first_seen_at=None, **kw):
        lid = add_listing(c, sid, name, **({"first_seen_at": first_seen_at} if first_seen_at else {}))
        add_report_eval(c, lid, tier, **kw)
        return lid

    make("near", pct=18)
    make("gap", pct=26, nedenler=["km_yuksek"])
    make("typo", pct=60, nedenler=["fiyat_asiri_dusuk"])
    make("mixed", pct=50, nedenler=["model_belirsiz", "km_yuksek"])
    make("few", pct=30, n=5)
    make("old", pct=19, first_seen_at=ago(days=10))
    repriced = make("repriced", pct=15, first_seen_at=ago(days=10))
    c.execute("INSERT INTO listing_history (listing_id, field, old_value, new_value, changed_at) "
              "VALUES (%s,'price_gbp','6000','5000', now() - interval '1 day')", (repriced,))
    sent = make("sent", pct=40)
    db.save_alert(sent, "c1", "guclu", 1, evaluation_id=None, price_gbp=None)
    make("green", tier="guclu", pct=45)
    make("other_version", pct=17, version="v0")
    rows = db.near_misses("v1", days=7, min_comparables=8, skip_reasons=["fiyat_asiri_dusuk", "model_belirsiz"], limit=10)
    assert item_names(c, rows) == ["gap", "near", "repriced"]
    assert rows[0]["nedenler"] == ["km_yuksek"] and rows[1]["nedenler"] is None and rows[0]["profit_pct"] == 26
    assert item_names(c, db.near_misses("v1", skip_reasons=[], limit=2)) == ["typo", "mixed"]  # boş süzgeç + sınır


def test_alerted_votes_one_row_per_alerted_listing_newest_first_with_vote_state(db):
    c, sid = db.conn, add_source(db.conn)
    a, b, old, digest = (add_listing(c, sid, n) for n in ("a", "b", "old", "digest"))
    db.save_alert(a, "c1", "guclu", 1, evaluation_id=None, price_gbp=None)
    db.save_alert(a, "c2", "guclu", 2, evaluation_id=None, price_gbp=None)  # iki aboneye gitti: tek satır
    db.save_alert(b, "c1", "tahmini", 3, evaluation_id=None, price_gbp=None)
    db.save_alert(old, "c1", "guclu", 4, evaluation_id=None, price_gbp=None)
    db.save_alert(digest, "c1", "pazarlik", 5, evaluation_id=None, price_gbp=None)  # özet kaydı: oylanacak bildirim değil
    c.execute("UPDATE alerts SET sent_at = now() - interval '2 days' WHERE listing_id=%s", (a,))
    c.execute("UPDATE alerts SET sent_at = now() - interval '1 day' WHERE listing_id=%s", (b,))
    c.execute("UPDATE alerts SET sent_at = now() - interval '40 days' WHERE listing_id=%s", (old,))
    c.execute("INSERT INTO feedback (listing_id, action) VALUES (%s,'ilgilendim'), (%s,'audit_dogru')", (a, b))  # denetim oy değildir
    rows = db.alerted_votes(30)
    assert [(r["id"], r["tier"], r["voted"]) for r in rows] == [(b, "tahmini", False), (a, "guclu", True)]
    assert rows[0]["is_active"] is True and len(db.alerted_votes(60)) == 3


def test_disappeared_counts_split_by_reason_and_listing_counts(db):
    """Kaybolan ilanlar: yalnız son 7 günde pasifleşen, kopya olmayan ilanlar nedene göre (boş neden = belirsiz); pasif doğan sayılmaz."""
    c, sid = db.conn, add_source(db.conn)
    sold = add_listing(c, sid, "sold", is_active=False, inactive_at=ago(days=1), inactive_reason="satildi")
    db.save_alert(sold, "c1", "guclu", 1, evaluation_id=None, price_gbp=None)
    add_listing(c, sid, "unclear", is_active=False, inactive_at=ago(days=2), inactive_reason="belirsiz")
    add_listing(c, sid, "removed", is_active=False, inactive_at=ago(days=1), inactive_reason="kaldirildi")
    add_listing(c, sid, "no_reason", is_active=False, inactive_at=ago(days=1))
    add_listing(c, sid, "last_month", is_active=False, inactive_at=ago(days=10), inactive_reason="satildi")
    add_listing(c, sid, "born_inactive", is_active=False, inactive_reason="satildi")
    add_listing(c, sid, "dup", is_active=False, inactive_at=ago(days=1), inactive_reason="belirsiz", duplicate_of=sold)
    add_listing(c, sid, "live")
    add_listing(c, sid, "live_old", first_seen_at=ago(days=10))
    got = {r["reason"]: (r["n"], r["alerted"]) for r in db.disappeared_counts(7)}
    assert got == {"satildi": (1, 1), "belirsiz": (2, 0), "kaldirildi": (1, 0)}
    assert dict(db.listing_counts(7)) == {"new_n": 8, "active_n": 2}

