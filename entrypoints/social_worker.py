"""Sosyal medya okuyucu işçisi (VPS). systemd zamanlayıcısı ~20 dk'da bir çağırır; tur zamanı gelmediyse hiçbir yere bağlanmaz.

  python -m entrypoints.social_worker run <facebook|instagram> [--force]  # zamanı geldiyse bir tur (--force: yalnız pencere/sıra atlanır)
  python -m entrypoints.social_worker resume <platform> --yes            # sahip hesabı kontrol etti: freni kaldır
  python -m entrypoints.social_worker status                             # fren, son tur, sonraki tur, kaynak hataları
  python -m entrypoints.social_worker login <platform>                   # sahip VPS'teki tarayıcıda KENDİSİ girer (kod şifre yazmaz)
  python -m entrypoints.social_worker browse facebook                    # sahip kayıtlı oturumla tarayıcıda gruplara KENDİSİ katılır
  python -m entrypoints.social_worker compare facebook [--force]         # deneme A/B: grup sayfaları ↔ birleşik akış kapsaması

Ortam: SOCIAL_MODE=trial (zorunlu: 1. aşama yalnız deneme dosyasına yazar, veritabanına ASLA), SOCIAL_STATE_DIR, SOCIAL_SOURCES_CSV,
SOCIAL_EXPECTED_IP_<PLATFORM>. Platformlar birbirinden bağımsızdır: ayrı kilit, ayrı durum dosyası, ayrı fren.
Çıkış kodu: 0 tamam/atlandı, 2 fren (bu turda çekildi ya da hâlâ çekili), 1 hata."""
import argparse
import fcntl
import importlib
import json
import os
import random
import re
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from application.social_port import PLATFORMS, SocialSource
from application.social_run import (ERROR, SKIP_BRAKE, STOPPED, compare_feeds, current_block, is_due, key, local, resume,
                                    run_cycle, status_lines)
from domain.social_schedule import in_window
from infrastructure.config import redact
from infrastructure.fx import frankfurter
from infrastructure.social_files import FileStateStore, JsonlTrialSink, SourcesFileError, delete_old_trial_files, load_sources

FETCHERS = {"facebook": "infrastructure.collectors.facebook_browser",
            "instagram": "infrastructure.collectors.instagram_instaloader"}  # tembel yüklenir: Playwright/Instaloader yalnız turda
DEFAULT_STATE_DIR = "/var/lib/kktc-social"
DEFAULT_SOURCES_CSV = "/etc/kktc-social/sources.csv"
TRIAL_MODE = "trial"
TRIAL_KEEP_DAYS = 14
EXIT_OK, EXIT_ERROR, EXIT_BRAKE = 0, 1, 2
MODE_REFUSED = "SOCIAL_MODE=trial değil: veritabanı kipi 2. aşamada açılacak"


class UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # argparse'ın 2 çıkışı "fren" koduyla karışmasın
        self.print_usage(sys.stderr)
        raise UsageError(message)


@dataclass
class Config:
    env: Mapping[str, str]

    @property
    def state_dir(self) -> Path:
        return Path(self.env.get("SOCIAL_STATE_DIR") or DEFAULT_STATE_DIR)

    @property
    def sources_csv(self) -> Path:
        return Path(self.env.get("SOCIAL_SOURCES_CSV") or DEFAULT_SOURCES_CSV)

    @property
    def trial_dir(self) -> Path:
        return self.state_dir / "trial"

    def expected_ip(self, platform: str) -> str:
        return self.env.get(f"SOCIAL_EXPECTED_IP_{platform.upper()}", "")

    def store_path(self, platform: str) -> Path:
        return self.state_dir / f"{platform}.state.json"

    def store(self, platform: str) -> FileStateStore:
        return FileStateStore(self.store_path(platform))

    def trial_mode(self) -> bool:
        return (self.env.get("SOCIAL_MODE") or "").strip().lower() == TRIAL_MODE


@contextmanager
def platform_lock(state_dir: Path, platform: str) -> Iterator[bool]:
    """Platform başına tek çalışma (flock, beklemeden). True = kilit alındı; False = başka bir çalışma sürüyor."""
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(state_dir / f"{platform}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            got = True
        except OSError:
            got = False
        yield got
    finally:
        os.close(fd)  # kapatınca kilit bırakılır


@contextmanager
def rate_proxy(env: Mapping[str, str], platform: str) -> Iterator[None]:
    """Döviz kuru isteği (infrastructure/fx/frankfurter, httpx) de platformun proxy'sinden çıksın: VPS güvenlik duvarı işçi kullanıcısına
    proxy dışında çıkış vermez; aksi halde TL/EUR fiyatlı her ilan kaynağı "işleme hatası" verirdi. Yalnız bu süreçte, tur boyunca;
    Playwright ve Instaloader kendi proxy ayarlarını kullanır."""
    proxy = (env.get(f"SOCIAL_PROXY_{platform.upper()}") or "").strip()
    names = ("HTTPS_PROXY", "HTTP_PROXY")
    old = {n: os.environ.get(n) for n in names}
    if proxy:
        for n in names:
            os.environ[n] = proxy
    try:
        yield
    finally:
        for n, v in old.items():
            if v is None:
                os.environ.pop(n, None)
            else:
                os.environ[n] = v


_URL = re.compile(r"https?://\S+")


def safe_error(e: BaseException) -> str:
    """Hata metni günlüğe: gizli değerler maskelenir, adresler (grup bağlantısı olabilir) atılır."""
    return f"{type(e).__name__}: {_URL.sub('<adres>', redact(str(e)))[:300]}"


def _fetcher_module(platform: str, import_module):
    try:
        return import_module(FETCHERS[platform])
    except ModuleNotFoundError as e:
        raise RuntimeError(f"{platform} okuyucu modülü yüklenemedi (eksik: {e.name})") from None


def _not_due_text(store: FileStateStore, platform: str, now: datetime) -> str:
    if not in_window(now):
        return f"{platform}: gündüz penceresi dışında (KKTC 08:00–23:00), okunmaz"
    try:
        nxt = datetime.fromisoformat(store.get_state(key("next_after", platform)) or "")
    except ValueError:
        nxt = None
    return f"{platform}: sıra gelmedi (sonraki tur en erken {local(nxt)})"


def cmd_run(cfg: Config, platform: str, force: bool, *, clock, sleep, rng, import_module, log) -> int:
    if not cfg.trial_mode():
        log(MODE_REFUSED)
        return EXIT_ERROR
    with platform_lock(cfg.state_dir, platform) as got:
        if not got:
            log(f"{platform}: önceki çalışma sürüyor, bu çağrı atlandı")
            return EXIT_OK
        now = clock()
        if removed := delete_old_trial_files(cfg.trial_dir, TRIAL_KEEP_DAYS, now):
            log(f"{removed} eski deneme dosyası silindi ({TRIAL_KEEP_DAYS} günden eski)")
        store = cfg.store(platform)
        block = current_block(store, platform, now)  # fren --force ile de aşılmaz
        if block is not None:
            log(f"{platform}: tur yok, {block.text}")
            return EXIT_BRAKE if block.kind == "fren" else EXIT_OK
        if not force and not is_due(store, platform, now):  # zamanı gelmedi: okuyucu hiç kurulmaz, ağa çıkılmaz
            log(_not_due_text(store, platform, now))
            return EXIT_OK
        sources = load_sources(cfg.sources_csv, platform)
        if not sources:
            log(f"{platform}: aktif kaynak yok ({cfg.sources_csv})")
            return EXIT_OK
        fetcher = _fetcher_module(platform, import_module).build(cfg.env, cfg.state_dir)
        sink = JsonlTrialSink(cfg.trial_dir, platform, clock, aliases={str(s.source_id or s.key): s.alias for s in sources})
        frankfurter.use_store(store)  # kur servisi yanıt vermezse son bilinen kur (durum dosyasında) kullanılır
        with rate_proxy(cfg.env, platform):
            report = run_cycle(platform, fetcher, sources, store, sink, now=now, sleep=sleep, rng=rng,
                               expected_ip=cfg.expected_ip(platform), log=log)
        for line in report.summary():
            log(line)
        return EXIT_BRAKE if report.status in (STOPPED, SKIP_BRAKE) else EXIT_OK


def cmd_resume(cfg: Config, platform: str, yes: bool, *, log) -> int:
    if not yes:
        log(f"Önce {platform} hesabına tarayıcıdan gir; uyarı/doğrulama yoksa: resume {platform} --yes")
        return EXIT_ERROR
    with platform_lock(cfg.state_dir, platform) as got:
        if not got:
            log(f"{platform}: çalışma sürüyor, bitince tekrar dene")
            return EXIT_ERROR
        had = resume(cfg.store(platform), platform)
        log(f"{platform}: " + ("fren kaldırıldı" if had else "fren yoktu")
            + "; 7 gün içinde yeni bir hız uyarısı gelirse yine HARD fren olur")
        return EXIT_OK


def cmd_status(cfg: Config, *, clock, log) -> int:
    now = clock()
    for platform in PLATFORMS:
        if not cfg.store_path(platform).exists():
            log(f"{platform}: henüz hiç çalışmadı")
            continue
        try:
            sources: list[SocialSource] = load_sources(cfg.sources_csv, platform)
        except SourcesFileError:
            sources = []
            log(f"{platform}: kaynak listesi okunamadı, kaynak hataları gösterilmiyor")
        for line in status_lines(cfg.store(platform), platform, now, sources):
            log(line)
    return EXIT_OK


def cmd_login(cfg: Config, platform: str, *, import_module, log) -> int:
    with platform_lock(cfg.state_dir, platform) as got:
        if not got:
            log(f"{platform}: çalışma sürüyor (tarayıcı profili kullanımda), bitince tekrar dene")
            return EXIT_ERROR
        _fetcher_module(platform, import_module).login(cfg.env, cfg.state_dir)
        log(f"{platform}: giriş adımı bitti; ilk turdan önce `status` ile kontrol et")
        return EXIT_OK


def cmd_browse(cfg: Config, platform: str, *, import_module, log) -> int:
    with platform_lock(cfg.state_dir, platform) as got:
        if not got:
            log(f"{platform}: çalışma sürüyor (okuma turu), bitince tekrar dene")
            return EXIT_ERROR
        _fetcher_module(platform, import_module).browse(cfg.env, cfg.state_dir)
        log(f"{platform}: elle kullanım bitti")
        return EXIT_OK


def cmd_compare(cfg: Config, platform: str, force: bool, *, clock, sleep, rng, import_module, log) -> int:
    if not cfg.trial_mode():
        log(MODE_REFUSED)
        return EXIT_ERROR
    with platform_lock(cfg.state_dir, platform) as got:
        if not got:
            log(f"{platform}: önceki çalışma sürüyor, karşılaştırma atlandı")
            return EXIT_OK
        now = clock()
        store = cfg.store(platform)
        block = current_block(store, platform, now)
        if block is not None:
            log(f"{platform}: karşılaştırma yok, {block.text}")
            return EXIT_BRAKE if block.kind == "fren" else EXIT_OK
        if not force and not is_due(store, platform, now):
            log(_not_due_text(store, platform, now))
            return EXIT_OK
        sources = load_sources(cfg.sources_csv, platform)
        if not sources:
            log(f"{platform}: aktif kaynak yok ({cfg.sources_csv})")
            return EXIT_OK
        fetcher = _fetcher_module(platform, import_module).build(cfg.env, cfg.state_dir)
        out = compare_feeds(fetcher, sources, store, now=now, sleep=sleep, rng=rng, expected_ip=cfg.expected_ip(platform), log=log)
        cfg.trial_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = cfg.trial_dir / f"compare-{platform}-{now:%Y%m%d-%H%M}.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        for alias, g in out.get("gruplar", {}).items():
            if "hata" in g:
                log(f"{platform} {alias}: okunamadı ({g['hata']})")
            else:
                share = "-" if g["kapsama"] is None else f"%{round(100 * g['kapsama'])}"
                log(f"{platform} {alias}: grup sayfasında {g['grup_ici']} gönderi, birleşik akışta da görülen {g['ortak']} ({share})")
        log(f"{platform}: karşılaştırma [{out['durum']}] {path.name} dosyasına yazıldı" + (f" — {out['not']}" if out.get("not") else ""))
        if out["durum"] in (STOPPED, SKIP_BRAKE):
            return EXIT_BRAKE
        return EXIT_ERROR if out["durum"] == ERROR else EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="python -m entrypoints.social_worker", description="Sosyal medya okuyucu işçisi (deneme kipi)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="zamanı geldiyse bir okuma turu")
    run.add_argument("platform", choices=PLATFORMS)
    run.add_argument("--force", action="store_true", help="gündüz penceresi ve sıra beklenmez (fren ASLA aşılmaz)")
    res = sub.add_parser("resume", help="sahip hesabı kontrol etti: freni kaldır")
    res.add_argument("platform", choices=PLATFORMS)
    res.add_argument("--yes", action="store_true", help="hesabı kontrol ettim")
    sub.add_parser("status", help="fren, son tur, sonraki tur")
    login = sub.add_parser("login", help="sahip tarayıcıda kendisi girer")
    login.add_argument("platform", choices=PLATFORMS)
    brw = sub.add_parser("browse", help="sahip kayıtlı oturumla tarayıcıda kendisi gezinir (gruplara katılma)")
    brw.add_argument("platform", choices=["facebook"])
    cmp_ = sub.add_parser("compare", help="deneme A/B: grup sayfaları ↔ birleşik akış")
    cmp_.add_argument("platform", choices=["facebook"])
    cmp_.add_argument("--force", action="store_true")
    return parser


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None, *,
         clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc), sleep: Callable[[float], None] = time.sleep,
         rng: random.Random | None = None, import_module=importlib.import_module, log: Callable[[str], None] = print) -> int:
    try:
        args = build_parser().parse_args(argv)
    except UsageError as e:
        log(f"komut hatalı: {e}")
        return EXIT_ERROR
    cfg = Config(os.environ if env is None else env)
    rng = rng or random.Random()
    try:
        if args.cmd == "run":
            return cmd_run(cfg, args.platform, args.force, clock=clock, sleep=sleep, rng=rng, import_module=import_module, log=log)
        if args.cmd == "resume":
            return cmd_resume(cfg, args.platform, args.yes, log=log)
        if args.cmd == "status":
            return cmd_status(cfg, clock=clock, log=log)
        if args.cmd == "login":
            return cmd_login(cfg, args.platform, import_module=import_module, log=log)
        if args.cmd == "browse":
            return cmd_browse(cfg, args.platform, import_module=import_module, log=log)
        return cmd_compare(cfg, args.platform, args.force, clock=clock, sleep=sleep, rng=rng, import_module=import_module, log=log)
    except SourcesFileError as e:
        log(str(e))
        return EXIT_ERROR
    except Exception as e:
        log(f"{getattr(args, 'platform', 'sosyal')}: hata — {safe_error(e)}")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
