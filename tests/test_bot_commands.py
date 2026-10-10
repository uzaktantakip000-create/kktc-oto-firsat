"""Bot komutlarının tablo testi (05.10.2026): her komut sahip ve abone için tam BİR cevap verir; sessiz kalan komut yok.
Sahte veritabanı + yakalanan Telegram çağrıları; gerçek ağ/DB yok. Komutların iç mantığı kendi test dosyalarında (settings_store, price_book_cmd...)."""
import re
from datetime import datetime, timedelta, timezone

import pytest

from application import bot_poll, notify, report

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

    def alert_recent(self, key, hours):  # gerçeği (repository) veritabanı saatini kullanır; burada Python saati
        v = self.state.get(f"alert:{key}")
        return v is not None and datetime.fromisoformat(v) > datetime.now(timezone.utc) - timedelta(hours=hours)

    def mark_alerted(self, key):
        self.state[f"alert:{key}"] = datetime.now(timezone.utc).isoformat()

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
    monkeypatch.setattr(bot_poll.sources_cmd, "menu", lambda repo: ("KAYNAKLAR", {"inline_keyboard": []}))
    monkeypatch.setattr(bot_poll.sources_cmd, "propose_link", lambda repo, raw: (f"KAYNAK-ÖNERİSİ {raw}", None) if raw.startswith("http") else None)
    monkeypatch.setattr(bot_poll.sources_cmd, "add_instagram", lambda repo, a: f"EKLE{a}")
    monkeypatch.setattr(bot_poll.sources_cmd, "set_level", lambda repo, a: f"SEVIYE{a}")
    monkeypatch.setattr(bot_poll.sources_cmd, "change_status", lambda repo, a, to: f"{to}{a}")
    monkeypatch.setattr(bot_poll.llm_reader, "from_env", lambda repo: None)
    ads, cards, card_calls = [], {}, []
    monkeypatch.setattr(bot_poll.ad_check, "handle", lambda repo, raw, image, reader, **kw: ads.append((raw, image, kw)) or "ILAN-CEVABI")
    # ilan dosyası (application/dossier): yalnız `cards`ta olan link "veritabanında bulunur"; öbür her mesajda None (eski akış)
    monkeypatch.setattr(bot_poll.dossier, "handle", lambda repo, raw, subscriber=None: card_calls.append((raw, subscriber)) or cards.get(raw))

    class Bot:
        def send(self, repo, chat, text):
            calls.clear()
            bot_poll._handle_message(repo, "tok", OWNER, {"chat": {"id": int(chat)}, "from": {"first_name": "x"}, "text": text})
            return [kw["text"] for m, kw in calls if m == "sendMessage"]

    b = Bot()
    b.calls, b.ads, b.cards, b.card_calls = calls, ads, cards, card_calls
    return b


OWNER_CASES = [
    ("/durum", "DURUM"), ("/son", "SON"), ("/fiyat corolla 2014", "FIYAT corolla 2014"),
    ("/satti corolla 2014 7200", "SATTI corolla 2014 7200"), ("/kaynaklar", "KAYNAKLAR"),
    ("/kaynak_ekle abc", "EKLEabc"), ("/kaynak_ekle https://www.instagram.com/x/", "KAYNAK-ÖNERİSİ https://www.instagram.com/x/"), ("/kaynak_seviye x mor", "SEVIYE x mor"),
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
    assert bot.send(Repo({FRIEND: "durduruldu"}), FRIEND, "/yardim") == [bot_poll.HELP_SUBSCRIBER + "\n\n" + bot_poll.STOPPED_REPLY]
    for status in (None, "bekliyor", "reddedildi"):  # onaylı olmayana ilan kontrolü vaat edilmez
        out = bot.send(Repo({FRIEND: status} if status else {}), FRIEND, "/yardim")
        assert len(out) == 1 and "İlan kontrolü:" not in out[0] and "cevap veririm" not in out[0], status
        assert ("/start" in out[0]) is (status is None), status  # reddedilene yeniden başvuru daveti yok


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
    ("onayli", "Zaten onaylısın"), ("durduruldu", "durdurulmuş"), ("reddedildi", bot_poll.REJECTED_REPLY)])
def test_start_for_known_people_does_not_ping_the_owner_again(bot, status, start_reply):
    repo = Repo({FRIEND: status})
    out = bot.send(repo, FRIEND, "/start")
    assert len(out) == 1 and start_reply in out[0]
    assert not any(m == "sendMessage" and kw["chat_id"] == OWNER for m, kw in bot.calls)
    assert repo.conn.subs[FRIEND] == status and not any(s.startswith("INSERT INTO subscribers") for s, _ in repo.conn.sql)


def test_start_for_a_rejected_person_is_neutral_and_says_nothing_was_received(bot):
    repo = Repo({FRIEND: "reddedildi"})
    for _ in range(3):  # her /start sahibi yeniden rahatsız etmez
        assert bot.send(repo, FRIEND, "/start") == [bot_poll.REJECTED_REPLY]
        assert not any(kw["chat_id"] == OWNER for m, kw in bot.calls)
    assert "alındı" not in bot_poll.REJECTED_REPLY and "/start" not in bot_poll.REJECTED_REPLY
    assert repo.state == {} and repo.conn.subs[FRIEND] == "reddedildi"


OWNER_PING_BUTTONS = {"inline_keyboard": [[{"text": "✅ Onayla", "callback_data": f"sub:onayli:{FRIEND}"},
                                           {"text": "⛔ Reddet", "callback_data": f"sub:reddedildi:{FRIEND}"}]]}


def test_start_for_a_new_person_asks_the_owner_first_then_confirms_once(bot):
    repo = Repo()
    out = bot.send(repo, FRIEND, "/start")
    assert out == ["👤 x bildirim almak istiyor.", "Başvurun alındı. Onaylanınca haber vereceğim."]  # önce sahip, sonra başvuran
    assert [kw["reply_markup"] for m, kw in bot.calls if kw["chat_id"] == OWNER] == [OWNER_PING_BUTTONS]  # biçim aynı
    assert repo.conn.subs[FRIEND] == "bekliyor"
    assert bot.send(repo, FRIEND, "/start") == ["Başvurun onay bekliyor; onaylanınca haber vereceğim."]  # hemen ikinci kez: sahibe tekrar yazılmaz
    assert bot.send(repo, OWNER, "/start") == [bot_poll.WELCOME_OWNER]


def test_a_waiting_applicant_reasks_the_owner_at_most_every_six_hours(bot):
    repo = Repo({FRIEND: "bekliyor"})  # eski kayıt (iz yok): sahibe giden ilk soru kaybolmuş olabilir
    assert bot.send(repo, FRIEND, "/start") == ["👤 x bildirim almak istiyor.", "Başvurun onay bekliyor; onaylanınca haber vereceğim."]
    assert [kw["reply_markup"] for m, kw in bot.calls if kw["chat_id"] == OWNER] == [OWNER_PING_BUTTONS]
    assert bot.send(repo, FRIEND, "/start") == ["Başvurun onay bekliyor; onaylanınca haber vereceğim."]
    repo.state[f"alert:basvuru:{FRIEND}"] = (datetime.now(timezone.utc) - timedelta(hours=5, minutes=50)).isoformat()
    assert bot.send(repo, FRIEND, "/start") == ["Başvurun onay bekliyor; onaylanınca haber vereceğim."]  # 6 saat dolmadı
    repo.state[f"alert:basvuru:{FRIEND}"] = (datetime.now(timezone.utc) - timedelta(hours=6, minutes=10)).isoformat()
    assert bot.send(repo, FRIEND, "/start")[0] == "👤 x bildirim almak istiyor."  # 6 saat geçti: bir kez daha sorulur
    assert bot.send(repo, FRIEND, "/start") == ["Başvurun onay bekliyor; onaylanınca haber vereceğim."]
    assert repo.conn.subs[FRIEND] == "bekliyor" and not any(s.startswith("INSERT INTO subscribers") for s, _ in repo.conn.sql)


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


def test_a_known_listing_link_gets_its_dossier_for_owner_and_subscriber(bot):
    link = "https://www.kktcarabam.com/259593-honda-fit-lefkosa-benzin-otomatik"
    bot.cards[link] = "📂 İLAN DOSYASI"
    assert bot.send(Repo(), OWNER, link) == ["📂 İLAN DOSYASI"] and bot.ads == []  # kaynak önerisi değil, ilan kontrolü değil
    assert bot.send(Repo({FRIEND: "onayli"}), FRIEND, link) == ["📂 İLAN DOSYASI"]
    assert bot.card_calls[-1] == (link, FRIEND)  # abonenin kendi kotası
    assert bot.send(Repo({FRIEND: "bekliyor"}), FRIEND, link) == [bot_poll.PENDING_REPLY]  # onaysız kişiye dosya yok
    assert bot.card_calls[-1] == (link, FRIEND) and len(bot.card_calls) == 2


def test_owner_free_text_unknown_commands_and_links(bot):
    repo = Repo()
    assert bot.send(repo, OWNER, "tamam") == [bot_poll.CHATTER_REPLY] and bot.ads == []  # kota/yapay zekâ harcanmaz
    assert bot.send(repo, OWNER, "teşekkürler 👍") == [bot_poll.CHATTER_REPLY]
    link = "https://kibrisarabaal.com/ilan/3107-2012-model-otomatik-666946/"
    assert bot.send(repo, OWNER, link) == [f"KAYNAK-ÖNERİSİ {link}"] and bot.ads == []  # sahibin linki: kaynak yönetimi cevaplar (07.10.2026)
    assert bot.send(Repo({FRIEND: "onayli"}), FRIEND, link) == [bot_poll.LINK_REPLY]  # abonenin linki: eskisi gibi
    assert bot.send(repo, OWNER, "/yanlis_komut") == [bot_poll.UNKNOWN_OWNER_REPLY]
    assert bot.send(repo, OWNER, "2015 Toyota Vitz 5000£") == ["ILAN-CEVABI"] and bot.ads == [("2015 Toyota Vitz 5000£", None, {})]
    assert bot.send(repo, OWNER, "Satılık 2012 model araç 8500 stg 240000 km") == ["ILAN-CEVABI"]  # rakamlı gerçek ilan metni
    assert bot.send(Repo({FRIEND: "onayli"}), FRIEND, "merhaba") == [bot_poll.CHATTER_REPLY]  # onaylı abone de ilan kontrolü kullanır
    assert bot.send(Repo({FRIEND: "onayli"}), FRIEND, "2015 Toyota Vitz 5000£") == ["ILAN-CEVABI"]
    assert bot.ads[-1] == ("2015 Toyota Vitz 5000£", None, {"subscriber": FRIEND})  # kendi kotası/standart kurallar
    # onaylı olmayan kişi ilan kontrolü kullanamaz (yapay zekâ maliyeti yok) ama sessiz de kalınmaz
    for status, reply in (("durduruldu", bot_poll.STOPPED_REPLY), ("bekliyor", bot_poll.PENDING_REPLY), ("reddedildi", bot_poll.REJECTED_REPLY)):
        n = len(bot.ads)
        for text in ("2015 Toyota Vitz 5000£", "merhaba"):
            assert bot.send(Repo({FRIEND: status}), FRIEND, text) == [reply] and len(bot.ads) == n, (status, text)
    assert bot.send(Repo(), "99", "2015 Toyota Vitz 5000£") == [bot_poll.GUEST_REPLY]
    assert bot_poll.PENDING_REPLY == "Başvurun onay bekliyor; onaylanınca ilan kontrolü de açılır."
    assert bot_poll.STOPPED_REPLY == "Bildirimlerin kapalı. İlan kontrolü için önce /basla yaz."


def _photo(chat, caption="Vitz"):
    return {"chat": {"id": int(chat)}, "from": {"first_name": "x"}, "photo": [{"file_id": "f", "file_size": 1000}], "caption": caption}


def test_photos_from_people_who_are_not_approved_get_a_reply_but_no_download_or_check(bot, monkeypatch):
    monkeypatch.setattr(bot_poll, "_download_photo", lambda *a: pytest.fail("onaylı olmayanın görüntüsü indirilmemeli"))
    for status, reply in ((None, bot_poll.GUEST_REPLY), ("bekliyor", bot_poll.PENDING_REPLY),
                          ("durduruldu", bot_poll.STOPPED_REPLY), ("reddedildi", bot_poll.REJECTED_REPLY)):
        bot.calls.clear()
        bot_poll._handle_message(Repo({FRIEND: status} if status else {}), "tok", OWNER, _photo(FRIEND))
        assert [kw["text"] for m, kw in bot.calls] == [reply] and bot.ads == [], status


def test_a_subscribers_photo_is_checked_against_the_subscribers_own_quota(monkeypatch):
    """Gerçek ad_check.handle (yapay zekâ anahtarı yok → ağ yok): abonenin görüntüsü kendi kotasından düşer, sahibinkinden değil."""
    sent = []
    monkeypatch.setattr(bot_poll, "api", lambda token, method, **kw: sent.append(kw["text"]))
    monkeypatch.setattr(bot_poll, "_download_photo", lambda token, photo: b"img")
    monkeypatch.setattr(bot_poll.llm_reader, "from_env", lambda repo: None)
    repo = Repo({FRIEND: "onayli"})
    bot_poll._handle_message(repo, "tok", OWNER, _photo(FRIEND))
    day = r"adcheck:\d{4}-\d{2}-\d{2}"
    (key,) = repo.state
    assert re.fullmatch(rf"{day}:{FRIEND}", key) and repo.state[key] == "1"  # abonenin kendi kotası: adcheck:<gün>:<sohbet>
    assert len(sent) == 1 and "yapay zekâ anahtarı" in sent[0]
    bot_poll._handle_message(repo, "tok", OWNER, _photo(OWNER))  # karşılaştırma: sahibin görüntüsü sahibin kotasından
    (owner_key,) = set(repo.state) - {key}
    assert re.fullmatch(day, owner_key) and repo.state[owner_key] == "1" and repo.state[key] == "1"


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


def test_if_the_owner_cannot_be_asked_the_applicant_hears_no_received_and_the_next_start_asks_again(monkeypatch):
    """Sahibe giden başvuru sorusu düşerse (ör. 429) başvurana "alındı" denmez, hata cevabı gider; tekrar /start sahibe yeniden sorar."""
    repo, sent, owner_down = Repo(), [], [True]
    batches = [[{"update_id": i, "message": {"chat": {"id": int(FRIEND)}, "from": {"first_name": "Erkan"}, "text": "/start"}}] for i in (1, 2)]

    def fake_api(token, method, **kw):
        if method == "getUpdates":
            return batches.pop(0)
        if kw["chat_id"] == OWNER and owner_down[0]:
            raise bot_poll.TelegramError("sendMessage", 429, "Too Many Requests")
        sent.append((kw["chat_id"], kw["text"], kw.get("reply_markup")))
        return {}
    monkeypatch.setattr(bot_poll, "api", fake_api)
    assert bot_poll.poll_bot(repo, "t", OWNER) == 1
    assert sent == [(FRIEND, bot_poll.ERROR_REPLY, None)]  # "alındı" DENMEDİ; "birazdan tekrar dene" dendi
    assert repo.conn.subs[FRIEND] == "bekliyor" and f"alert:basvuru:{FRIEND}" not in repo.state
    owner_down[0] = False
    assert bot_poll.poll_bot(repo, "t", OWNER) == 1
    assert sent[1:] == [(OWNER, "👤 Erkan bildirim almak istiyor.", OWNER_PING_BUTTONS),
                        (FRIEND, "Başvurun onay bekliyor; onaylanınca haber vereceğim.", None)]
    assert f"alert:basvuru:{FRIEND}" in repo.state and repo.state["tg_offset"] == "3"


def _cb(i, sender, data=f"sub:onayli:{FRIEND}"):
    return {"update_id": i, "callback_query": {"id": str(i), "from": {"id": int(sender)}, "data": data}}


def test_a_failing_owner_button_tells_the_owner_to_press_again_but_nobody_else(monkeypatch):
    repo = Repo()
    sent = _poll(monkeypatch, repo, [_cb(20, OWNER), _cb(21, FRIEND, "fb:pas:L1")], lambda *a: None)

    def boom(repo_, token, owner, cb):
        raise RuntimeError("veritabanı koptu")
    monkeypatch.setattr(bot_poll, "_handle_callback", boom)
    assert bot_poll.poll_bot(repo, "t", OWNER) == 2 and repo.state["tg_offset"] == "22"
    assert sent == [("sendMessage", {"chat_id": OWNER, "text": bot_poll.CALLBACK_ERROR_REPLY})]  # abonenin düğmesi için mesaj yok

    def telegram_down(token, method, **kw):  # hata mesajının kendisi de gidemezse tur yine bozulmaz
        if method == "getUpdates":
            return [_cb(30, OWNER)]
        raise bot_poll.TelegramError("sendMessage", 429, "Too Many Requests")
    monkeypatch.setattr(bot_poll, "api", telegram_down)
    assert bot_poll.poll_bot(repo, "t", OWNER) == 1 and repo.state["tg_offset"] == "31"


# --- düğmeler -------------------------------------------------------------------------------------------------------
def test_a_double_tap_stores_one_vote_and_a_second_action_is_a_second_vote(bot):
    repo = Repo({FRIEND: "onayli"})
    tap = lambda action: bot_poll._handle_callback(repo, "t", OWNER, {"id": "1", "from": {"id": int(FRIEND)}, "data": f"fb:{action}:L1"})  # noqa: E731
    tap("ilgilendim")
    tap("ilgilendim")
    assert repo.conn.feedback == [("L1", "ilgilendim", "chat:2")]
    tap("yanlis_fiyat")
    assert len(repo.conn.feedback) == 2


# --- oy düğmesi: oy verilince BASILAN mesajın düğmesi "✅ Kaydedildi" olur ----------------------------------------------
LID = "3f2b8c1e-5a7d-4e9a-8b1c-2d4f6a8c0e12"
WA = "https://wa.me/905330000021?text=Merhaba"
WA_ROW = [{"text": "📲 WhatsApp'tan ulaş", "url": WA}]


def saved(text, listing=LID):
    return {"text": text, "callback_data": f"fb:kayitli:{listing}"}


class VoteRepo(Repo):
    def __init__(self, subs=None):
        super().__init__(subs)
        self.sold, self.blocked = [], []

    def mark_sold(self, listing_id):
        self.sold.append(listing_id)

    def block_seller_of(self, listing_id, reason):
        self.blocked.append(listing_id)
        return True


class Telegram:
    """bot_poll.api yerine: bütün Telegram çağrılarını yakalar (düğme cevabı `_answer` da buradan geçer); `fail`: yöntem -> fırlatılacak hata."""

    def __init__(self, monkeypatch, fail=None):
        self.calls, self.fail = [], fail or {}
        monkeypatch.setattr(bot_poll, "api", self)

    def __call__(self, token, method, **kw):
        self.calls.append((method, kw))
        if method in self.fail:
            raise self.fail[method]
        return {}

    @property
    def edits(self):
        return [kw for m, kw in self.calls if m == "editMessageReplyMarkup"]

    @property
    def toasts(self):
        return [kw.get("text") for m, kw in self.calls if m == "answerCallbackQuery"]


def old_keyboard(lid=LID, wa=None):
    """Eski (03.10.2026 öncesi) fırsat mesajı: iki satırda 5 oy düğmesi."""
    def btn(text, action):
        return {"text": text, "callback_data": f"fb:{action}:{lid}"}
    return {"inline_keyboard": ([[{"text": "📲 WhatsApp'tan ulaş", "url": wa}]] if wa else [])
            + [[btn("İlgileniyorum", "ilgilendim"), btn("Pas", "pas")],
               [btn("Yanlış fiyat", "yanlis_fiyat"), btn("Zaten satılmış", "satilmis"), btn("Kusurlu/sahte", "kusurlu")]]}


def press(repo, sender, action, markup, *, message_id=77, listing=LID, owner=OWNER, chat=None):
    """`sender` basar; mesaj `chat` (varsayılan: göndericinin kendi sohbeti) içindeki `message_id`'li mesajdır ve `markup` düğmelerini taşır."""
    message = {"message_id": message_id, "chat": {"id": int(chat or sender)}}
    if markup is not None:
        message["reply_markup"] = markup
    bot_poll._handle_callback(repo, "t", owner, {"id": "9", "from": {"id": int(sender)}, "data": f"fb:{action}:{listing}", "message": message})


def control_vote(tg, repo):
    """Karşı sınama: sahibin oyu düğmeyi gerçekten değiştirir. "Düzenleme yok" sınamalarına eklenir; düzenleme yolu hiç çalışmasa o sınamalar boş geçerdi."""
    press(repo, OWNER, "ilgilendim", notify.keyboard("KONTROL-ILAN", WA), message_id=1, listing="KONTROL-ILAN")
    assert [(e["chat_id"], e["message_id"]) for e in tg.edits] == [(int(OWNER), 1)]
    return ("KONTROL-ILAN", "ilgilendim", f"chat:{OWNER}")


@pytest.mark.parametrize("action,label", [("ilgilendim", "✅ Kaydedildi: 👍 İşe yarar"), ("yanlis_fiyat", "✅ Kaydedildi: 👎 Yanlış")])
def test_owner_vote_is_stored_the_pressed_message_shows_saved_and_the_whatsapp_button_stays(monkeypatch, action, label):
    tg, repo = Telegram(monkeypatch), VoteRepo()
    press(repo, OWNER, action, notify.keyboard(LID, WA))
    assert repo.conn.feedback == [(LID, action, f"chat:{OWNER}")]
    (edit,) = tg.edits
    assert (edit["chat_id"], edit["message_id"]) == (int(OWNER), 77)
    assert edit["reply_markup"] == {"inline_keyboard": [WA_ROW, [saved(label)]]}  # tek düğme; WhatsApp satırı aynen
    assert tg.toasts == ["Not aldım 👍"]  # eski bildirim yazısı değişmedi


def test_a_message_without_a_whatsapp_button_ends_up_with_only_the_saved_button(monkeypatch):
    tg = Telegram(monkeypatch)
    press(VoteRepo(), OWNER, "ilgilendim", notify.keyboard(LID))
    assert tg.edits[0]["reply_markup"] == {"inline_keyboard": [[saved("✅ Kaydedildi: 👍 İşe yarar")]]}


@pytest.mark.parametrize("action,name", [("pas", "Pas"), ("satilmis", "Zaten satılmış"), ("kusurlu", "Kusurlu/sahte"),
                                         ("ilgilendim", "👍 İşe yarar"), ("yanlis_fiyat", "👎 Yanlış")])
def test_old_messages_with_five_buttons_collapse_to_one_saved_button_named_after_the_vote(monkeypatch, action, name):
    tg, repo = Telegram(monkeypatch), VoteRepo()
    press(repo, OWNER, action, old_keyboard(wa=WA))
    assert tg.edits[0]["reply_markup"] == {"inline_keyboard": [WA_ROW, [saved(f"✅ Kaydedildi: {name}")]]}  # iki satır + 5 düğme -> TEK düğme
    assert repo.conn.feedback == [(LID, action, f"chat:{OWNER}")]
    assert (repo.sold, repo.blocked) == ([LID] if action == "satilmis" else [], [])  # eski eylemler aynen: satılmış kapatır; kusurlu 10 oydan önce yalnız kayıt


def test_the_monthly_audit_buttons_also_turn_into_saved(monkeypatch):
    tg = Telegram(monkeypatch)
    audit = {"inline_keyboard": [[{"text": "✅ Doğru", "callback_data": f"fb:audit_dogru:{LID}"},
                                  {"text": "❌ Yanlış", "callback_data": f"fb:audit_yanlis:{LID}"}]]}
    press(VoteRepo(), OWNER, "audit_yanlis", audit)
    assert tg.edits[0]["reply_markup"] == {"inline_keyboard": [[saved("✅ Kaydedildi: Yanlış")]]}


def test_a_weekly_report_keyboard_changes_only_the_pressed_listings_buttons(monkeypatch):
    """Rapor tek mesajda birçok ilanın düğmesini taşır ve satıra 2 ilan sığdırır: yanındaki ilanın düğmeleri silinmemeli."""
    ids = [f"00000000-0000-0000-0000-{i:012d}" for i in (1, 2, 3)]
    markup = report._vote_keyboard([{"id": i} for i in ids])
    original = [[dict(b) for b in row] for row in markup["inline_keyboard"]]
    assert [[b["text"] for b in row] for row in original] == [["1 👍", "1 👎", "2 👍", "2 👎"], ["3 👍", "3 👎"]]  # ön koşul: bir satırda iki ilan
    tg, repo = Telegram(monkeypatch), VoteRepo()
    press(repo, OWNER, "yanlis_fiyat", markup, listing=ids[1])
    rows = tg.edits[-1]["reply_markup"]["inline_keyboard"]
    assert rows == [[original[0][0], original[0][1], saved("2 ✅ 👎", ids[1])], original[1]]  # 1. ve 3. ilan aynen
    press(repo, OWNER, "ilgilendim", {"inline_keyboard": rows}, listing=ids[2])  # Telegram bir sonraki basışta GÜNCEL düğmeleri yollar
    rows = tg.edits[-1]["reply_markup"]["inline_keyboard"]
    assert rows == [[original[0][0], original[0][1], saved("2 ✅ 👎", ids[1])], [saved("3 ✅ 👍", ids[2])]]
    press(repo, OWNER, "ilgilendim", {"inline_keyboard": rows}, listing=ids[0])
    assert tg.edits[-1]["reply_markup"]["inline_keyboard"] == [[saved("1 ✅ 👍", ids[0]), saved("2 ✅ 👎", ids[1])], [saved("3 ✅ 👍", ids[2])]]
    assert repo.conn.feedback == [(ids[1], "yanlis_fiyat", "chat:1"), (ids[2], "ilgilendim", "chat:1"), (ids[0], "ilgilendim", "chat:1")]
    assert markup["inline_keyboard"] == original  # gelen düğme dizisi yerinde değiştirilmedi


@pytest.mark.parametrize("error", [bot_poll.TelegramError("editMessageReplyMarkup", 400, "Bad Request: message is not modified"),
                                   bot_poll.TelegramError("editMessageReplyMarkup", 400, "Bad Request: message to edit not found"),
                                   bot_poll.TelegramError("editMessageReplyMarkup", 0, "ConnectError"),
                                   ValueError("beklenmedik yanıt")])
def test_a_failing_edit_never_loses_the_vote_or_the_answer_and_logs_one_short_line_without_ids(monkeypatch, capsys, error):
    owner = "55501234"
    tg, repo = Telegram(monkeypatch, fail={"editMessageReplyMarkup": error}), VoteRepo()
    repo.conn.subs[owner] = "onayli"
    press(repo, owner, "yanlis_fiyat", notify.keyboard(LID, WA), owner=owner)
    assert repo.conn.feedback == [(LID, "yanlis_fiyat", f"chat:{owner}")] and tg.toasts == ["Not aldım 👍"] and len(tg.edits) == 1
    out = capsys.readouterr().out
    assert out.startswith("oy düğmesi güncellenemedi") and out.count("\n") == 1 and owner not in out and LID not in out


@pytest.mark.parametrize("markup", [None, {"inline_keyboard": []}, notify.keyboard("BASKA-ILAN", WA),
                                    {"inline_keyboard": [WA_ROW, [saved("✅ Kaydedildi: 👍 İşe yarar")]]}])  # düğmesiz / başka ilanın / zaten değişmiş
def test_nothing_to_replace_means_no_edit_but_the_vote_is_still_stored_and_answered(monkeypatch, markup):
    tg, repo = Telegram(monkeypatch), VoteRepo()
    control = control_vote(tg, repo)
    press(repo, OWNER, "ilgilendim", markup)
    assert repo.conn.feedback == [control, (LID, "ilgilendim", f"chat:{OWNER}")] and len(tg.edits) == 1 and tg.toasts == ["Not aldım 👍"] * 2


def test_a_callback_without_a_message_still_stores_and_answers(monkeypatch):
    tg, repo = Telegram(monkeypatch), VoteRepo()
    control = control_vote(tg, repo)
    bot_poll._handle_callback(repo, "t", OWNER, {"id": "9", "from": {"id": int(OWNER)}, "data": f"fb:pas:{LID}"})  # Telegram mesajı yollamadı
    assert repo.conn.feedback == [control, (LID, "pas", f"chat:{OWNER}")] and len(tg.edits) == 1 and tg.toasts == ["Not aldım 👍"] * 2


def test_a_repeat_press_on_a_stale_keyboard_stores_one_vote_but_still_shows_saved(monkeypatch):
    """Düzenleme ilk basışta düşmüşse (eski mesaj, hız sınırı...) ikinci basış oyu yinelemez, düğmeyi yine düzeltmeyi dener."""
    tg, repo = Telegram(monkeypatch, fail={"editMessageReplyMarkup": bot_poll.TelegramError("editMessageReplyMarkup", 429, "Too Many Requests")}), VoteRepo()
    press(repo, OWNER, "ilgilendim", notify.keyboard(LID, WA))
    tg.fail = {}
    press(repo, OWNER, "ilgilendim", notify.keyboard(LID, WA))
    assert repo.conn.feedback == [(LID, "ilgilendim", f"chat:{OWNER}")] and len(tg.edits) == 2
    assert tg.edits[1]["reply_markup"]["inline_keyboard"][1] == [saved("✅ Kaydedildi: 👍 İşe yarar")]


def test_someone_not_approved_stores_nothing_and_no_button_changes(monkeypatch):
    tg, repo = Telegram(monkeypatch), VoteRepo()
    control = control_vote(tg, repo)
    press(repo, "99", "ilgilendim", notify.keyboard(LID, WA))
    assert repo.conn.feedback == [control] and len(tg.edits) == 1 and tg.toasts == ["Not aldım 👍", None]


@pytest.mark.parametrize("sender", [OWNER, FRIEND, "99"])
def test_pressing_the_saved_button_stores_nothing_and_says_already_saved(monkeypatch, sender):
    tg, repo = Telegram(monkeypatch), VoteRepo({FRIEND: "onayli"})
    press(repo, sender, "kayitli", {"inline_keyboard": [WA_ROW, [saved("✅ Kaydedildi: 👍 İşe yarar")]]})
    assert repo.conn.sql == [] and repo.conn.feedback == [] and (repo.sold, repo.blocked) == ([], [])  # veritabanına hiç dokunulmadı
    assert tg.edits == [] and tg.toasts == ["Zaten kaydedildi"]


def test_saved_button_callback_data_fits_telegrams_64_bytes_for_uuid_listing_ids():
    import uuid
    lid = str(uuid.uuid4())
    assert bot_poll.SAVED_ACTION == "kayitli" and bot_poll.SAVED_ACTION not in bot_poll.FEEDBACK_ACTIONS  # basınca oy olarak yazılmaz
    for action in (*bot_poll.FEEDBACK_ACTIONS, bot_poll.SAVED_ACTION):
        assert len(f"fb:{action}:{lid}".encode()) <= 64, action
    for action in bot_poll.FEEDBACK_ACTIONS:
        markup = bot_poll._saved_markup(old_keyboard(lid, WA), lid, action)
        assert all(len(b["callback_data"].encode()) <= 64 for row in markup["inline_keyboard"] for b in row if "callback_data" in b)


def test_a_subscribers_press_edits_only_the_subscribers_message_and_stays_a_subscriber_vote(monkeypatch):
    """Sahip ve abonenin mesajları ayrıdır (her sohbete ayrı mesaj): abonenin basışı yalnız kendi mesajını düzenler, oyu 'chat:<abone>' olarak
    kaydolur ve sahibin eylemlerini tetiklemez. Sayımların abone oyunu saymaması (OWNER_VOTE_SQL) test_repository_db.py'de gerçek veritabanıyla."""
    tg, repo = Telegram(monkeypatch), VoteRepo({FRIEND: "onayli"})
    press(repo, FRIEND, "satilmis", notify.keyboard(LID, WA), message_id=88)  # abonenin kendi kopyası; sahibin kopyası (sohbet 1, mesaj 77) hiç gelmedi
    assert repo.conn.feedback == [(LID, "satilmis", f"chat:{FRIEND}")] and (repo.sold, repo.blocked) == ([], [])
    assert [(e["chat_id"], e["message_id"]) for e in tg.edits] == [(int(FRIEND), 88)]  # sahibin sohbetine/mesajına hiçbir çağrı yok
    assert not any(kw.get("chat_id") in (int(OWNER), OWNER) for m, kw in tg.calls)
    assert tg.edits[0]["reply_markup"]["inline_keyboard"][1] == [saved("✅ Kaydedildi: Zaten satılmış")]


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


@pytest.mark.parametrize("action", ["onayli", "reddedildi"])
def test_only_the_owner_can_approve_or_reject(monkeypatch, action):
    sent = []
    monkeypatch.setattr(bot_poll, "api", lambda token, method, **kw: sent.append((method, kw)))
    monkeypatch.setattr(bot_poll, "_answer", lambda *a, **k: None)
    for presser in (FRIEND, "3"):  # bekleyen kişi kendi düğmesini (iletilmiş mesaj) basamaz; başka onaylı abone de
        repo = Repo({FRIEND: "bekliyor", "3": "onayli"})
        bot_poll._handle_callback(repo, "t", OWNER, {"id": "1", "from": {"id": int(presser)}, "data": f"sub:{action}:{FRIEND}"})
        assert repo.conn.subs[FRIEND] == "bekliyor" and not any(s.startswith("UPDATE subscribers") for s, _ in repo.conn.sql), presser
    assert sent == []  # kimseye mesaj yok
