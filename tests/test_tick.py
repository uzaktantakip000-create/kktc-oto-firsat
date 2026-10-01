from datetime import datetime, timedelta, timezone

from entrypoints.tick import due_jobs, heartbeat_gap

DAY = datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)   # KKTC 12:00
NIGHT = datetime(2026, 10, 2, 1, 0, tzinfo=timezone.utc)  # KKTC 04:00


def test_first_run_everything_is_due():
    assert set(due_jobs(DAY, {})) == {"kktcar", "kibrisarabaal", "instagram", "facebook", "mezunum"}


def test_only_jobs_whose_interval_passed_are_due():
    last = {"kktcar": DAY - timedelta(minutes=16), "kibrisarabaal": DAY - timedelta(minutes=5),
            "instagram": DAY - timedelta(minutes=13), "facebook": DAY - timedelta(minutes=60), "mezunum": DAY - timedelta(minutes=10)}
    assert set(due_jobs(DAY, last)) == {"kktcar", "instagram"}  # instagram gündüz 15 dk (13 dk geçti, 3 dk tolerans)


def test_night_runs_less_often():
    last = {j: NIGHT - timedelta(minutes=20) for j in ("kktcar", "kibrisarabaal", "instagram", "facebook", "mezunum")}
    assert due_jobs(NIGHT, last) == []  # gece aralıkları 30/60/120 dk


def test_heartbeat_gap_alert_window():
    now = DAY
    assert heartbeat_gap(now, None) is None
    assert heartbeat_gap(now, now - timedelta(minutes=20)) is None
    assert heartbeat_gap(now, now - timedelta(minutes=90)) == 90
    assert heartbeat_gap(now, now - timedelta(days=10)) is None  # uzun duraklama: bilinçli kapatma sayılır


def test_facebook_every_two_hours_by_day_and_eight_by_night():
    assert "facebook" in due_jobs(DAY, {"facebook": DAY - timedelta(minutes=118)})  # 120 dk - 3 dk tolerans
    assert "facebook" not in due_jobs(DAY, {"facebook": DAY - timedelta(minutes=100)})
    assert "facebook" not in due_jobs(NIGHT, {"facebook": NIGHT - timedelta(hours=4)})
    assert "facebook" in due_jobs(NIGHT, {"facebook": NIGHT - timedelta(hours=8)})
