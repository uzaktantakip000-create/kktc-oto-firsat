"""deploy/bot/kktc-fb-fast.{path,service}: Facebook hızlı şeridi (11.10.2026) sunucuya kurulmadan önce bozulamayacak değişmezler."""
import re

from tests.test_deploy_backup_units import BOT, directives


def test_path_watches_the_handoff_folder_and_starts_the_fast_lane():
    p = directives("kktc-fb-fast.path")
    assert p["PathChanged"] == "/var/lib/kktc-social-devir" and p["Unit"] == "kktc-fb-fast.service"
    assert p["WantedBy"] == "paths.target"


def test_service_runs_like_the_tick_but_writes_nothing_else():
    s, t = directives("kktc-fb-fast.service"), directives("kktc-tick.service")
    assert s["Type"] == "oneshot" and s["User"] == "kktc-bot" and s["ExecStart"].endswith("-m entrypoints.fb_fast")
    assert s["ReadWritePaths"] == "/var/lib/kktc-bot" and "StateDirectory" not in s  # kaynak listesini yalnız tur yazar
    for key in ("EnvironmentFile", "WorkingDirectory", "ProtectSystem", "ProtectHome", "NoNewPrivileges", "UMask", "MemoryMax",
                "CapabilityBoundingSet", "ConditionFileNotEmpty"):
        assert s[key] == t[key], key
    assert int(re.fullmatch(r"(\d+)min", s["TimeoutStartSec"]).group(1)) <= 10


def test_setup_installs_and_enables_the_path_and_updater_waits_for_a_running_fast_lane():
    setup = (BOT / "setup.sh").read_text(encoding="utf-8")
    assert re.search(r"for unit in [^\n]*kktc-fb-fast\.service kktc-fb-fast\.path[^\n]*; do", setup)
    assert re.search(r"systemctl enable [^\n]*kktc-fb-fast\.path", setup)
    commands = "\n".join(line for line in setup.splitlines() if not line.strip().startswith(("echo", "#")))
    assert not re.search(r"systemctl (start|restart|enable --now)[^\n]*kktc-fb-fast", commands)  # kendiliğinden başlatmaz
    update = (BOT / "update.sh").read_text(encoding="utf-8")
    assert "for unit in kktc-tick.service kktc-browser.service kktc-fb-fast.service; do" in update  # çalışırken kod değişmez
