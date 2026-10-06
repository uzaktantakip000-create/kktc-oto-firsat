"""Yedek: veritabanındaki TÜM tabloları CSV olarak dışa aktarır. Veritabanını yalnız OKUR (tek tutarlı anlık görüntü, salt okunur işlem).
Kullanım:
  python -m entrypoints.backup [hedef_klasör]
      ELLE (eskisi gibi): tam o klasöre yazar (varsayılan: ~/KKTC-yedek/<tarih-saat>/). Hiçbir şey silinmez, veritabanına HİÇ yazılmaz.
  python -m entrypoints.backup --dizin <klasör> [--sakla N]
      GÖZETİMSİZ (VPS haftalık zamanlayıcısı, deploy/bot/kktc-backup.*): <klasör>/<tarih-saat>/ altına yazar. Yedek BAŞARIYLA bitince
      bot_state.backup_last_ok = o anın UTC zamanı (ISO) yazılır (yeni bağlantıdan geri okunup doğrulanır; tek veritabanı yazması budur).
      --sakla N: en yeni N tam yedek kalır, daha eskileri silinir. Yalnız bu aracın kendi yazdıkları silinir (klasör adı <YYYYMMDD-HHMMSS>,
      içinde yalnız manifest.json ve manifest'teki tabloların .csv dosyaları); başka bir şey görülürse o klasöre dokunulmaz.
Başarısız yedek: çıkış kodu 0 DEĞİL, yarım kalan yeni klasör silinir, ESKİ yedeklere dokunulmaz, backup_last_ok yazılmaz.
Dosyalar kişisel veri (telefon, ilan metni) içerir: izinleri 0600 (klasör 0700); git deposunun DIŞINDA kalır, araç depo içindeki bir klasöre yazmayı reddeder.
Her veritabanı-yazan adımdan (migration, toplu UPDATE/DELETE) ÖNCE de çalıştırılır. (Supabase ücretsiz planda otomatik yedek/geri dönüş olmayabilir.)
Geri yükleme: tablo şeması aynıysa `COPY <tablo> FROM STDIN WITH CSV HEADER` ile; asıl amaç yanlışlıkla silinen/değişen VERİYİ kurtarabilmek."""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg import sql

from infrastructure.config import load_env, redact, require
from infrastructure.db.repository import Repository

STATE_KEY = "backup_last_ok"  # bot_state: son BAŞARILI gözetimsiz yedeğin UTC zamanı (ISO); sabah durum kodu bunu okur
MANIFEST = "manifest.json"
NAME_FORMAT = "%Y%m%d-%H%M%S"
STALE_INCOMPLETE_S = 6 * 3600  # manifest'i olmayan (yarım kalmış) eski klasör bu kadar saatten sonra temizlenir; yeni olana (çalışan yedek olabilir) dokunulmaz


def backup_name(now: datetime) -> str:
    return f"{now:{NAME_FORMAT}}"


def parse_backup_name(name: str) -> datetime | None:
    """Klasör adı bu aracın <YYYYMMDD-HHMMSS> kalıbına uyuyorsa zamanı, değilse None."""
    if not re.fullmatch(r"\d{8}-\d{6}", name):
        return None
    try:
        return datetime.strptime(name, NAME_FORMAT)
    except ValueError:
        return None


def default_target(now: datetime | None = None) -> Path:
    return Path.home() / "KKTC-yedek" / backup_name(now or datetime.now(timezone.utc))


def inside_git_repo(path: Path) -> bool:
    """Klasör (ya da üst klasörlerinden biri) bir git deposu mu? Kişisel veri depoya girmesin."""
    p = path.expanduser().resolve()
    return any((parent / ".git").exists() for parent in [p, *p.parents])


def open_private(path: Path):
    """Yeni dosyayı yalnız sahibi okuyup yazabilecek (0600) biçimde açar; var olanı ezmez."""
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb")


def list_tables(conn) -> list[str]:
    rows = conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' "
                        "AND table_type = 'BASE TABLE' ORDER BY 1").fetchall()
    return [r[0] for r in rows]


def export_table(conn, table: str, path: Path) -> int:
    """Tabloyu CSV'ye (başlık satırıyla, 0600) yazar; satır sayısını döner."""
    with conn.cursor() as cur, open_private(path) as f:
        with cur.copy(sql.SQL("COPY (SELECT * FROM {}) TO STDOUT WITH CSV HEADER").format(sql.Identifier(table))) as copy:
            for chunk in copy:
                f.write(chunk)
    return conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))).fetchone()[0]


def read_manifest(folder: Path) -> dict | None:
    """Klasördeki manifest.json (tam yedeğin işareti: en son yazılır); yok ya da bozuksa None."""
    try:
        data = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("tablolar"), dict) else None


def own_files(folder: Path) -> list[str] | None:
    """Klasördeki dosyaların HEPSİ bu aracın yazdıklarıysa adlarını, değilse None döner.
    Tam yedekte (manifest var): yalnız manifest.json ve manifest'teki tabloların .csv'leri. Yarım klasörde: yalnız *.csv ve manifest.json.
    Alt klasör, bağlantı (symlink) ya da tanımadığı herhangi bir dosya varsa None: o klasöre dokunulmaz."""
    manifest = read_manifest(folder)
    allowed = {MANIFEST} | {f"{t}.csv" for t in manifest["tablolar"]} if manifest else None

    def ours(name: str) -> bool:
        return name in allowed if allowed is not None else (name == MANIFEST or name.endswith(".csv"))

    names = []
    try:
        with os.scandir(folder) as it:
            for e in it:
                if e.is_symlink() or not e.is_file(follow_symlinks=False) or not ours(e.name):
                    return None
                names.append(e.name)
    except OSError:
        return None
    return names


def remove_backup_dir(folder: Path) -> bool:
    """Bu aracın yazdığı yedek klasörünü siler; başka bir şey görürse hiçbir şeyi silmez. Silindiyse True."""
    names = own_files(folder)
    if names is None:
        return False
    try:
        for name in names:
            os.unlink(folder / name)
        os.rmdir(folder)
    except OSError:
        return False
    return True


def apply_retention(parent: Path, keep: int, current: str | None = None, now: datetime | None = None) -> tuple[list[str], list[str]]:
    """`parent` içinde en yeni `keep` TAM yedeği bırakır, eskilerini siler. Yalnız adı <YYYYMMDD-HHMMSS> olan, gerçek klasör olan ve içinde
    yalnız bu aracın dosyaları bulunan klasörlere dokunur. `current` (az önce alınan yedek) saat sapması olsa bile asla silinmez.
    Manifest'siz klasör yedek sayılmaz: 6 saatten eskiyse (yarım kalmış yedek) temizlenir, yenisine dokunulmaz.
    Döner: (silinenler, silinemeyip yerinde bırakılanlar)."""
    if keep < 1:
        raise ValueError("keep en az 1 olmalı")
    now_ts = (now or datetime.now(timezone.utc)).timestamp()
    complete, incomplete = [], []
    with os.scandir(parent) as it:
        for e in sorted(it, key=lambda x: x.name):
            if parse_backup_name(e.name) is None or e.is_symlink() or not e.is_dir(follow_symlinks=False):
                continue
            if read_manifest(Path(e.path)) is not None:
                complete.append(e.name)
            elif now_ts - e.stat(follow_symlinks=False).st_mtime > STALE_INCOMPLETE_S:
                incomplete.append(e.name)
    keep_names = set(complete[-keep:]) | ({current} if current else set())
    removed, left = [], []
    for name in [n for n in complete if n not in keep_names] + [n for n in incomplete if n != current]:
        (removed if remove_backup_dir(parent / name) else left).append(name)
    return removed, left


def run_backup(conn, target: Path, now: datetime | None = None) -> dict:
    if inside_git_repo(target):
        raise SystemExit(f"Yedek klasörü bir git deposunun içinde olamaz (kişisel veri GitHub'a çıkabilir): {target}")
    if not target.parent.exists():  # yeni kurulan üst klasör de yalnız sahibine açık (0700); var olana dokunulmaz
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(target.parent, 0o700)
    target.mkdir(mode=0o700, exist_ok=False)  # klasör zaten varsa burada düşer: hiçbir şey silinmez
    os.chmod(target, 0o700)
    try:
        manifest = {"alindi": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"), "tablolar": {}}
        for table in list_tables(conn):
            manifest["tablolar"][table] = export_table(conn, table, target / f"{table}.csv")
        with open_private(target / MANIFEST) as f:  # EN SON yazılır: varsa yedek tamdır
            f.write(json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8"))
    except BaseException:
        remove_backup_dir(target)  # yarım kalan YENİ klasör (kişisel veri) kalmasın; eski yedeklere dokunulmaz
        raise
    return manifest


def open_snapshot(dsn: str):
    """Yedek bağlantısı: tüm tablolar aynı anlık görüntüden, işlem salt okunur."""
    conn = psycopg.connect(dsn, prepare_threshold=None, connect_timeout=30)
    conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    conn.read_only = True
    return conn


def record_success(dsn: str, when: datetime) -> str:
    """bot_state.backup_last_ok = `when` (UTC ISO). Yazı ayrı, autocommit bağlantıyla yapılır ve YENİ bir bağlantıdan geri okunup doğrulanır
    (yedek bağlantısı salt okunur ve açık işlemde olabilir; bağlantı kapanınca geri alınan yazı "yazıldı" görünmesin)."""
    value = when.astimezone(timezone.utc).isoformat(timespec="seconds")
    writer = Repository(dsn)  # autocommit=True
    try:
        writer.set_state(STATE_KEY, value)
    finally:
        writer.conn.close()
    reader = Repository(dsn)
    try:
        got = reader.get_state(STATE_KEY)
    finally:
        reader.conn.close()
    if got != value:
        raise RuntimeError(f"{STATE_KEY} yeni bağlantıdan okununca beklenen değer değil")
    return value


def dir_bytes(folder: Path) -> int:
    return sum(p.stat().st_size for p in folder.iterdir() if p.is_file())


def human_size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB".replace(".", ",")
    return f"{max(round(n / 1024), 1)} KB"


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m entrypoints.backup", allow_abbrev=False,
                                description="Veritabanı tablolarını CSV olarak yedekler (başlıktaki açıklamaya bakın).")
    p.add_argument("hedef", nargs="?", help="elle: yedeğin yazılacağı klasörün KENDİSİ (varsayılan ~/KKTC-yedek/<tarih-saat>)")
    p.add_argument("--dizin", help="gözetimsiz: yedekler bu klasörün altına <tarih-saat>/ olarak yazılır; başarıdan sonra bot_state.backup_last_ok yazılır")
    p.add_argument("--sakla", type=int, help="yalnız --dizin ile: en yeni bu kadar tam yedek kalır, eskileri silinir (en az 1)")
    args = p.parse_args(argv)
    if args.dizin and args.hedef:
        p.error("hedef klasör ile --dizin birlikte verilmez")
    if args.sakla is not None and not args.dizin:
        p.error("--sakla yalnız --dizin ile kullanılır (elle yedekte hiçbir şey silinmez)")
    if args.sakla is not None and args.sakla < 1:
        p.error("--sakla en az 1 olmalı")
    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    load_env()
    unattended = args.dizin is not None
    now = datetime.now(timezone.utc)
    if unattended:
        parent = Path(args.dizin).expanduser()
        target = parent / backup_name(now)
    else:
        target = Path(args.hedef).expanduser() if args.hedef else default_target(now)
    started = time.monotonic()
    try:
        dsn = require("DATABASE_URL")
        conn = open_snapshot(dsn)
        try:
            manifest = run_backup(conn, target, now)
        finally:
            conn.close()
    except SystemExit as e:  # git deposu içi reddi: kendi mesajıyla, başarısız
        print(f"YEDEK ALINAMADI: {e.code}", file=sys.stderr)
        return 1
    except Exception as e:  # her hata (bağlantı, disk, izin): sıfırdan farklı çıkış, eski yedeklere dokunulmaz
        print(f"YEDEK ALINAMADI: {type(e).__name__}: {redact(str(e))[:300]}", file=sys.stderr)
        return 1
    seconds = time.monotonic() - started
    size = dir_bytes(target)
    total = sum(manifest["tablolar"].values())
    print(f"yedek alındı: {target}  ({len(manifest['tablolar'])} tablo, {total} satır)")
    for table, n in manifest["tablolar"].items():
        print(f"  {table}: {n}")
    print(f"boyut: {human_size(size)} ({size} bayt), süre: {seconds:.1f} sn")
    if not unattended:
        return 0
    code = 0
    try:
        print(f"{STATE_KEY} yazıldı: {record_success(dsn, datetime.now(timezone.utc))}")
    except Exception as e:  # yedek dosyaları sağlam; ama sabah durum kodu yedeği göremez: hata olarak bildir
        print(f"HATA: yedek alındı ama {STATE_KEY} yazılamadı: {type(e).__name__}: {redact(str(e))[:300]}", file=sys.stderr)
        code = 1
    if args.sakla is not None:
        try:
            removed, left = apply_retention(parent, args.sakla, current=target.name, now=now)
            print(f"saklama (en yeni {args.sakla}): {len(removed)} eski yedek silindi" + (f" ({', '.join(removed)})" if removed else ""))
            if left:
                print(f"UYARI: silinemeyen/tanınmayan içerikli klasör(ler) yerinde bırakıldı: {', '.join(left)}", file=sys.stderr)
        except Exception as e:  # yeni yedek tamam; eskileri temizleyememek yedeği başarısız yapmaz (günlükte uyarı)
            print(f"UYARI: eski yedekler temizlenemedi: {type(e).__name__}: {redact(str(e))[:300]}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
