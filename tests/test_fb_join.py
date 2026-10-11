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
    assert sent == [("fb_katilim_fb:111_uye", "📥 Okuyucu yeni bir Facebook grubuna katıldı: KKTC Oto Pazarı. Gölgede okunuyor (bildirim yok, "
                     "yalnız piyasa verisi); /kaynaklar'dan kapatabilirsin.")]
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
    assert repo.set == [] and [k for k, _ in sent] == ["fb_katilim_fb:111_sinir"] and "sınırı (8) dolu" in sent[0][1]


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
    selfwatch.join_check(object(), NOW, str(path))
    assert seen[0]["platformlar"]["facebook"]["katilim"][0]["durum"] == "uye"
