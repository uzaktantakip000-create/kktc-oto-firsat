"""⚡ ACİL SATICI kaydı ve aynı-araç kilidi, gerçek PostgreSQL ile (20.10.2026; sahip: "Facebook fırsatlarında neden oylama yok?").
⚡ artık alerts'e 'acil' seviyesiyle yazılır: sahibin oy verdiği yerlerde (haftalık rapor oy listesi, /son) görünür, ama 🟢/🟠'nin "bir kez gider"
hakkını, 🟠 kotasını, 🟢 sayılarını ve kaynak/öğrenme ölçülerini DEĞİŞTİRMEZ. Telegram sahte; ilanlar ve sohbet kimlikleri uydurma."""
from datetime import timedelta

import pytest

from application import notify, sources_cmd, status, urgent
from domain.settings import Settings
from infrastructure.db.repository import URGENT_TIER, unsaved_alert_key
from tests.test_repository_db import NOW, add_listing, add_source, ago
from tests.test_urgent import swift

pytestmark = pytest.mark.db


def green_eval(c, listing_id, version="v1", hours=1) -> str:
    """Bu sürümle 🟢 değerlendirme (pending_strong / unnotified_strong adayı); kimliği döner."""
    return c.execute(
        "INSERT INTO evaluations (listing_id, evaluated_at, comparables_n, confidence, tier, rules_version, market_median_gbp, exit_price_gbp, "
        "profit_gbp, profit_pct) VALUES (%s, now() - make_interval(hours => %s), 10, 'orta', 'guclu', %s, 9000, 8600, 1800, 30) RETURNING id",
        (listing_id, hours, version)).fetchone()["id"]


def subscribers(c):
    c.execute("INSERT INTO subscribers (chat_id, status, is_owner) VALUES ('own', 'onayli', TRUE), ('c1', 'onayli', FALSE)")


def test_flash_rows_are_reviewed_and_voted_but_never_count_as_green_or_orange(db):
    c, sid = db.conn, add_source(db.conn)
    subscribers(c)
    lid = add_listing(c, sid, "acil")
    eid = green_eval(c, lid)  # sonradan 🟢 olmuş ilan: ⚡ kaydı 🟢'yi YUTMAMALI
    db.save_alert(lid, "own", URGENT_TIER, 7, evaluation_id=eid, price_gbp=6000)
    gone = add_listing(c, sid, "gitti", is_active=False, inactive_at=ago(days=1))
    db.save_alert(gone, "own", URGENT_TIER, 8, evaluation_id=None, price_gbp=5000)

    # 🟢/🟠 "bir kez gider", kota ve sayımlar: değişmez
    assert not db.alert_exists(lid, "own", "guclu") and not db.alert_exists(lid, "own", "tahmini")
    assert db.alert_exists(lid, "own", URGENT_TIER)
    assert {r["id"] for r in db.pending_strong(36, "guclu")} == {lid}  # 🟢 sahibe de aboneye de hâlâ gider
    assert [r["id"] for r in db.unnotified_strong("v1", days=14)] == [lid]
    assert db.alerts_sent_since("tahmini", 24) == 0 and db.alerts_sent_since("guclu", 24) == 0
    assert db.week_alert_counts(7) == {}
    assert all(r["alerted"] == 0 for r in db.disappeared_counts(7))
    assert {r["name"]: r["strong_30d"] for r in sources_cmd._rows(db, ("web",))}["T"] == 0
    assert "0 🟢 ve 0 🟠 gönderildi" in status.build_heartbeat(db)

    # sahibin gözden geçirdiği yerler: görünür, ⚡ olarak
    (vote,) = [r for r in db.alerted_votes(30, chat_id="own") if r["id"] == lid]
    assert vote["tier"] == URGENT_TIER and vote["median_gbp"] == 9000 and vote["voted"] is False  # bildirim anındaki piyasa ortası
    assert db.alerted_votes(30, chat_id="c1", owner=False) == []  # aboneye gitmedi: onun listesinde yok
    assert {(r["id"], r["tier"]) for r in db.recent_opportunities()} == {(lid, URGENT_TIER), (gone, URGENT_TIER)}
    c.execute("INSERT INTO feedback (listing_id, action, note) VALUES (%s, 'ilgilendim', 'chat:own')", (lid,))
    assert [r["voted"] for r in db.alerted_votes(30, chat_id="own") if r["id"] == lid] == [True]


def test_a_listing_sent_both_as_flash_and_green_is_listed_once_as_green(db):
    c, sid = db.conn, add_source(db.conn)
    lid = add_listing(c, sid, "ikisi")
    db.save_alert(lid, "own", URGENT_TIER, 1, evaluation_id=None, price_gbp=6000)
    db.save_alert(lid, "own", "guclu", 2, evaluation_id=None, price_gbp=6000)
    db.save_alert(lid, "own", "tahmini", 3, evaluation_id=None, price_gbp=6000)
    assert [(r["id"], r["tier"]) for r in db.alerted_votes(30)] == [(lid, "guclu")]  # 'acil' harf sırasında önde ama 🟢 kazanır
    assert {r["tier"] for r in db.recent_opportunities()} == {"guclu", "tahmini", URGENT_TIER}  # /son: seviye başına satır (eskisi gibi)


def test_an_unrecorded_flash_trace_never_hides_a_later_green(db):
    c, sid = db.conn, add_source(db.conn)
    subscribers(c)
    lid = add_listing(c, sid, "iz")
    green_eval(c, lid)
    db.set_state(unsaved_alert_key(lid, "own", URGENT_TIER), "42")  # ⚡ gitti ama kaydı yazılamadı
    assert not db.alert_exists(lid, "own", "guclu")
    assert {r["id"] for r in db.pending_strong(36, "guclu")} == {lid} and [r["id"] for r in db.unnotified_strong("v1")] == [lid]
    db.set_state(unsaved_alert_key(lid, "own"), "43")  # karşılaştırma: 🟢 izi sahibe gitti sayılır (aboneye hâlâ aday)
    assert db.alert_exists(lid, "own", "guclu") and [r["id"] for r in db.unnotified_strong("v1")] == []


def test_urgent_sent_reads_the_lock_memory_within_the_window(db):
    c, sid = db.conn, add_source(db.conn)
    recorded = add_listing(c, sid, "kayitli", price_gbp=7770, seller_phone="905331112233")  # fiyat sonradan düştü
    db.save_alert(recorded, "own", URGENT_TIER, 1, evaluation_id=None, price_gbp=7955)
    db.save_alert(recorded, "c1", URGENT_TIER, 2, evaluation_id=None, price_gbp=7955)  # iki sohbet: tek satır
    c.execute("UPDATE alerts SET sent_at = now() - interval '1 day' WHERE listing_id=%s", (recorded,))
    before = add_listing(c, sid, "eski_kod", first_seen_at=ago(days=2), price_gbp=7800)  # bu değişiklikten önceki ⚡: alerts satırı yok
    old = add_listing(c, sid, "eski_acil")
    db.save_alert(old, "own", URGENT_TIER, 3, evaluation_id=None, price_gbp=7000)
    c.execute("UPDATE alerts SET sent_at = now() - interval '8 days' WHERE listing_id=%s", (old,))
    older = add_listing(c, sid, "cok_eski", first_seen_at=ago(days=9))
    green_only = add_listing(c, sid, "yesil", first_seen_at=ago(days=30))
    db.save_alert(green_only, "own", "guclu", 4, evaluation_id=None, price_gbp=6000)  # 🟢 satırı ⚡ anı değildir
    asked = [recorded, before, old, older, green_only, "bozuk-kimlik", ""]
    rows = {r["id"]: r for r in db.urgent_sent([str(x) for x in asked], 7)}
    assert set(rows) == {str(recorded), str(before)}
    assert rows[str(recorded)]["price_gbp"] == 7955 and rows[str(recorded)]["seller_phone"] == "905331112233"
    assert abs(rows[str(recorded)]["sent_at"] - ago(days=1)) < timedelta(minutes=5)
    assert rows[str(before)]["price_gbp"] == 7800 and abs(rows[str(before)]["sent_at"] - ago(days=2)) < timedelta(minutes=5)
    assert (rows[str(before)]["brand_norm"], rows[str(before)]["model_norm"], rows[str(before)]["year"]) == ("Toyota", "vitz", 2015)
    assert db.urgent_sent([], 7) == [] and db.urgent_sent(["x"], 7) == []


def test_save_evaluation_returns_the_new_row_id(db):
    lid = add_listing(db.conn, add_source(db.conn), "a")
    eid = db.save_evaluation(lid, {"comparables_n": 8, "confidence": "orta", "tier": "yok"})
    assert db.conn.execute("SELECT listing_id FROM evaluations WHERE id=%s", (eid,)).fetchone()["listing_id"] == lid


def test_swift_reposts_end_to_end_one_flash_with_buttons_recorded_then_locked(db, monkeypatch):
    """Swift dersi uçtan uca (gerçek sorgularla): aynı turda 2 kopya → 1 düğmeli ⚡ + alerts 'acil' satırı; ertesi turdaki üçüncü kopya
    veritabanındaki kayıttan kilitlenir. Fiyat satış kutusundan değil, pazarlık az: herkese gider (paylaşım kuralı aynı)."""
    c, sid = db.conn, add_source(db.conn, "Grup", platform="facebook")
    subscribers(c)
    calls = []

    def api(token, method, **kw):
        calls.append(kw)
        return {"message_id": len(calls)}

    monkeypatch.setattr(notify, "api", api)
    send = lambda listing, text: notify.send_urgent(db, "t", "own", listing, text)  # noqa: E731 — cron_evaluate.deliver'daki bağlantı
    share = lambda listing, text: notify.send_to_subscribers(db, "t", text, skip_chat="own", listing=listing)  # noqa: E731

    def copy(item, price_try):
        lid = add_listing(c, sid, item, brand_norm="Suzuki", model_norm="swift", year=2021, km=None, currency="TRY", price_amount=price_try,
                          price_gbp=round(price_try * 0.0185, 2), first_seen_at=NOW - timedelta(minutes=20))
        e = swift(str(lid), price_try)
        e.listing["evaluation_id"] = db.save_evaluation(lid, {"comparables_n": 12, "confidence": "orta", "tier": "pazarlik",
                                                              "market_median_gbp": 8500})
        return e

    a, b = copy("g1", 430_000), copy("g2", 420_000)
    assert urgent.run(db, [a, b], send, Settings(), NOW, share=share) == 1
    assert [k["chat_id"] for k in calls] == ["own", "c1"]
    assert all([x["callback_data"] for row in k["reply_markup"]["inline_keyboard"] for x in row]
               == [f"fb:ilgilendim:{b.listing['id']}", f"fb:yanlis_fiyat:{b.listing['id']}"] for k in calls)
    saved = c.execute("SELECT listing_id::text AS lid, chat_id, tier, evaluation_id::text AS eid, fiyat_gonderimde::float8 AS p FROM alerts "
                      "ORDER BY chat_id").fetchall()
    assert [(r["lid"], r["chat_id"], r["tier"], r["eid"], r["p"]) for r in saved] == [
        (b.listing["id"], "c1", URGENT_TIER, str(b.listing["evaluation_id"]), 7770.0),
        (b.listing["id"], "own", URGENT_TIER, str(b.listing["evaluation_id"]), 7770.0)]
    assert urgent.run(db, [copy("g3", 425_000)], send, Settings(), NOW, share=share) == 0 and len(calls) == 2  # sonraki tur: kilit
    assert {r["tier"] for r in db.alerted_votes(30, chat_id="c1", owner=False)} == {URGENT_TIER}  # abonenin oy listesinde de var
