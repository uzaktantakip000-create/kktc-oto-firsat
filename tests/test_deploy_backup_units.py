"""deploy/bot/kktc-backup.{service,timer}: sunucuya kurulmadan önce bozulamayacak değişmezler (zamanlama çakışması, yazılabilir yer, saklama sayısı)."""
import re
from pathlib import Path

BOT = Path(__file__).resolve().parent.parent / "deploy" / "bot"
BACKUP_DIR = "/var/lib/kktc-bot/backups"


def directives(name: str) -> dict[str, str]:
    """Birim dosyasındaki `Anahtar=değer` satırları (yorum satırları atlanır; aynı anahtar tekrarlanırsa sonuncusu)."""
    out = {}
    for line in (BOT / name).read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    return out


def test_timer_is_weekly_on_a_minute_no_other_job_starts_at():
    t = directives("kktc-backup.timer")
    m = re.fullmatch(r"Sun \*-\*-\* (\d\d):(\d\d):00 UTC", t["OnCalendar"])
    assert m, t["OnCalendar"]  # haftada bir, gün ve saat dilimi açık
    minute = int(m.group(2))
    ticks = {5, 20, 35, 50}  # kktc-tick.timer: *:05/15
    browser = 7  # kktc-browser.timer: çift saatlerin :07'si
    updater = {0, 10, 20, 30, 40, 50}  # kktc-bot-update.timer: *:0/10
    assert minute not in ticks | {browser} | updater
    assert 1 <= int(m.group(1)) <= 3  # gece bakımından (UTC 00:00-04:00 penceresinin ilk turu) sonra, hâlâ sessiz gece
    jitter = int(re.fullmatch(r"(\d+)", t["RandomizedDelaySec"]).group(1))
    assert not any(minute <= x < minute + 2 + jitter // 60 for x in ticks | {browser} | updater)  # gecikme payı dahil çakışmaz
    assert t["Persistent"] == "true" and t["Unit"] == "kktc-backup.service"


def test_service_writes_only_to_the_backup_folder_and_keeps_four():
    s = directives("kktc-backup.service")
    assert s["Type"] == "oneshot" and s["User"] == "kktc-bot" and s["EnvironmentFile"] == "/etc/kktc-bot/bot.env"
    assert s["ExecStart"].endswith(f"-m entrypoints.backup --dizin {BACKUP_DIR} --sakla 4")
    assert s["ReadWritePaths"] == BACKUP_DIR  # tek yazılabilir yer
    assert s["ProtectSystem"] == "strict" and s["UMask"] == "0077" and s["NoNewPrivileges"] == "yes"
    assert s["TimeoutStartSec"].endswith("min") and s["MemoryMax"].endswith("M")
    assert s["Restart"] == "on-failure"  # Pazar gecesi tek hata bir haftalık boşluk bırakmasın


def test_setup_installs_enables_and_creates_the_backup_folder():
    setup = (BOT / "setup.sh").read_text(encoding="utf-8")
    assert "kktc-backup.service kktc-backup.timer; do" in setup  # birimler kurulur
    assert re.search(r"systemctl enable [^\n]*kktc-backup\.timer", setup)  # yalnız etkinleştirilir
    commands = "\n".join(line for line in setup.splitlines() if not line.strip().startswith(("echo", "#")))  # yalnız çalışan satırlar
    assert not re.search(r"systemctl (start|restart|enable --now)[^\n]*kktc-backup", commands)  # kendiliğinden başlatmaz
    assert 'BACKUP_DIR=$STATE_DIR/backups' in setup and 'as_bot install -d -m 700 "$BACKUP_DIR"' in setup  # kktc-bot olarak, 0700
