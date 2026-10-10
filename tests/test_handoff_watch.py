"""Facebook devir hattının sessiz arızaları (application/handoff_watch; sosyal oturumla ortak tasarım, 10.10.2026). Devir klasörü geçici klasör,
durum dosyası sözlük; Telegram yok (notify_owner yakalanır). Kimlikler uydurma."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from application import handoff_watch as hw
from application import selfwatch
from domain.kktc_time import KKTC

NOW = datetime(2026, 10, 12, 18, 30, tzinfo=timezone.utc)  # KKTC 21:30: günlük örnek saati
H = timedelta(hours=1)


class Repo:
    def __init__(self, state=None):
        self.state = dict(state or {})

    def get_state(self, k, default=None):
        return self.state.get(k, default)

    def set_state(self, k, v):
        self.state[k] = v

    def sources(self, platform, statuses):
        return [{"id": 1, "name": "KKTC ARABA PAZARI", "url": "https://www.facebook.com/groups/kktcarabapazari", "status": "aktif"}]


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(hw, "notify_owner", lambda repo, key, text, repeat_hours=24: out.append((key, text)))
    return out


def line(seen: datetime, pid: int) -> str:
    return json.dumps({"surum": 1, "platform": "facebook", "anahtar": "fb:kktcarabapazari", "gonderi": str(10_000_000 + pid), "url": "",
                       "metin": "x", "paylasim_utc": None, "goruldu_utc": seen.isoformat(), "foto_url": None})


def write(folder, day: str, seens: list[datetime], tail: str = "") -> None:
    (folder / f"facebook-{day}.jsonl").write_text("".join(line(s, i) + "\n" for i, s in enumerate(seens)) + tail, encoding="utf-8")


def fb(posts=10, acik=True, last=NOW - H, end=NOW - H, cut=4, groups=None):
    return {"sonuc": "tamam", "devir": {"acik": acik, "son_yazim_utc": last.isoformat() if last else None},
            "gunluk": {"pencere_bitis_utc": end.isoformat(), "gonderi": posts, "ilan": 2, "kesik_metin": cut},
            "kaynaklar": {k: {"yeni_gonderi_24s": v} for k, v in (groups or {"fb:kktcarabapazari": 7}).items()}}


def keys(alerts):
    return [k for k, _ in alerts]


# --- devir kapalı / bayat ---
def test_closed_handoff_is_reported():
    assert keys(hw.handoff_alerts(fb(acik=False), None, NOW)) == ["social_facebook_devir_kapali"]


def test_posts_without_a_handoff_write_for_24_hours_is_reported_but_quiet_hours_are_not():
    assert keys(hw.handoff_alerts(fb(last=NOW - 25 * H), None, NOW)) == ["social_facebook_devir_bayat"]
    assert hw.handoff_alerts(fb(last=NOW - 6 * H), None, NOW) == []  # okuma olmayan saatlerde dosya doğal olarak eskir
    assert hw.handoff_alerts(fb(posts=0, last=NOW - 30 * H), None, NOW) == []  # okuyucu da bir şey görmedi
    assert keys(hw.handoff_alerts(fb(last=None), None, NOW)) == ["social_facebook_devir_bayat"]


def test_old_reader_without_the_new_fields_raises_nothing():
    old = {"sonuc": "tamam", "gunluk": {"gonderi": 10}}
    assert hw.handoff_alerts(old, None, NOW) == []


# --- satır sayıları ve aktarım ---
def test_window_count_matches_the_reader_and_needs_full_coverage(tmp_path):
    end = NOW - H
    write(tmp_path, "20261011", [end - 30 * H, end - 20 * H, end - 10 * H])
    write(tmp_path, "20261012", [end - 2 * H, end - H / 2], tail='{"yarım satır')
    scan = hw.scan_handoff(tmp_path, ("facebook-20261012.jsonl", 2), end, NOW)
    assert scan.window_lines == 4 and scan.stale_pending == 0
    young = tmp_path / "young"
    young.mkdir()
    write(young, "20261012", [end - 2 * H])  # klasör pencereden yeni: eşitlik ölçülemez
    assert hw.scan_handoff(young, ("", 0), end, NOW).window_lines is None


def test_count_mismatch_beyond_slack_is_reported():
    ok = hw.HandoffScan(window_lines=58, stale_pending=0)
    assert hw.handoff_alerts(fb(posts=60), ok, NOW) == []  # pay: 3 ya da %10
    bad = hw.HandoffScan(window_lines=40, stale_pending=0)
    (k, text), = hw.handoff_alerts(fb(posts=60), bad, NOW)
    assert k == "social_facebook_devir_eksik" and "60 gönderi" in text and "40 satır" in text


def test_lines_older_than_an_hour_beyond_the_cursor_mean_the_importer_is_stuck(tmp_path):
    write(tmp_path, "20261012", [NOW - 3 * H, NOW - 2 * H, NOW - H / 2])
    assert hw.scan_handoff(tmp_path, ("facebook-20261012.jsonl", 3), NOW, NOW).stale_pending == 0
    scan = hw.scan_handoff(tmp_path, ("facebook-20261012.jsonl", 1), NOW, NOW)
    assert scan.stale_pending == 1  # 2. satır (2 saatlik) aktarılmamış; 3. satır yarım saatlik: henüz değil
    assert hw.scan_handoff(tmp_path, ("facebook-20261011.jsonl", 9), NOW, NOW).stale_pending == 2  # imleç eski dosyada: yeni dosya hiç aktarılmamış
    assert keys(hw.handoff_alerts(fb(), scan, NOW)) == ["social_facebook_aktarim_geride"]


# --- günlük örnek: sessiz grup, kesik metin ---
def sample(day, groups, posts=40, cut=10):
    return {"gun": day, "gonderi": posts, "kesik": cut, "gruplar": groups}


def test_a_group_that_usually_posts_going_to_zero_is_reported_with_its_name():
    past = [sample(f"2026-10-0{d}", {"fb:kktcarabapazari": n}) for d, n in ((7, 6), (8, 9), (9, 5))]
    names = {"fb:kktcarabapazari": "KKTC ARABA PAZARI"}
    (k, text), = hw.history_alerts(past + [sample("2026-10-10", {"fb:kktcarabapazari": 0})], names)
    assert k == "social_facebook_grup_sessiz_kktcarabapazari" and "KKTC ARABA PAZARI" in text
    assert hw.history_alerts(past[1:] + [sample("2026-10-10", {"fb:kktcarabapazari": 0})], names) == []  # 2 günlük geçmiş az
    quiet = [sample(f"2026-10-0{d}", {"fb:kktcarabapazari": 2}) for d in (7, 8, 9)]
    assert hw.history_alerts(quiet + [sample("2026-10-10", {"fb:kktcarabapazari": 0})], names) == []  # zaten az paylaşan grup


def test_cut_text_ratio_must_be_high_two_days_in_a_row():
    hi, lo = dict(posts=50, cut=40), dict(posts=50, cut=20)
    assert keys(hw.history_alerts([sample("2026-10-09", {}, **hi), sample("2026-10-10", {}, **hi)], {})) == ["social_facebook_kesik_yuksek"]
    assert hw.history_alerts([sample("2026-10-09", {}, **lo), sample("2026-10-10", {}, **hi)], {}) == []
    assert hw.history_alerts([sample("2026-10-08", {}, **hi), sample("2026-10-10", {}, **hi)], {}) == []  # arada gün yok
    assert hw.history_alerts([sample("2026-10-09", {}, posts=10, cut=9), sample("2026-10-10", {}, posts=10, cut=9)], {}) == []  # az gönderi


def test_watch_samples_once_a_day_after_21_and_keeps_eight_days(tmp_path, sent):
    data = {"platformlar": {"facebook": fb()}}
    repo = Repo({hw.SAMPLE_KEY: json.dumps([sample(f"2026-10-0{d}", {}) for d in range(1, 10)])})
    hw.watch(repo, data, NOW - 2 * H, folder=tmp_path)  # KKTC 19:30: örnek yok
    assert len(json.loads(repo.state[hw.SAMPLE_KEY])) == 9
    hw.watch(repo, data, NOW, folder=tmp_path)
    hw.watch(repo, data, NOW + H / 4, folder=tmp_path)  # aynı gün ikinci tur: yeni örnek yok
    got = json.loads(repo.state[hw.SAMPLE_KEY])
    assert len(got) == hw.SAMPLE_KEEP and got[-1] == {"gun": "2026-10-12", "gonderi": 10, "kesik": 4, "gruplar": {"fb:kktcarabapazari": 7}}
    assert sent == []


def test_watch_without_a_facebook_status_does_nothing(tmp_path, sent):
    repo = Repo()
    hw.watch(repo, None, NOW, folder=tmp_path)
    hw.watch(repo, {"platformlar": {"instagram": {}}}, NOW, folder=tmp_path)
    assert sent == [] and repo.state == {}


def test_watch_sends_each_alert_through_notify_owner(tmp_path, sent):
    hw.watch(Repo(), {"platformlar": {"facebook": fb(acik=False)}}, NOW, folder=tmp_path)
    assert keys(sent) == ["social_facebook_devir_kapali"]


def test_selfwatch_runs_the_check_only_in_kktc_daytime(monkeypatch):
    calls = []
    monkeypatch.setattr(selfwatch, "read_social", lambda path=None: {"platformlar": {}})
    monkeypatch.setattr(hw, "watch", lambda repo, data, now: calls.append(now))
    night = datetime(2026, 10, 12, 2, 0, tzinfo=KKTC).astimezone(timezone.utc)
    selfwatch.handoff_check(Repo(), night)
    selfwatch.handoff_check(Repo(), NOW)
    assert calls == [NOW]
