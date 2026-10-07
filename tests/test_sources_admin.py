"""Bottan kaynak yönetimi (07.10.2026): link türü, aç/kapat korumaları, ekleme, düğmeler ve sosyal okuyucuya dosya aktarımı.
Sahte kaynak listesi (SQL yok; SQL'ler tests/test_sources_admin_db.py'de gerçek PostgreSQL ile)."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from application import bot_poll, source_readers, sources_cmd
from domain.source_links import key_of_url, parse_source_link
from entrypoints import cron_collect, tick

OWNER = "1"


def src(id, platform, name, url, status="aktif", priority=10, listings_7d=5, strong_30d=0):
    return {"id": id, "platform": platform, "name": name, "url": url, "status": status, "priority": priority, "alert_level": "yesil",
            "last_checked_at": None, "listings_7d": listings_7d, "strong_30d": strong_30d}


BASE = [
    src("w1", "web", "KKTCar", "https://kktcar.com/en/search/results", strong_30d=2),
    src("w2", "web", "KibrisArabaAl", "https://kibrisarabaal.com/"),
    src("w3", "web", "PazarKibris", "https://pazarkibris.com/", status="aday"),  # okuyucusu var: kapalı sayılır
    src("w4", "web", "BiArabacik", "https://biarabacik.com/", status="aday"),  # okuyucusu yok: istek
    src("w5", "web", "GalerimPlus", "https://www.galerimplus.com", status="erisim_reddediyor"),
    src("i1", "instagram", "kibris.car", "https://www.instagram.com/kibris.car/"),
    src("i2", "instagram", "eski", "https://www.instagram.com/eski.hesap/", status="pasif"),
    src("i3", "instagram", "gizli", "https://www.instagram.com/gizli.aday/", status="aday"),  # eski aday: listede görünmez
    src("f1", "facebook", "KKTC ARABA PAZARI", "https://www.facebook.com/groups/469402498541872/"),
    src("f2", "facebook", "Marketplace", "https://www.facebook.com/marketplace/110929015601194/cars/"),  # grup değil: görünmez
]


class Store:
    def __init__(self, rows):
        self.rows, self.state, self.conn = [dict(r) for r in rows], {}, None

    def get_state(self, k, default=None):
        return self.state.get(k, default)

    def set_state(self, k, v):
        self.state[k] = v

    def by(self, id):
        return next(r for r in self.rows if r["id"] == id)


@pytest.fixture
def store(monkeypatch):
    st = Store(BASE)
    monkeypatch.setattr(sources_cmd, "_rows", lambda repo, platforms: [dict(r) for r in repo.rows if r["platform"] in platforms])
    monkeypatch.setattr(sources_cmd, "_row", lambda repo, sid: next((dict(r) for r in repo.rows if r["id"] == sid), None))
    monkeypatch.setattr(sources_cmd, "_set_status", lambda repo, sid, status: repo.by(sid).update(status=status))

    def insert(repo, link, status):
        name = link.handle if link.platform != "facebook" else f"Facebook grubu {link.handle}"
        repo.rows.append(src(f"n{len(repo.rows)}", link.platform, name, link.url, status=status, priority=99))

    monkeypatch.setattr(sources_cmd, "_insert", insert)
    return st


# ---- link türü ----

@pytest.mark.parametrize("raw,kind,handle", [
    ("https://www.instagram.com/Kibris.Car/?hl=tr", "instagram", "kibris.car"),
    ("https://instagram.com/araba__kibris", "instagram", "araba__kibris"),
    ("https://www.instagram.com/p/ABC123/", "ig_post", ""),
    ("https://www.instagram.com/reel/ABC/", "ig_post", ""),
    ("https://www.instagram.com/", "ig_post", ""),
    ("https://www.facebook.com/groups/469402498541872/", "facebook", "469402498541872"),
    ("https://m.facebook.com/groups/Kibris.Arabam?ref=share", "facebook", "kibris.arabam"),
    ("https://www.facebook.com/groups/469402498541872/posts/123/", "fb_post", ""),
    ("https://www.facebook.com/marketplace/item/1/", "fb_post", ""),
    ("https://www.facebook.com/share/g/1AbCd/", "fb_share", ""),
    ("https://www.BiArabacik.com/ilan/5", "web", "biarabacik.com"),
    ("https://kktcar.com/en/search/results", "web", "kktcar.com"),
])
def test_link_kinds(raw, kind, handle):
    link = parse_source_link(raw)
    assert link is not None and (link.kind, link.handle) == (kind, handle)


@pytest.mark.parametrize("raw", ["kibris.car", "http://", "https://localhost/x", "https://a b.com", "https://x.com/a b", "", None])
def test_not_links(raw):
    assert parse_source_link(raw) is None


def test_keys_of_stored_urls():
    assert key_of_url("web", "https://www.kktcarabam.com/kategori/ikinci-el-araclar") == "web:kktcarabam.com"
    assert key_of_url("instagram", "https://www.instagram.com/kibris__arabam/") == "ig:kibris__arabam"
    assert key_of_url("facebook", "https://www.facebook.com/groups/Kibris.Arabam/") == "fb:kibris.arabam"
    assert key_of_url("facebook", "https://www.facebook.com/marketplace/1/cars/") is None
    assert key_of_url("instagram", "https://www.facebook.com/groups/1/") is None


def test_reader_registry_matches_collectors_and_schedule():
    assert set(source_readers.WEB_READERS) == set(cron_collect.WEB_COLLECTORS)
    assert set(source_readers.WEB_READERS) <= set(cron_collect.JOBS)
    assert set(source_readers.WEB_READERS) - {"kktcarabam"} <= set(tick.SCHEDULE)  # KKTCarabam tarayıcılı işte (kktc-browser / collect-browser)
    assert source_readers.reader_for_host("m.kibrisarabaal.com") == "kibrisarabaal"
    assert source_readers.reader_for_host("kibrisaraba.com") is None  # benzer ad başka site


# ---- görünümler ----

def test_menu_counts_and_buttons(store):
    text, markup = sources_cmd.menu(store)
    assert "🌐 Siteler: 2 açık, 1 kapalı, 1 istek" in text
    assert "📸 Instagram: 1 açık, 1 kapalı" in text and "👥 Facebook: 1 açık" in text
    assert [b["callback_data"] for b in markup["inline_keyboard"][0]] == ["src:list:web", "src:list:instagram", "src:list:facebook"]


def test_web_list_shows_toggles_requests_and_blocked_sites(store):
    text, markup = sources_cmd.category(store, "web")
    data = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    assert "src:off:w1" in data and "src:off:w2" in data and "src:on:w3" in data
    assert not any(d.endswith(":w4") for d in data)  # okuyucusu olmayan istek açılamaz: düğmesi yok
    assert "📝 BiArabacik — istek" in text and "⏸ PazarKibris — kapalı" in text and "30 günde 2 🟢" in text
    assert "Erişim vermeyen siteler: GalerimPlus" in text and data[-1] == "src:menu:"
    assert all(len(d.encode()) <= 64 for d in data)


def test_social_list_hides_old_candidates_and_non_groups(store):
    text, markup = sources_cmd.category(store, "instagram")
    assert "kibris.car" in text and "eski — kapalı" in text and "gizli" not in text and "en çok 15" in text
    text, _ = sources_cmd.category(store, "facebook")
    assert "KKTC ARABA PAZARI" in text and "Marketplace" not in text and "uzak ekrandan" in text


def test_social_list_shows_per_source_numbers_from_the_reader_file(store, tmp_path, monkeypatch):
    path = tmp_path / "durum.json"
    path.write_text(json.dumps({"surum": 1, "yazildi_utc": datetime.now(timezone.utc).isoformat(), "platformlar": {"instagram": {
        "sonuc": "tamam", "son_tur_utc": datetime.now(timezone.utc).isoformat(), "yeni_ilan": 3,
        "kaynaklar": {"ig:kibris.car": {"son_okuma_utc": "2026-10-07T08:00:00+00:00", "yeni_ilan_7g": 12, "hata": None}}}}}))
    monkeypatch.setattr(sources_cmd.selfwatch, "SOCIAL_STATUS_PATH", str(path))
    text, _ = sources_cmd.category(store, "instagram")
    assert "✅ kibris.car — 7 günde 12 ilan" in text and "Okuyucu: Instagram ✅" in text


# ---- aç / kapat ----

def test_close_and_reopen_a_site(store):
    changed, msg, platform = sources_cmd.toggle(store, "w1", False)
    assert changed and platform == "web" and "kapatıldı" in msg and store.by("w1")["status"] == "pasif"
    changed, msg, _ = sources_cmd.toggle(store, "w1", True)
    assert changed and store.by("w1")["status"] == "aktif"
    assert sources_cmd.toggle(store, "w3", True)[0] and store.by("w3")["status"] == "aktif"  # okuyucusu olan aday site açılır


def test_last_open_site_cannot_be_closed(store):
    assert sources_cmd.toggle(store, "w1", False)[0]
    changed, msg, _ = sources_cmd.toggle(store, "w2", False)
    assert not changed and "Son açık site" in msg and store.by("w2")["status"] == "aktif"


def test_site_without_reader_cannot_be_opened_and_repeat_press_is_harmless(store):
    changed, msg, _ = sources_cmd.toggle(store, "w4", True)
    assert not changed and "okuyucu yok" in msg and store.by("w4")["status"] == "aday"
    assert sources_cmd.toggle(store, "w2", True)[:2] == (False, "KibrisArabaAl zaten açık.")
    assert sources_cmd.toggle(store, "yok", True) == (False, "Bu kaynak bulunamadı.", None)


def test_instagram_cap_and_daily_limit(store, monkeypatch):
    monkeypatch.setattr(sources_cmd, "MAX_OPEN", {"instagram": 2, "facebook": 8})
    assert sources_cmd.toggle(store, "i2", True)[0]  # 2. açık hesap
    store.rows.append(src("i9", "instagram", "fazla", "https://www.instagram.com/fazla/", status="pasif"))
    changed, msg, _ = sources_cmd.toggle(store, "i9", True)
    assert not changed and "en çok 2" in msg
    monkeypatch.setattr(sources_cmd, "MAX_OPEN", {"instagram": 15, "facebook": 8})
    assert sources_cmd.toggle(store, "i9", True)[0]  # günün 2. açılışı
    store.rows.append(src("i8", "instagram", "ucuncu", "https://www.instagram.com/ucuncu/", status="pasif"))
    changed, msg, _ = sources_cmd.toggle(store, "i8", True)
    assert not changed and "24 saatte en çok 2" in msg
    old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    store.state["src:opened:instagram"] = json.dumps([old, old])  # 24 saatten eski açılışlar sayılmaz
    assert sources_cmd.toggle(store, "i8", True)[0]
    store.state["src:opened:instagram"] = "bozuk"  # bozuk kayıt kalıcı kilit olmaz
    assert sources_cmd.toggle(store, "i8", False)[0] and sources_cmd.toggle(store, "i8", True)[0]


def test_typed_open_close_commands_use_the_same_rules(store):
    assert "kapatıldı" in sources_cmd.change_status(store, "kktcar", "pasif")
    assert "Son açık site" in sources_cmd.change_status(store, "kibrisarabaal", "pasif")
    assert "açıldı" in sources_cmd.change_status(store, "kktcar", "deneme")
    assert "Birden fazla" in sources_cmd.change_status(store, "kibris", "pasif")
    assert "bulunamadı" in sources_cmd.change_status(store, "olmayan", "pasif")


# ---- link gönderip ekleme ----

def test_new_instagram_account_is_proposed_then_added_once(store):
    text, markup = sources_cmd.propose_link(store, "https://www.instagram.com/yeni.galeri/")
    assert "instagram.com/yeni.galeri" in text and markup["inline_keyboard"][0][0]["callback_data"] == "src:add:ig:yeni.galeri"
    assert "eklendi" in sources_cmd.add(store, "ig:yeni.galeri")
    row = next(r for r in store.rows if r["url"] == "https://www.instagram.com/yeni.galeri/")
    assert row["status"] == "aktif" and row["platform"] == "instagram"
    assert "zaten listede" in sources_cmd.add(store, "ig:yeni.galeri")  # düğmeye ikinci basış: çift kayıt yok
    assert "zaten listede" in sources_cmd.propose_link(store, "https://instagram.com/YENI.GALERI")[0]
    assert sum(r["url"] == "https://www.instagram.com/yeni.galeri/" for r in store.rows) == 1


def test_old_hidden_row_is_reopened_instead_of_duplicated(store):
    n = len(store.rows)
    assert "eklendi" in sources_cmd.add(store, "ig:gizli.aday")
    assert len(store.rows) == n and store.by("i3")["status"] == "aktif"


def test_closed_source_link_offers_reopen(store):
    text, markup = sources_cmd.propose_link(store, "https://www.instagram.com/eski.hesap/")
    assert "kapalı. Açayım mı?" in text and markup["inline_keyboard"][0][0]["callback_data"] == "src:on:i2"


def test_facebook_group_proposal_warns_about_membership(store):
    text, markup = sources_cmd.propose_link(store, "https://www.facebook.com/groups/1515097918635949/")
    assert "uzak ekrandan katıldıktan sonra" in text and markup["inline_keyboard"][0][0]["callback_data"] == "src:add:fb:1515097918635949"
    assert "eklendi" in sources_cmd.add(store, "fb:1515097918635949")
    assert any(r["name"] == "Facebook grubu 1515097918635949" and r["status"] == "aktif" for r in store.rows)


def test_site_links(store):
    text, markup = sources_cmd.propose_link(store, "https://kibrisarabaal.com/ilan/3107-2012-model-otomatik-666946/")
    assert "zaten listede ve taranıyor" in text and "kendiliğinden" in text and markup is None
    text, markup = sources_cmd.propose_link(store, "https://www.sahibinden.com/ilan/vasita-123")
    assert "sahibinden.com şu an taranmıyor" in text and markup["inline_keyboard"][0][0]["callback_data"] == "src:add:web:sahibinden.com"
    assert "istek olarak kaydedildi" in sources_cmd.add(store, "web:sahibinden.com")
    assert next(r for r in store.rows if r["name"] == "sahibinden.com")["status"] == "aday"
    assert "istek zaten kayıtlı" in sources_cmd.propose_link(store, "https://sahibinden.com/")[0]
    assert "erişimimizi reddediyor" in sources_cmd.propose_link(store, "https://galerimplus.com/")[0]
    assert "PazarKibris listede ama kapalı" in sources_cmd.propose_link(store, "https://pazarkibris.com/ilan/1")[0]


def test_posts_and_share_links_get_explanations_without_buttons(store):
    for raw, word in (("https://www.instagram.com/p/X/", "Instagram gönderisi"), ("https://www.facebook.com/groups/1/posts/2/", "Facebook gönderisi"),
                      ("https://www.facebook.com/share/g/abc/", "paylaşım kısaltması")):
        text, markup = sources_cmd.propose_link(store, raw)
        assert word in text and markup is None
    assert sources_cmd.propose_link(store, "kibris.car") is None


def test_add_rejects_tampered_keys(store):
    n = len(store.rows)
    for key in ("ig:kötü ad", "fb:", "xx:abc", "web:localhost", "ig:p"):
        assert "anlayamadım" in sources_cmd.add(store, key).lower(), key
    assert len(store.rows) == n


def test_proposal_respects_the_daily_limit(store):
    now = datetime.now(timezone.utc).isoformat()
    store.state["src:opened:facebook"] = json.dumps([now, now])
    text, markup = sources_cmd.propose_link(store, "https://www.facebook.com/groups/999/")
    assert "24 saatte en çok 2" in text and markup is None
    assert "24 saatte en çok 2" in sources_cmd.add(store, "fb:999")


# ---- bot: link ve düğmeler ----

@pytest.fixture
def tg(monkeypatch):
    calls = []
    monkeypatch.setattr(bot_poll, "api", lambda token, method, **kw: calls.append((method, kw)) or {})
    answers = []
    monkeypatch.setattr(bot_poll, "_answer", lambda token, cid, text=None, alert=False: answers.append((text, alert)))
    return calls, answers


def press(store, data, sender=OWNER):
    bot_poll._handle_callback(store, "tok", OWNER, {"id": "c", "from": {"id": int(sender)}, "data": data,
                                                    "message": {"chat": {"id": int(OWNER)}, "message_id": 7}})


def test_buttons_edit_the_message_in_place(store, tg):
    calls, answers = tg
    press(store, "src:list:web")
    method, kw = calls[-1]
    assert method == "editMessageText" and kw["message_id"] == 7 and "KKTCar" in kw["text"]
    press(store, "src:off:w1")
    assert store.by("w1")["status"] == "pasif" and answers[-1][1] is False and "⏸ KKTCar — kapalı" in calls[-1][1]["text"]
    press(store, "src:off:w2")  # son açık site: korumaya takılır, uyarı kutusu
    assert store.by("w2")["status"] == "aktif" and answers[-1][1] is True and "Son açık site" in answers[-1][0]
    press(store, "src:add:ig:yeni")
    assert "eklendi" in calls[-1][1]["text"] and calls[-1][1]["reply_markup"] == {"inline_keyboard": []}
    press(store, "src:no:")
    assert calls[-1][1]["text"] == "Tamam, eklemedim."
    press(store, "src:menu:")
    assert "📡 Kaynaklar" in calls[-1][1]["text"]


def test_only_the_owner_can_press_source_buttons(store, tg):
    calls, answers = tg
    press(store, "src:off:w1", sender="2")
    assert store.by("w1")["status"] == "aktif" and calls == [] and answers == [(None, False)]


def test_edit_failure_falls_back_to_a_new_message(store, monkeypatch):
    calls = []

    def api(token, method, **kw):
        calls.append(method)
        if method == "editMessageText":
            raise bot_poll.TelegramError(method, 400, "message can't be edited")
        return {}

    monkeypatch.setattr(bot_poll, "api", api)
    monkeypatch.setattr(bot_poll, "_answer", lambda *a, **k: None)
    press(store, "src:list:web")
    assert calls == ["editMessageText", "sendMessage"]
    calls.clear()

    def same(token, method, **kw):
        calls.append(method)
        raise bot_poll.TelegramError(method, 400, "Bad Request: message is not modified")

    monkeypatch.setattr(bot_poll, "api", same)
    press(store, "src:list:web")  # aynı düğmeye iki kez basış: sessiz
    assert calls == ["editMessageText"]
