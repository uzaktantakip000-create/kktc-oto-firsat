"""Yaz/kış saati: KKTC = Asia/Famagusta (yazın UTC+3, kışın UTC+2; geçiş 25.10.2026 01:00 UTC ve 28.03.2027 01:00 UTC).
Sabit +3 kışın saatleri 1 saat ileri gösterir ve KKTC saatine göre kurulan pencereleri kaydırırdı; her pencere Ocak ve Temmuz'da denenir."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from application import digest, health, history_cmd, maintenance, notify, price_book_job, report, status
from domain.kktc_time import KKTC, kktc_hour, to_kktc
from domain.profit import Tier
from domain.settings import Settings
from entrypoints.tick import due_jobs
from infrastructure.collectors.mezunum import parse_detail
from tests.test_estimated import est_ev
from tests.test_history_alarm_card import FakeRepo as HistoryRepo, opp
from tests.test_notify import FakeRepo as NotifyRepo, ev as notify_ev
from tests.test_quality import PEERS, Repo as QualityRepo
from tests.test_status import FakeRepo as StatusRepo, src
from tests.test_weekly_report import FakeRepo as ReportRepo, vote_row


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


# --- fırsat mesajı sessiz saatleri: KKTC 00:00–06:59 (yaz-kış aynı; yalnız 🟢/🟠 fırsat mesajı) ---
def _deal_alert(monkeypatch, now, evs=None, tier=Tier.STRONG):
    """`now` anında gönderilen TEK fırsat mesajının sendMessage alanları (mesaj her saatte hemen gider: tam bir çağrı beklenir)."""
    sent = []
    monkeypatch.setattr(notify, "api", lambda token, method, **kw: sent.append((method, kw)) or {"message_id": 1})
    assert notify.send_alerts(NotifyRepo(["a"]), "t", evs or [notify_ev(1)], tier=tier, now=now) == 1
    ((method, params),) = sent
    assert method == "sendMessage" and params["chat_id"] == "a"
    return params


def test_quiet_window_is_one_named_constant_midnight_to_seven():
    assert notify.QUIET_HOURS_KKTC == range(0, 7)


def test_a_deal_alert_at_three_in_the_morning_still_arrives_at_once_but_silently(monkeypatch):
    params = _deal_alert(monkeypatch, utc(2026, 7, 15, 0, 0))  # KKTC 03:00
    assert params["disable_notification"] is True
    assert params["text"].startswith("🟢 FIRSAT") and params["reply_markup"]["inline_keyboard"]  # içerik ve düğmeler aynı
    assert params["disable_web_page_preview"] is True


QUIET_CASES = [
    # yaz (UTC+3)
    (utc(2026, 7, 15, 20, 59), False, "yaz KKTC 23:59"), (utc(2026, 7, 15, 21, 0), True, "yaz KKTC 00:00"),
    (utc(2026, 7, 15, 21, 30), True, "yaz KKTC 00:30"), (utc(2026, 7, 15, 3, 59), True, "yaz KKTC 06:59"),
    (utc(2026, 7, 15, 4, 0), False, "yaz KKTC 07:00"), (utc(2026, 7, 15, 5, 30), False, "yaz KKTC 08:30"),
    # kış (UTC+2): sabit +3 olsaydı 21:30 UTC'yi 00:30 sanıp sustururdu, 04:30 UTC'yi 07:30 sanıp susturmazdı
    (utc(2027, 1, 15, 21, 30), False, "kış KKTC 23:30"), (utc(2027, 1, 15, 21, 59), False, "kış KKTC 23:59"),
    (utc(2027, 1, 15, 22, 0), True, "kış KKTC 00:00"), (utc(2027, 1, 15, 4, 30), True, "kış KKTC 06:30"),
    (utc(2027, 1, 15, 4, 59), True, "kış KKTC 06:59"), (utc(2027, 1, 15, 5, 0), False, "kış KKTC 07:00"),
    # geçiş günü 25.10.2026 (01:00 UTC'de 04:00 → 03:00): iki yanında da sınır doğru
    (utc(2026, 10, 24, 21, 0), True, "25.10 KKTC 00:00 (+3)"), (utc(2026, 10, 25, 0, 59), True, "25.10 KKTC 03:59 (+3)"),
    (utc(2026, 10, 25, 1, 0), True, "25.10 KKTC 03:00 (+2)"), (utc(2026, 10, 25, 4, 59), True, "25.10 KKTC 06:59 (+2)"),
    (utc(2026, 10, 25, 5, 0), False, "25.10 KKTC 07:00 (+2)"),
]


@pytest.mark.parametrize("moment,silent,label", QUIET_CASES, ids=[c[2] for c in QUIET_CASES])
def test_deal_alert_is_silent_exactly_between_midnight_and_seven_kktc_in_summer_and_winter(monkeypatch, moment, silent, label):
    params = _deal_alert(monkeypatch, moment)
    if silent:
        assert params["disable_notification"] is True, label
    else:
        assert "disable_notification" not in params, label  # gündüz mesajı eskisiyle birebir aynı: alan hiç gönderilmez


def test_estimated_deal_alert_is_silent_at_night_too_and_the_burst_summary_with_it(monkeypatch):
    assert _deal_alert(monkeypatch, utc(2026, 7, 15, 21, 30), [est_ev(1)], Tier.ESTIMATED)["disable_notification"] is True  # 🟠, KKTC 00:30
    assert "disable_notification" not in _deal_alert(monkeypatch, utc(2026, 7, 15, 5, 30), [est_ev(1)], Tier.ESTIMATED)  # KKTC 08:30

    class OwnerRepo(NotifyRepo):  # sahibe uyarı için (health.notify_owner)
        def alert_recent(self, key, hours):
            return False

        def mark_alerted(self, key):
            pass

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "owner")
    evs = [est_ev(i, price=5000 + i) for i in range(Settings().est_burst_limit + 1)]
    for moment, silent in ((utc(2026, 7, 15, 0, 30), True), (utc(2026, 7, 15, 9, 0), False)):  # KKTC 03:30 / 12:00
        subs, owner = [], []
        monkeypatch.setattr(notify, "api", lambda token, method, **kw: subs.append(kw) or {"message_id": 1})
        monkeypatch.setattr(health, "api", lambda token, method, **kw: owner.append(kw) or {})
        assert notify.send_alerts(OwnerRepo(["a", "b"]), "t", evs, tier=Tier.ESTIMATED, now=moment) == len(evs)
        assert [kw["chat_id"] for kw in subs] == ["a", "b"] and all(kw["text"].startswith(f"🟠 {len(evs)} tahmini") for kw in subs)
        if silent:
            assert all(kw["disable_notification"] is True for kw in subs)
        else:
            assert not any("disable_notification" in kw for kw in subs)
        (warning,) = owner  # sahibe "arıza freni" uyarısı gece de SESLİ: sistem mesajı fırsat mesajı değil
        assert warning["chat_id"] == "owner" and warning["text"].startswith("⚠️ 🟠 arıza freni") and "disable_notification" not in warning


def test_the_weekly_report_and_owner_warnings_stay_audible_at_night(monkeypatch):
    """Haftalık rapor ve sahibe sistem/hata uyarıları (sabah nabzı da aynı yoldan: health.notify_owner) gece de sesli gider."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "owner")
    sent = []
    monkeypatch.setattr(health, "api", lambda token, method, **kw: sent.append(kw) or {})
    monkeypatch.setattr(report, "recheck_before_send", lambda repo, cands: cands)
    monkeypatch.setattr(report, "late_sources", lambda repo, now=None: [])
    night = utc(2026, 7, 15, 0, 30)  # KKTC 03:30
    assert report.send_weekly_report(ReportRepo(votes=[vote_row(1)]), now=night) is True
    assert health.notify_owner(ReportRepo(), "hata", "⚠️ sistem uyarısı") is True
    assert len(sent) == 2 and sent[0]["text"].startswith("📊 Haftalık rapor") and sent[1]["text"].startswith("⚠️ sistem uyarısı")
    assert not any("disable_notification" in kw for kw in sent)
    assert _deal_alert(monkeypatch, night)["disable_notification"] is True  # aynı anda fırsat mesajı sessiz: fark yalnız onda


def test_without_an_injected_time_the_current_time_decides(monkeypatch):
    class Clock(datetime):
        fixed = None

        @classmethod
        def now(cls, tz=None):
            return cls.fixed

    monkeypatch.setattr(notify, "datetime", Clock)
    Clock.fixed = utc(2026, 7, 15, 0, 30)  # KKTC 03:30
    assert notify.quiet_extra() == {"disable_notification": True}
    Clock.fixed = utc(2027, 1, 15, 5, 30)  # KKTC 07:30
    assert notify.quiet_extra() == {}
