"""/oyla (application/vote_queue, sahip 20.10.2026): sahibe gitmiş, sahibin oylamadığı ve hâlâ ilanda görünen bildirimler tek tek, düğmeli,
düzenli gönderilir (🟢/🟠 önce, ⚡ sonra; en yeni önce). Siteye istek atılmaz; durum satırı veritabanının bildiği kadarını söyler."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from application import notify, vote_queue as vq
from application.notify import TelegramError
from tests.test_repository_db import add_listing

NOW = datetime(2026, 10, 20, 11, 0, tzinfo=timezone.utc)


def row(i=1, tier="guclu", days=1, **over):
    base = {"id": f"L{i}", "canon": f"L{i}", "tier": tier, "sent_at": NOW - timedelta(days=days), "price_sent": 4500.0,
            "year": 2011, "brand": "Nissan", "model": "March", "brand_norm": "Nissan", "model_norm": "march", "km": 157000,
            "price_gbp": 4500.0, "price_amount": 4500.0, "currency": "GBP", "url": f"https://kktcar.com/listing/x-{i}", "seller_phone": None,
            "last_seen_at": NOW - timedelta(hours=3), "posted_at": None, "first_seen_at": NOW - timedelta(days=days),
            "source_name": "KKTCar", "source_url": "https://kktcar.com/", "platform": "web", "median_gbp": 6200.0}
    return base | over


class Repo:
    def __init__(self, rows, islenen=None):
        self.rows, self.state, self.asked = rows, {"acil:islenen": json.dumps(islenen or [])}, []

    def get_state(self, k, default=None):
        return self.state.get(k, default)

    def owner_vote_queue(self, chat, ids):
        self.asked.append((chat, ids))
        return list(self.rows)


@pytest.fixture
def tg(monkeypatch):
    sent = []

    def api(token, method, **kw):
        sent.append(kw)
        if "fail" in kw.get("text", ""):
            raise TelegramError("sendMessage", 429, "Too Many Requests")
        return {"message_id": len(sent)}

    monkeypatch.setattr(vq, "_api", api)
    return sent


def test_order_is_green_first_then_urgent_newest_first_and_the_same_car_is_sent_once():
    rows = [row(1, "acil", days=1), row(2, "guclu", days=5), row(3, "guclu", days=2), row(4, "tahmini", days=3),
            row(5, "guclu", days=4, canon="L3"),  # L3'ün kopyası: tek mesaj
            row(6, "acil", days=2, price_gbp=4400.0)]  # ⚡ aynı marka/model/yıl, fiyat ±%5: aynı araç (urgent.same_car)
    got = [r["id"] for r in vq.pending(Repo(rows), "1")]
    assert got == ["L3", "L4", "L2", "L1"]


def test_the_urgent_memory_is_passed_to_the_query():
    repo = Repo([], islenen=["a", "b"])
    vq.pending(repo, "42")
    assert repo.asked == [("42", ["a", "b"])]


@pytest.mark.parametrize("over,expected", [
    ({}, "✅ Hâlâ ilanda (son görülme 20.10)"),
    ({"last_seen_at": NOW - timedelta(days=5)}, "❔ Hâlâ ilanda mı bilemiyorum (son görülme 15.10)"),
    ({"source_url": "https://kibrisarabaal.com/", "last_seen_at": NOW - timedelta(days=11)},
     "❔ KibrisArabaAl 10.10'dan beri bizi engelliyor: hâlâ ilanda mı bilemiyorum (son görülme 09.10)"),
    ({"platform": "facebook", "posted_at": NOW - timedelta(days=7)}, "❔ Facebook ilanı: satılıp satılmadığını izleyemiyorum (paylaşım 13.10)"),
    ({"platform": "instagram", "posted_at": None}, "❔ Instagram ilanı: Instagram artık taranmıyor, satılıp satılmadığını bilemiyorum (paylaşım 19.10)"),
])
def test_status_line_says_only_what_the_database_knows(over, expected):
    assert vq.status_line(row(**over), NOW) == expected


@pytest.mark.parametrize("over,expected", [
    # Mezunum/KKTCarabam: bilinen ilan listede yeniden görülünce yalnız last_alive_at güncellenir (canlıda Mezunum 13 ilanın 12'si böyle)
    ({"last_seen_at": NOW - timedelta(days=5), "last_alive_at": NOW - timedelta(hours=2)}, "✅ Hâlâ ilanda (son görülme 20.10)"),
    # KKTCar: sayfa okunamadı (touch) → last_seen_at yeni ama canlı görülmedi: "ilanda" denmez
    ({"last_seen_at": NOW - timedelta(hours=1), "last_alive_at": NOW - timedelta(days=5)}, "❔ Hâlâ ilanda mı bilemiyorum (son görülme 15.10)"),
    # KibrisArabaAl: son canlı görülme gösterilir (sonraki okunamayan "touch" değil)
    ({"source_url": "https://kibrisarabaal.com/", "last_seen_at": NOW - timedelta(days=10), "last_alive_at": NOW - timedelta(days=11)},
     "❔ KibrisArabaAl 10.10'dan beri bizi engelliyor: hâlâ ilanda mı bilemiyorum (son görülme 09.10)"),
    ({"last_alive_at": None}, "✅ Hâlâ ilanda (son görülme 20.10)"),  # 019 öncesi satır: last_seen_at
])
def test_status_line_uses_when_the_listing_was_last_seen_alive(over, expected):
    """22.10.2026: durum satırı `last_seen_at`'e bakıyordu; ilanın kaynakta canlı görüldüğü an `last_alive_at`'tir (migration 019)."""
    assert vq.status_line(row(**over), NOW) == expected


def test_message_shows_price_change_median_date_source_and_link():
    text = vq.message(row(price_gbp=4200.0, price_amount=4200.0), 2, 7, NOW)
    lines = text.splitlines()
    assert lines[0] == "2/7 🟢 2011 Nissan March · 157.000 km"
    assert "(bildirimde £4.500)" in lines[1] and "piyasa ortası o gün £6.200" in lines[1]
    assert lines[2] == "📅 Bildirim 19.10 · KKTCar" and lines[-1] == "https://kktcar.com/listing/x-1"
    assert "bildirimde" not in vq.message(row(), 1, 1, NOW)  # fiyat aynıysa tek fiyat


def test_send_posts_a_header_then_one_buttoned_message_per_car(tg):
    n = vq.send(Repo([row(1), row(2, "acil", seller_phone="05331234567", model_norm="note", model="Note")]), "t", "1", now=NOW, gap=0)
    assert n == 2 and len(tg) == 3
    assert tg[0]["text"].startswith("🗳 OY BEKLEYENLER: 2 araç") and "reply_markup" not in tg[0]
    assert all(m["chat_id"] == "1" for m in tg)
    kb = tg[1]["reply_markup"]["inline_keyboard"]
    assert kb[-1] == [{"text": "👍 İşe yarar", "callback_data": "fb:ilgilendim:L1"}, {"text": "👎 Yanlış", "callback_data": "fb:yanlis_fiyat:L1"}]
    assert tg[2]["reply_markup"]["inline_keyboard"][0][0]["text"] == "📲 WhatsApp'tan ulaş"  # telefon varsa WhatsApp


def test_nothing_left_says_so(tg):
    assert vq.send(Repo([]), "t", "1", now=NOW, gap=0) == 0 and tg == [{"chat_id": "1", "text": vq.NONE_LEFT}]


def test_rate_limit_stops_and_the_rest_comes_with_the_next_call(tg):
    rows = [row(1, days=1), row(2, days=2, model="fail", model_norm="x2"), row(3, days=3, model_norm="x3")]
    assert vq.send(Repo(rows), "t", "1", now=NOW, gap=0) == 1 and len(tg) == 3  # başlık + 1 + 429'da durdu


def test_buttons_are_the_same_as_the_green_alert():
    assert vq.keyboard is notify.keyboard


# --- Repository.owner_vote_queue (gerçek PostgreSQL) ---------------------------------------------------------------------------------
OWNER, SUB = "100", "200"


def setup(db):
    c = db.conn
    c.execute("INSERT INTO subscribers (chat_id, name, status, is_owner) VALUES (%s,'Sahip','onayli',TRUE), (%s,'Abone','onayli',FALSE)", (OWNER, SUB))
    return c.execute("INSERT INTO sources (platform, name, url, status) VALUES ('web','Site','https://kktcar.com/','aktif') RETURNING id").fetchone()["id"]


def alert(db, lid, chat, tier="guclu", days=1, kind=None):
    db.conn.execute("INSERT INTO alerts (listing_id, chat_id, tier, sent_at, telegram_msg_id, fiyat_gonderimde, kind) VALUES (%s,%s,%s,%s,'1',%s,%s)",
                    (lid, chat, tier, datetime.now(timezone.utc) - timedelta(days=days), 6000, kind))


def vote(db, lid, chat=None, action="ilgilendim"):
    db.conn.execute("INSERT INTO feedback (listing_id, action, note) VALUES (%s,%s,%s)", (lid, action, f"chat:{chat}" if chat else None))


def ids(db, urgent=()):
    return {str(r["id"]) for r in db.owner_vote_queue(OWNER, list(urgent))}


@pytest.mark.db
def test_queue_has_owner_alerts_without_an_owner_vote(db):
    sid = setup(db)
    a, b, c_, d, e = (add_listing(db.conn, sid, x) for x in "abcde")
    alert(db, a, OWNER)
    alert(db, b, OWNER); vote(db, b)  # sahip oyladı (eski kayıt: not boş)
    alert(db, c_, OWNER); vote(db, c_, SUB)  # yalnız abone oyladı: sahip için hâlâ bekliyor
    alert(db, d, SUB)  # sahibe gitmedi
    alert(db, e, OWNER, kind="geri_cekildi")  # geri çekilen
    vote(db, a, OWNER, "audit_dogru")  # denetim oy sayılmaz
    assert ids(db) == {str(a), str(c_)}


@pytest.mark.db
def test_inactive_and_voted_canonical_are_left_out_and_old_urgent_comes_from_memory(db):
    sid = setup(db)
    gone = add_listing(db.conn, sid, "g", is_active=False)
    alert(db, gone, OWNER)
    canon = add_listing(db.conn, sid, "c")
    vote(db, canon, OWNER)
    copy = add_listing(db.conn, sid, "k", duplicate_of=canon)
    alert(db, copy, OWNER)
    old_urgent = add_listing(db.conn, sid, "u")  # 20.10 öncesi ⚡: alerts'te yok
    rows = db.owner_vote_queue(OWNER, [str(old_urgent), "bozuk-kimlik"])
    assert [(str(r["id"]), r["tier"]) for r in rows] == [(str(old_urgent), "acil")]


@pytest.mark.db
def test_the_best_tier_and_the_price_at_send_time_are_returned(db):
    sid = setup(db)
    lid = add_listing(db.conn, sid, "a")
    alert(db, lid, OWNER, "acil", days=3)
    alert(db, lid, OWNER, "guclu", days=1)
    [r] = db.owner_vote_queue(OWNER, [str(lid)])  # ⚡ hafızasında da olsa alerts satırı varken ikinci satır yok
    assert r["tier"] == "guclu" and r["price_sent"] == 6000 and r["canon"] == lid
    assert "last_alive_at" in r and "last_seen_at" in r  # durum satırı canlı görülme anını kullanır (vote_queue.last_alive)


def test_a_suspicious_km_is_marked():
    assert "123.456 km (şüpheli: yer tutucu ya da eksik yazılmış)" in vq.message(row(km=123456, year=2012), 1, 1, NOW)
    assert "214 km (şüpheli" in vq.message(row(km=214, year=2014), 1, 1, NOW)
    assert "şüpheli" not in vq.message(row(), 1, 1, NOW)
