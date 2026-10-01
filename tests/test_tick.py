from datetime import datetime, timedelta, timezone

from entrypoints.tick import due_jobs, heartbeat_gap

DAY = datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)   # KKTC 12:00
NIGHT = datetime(2026, 10, 2, 1, 0, tzinfo=timezone.utc)  # KKTC 04:00


def test_first_run_everything_is_due():
    assert set(due_jobs(DAY, {})) == {"kktcar", "kibrisarabaal", "instagram", "facebook"}


def test_only_jobs_whose_interval_passed_are_due():
    last = {"kktcar": DAY - timedelta(minutes=16), "kibrisarabaal": DAY - timedelta(minutes=5),
            "instagram": DAY - timedelta(minutes=29), "facebook": DAY - timedelta(hours=3)}
    assert set(due_jobs(DAY, last)) == {"kktcar", "instagram"}  # instagram: 30 dk - 3 dk tolerans


def test_night_runs_less_often():
    last = {j: NIGHT - timedelta(minutes=20) for j in ("kktcar", "kibrisarabaal", "instagram", "facebook")}
    assert due_jobs(NIGHT, last) == []  # gece aralıkları 30/120 dk


def test_heartbeat_gap_alert_window():
    now = DAY
    assert heartbeat_gap(now, None) is None
    assert heartbeat_gap(now, now - timedelta(minutes=20)) is None
    assert heartbeat_gap(now, now - timedelta(minutes=90)) == 90
    assert heartbeat_gap(now, now - timedelta(days=10)) is None  # uzun duraklama: bilinçli kapatma sayılır
