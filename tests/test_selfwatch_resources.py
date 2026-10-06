"""Öz-izleme 4: VPS kaynakları (application/selfwatch.py: read_resources, resource_watch). Boş disk < %15 ya da kullanılabilir bellek < 400 MB ise
tek uyarı (12 saatte bir tekrar). Yalnız standart kütüphane; /proc/meminfo olmayan yerde (macOS) sessizce atlanır. Ölçümler sahte/geçici dosyadır."""
from datetime import timedelta

import pytest

from application import selfwatch
from application.runner_gate import VPS_TICK_KEY
from entrypoints import tick
from tests.test_runner_gate import SECRET_DSN, Tick, where
from tests.test_selfwatch import NOW, Repo, tg  # noqa: F401  (tg: pytest fixture)
from tests.test_selfwatch_failover import Boom


def snap(disk_pct=40.0, mem_mb=3000.0, disk_gb=30.0):
    return {"disk_pct": disk_pct, "disk_gb": disk_gb, "mem_mb": mem_mb}


def meminfo(tmp_path, kb=3_072_000, extra=""):
    path = tmp_path / "meminfo"
    path.write_text(f"MemTotal:        8000000 kB\nMemFree:          100000 kB\nMemAvailable:   {kb} kB\nBuffers:           10 kB\n{extra}")
    return str(path)


def test_the_thresholds_are_the_agreed_ones():
    assert selfwatch.DISK_MIN_FREE_PCT == 15 and selfwatch.MEM_MIN_MB == 400


# --- ölçüm --------------------------------------------------------------------------------------------------------------------
def test_read_resources_reads_memavailable_in_mb_and_the_disk_of_the_given_root(tmp_path):
    got = selfwatch.read_resources(str(tmp_path), meminfo(tmp_path))
    assert got["mem_mb"] == pytest.approx(3000.0)  # 3.072.000 kB
    assert 0 < got["disk_pct"] <= 100 and got["disk_gb"] > 0


def test_read_resources_computes_the_free_percentage_from_disk_usage(tmp_path, monkeypatch):
    monkeypatch.setattr(selfwatch.shutil, "disk_usage", lambda root: (80 * 1024 ** 3, 68 * 1024 ** 3, 12 * 1024 ** 3))
    got = selfwatch.read_resources("/", meminfo(tmp_path))
    assert got["disk_pct"] == pytest.approx(15.0) and got["disk_gb"] == pytest.approx(12.0)


def test_read_resources_skips_silently_without_proc_meminfo_or_with_an_unreadable_one(tmp_path):
    assert selfwatch.read_resources("/", str(tmp_path / "yok")) is None  # macOS: /proc/meminfo yok
    assert selfwatch.read_resources("/", meminfo(tmp_path, kb="bozuk")) is None
    empty = tmp_path / "bos"
    empty.write_text("MemTotal: 1 kB\n")  # MemAvailable satırı yok (çok eski çekirdek)
    assert selfwatch.read_resources("/", str(empty)) is None
    assert selfwatch.read_resources(str(tmp_path / "yok-klasor"), meminfo(tmp_path)) is None  # disk ölçülemedi


def test_read_resources_works_on_this_machine_when_proc_meminfo_exists_else_returns_none():
    got = selfwatch.read_resources()
    assert got is None or (got["mem_mb"] > 0 and 0 <= got["disk_pct"] <= 100)


# --- uyarı --------------------------------------------------------------------------------------------------------------------
def test_alerts_with_the_numbers_when_the_disk_is_below_15_percent(tg):
    selfwatch.resource_watch(Repo(), NOW, read=lambda: snap(disk_pct=12.4, disk_gb=9.5, mem_mb=6300))
    assert tg == ["⚠️ Sunucuda kaynak azalıyor: disk %12 boş (9,5 GB), bellek 6,2 GB kullanılabilir. Eşik: disk %15, bellek 400 MB."]


def test_alerts_with_the_numbers_when_the_memory_is_below_400_mb(tg):
    selfwatch.resource_watch(Repo(), NOW, read=lambda: snap(disk_pct=60, disk_gb=46.2, mem_mb=350.4))
    assert tg == ["⚠️ Sunucuda kaynak azalıyor: disk %60 boş (46,2 GB), bellek 350 MB kullanılabilir. Eşik: disk %15, bellek 400 MB."]


@pytest.mark.parametrize("disk,mem,expect", [(15.0, 400.0, False), (14.99, 4000, True), (50, 399.9, True), (5, 100, True), (80, 7000, False)])
def test_the_boundaries(tg, disk, mem, expect):
    selfwatch.resource_watch(Repo(), NOW, read=lambda: snap(disk_pct=disk, mem_mb=mem))
    assert bool(tg) is expect


def test_one_alert_is_not_repeated_within_12_hours_and_repeats_after(tg):
    repo = Repo()
    low = lambda: snap(disk_pct=9)  # noqa: E731
    selfwatch.resource_watch(repo, NOW, read=low)
    for minutes in (15, 30, 6 * 60, 12 * 60 - 1):
        repo.now = NOW + timedelta(minutes=minutes)
        selfwatch.resource_watch(repo, repo.now, read=low)
    assert len(tg) == 1
    repo.now = NOW + timedelta(hours=12, minutes=1)
    selfwatch.resource_watch(repo, repo.now, read=low)
    assert len(tg) == 2


def test_nothing_is_sent_or_written_when_the_measurement_is_unavailable_or_fine(tg):
    repo = Repo()
    selfwatch.resource_watch(repo, NOW, read=lambda: None)  # /proc/meminfo yok
    selfwatch.resource_watch(repo, NOW, read=lambda: snap())
    assert tg == [] and repo.writes == [] and repo.reads == []


def test_without_a_given_reader_the_module_level_one_is_used_at_call_time(tg, monkeypatch):
    monkeypatch.setattr(selfwatch, "read_resources", lambda: snap(disk_pct=3))
    selfwatch.resource_watch(Repo(), NOW)
    assert len(tg) == 1


# --- tick.py bağlantısı -------------------------------------------------------------------------------------------------------
def run_tick(monkeypatch, runner, repo, eval_ok=True):
    where(monkeypatch, runner)
    Tick(monkeypatch, repo, eval_ok=eval_ok)
    monkeypatch.setattr(tick, "due_jobs", lambda *a: [])
    tick.main()


def test_a_vps_tick_checks_the_resources_and_the_scan_is_unchanged(monkeypatch, tg):
    monkeypatch.setattr(selfwatch, "read_resources", lambda: snap(disk_pct=8))
    monkeypatch.setattr(selfwatch, "listener_watch", lambda repo, now: None)
    repo = Repo()
    run_tick(monkeypatch, "vps", repo)
    assert len(tg) == 1 and tg[0].startswith("⚠️ Sunucuda kaynak azalıyor: disk %8 boş") and VPS_TICK_KEY in repo.state


def test_the_resource_check_also_runs_when_the_evaluation_failed(monkeypatch, tg):
    monkeypatch.setattr(selfwatch, "read_resources", lambda: snap(mem_mb=100))
    monkeypatch.setattr(selfwatch, "listener_watch", lambda repo, now: None)
    with pytest.raises(SystemExit) as e:
        run_tick(monkeypatch, "vps", Repo(), eval_ok=False)
    assert e.value.code == 1 and len(tg) == 1


@pytest.mark.parametrize("runner", ["github", "yerel"])
def test_the_resource_check_never_runs_off_the_vps(monkeypatch, tg, runner):
    calls = []
    monkeypatch.setattr(selfwatch, "read_resources", lambda: calls.append(1) or snap(disk_pct=1))
    run_tick(monkeypatch, runner, Repo())
    assert calls == [] and tg == []


def test_a_failing_measurement_or_alert_never_breaks_the_tick(monkeypatch, tg, capsys):
    monkeypatch.setattr(selfwatch, "listener_watch", lambda repo, now: None)

    def boom():
        raise RuntimeError(SECRET_DSN)

    monkeypatch.setattr(selfwatch, "read_resources", boom)
    repo = Repo()
    run_tick(monkeypatch, "vps", repo)  # çıkış 0
    assert VPS_TICK_KEY in repo.state
    monkeypatch.setattr(selfwatch, "read_resources", lambda: snap(disk_pct=1))
    selfwatch.after_tick(Boom(), True, NOW)  # veritabanı çökük: uyarı yazılamaz, yine fırlatmaz
    out = capsys.readouterr().out
    assert "öz-izleme (kaynak izleme) başarısız: RuntimeError" in out and SECRET_DSN not in out
