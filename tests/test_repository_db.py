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
    assert items(db.unevaluated_active(recent_hours=3, rules_version="yeni")) == set()  # hızlı tur sürüm dalına bakmaz
    assert db.count_stale_rules("yeni") == 6  # bilgi sayacı: fresh_old, fresh_null, old, repriced, old_green, alerted
    assert db.conn.execute("SELECT count(*) AS n FROM evaluations").fetchone()["n"] == 7  # HİÇBİR satır silinmedi


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
