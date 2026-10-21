"""Sahibe giden her mesaj abonelere de (sahip kararı 22.10.2026: "bana gelen tüm mesajlar botu takip eden herkese aynı şekilde gitsin; zamanla
eğer ben istersem ... müdahale ederiz"; spec §24.35). Merkez: notify.share_with_subscribers (health.notify_owner ve notify.send_to_all
kullanır). Ağ yok; sohbet kimlikleri uydurma ("100" sahip)."""
from datetime import datetime, timezone

import pytest

from application import audit, health, notify, report
from entrypoints import tick

OWNER = "100"
SUBS = [{"chat_id": OWNER, "is_owner": True}, {"chat_id": "200", "is_owner": False}, {"chat_id": "300", "is_owner": False}]


class Repo:
    """Tekrar sınırı (alert_recent/mark_alerted), onaylı aboneler ve abone durdurma (conn.execute) sahtesi."""

    def __init__(self, subs=SUBS, recent=False):
        self.subs, self.recent, self.marked, self.sql, self.conn = [dict(s) for s in subs], recent, [], [], self

    def alert_recent(self, key, hours):
        return self.recent or key in self.marked

    def mark_alerted(self, key):
        self.marked.append(key)

    def approved_subscribers(self):
        return self.subs

    def execute(self, sql, params):
        self.sql.append((sql, params))


@pytest.fixture
def tg(monkeypatch):
    """health.api ve notify.api'yi yakalar (sahibin ve abonelerin bütün mesajları); `fail`: sohbet -> fırlatılacak hata."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", OWNER)
    calls, fail = [], {}

    def api(token, method, **kw):
        err = fail.get(kw["chat_id"])
        if err is not None:
            raise err
        calls.append(kw)
        return {"message_id": len(calls)}

    monkeypatch.setattr(health, "api", api)
    monkeypatch.setattr(notify, "api", api)
    return calls, fail


def chats(calls):
    return [c["chat_id"] for c in calls]


# --- health.notify_owner: sahibe, sonra abonelere ---
def test_owner_first_then_every_approved_subscriber_once_with_the_same_text_and_fields(tg):
    calls, _ = tg
    repo = Repo(subs=SUBS + [{"chat_id": OWNER, "is_owner": False}])  # sahibin sohbeti ikinci kez listede olsa da bir kez
    assert health.notify_owner(repo, "vps_failover", "⚠️ sunucu durdu", disable_web_page_preview=True, disable_notification=True) is True
    assert chats(calls) == [OWNER, "200", "300"]
    assert all(c["text"] == "⚠️ sunucu durdu" and c["disable_web_page_preview"] is True and c["disable_notification"] is True for c in calls)


def test_subscribers_get_the_redacted_and_cut_text_of_the_owner(tg):
    calls, _ = tg
    health.notify_owner(Repo(), "k", "hata url: https://api.telegram.org/bot123456:SECRETSECRET/x " + "y" * 50, max_chars=40)
    assert len({c["text"] for c in calls}) == 1 and "SECRETSECRET" not in calls[1]["text"] and len(calls[1]["text"]) <= 40


def test_one_dedupe_key_per_message_not_per_chat(tg):
    calls, _ = tg
    repo = Repo()
    assert health.notify_owner(repo, "src_alarm:KKTCar", "⚠️ x", repeat_hours=24) is True
    assert repo.marked == ["src_alarm:KKTCar"]  # abone sayısı kadar değil, TEK kayıt
    assert health.notify_owner(repo, "src_alarm:KKTCar", "⚠️ x", repeat_hours=24) is False  # tekrar sınırı herkes için
    assert chats(calls) == [OWNER, "200", "300"]


def test_owner_send_failing_sends_nobody_and_marks_nothing(tg):
    calls, fail = tg
    fail[OWNER] = notify.TelegramError("sendMessage", 429, "Too Many Requests")
    repo = Repo()
    assert health.notify_owner(repo, "k", "x") is False and calls == [] and repo.marked == []  # sonraki turda hepsi birlikte


def test_blocked_subscriber_is_stopped_others_still_get_it_and_log_is_masked(tg, capsys):
    calls, fail = tg
    subs = [{"chat_id": OWNER, "is_owner": True}, {"chat_id": "123456722", "is_owner": False}, {"chat_id": "300", "is_owner": False}]
    fail["123456722"] = notify.TelegramError("sendMessage", 403, "Forbidden: bot was blocked by the user")
    repo = Repo(subs=subs)
    assert health.notify_owner(repo, "k", "x") is True and repo.marked == ["k"]
    assert chats(calls) == [OWNER, "300"]
    assert repo.sql == [("UPDATE subscribers SET status='durduruldu' WHERE chat_id=%s", ("123456722",))]
    out = capsys.readouterr().out
    assert "chat …722" in out and "123456722" not in out


def test_other_subscriber_errors_do_not_stop_the_rest(tg):
    calls, fail = tg
    fail["200"] = RuntimeError("beklenmedik")  # Telegram dışı hata da öbürünü durdurmaz
    repo = Repo(subs=SUBS + [{"chat_id": "400", "is_owner": False}])
    fail["300"] = notify.TelegramError("sendMessage", 400, "chat not found")  # 403 değil: abone durdurulmaz
    assert health.notify_owner(repo, "k", "x") is True
    assert chats(calls) == [OWNER, "400"] and repo.sql == []


def test_subscriber_list_failing_still_counts_as_sent(tg, capsys):
    calls, _ = tg

    class Down(Repo):
        def approved_subscribers(self):
            raise RuntimeError("bağlantı koptu")

    repo = Down()
    assert health.notify_owner(repo, "k", "x") is True and chats(calls) == [OWNER] and repo.marked == ["k"]
    assert "abonelere paylaşılamadı: RuntimeError" in capsys.readouterr().out


def test_share_false_keeps_it_to_the_owner(tg):
    calls, _ = tg
    assert health.notify_owner(Repo(), "weekly_report", "📊", share=False) is True and chats(calls) == [OWNER]


# --- yalnız sahibe kalacak türler: tek satır (OWNER_ONLY_KINDS, baştan eşleşme) ---
def test_owner_only_kinds_are_honoured_by_key_prefix(tg, monkeypatch):
    calls, _ = tg
    assert notify.OWNER_ONLY_KINDS == frozenset()  # bugün: hepsi herkese (sahip kararı)
    monkeypatch.setattr(notify, "OWNER_ONLY_KINDS", frozenset({"vps_"}))
    health.notify_owner(Repo(), "vps_failover", "⚠️ sunucu")
    assert chats(calls) == [OWNER]
    calls.clear()
    health.notify_owner(Repo(), "src_alarm:KKTCar", "⚠️ site")
    assert chats(calls) == [OWNER, "200", "300"]


# --- yönetim düğmeleri yalnız sahibin kopyasında ---
def test_admin_buttons_are_stripped_for_subscribers_votes_and_links_stay(tg):
    calls, _ = tg
    markup = {"inline_keyboard": [
        [{"text": "📲 WhatsApp", "url": "https://wa.me/900000000000"}],
        [{"text": "👍", "callback_data": "fb:ilgilendim:L1"}, {"text": "👎", "callback_data": "fb:yanlis_fiyat:L1"}],
        [{"text": "✅ Onayla", "callback_data": "sub:onayli:999"}, {"text": "⛔ Reddet", "callback_data": "sub:reddedildi:999"}],
        [{"text": "▶️ Aç", "callback_data": "src:on:7"}, {"text": "➕", "callback_data": "disc:ekle:x"},
         {"text": "✅ Evet", "callback_data": "mute:evet:a|b"}, {"text": "❌ Yanlış", "callback_data": "fb:audit_yanlis:L1"},
         {"text": "?", "callback_data": "yeni:tur:1"}]]}
    health.notify_owner(Repo(), "k", "x", reply_markup=markup)
    owner, *subs = calls
    assert owner["reply_markup"] == markup  # sahip her şeyi görür
    for c in subs:
        assert c["reply_markup"] == {"inline_keyboard": [markup["inline_keyboard"][0], markup["inline_keyboard"][1]]}


def test_only_admin_buttons_means_subscribers_get_no_keyboard_at_all():
    only_admin = {"inline_keyboard": [[{"text": "✅", "callback_data": "fb:audit_dogru:L1"}, {"text": "❌", "callback_data": "fb:audit_yanlis:L1"}]]}
    assert notify.subscriber_markup(only_admin) is None and notify.subscriber_markup(None) is None


# --- send_to_all: kendi tekrar sınırını tutan yollar (av, fotoğrafta fiyat, denetim, keşif, kesinti) ---
def test_send_to_all_raises_on_owner_failure_and_sends_nobody(tg):
    calls, fail = tg
    fail[OWNER] = notify.TelegramError("sendMessage", 500, "x")
    with pytest.raises(notify.TelegramError):
        notify.send_to_all(Repo(), "t", OWNER, "🎯 av", "av")
    assert calls == []


def test_send_to_all_returns_the_owner_message_and_shares(tg):
    calls, _ = tg
    assert notify.send_to_all(Repo(), "t", OWNER, "🎯 av", "av") == {"message_id": 1}
    assert chats(calls) == [OWNER, "200", "300"] and {c["text"] for c in calls} == {"🎯 av"}


def test_monthly_audit_reaches_subscribers_without_the_audit_buttons(tg):
    calls, _ = tg

    class AuditRepo(Repo):
        def audit_sample(self, n):
            return [dict(id="L1", km=80000, price_gbp=5000, year=2015, brand="Toyota", model="Vitz", transmission="otomatik", fuel="benzin",
                         price_raw="5000 stg", source_name="KKTCar", url="https://example.org/ilan/1")]

    repo = AuditRepo()
    assert audit.send_monthly_audit(repo, "t", OWNER) == 1 and repo.marked == ["audit"]
    owner, *subs = calls
    assert "fb:audit_yanlis:L1" in str(owner["reply_markup"])
    assert chats(subs) == ["200", "300"] and all(c["text"] == owner["text"] and "reply_markup" not in c for c in subs)


def test_tick_hunt_and_photo_price_cards_go_to_everyone(tg, monkeypatch):
    calls, _ = tg
    import application.settings_store as settings_store
    monkeypatch.setattr(settings_store, "load_settings", lambda repo: None)
    monkeypatch.setattr(tick.hunt, "run", lambda repo, owner, send, s: send("🎯 AV #1") and 1)
    monkeypatch.setattr(tick.ask_price, "run", lambda repo, send, s: send("🖼 FOTOĞRAFTA FİYAT") and 1)
    tick.run_hunts(Repo())
    tick.run_ask_price(Repo())
    assert [(c["chat_id"], c["text"]) for c in calls] == [(OWNER, "🎯 AV #1"), ("200", "🎯 AV #1"), ("300", "🎯 AV #1"),
                                                         (OWNER, "🖼 FOTOĞRAFTA FİYAT"), ("200", "🖼 FOTOĞRAFTA FİYAT"),
                                                         ("300", "🖼 FOTOĞRAFTA FİYAT")]


def test_weekly_report_is_not_copied_from_the_owner_each_subscriber_gets_their_own(tg, monkeypatch):
    """Haftalık rapor zaten herkese gider (08.10.2026), ama kişiye özel oy listesiyle: sahibin kopyası abonelere paylaşılmaz."""
    calls, _ = tg
    seen = []
    monkeypatch.setattr(report, "api", lambda token, method, **kw: seen.append(kw["chat_id"]))
    monkeypatch.setattr(report, "_shared", lambda repo, recheck, now: [])
    monkeypatch.setattr(report, "_compose", lambda repo, now, shared, chat, owner: (f"📊 {chat}", None))
    assert report.send_weekly_report(Repo(), now=datetime(2026, 10, 22, 9, tzinfo=timezone.utc)) is True
    assert [(c["chat_id"], c["text"]) for c in calls] == [(OWNER, f"📊 {OWNER}")] and seen == ["200", "300"]
