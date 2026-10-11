"""Facebook grubuna katılım (application/fb_join; sosyal oturumla sözleşme, 11.10.2026): durum dosyasındaki platformlar.facebook.katilim
listesi → aday grup okumaya açılır + sahibe bir kez haber. Sahte veritabanı; Telegram yok; grup kimlikleri uydurma."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from application import fb_join, selfwatch, sources_cmd

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)


def src(i, key, status, name=None):
    return {"id": i, "name": name or f"Grup {i}", "url": f"https://www.facebook.com/groups/{key}/", "status": status}


class Repo:
    def __init__(self, rows, open_count=4):
        self.rows, self.state, self.set, self.open_n = list(rows), {}, [], open_count

    def sources(self, platform, statuses):
        return [r for r in self.rows if r["status"] in statuses]

    def get_state(self, k, default=None):
        return self.state.get(k, default)

    def set_state(self, k, v):
        self.state[k] = v


@pytest.fixture
def sent(monkeypatch):
    out, once = [], set()

    def notify(repo, key, text, repeat_hours=24, **extra):
        if key in once:
            return False
        once.add(key)
        out.append((key, text))
        return True

    monkeypatch.setattr(fb_join, "notify_owner", notify)
    monkeypatch.setattr(sources_cmd, "_set_status", lambda repo, sid, status: repo.set.append((sid, status)))
    monkeypatch.setattr(sources_cmd, "_open_count", lambda repo, platform: repo.open_n + len(repo.set))
    return out


def status(*items):
    return {"surum": 1, "platformlar": {"facebook": {"katilim": [{"anahtar": k, "durum": d, "zaman_utc": NOW.isoformat()} for k, d in items]}}}


def test_member_opens_a_candidate_group_once_and_tells_the_owner(sent):
    repo = Repo([src(7, "111", "aday", "KKTC Oto Pazarı")])
    assert fb_join.check(repo, status(("fb:111", "uye")), NOW, log=lambda _: None) == 1
    assert repo.set == [("7", "aktif")]
    assert sent == [("fb_katilim_fb:111_uye", "📥 Okuyucu yeni bir Facebook grubuna katıldı: KKTC Oto Pazarı. Fırsatları artık anında gelecek. "
                     "/kaynaklar'dan kapatabilirsin.")]  # sahibin eklediği grup yeşil (src: alert_level yok → gölge değil)
    assert len(json.loads(repo.state[sources_cmd.OPENED_KEY.format("facebook")])) == 1  # sahibin 24 saatlik açılış rayına sayılır
    repo.rows[0]["status"] = "aktif"
    assert fb_join.check(repo, status(("fb:111", "uye")), NOW + timedelta(minutes=15), log=lambda _: None) == 0 and len(sent) == 1


@pytest.mark.parametrize("closed", ["pasif", "disari", "erisim_reddediyor"])
def test_a_group_the_owner_closed_is_never_opened_or_mentioned(sent, closed):
    repo = Repo([src(7, "111", closed), src(8, "111", "aday")])  # aynı grubun eski aday satırı da olsa
    assert fb_join.check(repo, status(("fb:111", "uye"), ("fb:111", "sorulu")), NOW) == 0
    assert repo.set == [] and sent == []


def test_full_limit_keeps_the_group_candidate_and_says_so_once(sent):
    repo = Repo([src(7, "111", "aday")], open_count=sources_cmd.MAX_OPEN["facebook"])
    for _ in range(2):
        assert fb_join.check(repo, status(("fb:111", "uye")), NOW) == 0
    assert repo.set == [] and [k for k, _ in sent] == ["fb_katilim_fb:111_sinir"] and f"sınırı ({sources_cmd.MAX_OPEN['facebook']}) dolu" in sent[0][1]


def test_daily_open_rail_delays_the_opening_to_a_later_turn(sent):
    repo = Repo([src(7, "111", "aday")])
    repo.state[sources_cmd.OPENED_KEY.format("facebook")] = json.dumps([(NOW - timedelta(hours=h)).isoformat() for h in (1, 2)])
    assert fb_join.check(repo, status(("fb:111", "uye")), NOW) == 0 and repo.set == [] and sent == []
    assert fb_join.check(repo, status(("fb:111", "uye")), NOW + timedelta(hours=23), log=lambda _: None) == 1 and repo.set == [("7", "aktif")]


def test_waiting_question_and_closed_groups_are_reported_once_and_errors_are_silent(sent):
    repo = Repo([src(1, "111", "aday", "A"), src(2, "222", "aday", "B"), src(3, "333", "aday", "C"), src(4, "444", "aday", "D")])
    repo.rows += [src(5, "555", "aday", "E"), src(6, "666", "aday", "F")]
    data = status(("fb:111", "bekliyor"), ("fb:222", "sorulu"), ("fb:333", "kapali"), ("fb:444", "hata"), ("fb:555", "reddedildi"), ("fb:666", "yeni_kod"))
    fb_join.check(repo, data, NOW)
    fb_join.check(repo, data, NOW + timedelta(minutes=15))
    assert [k for k, _ in sent] == ["fb_katilim_fb:111_bekliyor", "fb_katilim_fb:222_sorulu", "fb_katilim_fb:333_kapali", "fb_katilim_fb:555_reddedildi"]
    assert "https://www.facebook.com/groups/222/" in sent[1][1] and repo.set == []


def test_waiting_then_member_gives_two_messages(sent):
    repo = Repo([src(1, "111", "aday", "A")])
    fb_join.check(repo, status(("fb:111", "bekliyor")), NOW)
    fb_join.check(repo, status(("fb:111", "uye")), NOW + timedelta(hours=5), log=lambda _: None)
    assert [k for k, _ in sent] == ["fb_katilim_fb:111_bekliyor", "fb_katilim_fb:111_uye"] and repo.set == [("1", "aktif")]


@pytest.mark.parametrize("data", [None, {}, {"platformlar": {}}, {"platformlar": {"facebook": {}}}, {"platformlar": {"facebook": {"katilim": "x"}}},
                                  status(("fb:yok", "uye")), status(("ig:111", "uye")), status(("fb:111", "katildi")), status(("fb:111", "sinir")),
                                  {"platformlar": {"facebook": {"katilim": [None, 3, {"anahtar": 5, "durum": "uye"}]}}}])
def test_missing_or_odd_fields_do_nothing(sent, data):
    repo = Repo([src(7, "111", "aday")])
    assert fb_join.check(repo, data, NOW) == 0 and repo.set == [] and sent == []


def test_an_already_open_group_is_left_alone(sent):
    repo = Repo([src(7, "111", "aktif")])
    assert fb_join.check(repo, status(("fb:111", "uye"), ("fb:111", "bekliyor")), NOW) == 0 and sent == [] and repo.set == []


def test_selfwatch_runs_the_join_check_on_the_server_only(monkeypatch, tmp_path):
    path = tmp_path / "durum.json"
    path.write_text(json.dumps(status(("fb:111", "uye"))), encoding="utf-8")
    seen = []
    monkeypatch.setattr(fb_join, "check", lambda repo, data, now: seen.append(data))
    for name in ("membership", "discover", "promote"):
        monkeypatch.setattr(fb_join, name, lambda *a, **k: None)
    selfwatch.join_check(object(), NOW, str(path))
    assert seen[0]["platformlar"]["facebook"]["katilim"][0]["durum"] == "uye"


# --- grup keşfi ---
def kesif(*items):
    return {"surum": 1, "platformlar": {"facebook": {"kesif": [dict(anahtar=k, ad=n, uye=u, gizlilik=g, gonderi_gun=p, zaman_utc=NOW.isoformat())
                                                               for k, n, u, g, p in items]}}}


@pytest.fixture
def inserted(monkeypatch):
    out = []

    def insert(repo, key, name, status, priority):
        if any(k == key for k, *_ in out):
            return False
        out.append((key, name, status, priority))
        return True

    monkeypatch.setattr(fb_join, "_insert_discovered", insert)
    return out


@pytest.mark.parametrize("members,posts,prio", [(15000, None, 2), (3000, 6, 2), (3000, 1, 3), (500, 8, 3), (500, 1, 4), (None, None, 4),
                                                (True, "x", 4)])
def test_discovery_priority_prefers_big_and_lively_groups(members, posts, prio):
    assert fb_join.discovery_priority(members, posts) == prio


def test_public_groups_are_read_without_joining_closed_ones_wait_in_line(sent, inserted):
    repo = Repo([src(1, "111", "pasif"), src(2, "222", "disari")])
    data = kesif(("fb:111", "Sahibin kapattığı", 9000, "acik", 9), ("fb:222", "Dışarıda", 9000, "acik", 9),
                 ("fb:333", "KKTC Araba Pazarı", 20000, "acik", 30), ("fb:444", "Girne Oto", 800, "kapali", 2),
                 ("fb:555", "Kıbrıs Araç\nAlım", 3000, "kapali", 7), ("fb:333", "kopya", 1, "acik", 1), ("fb:BÜYÜK", "bozuk", 1, "acik", 1))
    assert fb_join.discover(repo, data, NOW, log=lambda _: None) == (3, 1)
    assert inserted == [("fb:333", "KKTC Araba Pazarı", "aktif", 2), ("fb:555", "Kıbrıs Araç Alım", "aday", 2),
                        ("fb:444", "Girne Oto", "aday", 4)]  # var olan 111/222'ye dokunulmaz
    ((key, text),) = sent
    assert "1 açık grup okunmaya başladı (katılmadan): KKTC Araba Pazarı" in text and "2 kapalı grup katılma sırasına girdi" in text


def test_discovery_daily_caps_and_full_limit(sent, inserted, monkeypatch):
    monkeypatch.setattr(fb_join, "DISCOVER_PER_DAY", 3)
    monkeypatch.setattr(fb_join, "DISCOVER_OPEN_PER_DAY", 1)
    repo = Repo([])
    data = kesif(*[(f"fb:{i}00", f"G{i}", 20000 - i, "acik", 9) for i in range(1, 6)])
    assert fb_join.discover(repo, data, NOW, log=lambda _: None) == (3, 1)
    assert [s for _, _, s, _ in inserted] == ["aktif", "aday", "aday"]  # günde 1 açık; kalanlar sıraya
    assert fb_join.discover(repo, data, NOW, log=lambda _: None) == (0, 0)  # günlük ekleme sınırı doldu
    full = Repo([], open_count=sources_cmd.MAX_OPEN["facebook"])
    inserted.clear()
    assert fb_join.discover(full, kesif(("fb:900", "G9", 20000, "acik", 9)), NOW, log=lambda _: None) == (1, 0)
    assert inserted[0][2] == "aday"  # 50 açık sınırı dolu: açık grup da sıraya


def test_no_discovery_field_does_nothing(sent, inserted):
    assert fb_join.discover(Repo([]), status(("fb:111", "uye")), NOW) == (0, 0) and inserted == [] and sent == []


def test_a_public_group_that_needs_membership_goes_back_in_line(sent):
    repo = Repo([src(1, "111", "aktif"), src(2, "222", "aktif"), src(3, "333", "pasif"), src(4, "444", "aktif")])
    data = {"surum": 1, "platformlar": {"facebook": {
        "kaynaklar": {"fb:111": {"uyelik_gerekli": True, "hata": "fb-1: üyelik gerekli"}, "fb:222": {"uyelik_gerekli": False},
                      "fb:333": {"uyelik_gerekli": True}, "fb:444": {"uyelik_gerekli": True}, "fb:yok": {"uyelik_gerekli": True}},
        "katilim": [{"anahtar": "fb:444", "durum": "uye", "zaman_utc": NOW.isoformat()}]}}}  # 444 az önce katıldı: alan eski
    assert fb_join.needs_membership(data) == ["fb:111", "fb:333", "fb:yok"]
    assert fb_join.membership(repo, data, log=lambda _: None) == 1 and repo.set == [("1", "aday")]  # kapalı (333) ve bilinmeyen atlanır
    assert fb_join.membership(repo, {"platformlar": {"facebook": {"kaynaklar": "x"}}}) == 0



# --- gölge süresi (sahip 11.10.2026: okuyucunun bulduğu grup ilk 3 gün gölgede) ---
class PromoRepo(Repo):
    def __init__(self, rows, mix=(10, 0)):
        super().__init__(rows)
        self.mix, self.levels = mix, []

    def currency_mix(self, source_id, days):
        return self.mix

    def set_alert_level(self, source_id, level):
        self.levels.append((source_id, level))


def test_a_found_group_joins_in_shadow_and_goes_live_after_three_days(sent):
    repo = PromoRepo([src(7, "111", "aday", "Bulunan") | {"alert_level": "golge"}])
    fb_join.check(repo, status(("fb:111", "uye")), NOW, log=lambda _: None)
    assert "İlk 3 gün gölgede okunur" in sent[0][1] and repo.state[fb_join.SHADOW_KEY.format("fb:111")] == NOW.isoformat()
    repo.rows[0]["status"] = "aktif"
    assert fb_join.promote(repo, NOW + timedelta(days=2), log=lambda _: None) == 0 and repo.levels == []
    assert fb_join.promote(repo, NOW + timedelta(days=3, minutes=1), log=lambda _: None) == 1 and repo.levels == [(7, "yesil")]
    assert sent[-1][0] == "fb_golge_bitti_fb:111" and "Artık fırsatları anında gelecek" in sent[-1][1]


def test_owner_shadowed_group_stays_and_euro_group_is_held_back(sent):
    manual = PromoRepo([src(7, "111", "aktif") | {"alert_level": "golge"}])  # sahip elle gölgeye aldı: kayıt yok
    assert fb_join.promote(manual, NOW + timedelta(days=30)) == 0 and manual.levels == []
    euro = PromoRepo([src(8, "222", "aktif", "Cyprus Cars") | {"alert_level": "golge"}], mix=(10, 4))
    fb_join.start_shadow(euro, "fb:222", NOW)
    assert fb_join.promote(euro, NOW + timedelta(days=4)) == 0 and euro.levels == []
    assert sent[-1][0] == "fb_golge_euro_fb:222" and "%40 kadarı euro fiyatlı" in sent[-1][1]


def test_a_discovered_public_group_starts_its_shadow_clock(sent, inserted):
    repo = Repo([])
    fb_join.discover(repo, kesif(("fb:333", "KKTC Araba Pazarı", 20000, "acik", 30), ("fb:444", "Girne Oto", 800, "kapali", 2)), NOW, log=lambda _: None)
    assert fb_join.SHADOW_KEY.format("fb:333") in repo.state and fb_join.SHADOW_KEY.format("fb:444") not in repo.state
    assert "İlk 3 gün gölgede okunur" in sent[0][1]



@pytest.mark.parametrize("kind,prio", [("arac", 2), (None, 2), ("alim_satim", 3), ("ogrenci", 3)])
def test_buy_sell_and_student_groups_come_one_step_later(kind, prio):
    assert fb_join.discovery_priority(15000, 9, kind) == prio
    assert fb_join.discovery_priority(100, 0, "ogrenci") == 5
