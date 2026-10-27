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


# --- satış kutusu fiyatı (botun kendi sayacı, 27.10.2026) ---
def box(day="2026-10-11", lines=900, priced=0):
    return Repo({hw.BOX_KEY + day: json.dumps({"lines": lines, "priced": priced})})


def test_almost_no_sale_box_price_yesterday_is_reported_with_the_counts(tmp_path, sent):
    hw.watch(box(priced=1), {"platformlar": {"facebook": fb(posts=0)}}, NOW, folder=tmp_path)
    (k, text), = sent
    assert k == "fb_satis_kutusu" and text == ("⚠️ Facebook satış kutusu fiyatı dün neredeyse hiç gelmedi (1/900 gönderide fiyat; olağan ~%25). "
                                               "Fiyatlı Facebook ilanı azalır; sosyal okuyucu tarafında kontrol edilmeli.")
    assert "iletildi" not in text  # hiçbir şey okuyucu ekibine iletmiyor


def test_sale_box_alert_uses_a_ratio_not_exact_zero():
    assert hw.box_alert(box(priced=0), NOW)[0] == "fb_satis_kutusu"
    k, text = hw.box_alert(box(lines=1454, priced=6), NOW)  # gerçek kesinti: %0,4
    assert k == "fb_satis_kutusu" and "6/1454" in text
    assert hw.box_alert(box(lines=1000, priced=49), NOW) is not None  # %4,9
    assert hw.box_alert(box(lines=1000, priced=50), NOW) is None  # %5 sınırı: uyarı yok
    assert hw.box_alert(box(priced=300), NOW) is None  # sağlıklı: %33


def test_sale_box_alert_stays_quiet_when_few_lines_or_only_today_or_corrupt():
    assert hw.box_alert(box(lines=499, priced=0), NOW) is None  # az satır: tesadüf olabilir
    assert hw.box_alert(box(day="2026-10-12"), NOW) is None  # bugün henüz bitmedi
    assert hw.box_alert(Repo(), NOW) is None
    assert hw.box_alert(Repo({hw.BOX_KEY + "2026-10-11": "{bozuk"}), NOW) is None
    assert hw.box_alert(Repo({hw.BOX_KEY + "2026-10-11": json.dumps({"lines": 900})}), NOW) is None  # priced yok
    for bad in (None, "0", 0.0, -1, True, [], {}):
        assert hw.box_alert(Repo({hw.BOX_KEY + "2026-10-11": json.dumps({"lines": 900, "priced": bad})}), NOW) is None
    assert hw.box_alert(box(lines=500), NOW)[0] == "fb_satis_kutusu"


# --- kaynak okunmadı (27.10.2026: 40 kaynaktan 12'si 12-23 saat okunmuyordu) ---
def srcs(**ages):
    """ages: anahtar sonu -> okunmadan geçen saat (None: son_okuma_utc yok). Hepsi son 7 günde ilan görmüş sayılır."""
    return {f"fb:{k}": {"arac_7g": 20, "yeni_ilan_7g": 5, "hata": None, "uyelik_gerekli": False, "yeni_gonderi_24s": 3,
                       "son_okuma_utc": None if h is None else (NOW - h * H).isoformat()} for k, h in ages.items()}


def running(rows, round_ago=1.0):
    f = fb()
    f["son_tur_utc"] = (NOW - round_ago * H).isoformat()
    f["kaynaklar"] = rows
    return f


def test_sources_unread_for_more_than_14_hours_are_reported_once_as_a_group_with_aliases():
    f = running(srcs(a=2, b=15, c=13.9, d=20))
    (k, text), = [a for a in hw.handoff_alerts(f, None, NOW) if a[0] == "fb_grup_okunmadi"]
    assert "2 kaynak" in text and "fb-02, fb-04" in text  # sıralı anahtarlara göre: a=01, b=02, c=03, d=04
    assert "fb:" not in text and "okunmadı" in text


def test_unread_sources_alert_needs_a_running_reader_and_active_sources():
    old = srcs(a=20)
    assert keys(hw.handoff_alerts(running(old, round_ago=7), None, NOW)) == []  # okuyucu kendisi durmuş: başka alarm var
    assert keys(hw.handoff_alerts(running(old, round_ago=5.9), None, NOW)) == ["fb_grup_okunmadi"]
    no_round = running(old)
    del no_round["son_tur_utc"]
    assert hw.handoff_alerts(no_round, None, NOW) == []
    assert hw.handoff_alerts(running(old, round_ago=-1), None, NOW) == []  # ileri tarihli tur: güvenilmez
    idle = srcs(a=20)
    idle["fb:a"].update(arac_7g=0, yeni_ilan_7g=0)  # son 7 günde hiçbir şey görülmemiş: ölü kaynak, ayrı konu
    assert hw.handoff_alerts(running(idle), None, NOW) == []
    member = srcs(a=20)
    member["fb:a"]["uyelik_gerekli"] = True
    assert hw.handoff_alerts(running(member), None, NOW) == []
    car_only = srcs(a=20)
    car_only["fb:a"]["yeni_ilan_7g"] = 0  # arac_7g > 0 yeter
    assert keys(hw.handoff_alerts(running(car_only), None, NOW)) == ["fb_grup_okunmadi"]


def test_unread_sources_alert_ignores_missing_or_corrupt_fields():
    rows = srcs(a=None, b=20)
    rows["fb:c"] = {"son_okuma_utc": "dün", "arac_7g": 5}
    rows["fb:d"] = "bozuk"
    rows["fb:e"] = {"son_okuma_utc": "2026-10-11T10:00:00", "arac_7g": 5}  # saat dilimsiz
    rows["fb:f"] = {"son_okuma_utc": (NOW - 30 * H).isoformat(), "arac_7g": True, "yeni_ilan_7g": "5"}  # sayı değil: etkin sayılmaz
    (k, text), = hw.handoff_alerts(running(rows), None, NOW)
    assert k == "fb_grup_okunmadi" and "1 kaynak" in text and "fb-02" in text
    assert hw.handoff_alerts(running({}), None, NOW) == []
    bad = running(rows)
    bad["kaynaklar"] = ["x"]
    assert hw.handoff_alerts(bad, None, NOW) == []


def test_unread_sources_message_lists_at_most_ten_aliases(sent, tmp_path):
    f = running(srcs(**{f"g{i:02d}": 20 for i in range(12)}))
    hw.watch(Repo(), {"platformlar": {"facebook": f}}, NOW, folder=tmp_path)
    (k, text), = sent
    assert k == "fb_grup_okunmadi" and "12 kaynak" in text and "fb-10" in text and "fb-11" not in text and "ve 2 tane daha" in text
