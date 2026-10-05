"""Yaz/kış saati: KKTC = Asia/Famagusta (yazın UTC+3, kışın UTC+2; geçiş 25.10.2026 01:00 UTC ve 28.03.2027 01:00 UTC).
Sabit +3 kışın saatleri 1 saat ileri gösterir ve KKTC saatine göre kurulan pencereleri kaydırırdı; her pencere Ocak ve Temmuz'da denenir."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from application import digest, health, history_cmd, maintenance, price_book_job, status
from domain.kktc_time import KKTC, kktc_hour, to_kktc
from entrypoints.tick import due_jobs
from infrastructure.collectors.mezunum import parse_detail
from tests.test_history_alarm_card import FakeRepo as HistoryRepo, opp
from tests.test_quality import PEERS, Repo as QualityRepo
from tests.test_status import FakeRepo as StatusRepo, src


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


JULY, JANUARY = utc(2026, 7, 15, 5, 30), utc(2027, 1, 15, 6, 30)  # ikisi de KKTC 08:30


# --- ortak saat yardımcısı ---
def test_summer_is_plus_three_winter_is_plus_two():
    assert to_kktc(JULY).utcoffset() == timedelta(hours=3) and kktc_hour(JULY) == 8
    assert to_kktc(JANUARY).utcoffset() == timedelta(hours=2) and kktc_hour(JANUARY) == 8
    assert kktc_hour(datetime(2027, 1, 15, 6, 30)) == 8  # saat dilimsiz an UTC sayılır (makinenin saatine bakılmaz)


def test_autumn_transition_2026_10_25():
    before, after = utc(2026, 10, 25, 0, 59), utc(2026, 10, 25, 1, 0)
    assert to_kktc(before).isoformat() == "2026-10-25T03:59:00+03:00"
    assert to_kktc(after).isoformat() == "2026-10-25T03:00:00+02:00"  # saat 04:00'ten 03:00'e geri alınır
    assert kktc_hour(utc(2026, 10, 25, 5, 0)) == 7 and kktc_hour(utc(2026, 10, 25, 6, 0)) == 8
    assert to_kktc(utc(2027, 3, 28, 1, 0)).isoformat() == "2027-03-28T04:00:00+03:00"  # bahar: 03:00'ten 04:00'e ileri


# --- sabah nabzı: yaz-kış KKTC 08:00–11:59 ---
def _pulse(monkeypatch, now):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(health, "api", lambda *a, **kw: None)
    return status.send_morning_status(StatusRepo([src("KKTCar")]), now)


def test_morning_pulse_window_is_local_in_summer(monkeypatch):
    assert _pulse(monkeypatch, utc(2026, 7, 15, 4, 59)) is False  # KKTC 07:59
    assert _pulse(monkeypatch, JULY) is True                     # KKTC 08:30
    assert _pulse(monkeypatch, utc(2026, 7, 15, 8, 59)) is True   # KKTC 11:59
    assert _pulse(monkeypatch, utc(2026, 7, 15, 9, 0)) is False   # KKTC 12:00


def test_morning_pulse_window_is_local_in_winter(monkeypatch):
    assert _pulse(monkeypatch, utc(2027, 1, 15, 5, 30)) is False  # KKTC 07:30 (sabit +3 olsaydı 08:30 sanıp gönderirdi)
    assert _pulse(monkeypatch, JANUARY) is True                   # KKTC 08:30
    assert _pulse(monkeypatch, utc(2027, 1, 15, 9, 59)) is True   # KKTC 11:59 (sabit +3 olsaydı 12:59 sanıp susardı)
    assert _pulse(monkeypatch, utc(2027, 1, 15, 10, 0)) is False  # KKTC 12:00


def test_morning_pulse_on_transition_day(monkeypatch):
    assert _pulse(monkeypatch, utc(2026, 10, 24, 5, 0)) is True   # cumartesi KKTC 08:00 (+3)
    assert _pulse(monkeypatch, utc(2026, 10, 25, 5, 30)) is False  # pazar KKTC 07:30 (+2)
    assert _pulse(monkeypatch, utc(2026, 10, 25, 6, 0)) is True   # pazar KKTC 08:00 (+2)


# --- /durum ve /son ekranındaki saatler ---
def test_status_report_shows_local_time_summer_and_winter():
    repo = StatusRepo([src("KKTCar")])
    repo.state["tick:last"] = "2026-07-15T09:00:00+00:00"
    text = status.build_status(repo, utc(2026, 7, 15, 9, 5))
    assert "📊 Sistem raporu — 15.07 12:05" in text and "Son kontrol saat 12:00." in text
    repo.state["tick:last"] = "2027-01-15T09:00:00+00:00"
    text = status.build_status(repo, utc(2027, 1, 15, 9, 5))
    assert "📊 Sistem raporu — 15.01 11:05" in text and "Son kontrol saat 11:00." in text


def test_son_shows_local_time_summer_winter_and_transition():
    def first_line(sent_at):
        o = opp() | {"sent_at": sent_at}
        return history_cmd.last_opportunities(HistoryRepo(opps=[o])).split("\n\n")[1]

    assert first_line(utc(2026, 7, 15, 11, 20)).startswith("🟢 15.07 14:20 ·")
    assert first_line(utc(2027, 1, 15, 11, 20)).startswith("🟢 15.01 13:20 ·")
    assert first_line(utc(2026, 10, 25, 0, 30)).startswith("🟢 25.10 03:30 ·")  # yaz saati son saat
    assert first_line(utc(2026, 10, 25, 1, 30)).startswith("🟢 25.10 03:30 ·")  # kış saati: 03:xx ikinci kez yaşanır


# --- Mezunum: site saati KKTC duvar saati ---
MEZUNUM = (Path(__file__).parent / "fixtures/mezunum_detail_citroen.html").read_text()


def test_mezunum_site_time_is_kktc_wall_clock():
    summer = parse_detail(MEZUNUM)["posted_at"]
    assert summer.astimezone(timezone.utc) == utc(2026, 8, 21, 12, 57, 14)  # 15:57:14 (+3)
    winter = parse_detail(MEZUNUM.replace('"validFrom": "2026-08-21 15:57:14"', '"validFrom": "2027-01-15 15:57:14"'))["posted_at"]
    assert winter.astimezone(timezone.utc) == utc(2027, 1, 15, 13, 57, 14)  # 15:57:14 (+2)
    assert winter.tzinfo is KKTC


# --- tarama sıklığı: gündüz = KKTC 08:00–24:00 ---
def _is_day(now):
    """kktcar gündüz 15 dk, gece 30 dk: 20 dk önce bakıldıysa yalnız gündüz sırası gelir."""
    return "kktcar" in due_jobs(now, {"kktcar": now - timedelta(minutes=20)})


def test_day_night_scan_window_is_local_all_year():
    assert not _is_day(utc(2026, 7, 15, 4, 30)) and _is_day(JULY)                         # yaz KKTC 07:30 / 08:30
    assert _is_day(utc(2026, 7, 15, 20, 59)) and not _is_day(utc(2026, 7, 15, 21, 0))     # yaz KKTC 23:59 / 00:00
    assert not _is_day(utc(2027, 1, 15, 5, 30)) and _is_day(JANUARY)                      # kış KKTC 07:30 / 08:30
    assert _is_day(utc(2027, 1, 15, 21, 59)) and not _is_day(utc(2027, 1, 15, 22, 0))     # kış KKTC 23:59 / 00:00
    assert not _is_day(utc(2026, 10, 25, 5, 30)) and _is_day(utc(2026, 10, 25, 6, 0))     # geçiş günü KKTC 07:30 / 08:00 (+2)


# --- 🟡 özet (kapalı; açılırsa) gönderim penceresi: KKTC 08:00–23:00 ---
class DigestRepo:
    def __init__(self):
        self.asked = 0

    def alert_recent(self, key, hours):
        return False

    def approved_subscribers(self):
        self.asked += 1
        return []


def _digest_window_open(monkeypatch, now):
    monkeypatch.setattr(digest, "ENABLED", True)
    repo = DigestRepo()
    digest.send_daily_digest(repo, "token", now)
    return repo.asked == 1


def test_digest_send_window_is_local_all_year(monkeypatch):
    assert _digest_window_open(monkeypatch, JULY) and _digest_window_open(monkeypatch, JANUARY)
    assert not _digest_window_open(monkeypatch, utc(2026, 7, 15, 4, 30))   # yaz KKTC 07:30
    assert not _digest_window_open(monkeypatch, utc(2027, 1, 15, 5, 30))   # kış KKTC 07:30
    assert _digest_window_open(monkeypatch, utc(2027, 1, 15, 20, 59))      # kış KKTC 22:59
    assert not _digest_window_open(monkeypatch, utc(2027, 1, 15, 21, 0))   # kış KKTC 23:00


# --- gece işleri bilerek UTC: yaz-kış UTC 00–04 (KKTC yazın 03–07, kışın 02–06), sabah nabzından önce biter ---
def test_night_jobs_stay_on_utc_window_all_year():
    assert maintenance.NIGHT_HOURS_UTC == price_book_job.NIGHT_HOURS_UTC == range(0, 4)
    for night, day in ((utc(2026, 7, 15, 1, 0), utc(2026, 7, 15, 4, 0)), (utc(2027, 1, 15, 1, 0), utc(2027, 1, 15, 4, 0))):
        assert maintenance.run_maintenance(QualityRepo(list(PEERS)), day) is None
        assert maintenance.run_maintenance(QualityRepo(list(PEERS)), night) is not None
    last_night_hour = utc(2027, 1, 15, 3, 59)  # kışın KKTC 05:59: sabah nabzı (KKTC 08:00) penceresinden önce
    assert kktc_hour(last_night_hour) < status.MORNING_HOURS_KKTC.start
