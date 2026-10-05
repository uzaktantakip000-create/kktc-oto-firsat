"""Bot komutlarının tablo testi (05.10.2026): her komut sahip ve abone için tam BİR cevap verir; sessiz kalan komut yok.
Sahte veritabanı + yakalanan Telegram çağrıları; gerçek ağ/DB yok. Komutların iç mantığı kendi test dosyalarında (settings_store, price_book_cmd...)."""
import pytest

from application import bot_poll

OWNER = "1"
FRIEND = "2"


class Conn:
    def __init__(self, subs):
        self.subs, self.feedback, self.sql, self._row = subs, [], [], None

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        self.sql.append((s, params))
        self._row = None
        if s.startswith("SELECT * FROM subscribers WHERE chat_id"):
            st = self.subs.get(params[0])
            self._row = {"chat_id": params[0], "status": st} if st else None
        elif s.startswith("SELECT 1 FROM subscribers WHERE chat_id=%s AND status='onayli'"):
            self._row = {"x": 1} if self.subs.get(params[0]) == "onayli" else None
        elif s.startswith("UPDATE subscribers SET status='durduruldu'"):
            self.subs[params[0]] = "durduruldu"
        elif s.startswith("UPDATE subscribers SET status='onayli'"):
            self.subs[params[0]] = "onayli"
        elif s.startswith("INSERT INTO subscribers (chat_id,name,status)"):
            self.subs.setdefault(params[0], "bekliyor")
        elif s.startswith("SELECT 1 FROM feedback"):
            self._row = {"x": 1} if tuple(params) in self.feedback else None
        elif s.startswith("INSERT INTO feedback"):
            self.feedback.append(tuple(params))
        return self

    def fetchone(self):
        return self._row


class Repo:
    def __init__(self, subs=None):
        self.conn = Conn({OWNER: "onayli", **(subs or {})})
        self.state = {}

    def get_state(self, k, default=None):
        return self.state.get(k, default)

    def set_state(self, k, v):
        self.state[k] = v

    def blocked_phones(self):
        return []

    def pas_count(self, listing_id):
        return "Toyota", "vitz", 1

    def feedback_votes(self):
        return 0


@pytest.fixture
def bot(monkeypatch):
    calls = []
    monkeypatch.setattr(bot_poll, "api", lambda token, method, **kw: calls.append((method, kw)) or [])
    monkeypatch.setattr(bot_poll, "_answer", lambda *a, **k: None)
    monkeypatch.setattr(bot_poll.status, "build_status", lambda repo: "DURUM")
    monkeypatch.setattr(bot_poll.history_cmd, "last_opportunities", lambda repo: "SON")
    monkeypatch.setattr(bot_poll.price_book_cmd, "fiyat_reply", lambda repo, a: f"FIYAT{a}")
    monkeypatch.setattr(bot_poll.price_book_cmd, "record_sale", lambda repo, a: f"SATTI{a}")
    monkeypatch.setattr(bot_poll.sources_cmd, "sources_report", lambda repo: "KAYNAKLAR")
    monkeypatch.setattr(bot_poll.sources_cmd, "add_instagram", lambda repo, a: f"EKLE{a}")
    monkeypatch.setattr(bot_poll.sources_cmd, "set_level", lambda repo, a: f"SEVIYE{a}")
    monkeypatch.setattr(bot_poll.sources_cmd, "change_status", lambda repo, a, to: f"{to}{a}")
    monkeypatch.setattr(bot_poll.llm_reader, "from_env", lambda repo: None)
    ads = []
    monkeypatch.setattr(bot_poll.ad_check, "handle", lambda repo, raw, image, reader, **kw: ads.append((raw, image, kw)) or "ILAN-CEVABI")

    class Bot:
        def send(self, repo, chat, text):
            calls.clear()
            bot_poll._handle_message(repo, "tok", OWNER, {"chat": {"id": int(chat)}, "from": {"first_name": "x"}, "text": text})
            return [kw["text"] for m, kw in calls if m == "sendMessage"]

    b = Bot()
    b.calls, b.ads = calls, ads
    return b


OWNER_CASES = [
    ("/durum", "DURUM"), ("/son", "SON"), ("/fiyat corolla 2014", "FIYAT corolla 2014"),
    ("/satti corolla 2014 7200", "SATTI corolla 2014 7200"), ("/kaynaklar", "KAYNAKLAR"),
    ("/kaynak_ekle abc", "EKLE abc"), ("/kaynak_seviye x mor", "SEVIYE x mor"),
    ("/kaynak_ac abc", "deneme abc"), ("/kaynak_kapat abc", "pasif abc"),
]


@pytest.mark.parametrize("text,expected", OWNER_CASES)
def test_owner_commands_reply_once(bot, text, expected):
    assert bot.send(Repo(), OWNER, text) == [expected]


def test_owner_setting_commands_reply_once_and_save(bot):
    repo = Repo()
    assert "%25" in bot.send(repo, OWNER, "/esik %25")[0] and repo.state["cfg:strong_threshold"] == "0.25"
    assert "arasında olmalı" in bot.send(repo, OWNER, "/esik 10")[0] and repo.state["cfg:strong_threshold"] == "0.25"  # reddedilen değer kaydedilmez
    assert "£20.000" in bot.send(repo, OWNER, "/butce 20.000")[0] and repo.state["cfg:max_buy_gbp"] == "20000"
    assert "kaldırıldı" in bot.send(repo, OWNER, "/butce yok")[0] and repo.state["cfg:max_buy_gbp"] == ""
    assert "açık" in bot.send(repo, OWNER, "/tahmini AÇ")[0] and repo.state["cfg:estimated_alerts"] == "1"
    assert "Mercedes-Benz" in bot.send(repo, OWNER, "/istemiyorum mercedes")[0] and repo.state["cfg:blocked_brands"] == "Mercedes-Benz"
    assert "tekrar" in bot.send(repo, OWNER, "/istiyorum mercedes")[0] and repo.state["cfg:blocked_brands"] == ""
    ayar = bot.send(repo, OWNER, "/ayarlar")
    assert len(ayar) == 1 and "Ayarlar" in ayar[0]


def test_help_and_menu_commands_work_for_everyone(bot):
    repo = Repo({FRIEND: "onayli"})
    assert bot.send(repo, OWNER, "/yardim") == [bot_poll.HELP_OWNER]
    assert bot.send(repo, FRIEND, "/yardim") == [bot_poll.HELP_SUBSCRIBER]
    for text in (bot_poll.HELP_OWNER, bot_poll.HELP_SUBSCRIBER, bot_poll.WELCOME_OWNER):
        assert 0 < len(text) < 4096
    assert "/esik" not in bot_poll.HELP_SUBSCRIBER and "/durum" not in bot_poll.HELP_SUBSCRIBER  # abone sahip komutlarını görmez


def test_owner_only_commands_get_an_answer_for_subscribers_and_change_nothing(bot):
    repo = Repo({FRIEND: "onayli"})
    for cmd in sorted(bot_poll.OWNER_COMMANDS):
        assert bot.send(repo, FRIEND, f"{cmd} x") == [bot_poll.OWNER_ONLY_REPLY], cmd
    assert repo.state == {} and repo.conn.subs[FRIEND] == "onayli"
    assert bot.send(Repo(), "99", "/durum") == [bot_poll.OWNER_ONLY_REPLY]  # tanımsız kişi de sessiz kalmaz


def test_command_matching_is_case_insensitive_has_no_bot_suffix_trouble_and_is_exact(bot):
    repo = Repo({FRIEND: "onayli"})
    assert bot.send(repo, OWNER, "/Durum") == ["DURUM"]
    assert bot.send(repo, OWNER, "/durum@kktc_firsat_bot") == ["DURUM"]
    assert bot.send(repo, OWNER, "/son@kktc_firsat_bot") == ["SON"]
    assert bot.send(repo, FRIEND, "/dur@kktc_firsat_bot")[0].startswith("Bildirimler durduruldu") and repo.conn.subs[FRIEND] == "durduruldu"
    repo = Repo({FRIEND: "onayli"})
    bot.send(repo, FRIEND, "/durum")  # '/dur' ile karışmaz: abone durdurulmuş OLMAZ
    assert repo.conn.subs[FRIEND] == "onayli"


@pytest.mark.parametrize("status,start_reply", [
    ("onayli", "Zaten onaylısın"), ("durduruldu", "durdurulmuş"), ("bekliyor", "onay bekliyor")])
def test_start_for_known_subscribers_does_not_ping_the_owner_again(bot, status, start_reply):
    repo = Repo({FRIEND: status})
    out = bot.send(repo, FRIEND, "/start")
    assert len(out) == 1 and start_reply in out[0]
    assert not any(m == "sendMessage" and kw["chat_id"] == OWNER for m, kw in bot.calls)


def test_start_for_a_new_person_asks_the_owner_once(bot):
    repo = Repo()
    out = bot.send(repo, FRIEND, "/start")
    assert out == ["Başvurun alındı. Onaylanınca haber vereceğim.", "👤 x bildirim almak istiyor."]
    assert repo.conn.subs[FRIEND] == "bekliyor"
    assert bot.send(repo, FRIEND, "/start") == ["Başvurun onay bekliyor; onaylanınca haber vereceğim."]  # ikinci kez: sahibe tekrar yazılmaz
    assert bot.send(repo, OWNER, "/start") == [bot_poll.WELCOME_OWNER]


@pytest.mark.parametrize("status,dur,basla", [
    ("onayli", "Bildirimler durduruldu", "zaten açık"),
    ("durduruldu", "zaten durdurulmuş", "Bildirimler açıldı"),
    ("bekliyor", "onaylanması gerekir", "onaylanması gerekir"),
    ("reddedildi", "onaylanması gerekir", "onaylanması gerekir"),
    (None, "onaylanması gerekir", "onaylanması gerekir"),
])
def test_stop_and_start_notifications_always_answer(bot, status, dur, basla):
    for cmd, expect in (("/dur", dur), ("/basla", basla)):
        repo = Repo({FRIEND: status} if status else {})
        out = bot.send(repo, FRIEND, cmd)
        assert len(out) == 1 and expect in out[0], (status, cmd, out)


def test_owner_can_stop_and_restart_and_poll_keeps_it_stopped(bot):
    repo = Repo()
    assert "durduruldu" in bot.send(repo, OWNER, "/dur")[0] and repo.conn.subs[OWNER] == "durduruldu"
    assert "zaten durdurulmuş" in bot.send(repo, OWNER, "/dur")[0]
    assert "açıldı" in bot.send(repo, OWNER, "/basla")[0] and repo.conn.subs[OWNER] == "onayli"


def test_owner_free_text_unknown_commands_and_links(bot):
    repo = Repo()
    assert bot.send(repo, OWNER, "tamam") == [bot_poll.CHATTER_REPLY] and bot.ads == []  # kota/yapay zekâ harcanmaz
    assert bot.send(repo, OWNER, "teşekkürler 👍") == [bot_poll.CHATTER_REPLY]
    assert bot.send(repo, OWNER, "https://kibrisarabaal.com/ilan/3107-2012-model-otomatik-666946/") == [bot_poll.LINK_REPLY] and bot.ads == []
    assert bot.send(repo, OWNER, "/yanlis_komut") == [bot_poll.UNKNOWN_OWNER_REPLY]
    assert bot.send(repo, OWNER, "2015 Toyota Vitz 5000£") == ["ILAN-CEVABI"] and bot.ads == [("2015 Toyota Vitz 5000£", None, {})]
    assert bot.send(repo, OWNER, "Satılık 2012 model araç 8500 stg 240000 km") == ["ILAN-CEVABI"]  # rakamlı gerçek ilan metni
    assert bot.send(Repo({FRIEND: "onayli"}), FRIEND, "merhaba") == [bot_poll.CHATTER_REPLY]  # onaylı abone de ilan kontrolü kullanır
    assert bot.send(Repo({FRIEND: "onayli"}), FRIEND, "2015 Toyota Vitz 5000£") == ["ILAN-CEVABI"]
    assert bot.ads[-1] == ("2015 Toyota Vitz 5000£", None, {"subscriber": FRIEND})  # kendi kotası/standart kurallar
    for status in ("durduruldu", "bekliyor", "reddedildi"):  # onaylı olmayan kişi ilan kontrolü kullanamaz
        n = len(bot.ads)
        assert bot.send(Repo({FRIEND: status}), FRIEND, "2015 Toyota Vitz 5000£") == [] and len(bot.ads) == n
    assert bot.send(Repo(), "99", "2015 Toyota Vitz 5000£") == []


def test_every_reply_fits_one_telegram_message(bot):
    repo = Repo({FRIEND: "onayli"})
    for chat in (OWNER, FRIEND):
        for cmd in sorted(bot_poll.OWNER_COMMANDS | bot_poll.SHARED_COMMANDS | {"/bilinmeyen"}):
            for reply in bot.send(repo, chat, cmd):
                assert 0 < len(reply) < 4096, (chat, cmd)


# --- güncellemeleri işleme: tek komut kaybolmaz ya da ikinci kez çalışmaz -------------------------------------------
def _poll(monkeypatch, repo, updates, handler):
    sent = []

    def fake_api(token, method, **kw):
        if method == "getUpdates":
            return updates
        sent.append((method, kw))
        return {}
    monkeypatch.setattr(bot_poll, "api", fake_api)
    monkeypatch.setattr(bot_poll, "_handle_message", handler)
    return sent


def _upd(i, chat=1):
    return {"update_id": i, "message": {"chat": {"id": chat}, "from": {"first_name": "x"}, "text": f"/m{i}"}}


def test_a_failing_update_is_answered_and_does_not_block_the_next_ones(monkeypatch):
    repo, handled = Repo(), []

    def handler(repo_, token, owner, msg):
        handled.append(msg["text"])
        if msg["text"] == "/m11":
            raise ValueError("beklenmedik")
    sent = _poll(monkeypatch, repo, [_upd(10), _upd(11), _upd(12)], handler)
    assert bot_poll.poll_bot(repo, "t", OWNER) == 3
    assert handled == ["/m10", "/m11", "/m12"] and repo.state["tg_offset"] == "13"
    assert sent == [("sendMessage", {"chat_id": "1", "text": bot_poll.ERROR_REPLY})]  # hata sessiz geçmez, ayrıntı sızmaz


def test_an_interrupted_run_does_not_replay_commands_that_already_ran(monkeypatch):
    repo, handled = Repo(), []

    def handler(repo_, token, owner, msg):
        if msg["text"] == "/m11":
            raise KeyboardInterrupt  # iş yarıda kesildi (iptal/zaman aşımı)
        handled.append(msg["text"])
    _poll(monkeypatch, repo, [_upd(10), _upd(11), _upd(12)], handler)
    with pytest.raises(KeyboardInterrupt):
        bot_poll.poll_bot(repo, "t", OWNER)
    assert handled == ["/m10"] and repo.state["tg_offset"] == "11"  # /m10 bir daha çalışmaz; /m11 sonraki turda yeniden denenir


# --- düğmeler -------------------------------------------------------------------------------------------------------
def test_a_double_tap_stores_one_vote_and_a_second_action_is_a_second_vote(bot):
    repo = Repo({FRIEND: "onayli"})
    tap = lambda action: bot_poll._handle_callback(repo, "t", OWNER, {"id": "1", "from": {"id": int(FRIEND)}, "data": f"fb:{action}:L1"})  # noqa: E731
    tap("ilgilendim")
    tap("ilgilendim")
    assert repo.conn.feedback == [("L1", "ilgilendim", "chat:2")]
    tap("yanlis_fiyat")
    assert len(repo.conn.feedback) == 2


# --- onay/ret: sahibe de haber gider ---------------------------------------------------------------------------------
class NameConn(Conn):
    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        if s.startswith("SELECT name FROM subscribers"):
            self.sql.append((s, params))
            self._row = {"name": "Erkan"}
            return self
        return super().execute(sql, params)


def _approve(monkeypatch, action, fail_welcome=False):
    sent = []

    def fake_api(token, method, **kw):
        if method == "sendMessage" and kw["chat_id"] == FRIEND and fail_welcome:
            raise bot_poll.TelegramError("sendMessage", 403, "bot was blocked by the user")
        sent.append((method, kw))
        return {}
    monkeypatch.setattr(bot_poll, "api", fake_api)
    monkeypatch.setattr(bot_poll, "_answer", lambda *a, **k: None)
    repo = Repo({FRIEND: "bekliyor"})
    repo.conn = NameConn({OWNER: "onayli", FRIEND: "bekliyor"})
    bot_poll._handle_callback(repo, "t", OWNER, {"id": "1", "from": {"id": int(OWNER)}, "data": f"sub:{action}:{FRIEND}"})
    return repo, {kw["chat_id"]: kw["text"] for m, kw in sent if m == "sendMessage"}


def test_approving_tells_both_the_new_subscriber_and_the_owner(monkeypatch):
    repo, texts = _approve(monkeypatch, "onayli")
    assert texts[FRIEND] == bot_poll.WELCOME_SUBSCRIBER and "ilan" in texts[FRIEND]
    assert texts[OWNER].startswith("✅ Erkan onaylandı; ona hoş geldin mesajı gitti")


def test_owner_is_warned_when_the_welcome_message_cannot_be_delivered(monkeypatch):
    repo, texts = _approve(monkeypatch, "onayli", fail_welcome=True)
    assert FRIEND not in texts and texts[OWNER].startswith("⚠️ Erkan onaylandı ama ona mesaj gönderemedim")


def test_rejecting_tells_the_owner_and_nobody_else(monkeypatch):
    repo, texts = _approve(monkeypatch, "reddedildi")
    assert texts == {OWNER: "⛔ Erkan reddedildi; ona bildirim gitmeyecek."}
