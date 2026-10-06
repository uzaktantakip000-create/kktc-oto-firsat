"""Öz-izleme 5: sabah mesajına en çok 2 kısa satır (application/selfwatch.py: morning_lines; application/status.build_heartbeat):
"Taramalar: ..." (sunucu + yedek GitHub sağlığı, sunucuda disk/bellek) ve "Son veritabanı yedeği: ...". Kaydı olmayan parça ⚠️ vermez, atlanır."""
from datetime import datetime, timedelta, timezone

import pytest

from application import health, selfwatch, status
from application.runner_gate import VPS_TICK_KEY
from tests.test_runner_gate import SECRET_DSN, Repo as StateRepo, where
from tests.test_selfwatch import ago
from tests.test_selfwatch_failover import Boom
from tests.test_status import FakeRepo, NOW, src  # NOW: 02.10.2026 09:05 UTC (KKTC 12:05)

GH_TICK, GH_BROWSER, BACKUP = selfwatch.GH_TICK_KEY, selfwatch.GH_BROWSER_KEY, selfwatch.BACKUP_OK_KEY


def lines(state, now=NOW, runner="yerel", monkeypatch=None, snap=None):
    where(monkeypatch, runner)
    if snap is not None:
        monkeypatch.setattr(selfwatch, "read_resources", lambda: snap)
    return selfwatch.morning_lines(StateRepo(state), now)


@pytest.fixture(autouse=True)
def hermetic(monkeypatch):
    """Gerçek disk/bellek ölçümü kapalı (sonuç makineye bağlı olmasın); testler gerekirse kendi ölçümünü verir."""
    monkeypatch.setattr(selfwatch, "read_resources", lambda *a, **k: None)
    where(monkeypatch, "yerel")


def test_the_new_key_and_thresholds_are_the_agreed_ones():
    assert BACKUP == "backup_last_ok"
    assert selfwatch.GH_TICK_MAX_AGE == timedelta(hours=3) and selfwatch.GH_BROWSER_MAX_AGE == timedelta(hours=5)
    assert selfwatch.BACKUP_MAX_AGE == timedelta(days=8)


def test_no_keys_no_lines_and_no_error_noise(monkeypatch, capsys):
    assert lines({}, monkeypatch=monkeypatch) == []
    assert capsys.readouterr().out == ""  # kayıt yokluğu hata değil: log satırı da yok


# --- "Taramalar" satırı -------------------------------------------------------------------------------------------------------
def test_everything_healthy(monkeypatch):
    got = lines({VPS_TICK_KEY: ago(4, NOW), GH_TICK: ago(9, NOW), GH_BROWSER: ago(70, NOW)}, monkeypatch=monkeypatch)
    assert got == ["Taramalar: sunucuda ✅ (son tur 4 dk önce) · yedek GitHub ✅"]


@pytest.mark.parametrize("minutes,ok", [(34, True), (35, False), (52, False)])
def test_a_stale_vps_is_flagged_with_the_same_35_minute_limit_as_the_takeover(monkeypatch, minutes, ok):
    got = lines({VPS_TICK_KEY: ago(minutes, NOW), GH_TICK: ago(9, NOW)}, monkeypatch=monkeypatch)
    expected = (f"sunucuda ✅ (son tur {minutes} dk önce)" if ok else f"⚠️ sunucu turları durdu (son tur {minutes} dk önce, GitHub tarıyor)")
    assert got == [f"Taramalar: {expected} · yedek GitHub ✅"]


@pytest.mark.parametrize("hours,ok", [(3, True), (3.02, False), (7, False)])
def test_a_silent_github_tick_fallback_is_flagged_after_three_hours(monkeypatch, hours, ok):
    got = lines({VPS_TICK_KEY: ago(4, NOW), GH_TICK: ago(hours * 60, NOW)}, monkeypatch=monkeypatch)
    tail = "yedek GitHub ✅" if ok else f"⚠️ yedek GitHub sessiz (son çalışma {int(hours * 60) // 60} saat önce)"
    assert got == [f"Taramalar: sunucuda ✅ (son tur 4 dk önce) · {tail}"]


@pytest.mark.parametrize("hours,ok", [(5, True), (5.02, False), (30, False)])
def test_a_silent_github_browser_fallback_is_flagged_after_five_hours(monkeypatch, hours, ok):
    got = lines({VPS_TICK_KEY: ago(4, NOW), GH_TICK: ago(9, NOW), GH_BROWSER: ago(hours * 60, NOW)}, monkeypatch=monkeypatch)
    if ok:
        assert got == ["Taramalar: sunucuda ✅ (son tur 4 dk önce) · yedek GitHub ✅"]
    else:
        assert len(got) == 1 and "⚠️ KKTCarabam yedeği sessiz (son çalışma" in got[0] and "yedek GitHub ✅" not in got[0]


def test_both_fallbacks_silent_gives_two_warnings_in_the_same_line(monkeypatch):
    got = lines({VPS_TICK_KEY: ago(4, NOW), GH_TICK: ago(300, NOW), GH_BROWSER: ago(600, NOW)}, monkeypatch=monkeypatch)
    assert got == ["Taramalar: sunucuda ✅ (son tur 4 dk önce) · ⚠️ yedek GitHub sessiz (son çalışma 5 saat önce) · ⚠️ KKTCarabam yedeği sessiz (son çalışma 10 saat önce)"]


def test_a_missing_key_never_gives_a_warning_only_the_parts_with_records_are_shown(monkeypatch):
    assert lines({VPS_TICK_KEY: ago(4, NOW)}, monkeypatch=monkeypatch) == ["Taramalar: sunucuda ✅ (son tur 4 dk önce)"]  # yedek kaydı yok: ⚠️ yok
    assert lines({GH_TICK: ago(9, NOW)}, monkeypatch=monkeypatch) == ["Taramalar: yedek GitHub ✅"]  # VPS hiç kurulmadı: ⚠️ yok
    assert lines({GH_BROWSER: ago(9, NOW)}, monkeypatch=monkeypatch) == ["Taramalar: yedek GitHub ✅"]
    assert lines({GH_BROWSER: ago(900, NOW)}, monkeypatch=monkeypatch) == ["Taramalar: ⚠️ KKTCarabam yedeği sessiz (son çalışma 15 saat önce)"]


@pytest.mark.parametrize("value", ["", "bozuk", "2026-13-45", ago(5, NOW).replace("+00:00", ""), ago(-60, NOW)])
def test_a_garbage_naive_or_future_record_counts_as_missing_and_is_silent(monkeypatch, value):
    assert lines({VPS_TICK_KEY: value, GH_TICK: value, GH_BROWSER: value, BACKUP: value}, monkeypatch=monkeypatch) == []


def test_disk_and_memory_are_added_only_when_built_on_the_vps(monkeypatch):
    state = {VPS_TICK_KEY: ago(4, NOW), GH_TICK: ago(9, NOW)}
    snap = {"disk_pct": 62.4, "disk_gb": 48.0, "mem_mb": 6246.0}
    assert lines(state, runner="vps", monkeypatch=monkeypatch, snap=snap) == ["Taramalar: sunucuda ✅ (son tur 4 dk önce) · yedek GitHub ✅ · disk %62 boş, bellek 6,1 GB boş"]
    assert lines(state, runner="github", monkeypatch=monkeypatch, snap=snap) == ["Taramalar: sunucuda ✅ (son tur 4 dk önce) · yedek GitHub ✅"]  # GitHub makinesinin diski anlamsız
    assert lines(state, runner="yerel", monkeypatch=monkeypatch, snap=snap) == ["Taramalar: sunucuda ✅ (son tur 4 dk önce) · yedek GitHub ✅"]


def test_low_disk_or_memory_is_marked_and_a_missing_measurement_adds_nothing(monkeypatch):
    state = {VPS_TICK_KEY: ago(4, NOW), GH_TICK: ago(9, NOW)}
    low_disk = lines(state, runner="vps", monkeypatch=monkeypatch, snap={"disk_pct": 9.0, "disk_gb": 7.0, "mem_mb": 6000.0})
    assert low_disk[0].endswith("· ⚠️ disk %9 boş, bellek 5,9 GB boş")
    low_mem = lines(state, runner="vps", monkeypatch=monkeypatch, snap={"disk_pct": 50.0, "disk_gb": 40.0, "mem_mb": 350.0})
    assert low_mem[0].endswith("· ⚠️ disk %50 boş, bellek 350 MB boş")
    monkeypatch.setattr(selfwatch, "read_resources", lambda *a, **k: None)  # /proc/meminfo yok (macOS)
    assert lines(state, runner="vps", monkeypatch=monkeypatch) == ["Taramalar: sunucuda ✅ (son tur 4 dk önce) · yedek GitHub ✅"]  # satıra eklenmez


def test_without_any_scan_record_there_is_no_line_even_on_the_vps(monkeypatch):
    """Disk/bellek ayrı satır açmaz (en çok 2 satır kuralı; asıl uyarı kaynak bekçisinden gelir)."""
    assert lines({}, runner="vps", monkeypatch=monkeypatch, snap={"disk_pct": 88.0, "disk_gb": 68.0, "mem_mb": 7000.0}) == []


# --- "Son veritabanı yedeği" satırı --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("age,text", [(timedelta(hours=5), "Son veritabanı yedeği: bugün"), (timedelta(days=1, hours=2), "Son veritabanı yedeği: 1 gün önce"),
                                      (timedelta(days=3, hours=1), "Son veritabanı yedeği: 3 gün önce"), (timedelta(days=8), "Son veritabanı yedeği: 8 gün önce"),
                                      (timedelta(days=8, hours=1), "⚠️ Son veritabanı yedeği: 8 gün önce"),
                                      (timedelta(days=20), "⚠️ Son veritabanı yedeği: 20 gün önce")])
def test_backup_line_with_a_warning_after_eight_days(monkeypatch, age, text):
    assert lines({BACKUP: (NOW - age).isoformat()}, monkeypatch=monkeypatch) == [text]


def test_the_backup_line_is_omitted_when_the_key_is_missing(monkeypatch):
    assert lines({VPS_TICK_KEY: ago(4, NOW)}, monkeypatch=monkeypatch) == ["Taramalar: sunucuda ✅ (son tur 4 dk önce)"]


def test_never_more_than_two_lines_in_any_combination(monkeypatch):
    full = {VPS_TICK_KEY: ago(4, NOW), GH_TICK: ago(900, NOW), GH_BROWSER: ago(900, NOW), BACKUP: ago(60 * 24 * 30, NOW)}
    got = lines(full, runner="vps", monkeypatch=monkeypatch, snap={"disk_pct": 5.0, "disk_gb": 4.0, "mem_mb": 100.0})
    assert len(got) == 2 and got[0].startswith("Taramalar:") and got[1].startswith("⚠️ Son veritabanı yedeği: 30 gün önce")


# --- hata dayanıklılığı -------------------------------------------------------------------------------------------------------
def test_a_failing_repo_gives_no_lines_and_never_raises(capsys):
    assert selfwatch.morning_lines(Boom(), NOW) == []
    out = capsys.readouterr().out
    assert out.count("öz-izleme") == 2 and "RuntimeError" in out and SECRET_DSN not in out and "GIZLI-PAROLA" not in out


def test_one_failing_read_does_not_hide_the_other_line(monkeypatch):
    where(monkeypatch, "yerel")
    repo = StateRepo({VPS_TICK_KEY: ago(4, NOW), BACKUP: ago(60 * 24 * 2, NOW)}, fail_read={VPS_TICK_KEY})
    assert selfwatch.morning_lines(repo, NOW) == ["Son veritabanı yedeği: 2 gün önce"]
    repo = StateRepo({VPS_TICK_KEY: ago(4, NOW), BACKUP: ago(60 * 24 * 2, NOW)}, fail_read={BACKUP})
    assert selfwatch.morning_lines(repo, NOW) == ["Taramalar: sunucuda ✅ (son tur 4 dk önce)"]


def test_a_failing_resource_measurement_drops_only_the_resource_part(monkeypatch, capsys):
    where(monkeypatch, "vps")

    def boom():
        raise RuntimeError(SECRET_DSN)

    monkeypatch.setattr(selfwatch, "read_resources", boom)
    repo = StateRepo({VPS_TICK_KEY: ago(4, NOW), BACKUP: ago(60 * 24 * 2, NOW)})
    assert selfwatch.morning_lines(repo, NOW) == ["Taramalar: sunucuda ✅ (son tur 4 dk önce)", "Son veritabanı yedeği: 2 gün önce"]  # sabah mesajı yine gider
    assert SECRET_DSN not in capsys.readouterr().out


# --- sabah nabzı (build_heartbeat / send_morning_status) ------------------------------------------------------------------------
def test_heartbeat_is_unchanged_when_there_are_no_records():
    text = status.build_heartbeat(FakeRepo([src("KKTCar")]), NOW)
    assert text == "✅ Sistem çalışıyor · son 24 saatte 3 yeni ilan tarandı, 2 🟢 ve 1 🟠 gönderildi." and len(text.splitlines()) == 1


def test_heartbeat_appends_the_new_lines_after_the_delay_warning():
    repo = FakeRepo([src("KKTCar"), src("Yeni", hours=None)])
    repo.state.update({VPS_TICK_KEY: ago(4, NOW), GH_TICK: ago(9, NOW), BACKUP: ago(60 * 24 * 3, NOW)})
    assert status.build_heartbeat(repo, NOW).splitlines() == [
        "✅ Sistem çalışıyor · son 24 saatte 3 yeni ilan tarandı, 2 🟢 ve 1 🟠 gönderildi.",
        "⚠️ 1 yerde gecikme var (ayrıntı: /durum).",
        "Taramalar: sunucuda ✅ (son tur 4 dk önce) · yedek GitHub ✅",
        "Son veritabanı yedeği: 3 gün önce",
    ]


def test_heartbeat_keeps_its_first_line_even_when_every_selfwatch_read_fails():
    class Flaky(FakeRepo):
        def get_state(self, k, default=None):
            if k in (VPS_TICK_KEY, GH_TICK, GH_BROWSER, BACKUP):
                raise RuntimeError(SECRET_DSN)
            return super().get_state(k, default)

    text = status.build_heartbeat(Flaky([src("KKTCar")]), NOW)
    assert text == "✅ Sistem çalışıyor · son 24 saatte 3 yeni ilan tarandı, 2 🟢 ve 1 🟠 gönderildi."  # sabah mesajı gene gider


def test_the_morning_message_carries_the_new_lines_and_is_still_sent_once(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    sent = []
    monkeypatch.setattr(health, "api", lambda *a, **kw: sent.append(kw["text"]))
    repo = FakeRepo([src("KKTCar")])
    at = datetime(2026, 10, 2, 5, 30, tzinfo=timezone.utc)  # KKTC 08:30: sabah penceresi
    repo.state.update({VPS_TICK_KEY: ago(4, at), BACKUP: ago(60 * 24 * 9, at)})
    assert status.send_morning_status(repo, at) is True
    assert sent[0].splitlines()[1:] == ["Taramalar: sunucuda ✅ (son tur 4 dk önce)", "⚠️ Son veritabanı yedeği: 9 gün önce"]
