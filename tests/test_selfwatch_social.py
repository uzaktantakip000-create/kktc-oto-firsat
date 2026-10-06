"""Öz-izleme: sosyal medya okuyucusunun (Instagram/Facebook; ayrı program) durum dosyası (application/selfwatch.py: read_social, judge_social, social_watch,
sabah satırı "Sosyal: ..."). Dosya yalnız OKUNUR; gerçek dosya yok: geçici dosya. Saatler KKTC yerel saatiyle kurulur (domain/kktc_time: yaz UTC+3, kış UTC+2).
Kurallar: dosya yok/bozuk/yanlış sürüm → satır da uyarı da yok; "susuyor" yalnız GÜNDÜZ (08–23) saatleriyle sayılır (gece boşluğu sayılmaz); uyarı yalnız gündüz."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from application import selfwatch, status
from application.runner_gate import VPS_TICK_KEY
from domain.kktc_time import KKTC
from tests.test_runner_gate import SECRET_DSN, where
from tests.test_selfwatch import Repo, tg  # noqa: F401  (tg: pytest fixture)
from tests.test_selfwatch_resources import run_tick
from tests.test_status import FakeRepo, src

H = timedelta(hours=1)
M = timedelta(minutes=1)


def kk(day, hh, mm=0, month=10, year=2026) -> datetime:
    """KKTC yerel saatinden UTC an (yaz: 07.10.2026 08:30 = 05:30 UTC; kış: 10.12.2026 08:30 = 06:30 UTC)."""
    return datetime(year, month, day, hh, mm, tzinfo=KKTC).astimezone(timezone.utc)


def plat(sonuc="tamam", last=None, new=0, errors=0, reason=None):
    return {"fren_nedeni": reason, "kaynak_hatasi": errors, "son_tur_utc": last.isoformat() if last else None,
            "sonraki_tur_utc": None, "sonuc": sonuc, "yeni_ilan": new}


def doc(platforms, written, surum=1):
    return {"platformlar": platforms, "surum": surum, "yazildi_utc": written.isoformat()}


@pytest.fixture(autouse=True)
def hermetic(monkeypatch):
    monkeypatch.setattr(selfwatch, "read_resources", lambda *a, **k: None)
    monkeypatch.setattr(selfwatch, "listener_watch", lambda repo, now: None)
    where(monkeypatch, "vps")


@pytest.fixture
def social(tmp_path, monkeypatch):
    """Sahte durum dosyası: `social(veri)` yazar (sözlük -> JSON, metin/bayt aynen) ve yolu döner; modül sabiti geçici dosyayı gösterir."""
    path = tmp_path / "durum.json"
    monkeypatch.setattr(selfwatch, "SOCIAL_STATUS_PATH", str(path))

    def write(data):
        if isinstance(data, bytes):
            path.write_bytes(data)
        else:
            path.write_text(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return str(path)

    return write


def lines(now, repo=None):
    return selfwatch.morning_lines(repo or Repo(now=now), now)


def judged(data, now):
    view = selfwatch.judge_social(data, now)
    return view, {p.key: p for p in view.platforms} if view else {}


# --- sabitler -----------------------------------------------------------------------------------------------------------------
def test_the_agreed_path_and_thresholds(monkeypatch):
    monkeypatch.undo()  # tests/conftest.py her testte yolu var olmayan bir yere çevirir; gerçek varsayılanı görmek için geri al
    assert selfwatch.SOCIAL_STATUS_PATH == "/var/lib/kktc-social-durum/durum.json" and selfwatch.SOCIAL_SCHEMA == 1
    assert (selfwatch.SOCIAL_DAY[0].hour, selfwatch.SOCIAL_DAY[1].hour) == (8, 23)
    assert selfwatch.SOCIAL_SILENT_AFTER == 5 * H and selfwatch.SOCIAL_FILE_STALE == H and selfwatch.SOCIAL_REPEAT_H == 24


# --- dosya okuma: sağlamlık ---------------------------------------------------------------------------------------------------
GOOD = {"platformlar": {}, "surum": 1, "yazildi_utc": "2026-10-07T11:20:02+00:00"}


def test_read_social_returns_the_parsed_document(social):
    assert selfwatch.read_social(social(GOOD)) == GOOD
    assert selfwatch.read_social() == GOOD  # yol verilmezse modül sabiti (çağrı anında aranır)


@pytest.mark.parametrize("content", [
    "", "   ", "{", "bozuk", "[]", "null", "42", '"metin"', "{}",
    json.dumps({"platformlar": {}}),  # sürüm yok
    json.dumps({**GOOD, "surum": 2}), json.dumps({**GOOD, "surum": "1"}), json.dumps({**GOOD, "surum": True}),
    json.dumps({**GOOD, "surum": 1.0}), json.dumps({**GOOD, "surum": None}), json.dumps({"surum": 1}),
    json.dumps({**GOOD, "platformlar": []}), json.dumps({**GOOD, "platformlar": None}), json.dumps({**GOOD, "platformlar": "instagram"}),
    b"\xff\xfe\x00\x01",  # geçersiz UTF-8
    '{"surum": 1, "platformlar": {}, "x": "' + "a" * 70000 + '"}',  # sınırdan büyük: kesilir, JSON bozulur
    "[" * 60000,  # aşırı iç içe
], ids=lambda c: repr(c)[:30])
def test_read_social_gives_none_for_every_bad_shape(social, content):
    assert selfwatch.read_social(social(content)) is None


def test_read_social_gives_none_for_a_missing_file_or_a_directory(tmp_path, capsys):
    assert selfwatch.read_social(str(tmp_path / "yok.json")) is None
    assert selfwatch.read_social(str(tmp_path)) is None
    assert selfwatch.read_social("/yok/klasor/durum.json") is None
    assert capsys.readouterr().out == ""  # sosyal taraf kurulu olmayabilir: log da yok


# --- gündüz süresi (08:00–23:00 KKTC yerel saati) -----------------------------------------------------------------------------
@pytest.mark.parametrize("start,end,expected", [
    (kk(6, 22, 50), kk(7, 8, 30), 40 * M),  # gece boşluğu sayılmaz: 22:50-23:00 + 08:00-08:30
    (kk(7, 8, 10), kk(7, 15, 0), 6 * H + 50 * M),  # tamamı gündüz
    (kk(6, 18, 0), kk(7, 8, 30), 5 * H + 30 * M),
    (kk(7, 8, 0), kk(7, 23, 0), 15 * H),
    (kk(6, 23, 0), kk(7, 8, 0), timedelta(0)),  # yalnız gece
    (kk(6, 23, 30), kk(7, 7, 30), timedelta(0)),
    (kk(7, 12, 0), kk(7, 12, 0), timedelta(0)),
    (kk(7, 15, 0), kk(7, 8, 0), timedelta(0)),  # ters aralık
    (kk(10, 22, 50, month=12), kk(11, 8, 30, month=12), 40 * M),  # kış (UTC+2): yerel saat aynı sonucu verir
    (kk(24, 22, 50), kk(25, 8, 30), 40 * M),  # 25.10.2026 yaz saati bitişi gecesi: pencereler yerel saatle kaymaz
    (kk(1, 12, 0), kk(7, 12, 0), 45 * H),  # aşırı eski kayıt: son 3 gün (45 saat gündüz) ile sınırlı
])
def test_day_elapsed_counts_only_the_overlap_with_the_kktc_daytime_windows(start, end, expected):
    assert selfwatch._day_elapsed(start, end) == expected


def test_the_windows_follow_the_local_clock_not_a_fixed_utc_offset():
    assert kk(7, 8, 30).hour == 5 and kk(10, 8, 30, month=12).hour == 6  # yazın 05:30 UTC, kışın 06:30 UTC
    now = datetime(2026, 12, 10, 5, 30, tzinfo=timezone.utc)  # kış: KKTC 07:30 (pencere henüz kapalı)
    assert selfwatch._day_elapsed(now - H, now) == timedelta(0)
    now = datetime(2026, 10, 7, 5, 30, tzinfo=timezone.utc)  # yaz: KKTC 08:30
    assert selfwatch._day_elapsed(now - H, now) == 30 * M


# --- yargı: susma ve alt durumlar ---------------------------------------------------------------------------------------------
def test_a_round_at_22_50_is_not_silent_at_08_30_the_next_morning():
    now = kk(7, 8, 30)
    view, got = judged(doc({"instagram": plat(last=kk(6, 22, 50))}, now), now)
    assert got["instagram"].state == "tamam" and got["instagram"].alert is None and view.stale is None
    assert got["instagram"].part == "Instagram ✅ (son tur 9 saat önce, 0 yeni)"  # gerçek süre yazılır, ama gündüz süresi 40 dk


def test_a_round_at_08_10_is_silent_at_15_00():
    now = kk(7, 15, 0)
    _, got = judged(doc({"instagram": plat(last=kk(7, 8, 10))}, now), now)
    assert got["instagram"].state == "susuyor" and got["instagram"].part == "Instagram ⚠️ susuyor (son tur 6 saat önce)"


@pytest.mark.parametrize("minutes,state", [(5 * 60, "tamam"), (5 * 60 + 1, "susuyor")])
def test_the_silence_limit_is_five_daytime_hours_and_the_boundary_is_not_silent(minutes, state):
    now = kk(7, 13, 10) + (minutes - 5 * 60) * M
    _, got = judged(doc({"facebook": plat(last=kk(7, 8, 10))}, now), now)
    assert got["facebook"].state == state


def test_a_round_the_evening_before_that_is_five_and_a_half_daytime_hours_old_is_silent_at_08_30():
    now = kk(7, 8, 30)
    _, got = judged(doc({"instagram": plat(last=kk(6, 18, 0))}, now), now)
    assert got["instagram"].state == "susuyor"


def test_microsecond_timestamps_and_other_utc_offsets_are_read():
    now = datetime(2026, 10, 7, 11, 20, 30, tzinfo=timezone.utc)
    data = {"platformlar": {"instagram": {**plat(), "son_tur_utc": "2026-10-07T11:05:03.120394+00:00"},
                            "facebook": {**plat(), "son_tur_utc": "2026-10-07T14:05:03+03:00"}},
            "surum": 1, "yazildi_utc": "2026-10-07T11:20:02.987654+00:00"}
    _, got = judged(data, now)
    assert got["instagram"].part == "Instagram ✅ (son tur 15 dk önce, 0 yeni)" and got["facebook"].part == "Facebook ✅ (son tur 15 dk önce, 0 yeni)"


@pytest.mark.parametrize("last", [None, "", "dün", 123, [], "2026-13-45", "2026-10-07T11:00:00", "2026-10-07T15:00:00+00:00"])  # yok/bozuk/saat dilimsiz/ileri tarihli
def test_tamam_without_a_usable_round_time_is_unclear_and_never_alerts(last):
    now = datetime(2026, 10, 7, 11, 20, 30, tzinfo=timezone.utc)
    _, got = judged(doc({"instagram": {**plat(), "son_tur_utc": last}}, now), now)
    assert got["instagram"].state == "belirsiz" and got["instagram"].part == "Instagram ⚠️ durum belirsiz" and got["instagram"].alert is None


@pytest.mark.parametrize("sonuc", [None, "bekliyor", "TAMAM", "", 5, ["tamam"], {"a": 1}])
def test_an_unknown_result_is_unclear_and_never_alerts(sonuc):
    now = kk(7, 14, 0)
    _, got = judged(doc({"instagram": {**plat(last=now - 10 * M), "sonuc": sonuc}}, now), now)
    assert got["instagram"].state == "belirsiz" and got["instagram"].alert is None


def test_a_brake_with_a_null_round_time_is_fine():
    now = kk(7, 14, 0)
    _, got = judged(doc({"facebook": plat("fren", None, reason="48 saat bekleme")}, now), now)
    assert got["facebook"].state == "fren" and got["facebook"].part == "Facebook ⚠️ fren (48 saat bekleme)"


def test_platforms_that_are_not_objects_or_have_odd_names_are_skipped_and_the_rest_still_judged():
    now = kk(7, 14, 0)
    data = doc({"instagram": "tamam", "facebook": plat(last=now - 10 * M), "tiktok": None, "x": [], "Insta Gram": plat(), "": plat(), "a" * 40: plat(), "kek": 5}, now)
    view, got = judged(data, now)
    assert list(got) == ["facebook"] and view.stale is None


def test_platformlar_that_is_not_an_object_gives_no_platforms_when_judged_directly():
    now = kk(7, 14, 0)
    assert selfwatch.judge_social({"platformlar": [], "yazildi_utc": now.isoformat()}, now).platforms == []
    assert selfwatch.judge_social({"yazildi_utc": now.isoformat()}, now).platforms == []


@pytest.mark.parametrize("written", [None, "", "dün", 7, "2026-10-07T11:00:00", "2026-10-07T20:00:00+00:00"])  # yok/bozuk/saat dilimsiz/çok ileri tarihli
def test_an_unusable_file_write_time_means_unexpected_shape_and_no_judgement(written):
    now = datetime(2026, 10, 7, 11, 20, 30, tzinfo=timezone.utc)
    assert selfwatch.judge_social({"platformlar": {"instagram": plat(last=now - 10 * M)}, "surum": 1, "yazildi_utc": written}, now) is None


def test_a_file_written_a_few_seconds_in_the_future_is_trusted():
    now = kk(7, 14, 0)
    assert selfwatch.judge_social(doc({}, now + 3 * M), now).stale is None


@pytest.mark.parametrize("minutes,stale", [(60, False), (61, True)])
def test_the_reader_counts_as_stopped_after_one_daytime_hour_without_a_file_write(minutes, stale):
    now = kk(7, 14, 0)
    assert (selfwatch.judge_social(doc({}, now - minutes * M), now).stale is not None) is stale


def test_the_overnight_gap_does_not_make_the_reader_look_stopped_at_08_05():
    now = kk(7, 8, 5)
    assert selfwatch.judge_social(doc({}, kk(6, 22, 40)), now).stale is None  # 20 dk + 5 dk gündüz


def test_counts_and_reasons_of_odd_types_are_ignored():
    now = kk(7, 14, 0)
    data = doc({"instagram": {**plat(last=now - 10 * M), "yeni_ilan": "3", "kaynak_hatasi": True}, "facebook": {**plat(last=now - 10 * M), "yeni_ilan": -1, "kaynak_hatasi": "2"},
                "tiktok": {**plat("fren"), "fren_nedeni": 5}}, now)
    _, got = judged(data, now)
    assert got["instagram"].part == "Instagram ✅ (son tur 10 dk önce)" and got["facebook"].part == "Facebook ✅ (son tur 10 dk önce)"
    assert got["tiktok"].part == "Tiktok ⚠️ fren"


# --- sabah satırı -------------------------------------------------------------------------------------------------------------
def test_the_morning_line_of_the_agreed_example(social):
    now = datetime(2026, 10, 7, 11, 20, 30, tzinfo=timezone.utc)  # KKTC 14:20
    social({"platformlar": {
        "facebook": {"fren_nedeni": "doğrulama isteniyor (checkpoint)", "kaynak_hatasi": 0, "son_tur_utc": "2026-10-07T09:12:40+00:00", "sonraki_tur_utc": None, "sonuc": "fren", "yeni_ilan": 0},
        "instagram": {"fren_nedeni": None, "kaynak_hatasi": 1, "son_tur_utc": "2026-10-07T11:05:03.120394+00:00", "sonraki_tur_utc": "2026-10-07T15:02:11+00:00", "sonuc": "tamam", "yeni_ilan": 3}},
        "surum": 1, "yazildi_utc": "2026-10-07T11:20:02+00:00"})
    assert lines(now) == ["Sosyal: Instagram ✅ (son tur 15 dk önce, 3 yeni, 1 kaynak hatası) · Facebook ⚠️ fren (doğrulama isteniyor (checkpoint))"]


def test_each_state_has_its_own_text(social):
    now = kk(7, 14, 0)
    social(doc({
        "instagram": plat(last=now - 2 * H, new=3),
        "facebook": plat("hata", None, errors=2),
        "alfa": plat(last=now - 10 * M),  # kaynak hatası yok, yeni ilan 0
        "beta": plat("fren", None),
        "gama": plat("hata", None),
        "delta": plat(last=kk(7, 8, 5)),  # 5 saat 55 dk: susuyor
    }, now))
    assert lines(now) == ["Sosyal: Instagram ✅ (son tur 2 saat önce, 3 yeni) · Facebook ⚠️ hata (2 kaynak hatası) · Alfa ✅ (son tur 10 dk önce, 0 yeni)"
                          " · Beta ⚠️ fren · Delta ⚠️ susuyor (son tur 5 saat önce) · Gama ⚠️ hata"]


def test_the_order_is_instagram_facebook_then_the_rest_alphabetically_and_unknown_names_are_capitalized(social):
    now = kk(7, 14, 0)
    social(doc({"zeta": plat(last=now - M), "facebook": plat(last=now - M), "tiktok": plat(last=now - M), "instagram": plat(last=now - M), "alfa": plat(last=now - M)}, now))
    names = [part.split(" ")[0] for part in lines(now)[0].removeprefix("Sosyal: ").split(" · ")]
    assert names == ["Instagram", "Facebook", "Alfa", "Tiktok", "Zeta"]


def test_a_source_error_count_is_added_only_to_a_healthy_line_and_a_missing_new_count_is_left_out(social):
    now = kk(7, 14, 0)
    data = doc({"instagram": {**plat(last=now - 30 * M, errors=3), "yeni_ilan": None}}, now)
    social(data)
    assert lines(now) == ["Sosyal: Instagram ✅ (son tur 30 dk önce, 3 kaynak hatası)"]


def test_a_long_brake_reason_is_collapsed_to_one_line_and_cut_to_60_characters(social):
    now = kk(7, 14, 0)
    reason = "uzun   ve\nçok satırlı bir neden: " + "x" * 100
    social(doc({"facebook": plat("fren", None, reason=reason)}, now))
    line = lines(now)[0]
    shown = line.removeprefix("Sosyal: Facebook ⚠️ fren (").removesuffix(")")
    assert "\n" not in line and "  " not in shown and len(shown) == selfwatch.SOCIAL_REASON_MAX and shown.endswith("…") and shown.startswith("uzun ve çok satırlı bir neden: x")


@pytest.mark.parametrize("reason", [None, "", "   \n ", 5, ["a"]])
def test_a_missing_or_unusable_brake_reason_leaves_just_fren(social, reason):
    now = kk(7, 14, 0)
    social(doc({"facebook": plat("fren", None, reason=reason)}, now))
    assert lines(now) == ["Sosyal: Facebook ⚠️ fren"]


def test_a_stopped_reader_puts_its_warning_in_front_of_the_platform_parts(social):
    now = kk(7, 14, 20)
    social(doc({"instagram": plat(last=kk(7, 11, 0)), "facebook": plat("fren", None, reason="doğrulama isteniyor")}, kk(7, 11, 0)))
    assert lines(now) == ["Sosyal: ⚠️ sosyal okuyucu durmuş görünüyor (son yazım 3 saat önce) · Instagram ✅ (son tur 3 saat önce, 0 yeni) · Facebook ⚠️ fren (doğrulama isteniyor)"]


def test_a_stopped_reader_with_no_platforms_gives_just_the_warning_and_a_healthy_empty_file_gives_no_line(social):
    now = kk(7, 14, 20)
    social(doc({}, kk(7, 11, 0)))
    assert lines(now) == ["Sosyal: ⚠️ sosyal okuyucu durmuş görünüyor (son yazım 3 saat önce)"]
    social(doc({}, now))
    assert lines(now) == []  # ilk tur henüz atılmadı: satır yok


def test_no_false_stopped_warning_at_the_morning_message_after_a_quiet_night(social):
    now = kk(7, 8, 5)
    social(doc({"instagram": plat(last=kk(6, 22, 50), new=2)}, kk(6, 22, 55)))
    assert lines(now) == ["Sosyal: Instagram ✅ (son tur 9 saat önce, 2 yeni)"]


@pytest.mark.parametrize("runner", ["github", "yerel"])
def test_the_line_exists_only_on_the_vps_and_the_file_is_not_even_read_elsewhere(social, monkeypatch, runner):
    now = kk(7, 14, 0)
    social(doc({"instagram": plat(last=now - 5 * M)}, now))
    where(monkeypatch, runner)
    monkeypatch.setattr(selfwatch, "read_social", lambda *a, **k: pytest.fail("VPS dışında okunmamalı"))
    assert lines(now) == []


@pytest.mark.parametrize("content", ["", "bozuk", json.dumps({**GOOD, "surum": 2}), json.dumps({"surum": 1, "platformlar": {}}), b"\xff\xfe"])
def test_a_missing_or_unreadable_file_gives_no_line_and_no_noise(social, tmp_path, monkeypatch, capsys, content):
    assert lines(kk(7, 14, 0)) == []  # dosya henüz yok
    social(content)
    assert lines(kk(7, 14, 0)) == [] and capsys.readouterr().out == ""


def test_the_social_line_comes_third_after_the_other_two_and_there_are_at_most_three(social, monkeypatch):
    now = kk(7, 14, 0)
    social(doc({"instagram": plat(last=now - 5 * M)}, now))
    repo = Repo({VPS_TICK_KEY: (now - 4 * M).isoformat(), selfwatch.BACKUP_OK_KEY: (now - 3 * 24 * H).isoformat()}, now=now)
    got = lines(now, repo)
    assert got == ["Taramalar: sunucuda ✅ (son tur 4 dk önce)", "Son veritabanı yedeği: 3 gün önce", "Sosyal: Instagram ✅ (son tur 5 dk önce, 0 yeni)"]


def test_a_failing_social_line_never_hides_the_other_lines_and_logs_only_the_error_type(social, monkeypatch, capsys):
    now = kk(7, 14, 0)

    def boom(*a, **k):
        raise RuntimeError(SECRET_DSN)

    monkeypatch.setattr(selfwatch, "read_social", boom)
    repo = Repo({VPS_TICK_KEY: (now - 4 * M).isoformat()}, now=now)
    assert lines(now, repo) == ["Taramalar: sunucuda ✅ (son tur 4 dk önce)"]
    out = capsys.readouterr().out
    assert "öz-izleme (sabah satırı (sosyal)) başarısız: RuntimeError" in out and SECRET_DSN not in out


def test_the_heartbeat_carries_the_social_line_and_the_first_line_is_unchanged_without_a_file(social):
    now = datetime(2026, 10, 2, 9, 5, tzinfo=timezone.utc)  # KKTC 12:05
    repo = FakeRepo([src("KKTCar")])
    first = "✅ Sistem çalışıyor · son 24 saatte 3 yeni ilan tarandı, 2 🟢 ve 1 🟠 gönderildi."
    assert status.build_heartbeat(repo, now) == first  # dosya yok: tek satır
    social(doc({"instagram": plat(last=now - 2 * H, new=3)}, now))
    assert status.build_heartbeat(repo, now).splitlines() == [first, "Sosyal: Instagram ✅ (son tur 2 saat önce, 3 yeni)"]


# --- anında uyarı -------------------------------------------------------------------------------------------------------------
def watch(repo, now, platforms=None, written=None, path=None):
    repo.now = now
    selfwatch.social_watch(repo, now, path)


def put(social, now, platforms, written=None):
    social(doc(platforms, written or now))


def test_a_brake_alerts_once_with_the_reason_and_a_stable_key(social, tg):
    now, repo = kk(7, 14, 0), Repo()
    put(social, now, {"facebook": plat("fren", None, reason="doğrulama isteniyor (checkpoint)")})
    watch(repo, now)
    assert tg == ["⚠️ Facebook okuyucusu durdu: doğrulama isteniyor (checkpoint). Uzak masaüstünden giriş gerekebilir."]
    assert list(repo.alerted) == ["social_facebook_fren"]


def test_a_brake_without_a_reason_still_alerts(social, tg):
    now, repo = kk(7, 14, 0), Repo()
    put(social, now, {"facebook": plat("fren", None)})
    watch(repo, now)
    assert tg == ["⚠️ Facebook okuyucusu durdu (fren). Uzak masaüstünden giriş gerekebilir."]


def test_a_reason_that_ends_with_a_full_stop_does_not_double_it(social, tg):
    now = kk(7, 14, 0)
    put(social, now, {"instagram": plat("fren", None, reason="48 saat bekleme.")})
    watch(Repo(), now)
    assert tg == ["⚠️ Instagram okuyucusu durdu: 48 saat bekleme. Uzak masaüstünden giriş gerekebilir."]


def test_an_error_state_alerts_with_its_own_key(social, tg):
    now, repo = kk(7, 14, 0), Repo()
    put(social, now, {"instagram": plat("hata", None, errors=2)})
    watch(repo, now)
    assert tg == ["⚠️ Instagram okuyucusu hata veriyor (proxy'ye ulaşılamıyor ya da kaynaklar sürekli hata veriyor). Sunucudaki kaydına bakılmalı."]
    assert list(repo.alerted) == ["social_instagram_hata"]


def test_a_silent_platform_alerts_with_the_real_age_and_its_own_key(social, tg):
    now, repo = kk(7, 15, 0), Repo()
    put(social, now, {"instagram": plat(last=kk(7, 8, 10))})
    watch(repo, now)
    assert tg == ["⚠️ Instagram okuyucusu sustu: son tur 6 saat önce. Sunucuda sosyal okuyucunun çalışıp çalışmadığına bakılmalı."]
    assert list(repo.alerted) == ["social_instagram_susuyor"]


def test_a_stopped_reader_alerts_once_and_suppresses_the_stale_platform_alerts(social, tg):
    now, repo = kk(7, 15, 0), Repo()
    put(social, now, {"instagram": plat(last=kk(7, 8, 10)), "facebook": plat("fren", None, reason="x")}, written=kk(7, 11, 30))
    watch(repo, now)
    assert tg == ["⚠️ Sosyal okuyucu durmuş görünüyor (son yazım 3 saat önce). Sunucuda çalışıp çalışmadığına bakılmalı."]
    assert list(repo.alerted) == ["social_okuyucu_durmus"]


def test_each_bad_platform_gets_its_own_alert_and_healthy_or_unclear_ones_get_none(social, tg):
    now, repo = kk(7, 14, 0), Repo()
    put(social, now, {"instagram": plat(last=now - 5 * M), "facebook": plat("fren", None, reason="x"), "tiktok": plat("hata", None),
                      "alfa": plat(None, None), "beta": plat(last=None)})
    watch(repo, now)
    assert sorted(repo.alerted) == ["social_facebook_fren", "social_tiktok_hata"] and len(tg) == 2


def test_the_same_alert_is_not_repeated_within_24_hours_and_repeats_after(social, tg):
    start, repo = kk(7, 14, 0), Repo()
    for minutes in (0, 15, 6 * 60, 24 * 60 - 1):
        now = start + minutes * M
        put(social, now, {"facebook": plat("fren", None, reason="x")})  # okuyucu hâlâ yazıyor: durum aynı
        watch(repo, now)
    assert len(tg) == 1
    now = start + 24 * H + M
    put(social, now, {"facebook": plat("fren", None, reason="x")})
    watch(repo, now)
    assert len(tg) == 2


def test_a_different_state_is_a_different_alert(social, tg):
    now, repo = kk(7, 14, 0), Repo()
    put(social, now, {"facebook": plat("fren", None, reason="x")})
    watch(repo, now)
    now += 15 * M
    put(social, now, {"facebook": plat("hata", None)})
    watch(repo, now)
    assert sorted(repo.alerted) == ["social_facebook_fren", "social_facebook_hata"] and len(tg) == 2


def test_healthy_state_sends_nothing_and_recovery_sends_no_message(social, tg):
    now, repo = kk(7, 14, 0), Repo()
    put(social, now, {"instagram": plat(last=now - 5 * M), "facebook": plat(last=now - 9 * M, new=1)})
    watch(repo, now)
    put(social, now + 15 * M, {"facebook": plat("fren", None, reason="x")})
    watch(repo, now + 15 * M)
    put(social, now + 30 * M, {"facebook": plat(last=now + 29 * M)})  # düzeldi
    watch(repo, now + 30 * M)
    assert len(tg) == 1  # yalnız fren uyarısı; "düzeldi" mesajı yok


@pytest.mark.parametrize("hh,mm,sends", [(23, 0, False), (23, 59, False), (0, 0, False), (3, 30, False), (7, 59, False),
                                         (8, 0, True), (12, 0, True), (22, 59, True)])
def test_alerts_go_only_during_kktc_daytime(social, tg, hh, mm, sends):
    now = kk(7, hh, mm)
    put(social, now, {"facebook": plat("fren", None, reason="x")})
    watch(Repo(), now)
    assert bool(tg) is sends


def test_a_stopped_reader_is_not_announced_at_night_either(social, tg):
    now = kk(7, 3, 0)
    put(social, now, {}, written=kk(6, 15, 0))
    watch(Repo(), now)
    assert tg == []


def test_the_reader_is_not_judged_stopped_right_after_a_quiet_night_and_alerts_nothing_at_08_05(social, tg):
    now = kk(7, 8, 5)
    put(social, now, {"instagram": plat(last=kk(6, 22, 50))}, written=kk(6, 22, 55))
    watch(Repo(), now)
    assert tg == []


@pytest.mark.parametrize("content", [None, "", "bozuk", json.dumps({**GOOD, "surum": 2}), json.dumps({"surum": 1, "platformlar": {"facebook": plat("fren")}}), b"\xff"])
def test_a_missing_or_unreadable_file_never_alerts(social, tg, content):
    now, repo = kk(7, 14, 0), Repo()
    if content is not None:
        social(content)  # None: dosya hiç yok
    watch(repo, now)
    assert tg == [] and repo.alerted == {}


def test_a_given_path_overrides_the_module_constant(tmp_path, tg):
    now = kk(7, 14, 0)
    path = tmp_path / "baska.json"
    path.write_text(json.dumps(doc({"facebook": plat("fren", None, reason="x")}, now)))
    watch(Repo(), now, path=str(path))  # modül sabiti (sosyal fixture'sız) gerçek yolu gösterir: orada dosya yok
    assert len(tg) == 1


# --- tick bağlantısı ----------------------------------------------------------------------------------------------------------
def after(monkeypatch, runner, repo, now):
    where(monkeypatch, runner)
    selfwatch.after_tick(repo, False, now)


def test_a_vps_tick_runs_the_social_check_and_the_alert_goes_out(social, tg, monkeypatch):
    now, repo = kk(7, 14, 0), Repo()
    put(social, now, {"facebook": plat("fren", None, reason="doğrulama isteniyor (checkpoint)")})
    after(monkeypatch, "vps", repo, now)
    assert tg == ["⚠️ Facebook okuyucusu durdu: doğrulama isteniyor (checkpoint). Uzak masaüstünden giriş gerekebilir."]


@pytest.mark.parametrize("runner", ["github", "yerel"])
def test_nothing_is_read_or_sent_off_the_vps(social, tg, monkeypatch, runner):
    now, repo = kk(7, 14, 0), Repo()
    put(social, now, {"facebook": plat("fren", None, reason="x")})
    monkeypatch.setattr(selfwatch, "read_social", lambda *a, **k: pytest.fail("VPS dışında okunmamalı"))
    after(monkeypatch, runner, repo, now)
    assert tg == [] and repo.alerted == {}


def test_a_failing_social_check_never_breaks_the_tick_or_the_other_checks(social, tg, monkeypatch, capsys):
    now, repo, ran = kk(7, 14, 0), Repo(), []
    monkeypatch.setattr(selfwatch, "resource_watch", lambda r, n: ran.append("kaynak"))

    def boom(*a, **k):
        raise RuntimeError(SECRET_DSN)

    monkeypatch.setattr(selfwatch, "read_social", boom)
    after(monkeypatch, "vps", repo, now)  # fırlatmaz
    out = capsys.readouterr().out
    assert ran == ["kaynak"] and "öz-izleme (sosyal okuyucu uyarısı) başarısız: RuntimeError" in out and SECRET_DSN not in out and tg == []


def test_a_failing_alert_write_never_breaks_the_tick(social, tg, monkeypatch, capsys):
    now = kk(7, 14, 0)
    put(social, now, {"facebook": plat("fren", None, reason="x")})

    class Boom(Repo):
        def alert_recent(self, key, hours):
            raise RuntimeError(SECRET_DSN)

    after(monkeypatch, "vps", Boom(), now)
    out = capsys.readouterr().out
    assert "başarısız: RuntimeError" in out and SECRET_DSN not in out


def test_tick_main_calls_the_social_check_on_the_vps_only(monkeypatch, tg):
    calls = []
    monkeypatch.setattr(selfwatch, "social_watch", lambda repo, now: calls.append(now))
    run_tick(monkeypatch, "vps", Repo())
    assert len(calls) == 1
    for runner in ("github", "yerel"):
        run_tick(monkeypatch, runner, Repo())
    assert len(calls) == 1
