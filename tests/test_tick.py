from datetime import datetime, timedelta, timezone

from entrypoints import tick
from entrypoints.tick import due_jobs, eval_stale_minutes, heartbeat_gap

DAY = datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)   # KKTC 12:00
NIGHT = datetime(2026, 10, 2, 1, 0, tzinfo=timezone.utc)  # KKTC 04:00


def test_first_run_everything_is_due():
    assert set(due_jobs(DAY, {})) == {"kktcar", "kibrisarabaal", "instagram", "facebook", "mezunum", "kibriscars", "pazarkibris", "sahibindenarabakibris"}


def test_only_jobs_whose_interval_passed_are_due():
    last = {"kktcar": DAY - timedelta(minutes=16), "kibrisarabaal": DAY - timedelta(minutes=5),
            "instagram": DAY - timedelta(minutes=13), "facebook": DAY - timedelta(minutes=60), "mezunum": DAY - timedelta(minutes=10),
            "kibriscars": DAY - timedelta(minutes=10), "pazarkibris": DAY - timedelta(minutes=10), "sahibindenarabakibris": DAY - timedelta(minutes=10)}
    assert set(due_jobs(DAY, last)) == {"kktcar", "instagram"}  # instagram gündüz 15 dk (13 dk geçti, 3 dk tolerans)


def test_night_runs_less_often():
    last = {j: NIGHT - timedelta(minutes=20) for j in ("kktcar", "kibrisarabaal", "instagram", "facebook", "mezunum", "kibriscars", "pazarkibris", "sahibindenarabakibris")}
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


def test_eval_stale_alert_window():
    now = DAY
    assert eval_stale_minutes(now, None, None) is None  # ilk kurulum: kayıt yok
    assert eval_stale_minutes(now, now - timedelta(minutes=30), None) is None
    assert eval_stale_minutes(now, now - timedelta(minutes=50), None) == 50
    assert eval_stale_minutes(now, now - timedelta(minutes=50), 90) is None  # kesinti uyarısı zaten verildi: çift mesaj yok
    assert eval_stale_minutes(now, now - timedelta(days=5), None) is None  # uzun duraklama: bilinçli kapatma sayılır


def test_timed_evaluate_survives_a_crash(monkeypatch, capsys):
    def boom():
        raise RuntimeError("değerlendirme çöktü")

    monkeypatch.setattr(tick.cron_evaluate, "main", boom)
    assert tick.timed_evaluate("değerlendirme") is False  # tur devam eder, sonunda hata ile biter
    assert "HATA" in capsys.readouterr().out
    monkeypatch.setattr(tick.cron_evaluate, "main", lambda: None)
    assert tick.timed_evaluate("değerlendirme") is True


def test_import_social_reads_the_handoff_dir_and_never_stops_the_tick(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(tick.social_import, "import_facebook", lambda repo, d, log: calls.append((repo, d)))
    errors = []
    tick.import_social("repo", errors, devir_dir="/devir")
    assert calls == [("repo", "/devir")] and errors == []

    def boom(repo, d, log):
        raise PermissionError("izin yok: /devir")

    monkeypatch.setattr(tick.social_import, "import_facebook", boom)
    tick.import_social("repo", errors, devir_dir="/devir")  # çökme turu durdurmaz; hata loga ve toplama hatalarına
    assert errors == [("sosyal_devir", "PermissionError: izin yok: /devir")]
    assert "sosyal_devir: HATA" in capsys.readouterr().out


def test_social_import_runs_before_the_first_evaluation():
    src = open(tick.__file__, encoding="utf-8").read()
    main = src[src.index("def main()"):]
    assert main.index("import_social(repo, errors)") < main.index('timed_evaluate("değerlendirme")')
