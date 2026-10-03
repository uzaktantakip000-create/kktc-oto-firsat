"""Yerel yedek: veritabanındaki TÜM tabloları CSV olarak dışa aktarır. SALT OKUNUR (veritabanına yazmaz), tek tutarlı anlık görüntü.
Kullanım: python -m entrypoints.backup [hedef_klasör]     (varsayılan: ~/KKTC-yedek/<tarih-saat>/)
Dosyalar kişisel veri (telefon, ilan metni) içerir: git deposunun DIŞINDA kalır; araç, git deposu içindeki bir klasöre yazmayı reddeder.
Her veritabanı-yazan adımdan (migration, toplu UPDATE/DELETE) ÖNCE çalıştırılır. (Supabase ücretsiz planda otomatik yedek/geri dönüş olmayabilir.)
Geri yükleme: tablo şeması aynıysa `COPY <tablo> FROM STDIN WITH CSV HEADER` ile; asıl amaç yanlışlıkla silinen/değişen VERİYİ kurtarabilmek."""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg import sql

from infrastructure.config import load_env, require


def default_target(now: datetime | None = None) -> Path:
    return Path.home() / "KKTC-yedek" / f"{(now or datetime.now(timezone.utc)):%Y%m%d-%H%M%S}"


def inside_git_repo(path: Path) -> bool:
    """Klasör (ya da üst klasörlerinden biri) bir git deposu mu? Kişisel veri depoya girmesin."""
    p = path.expanduser().resolve()
    return any((parent / ".git").exists() for parent in [p, *p.parents])


def list_tables(conn) -> list[str]:
    rows = conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' "
                        "AND table_type = 'BASE TABLE' ORDER BY 1").fetchall()
    return [r[0] for r in rows]


def export_table(conn, table: str, path: Path) -> int:
    """Tabloyu CSV'ye (başlık satırıyla) yazar; satır sayısını döner."""
    with conn.cursor() as cur, open(path, "wb") as f:
        with cur.copy(sql.SQL("COPY (SELECT * FROM {}) TO STDOUT WITH CSV HEADER").format(sql.Identifier(table))) as copy:
            for chunk in copy:
                f.write(chunk)
    return conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))).fetchone()[0]


def run_backup(conn, target: Path, now: datetime | None = None) -> dict:
    if inside_git_repo(target):
        raise SystemExit(f"Yedek klasörü bir git deposunun içinde olamaz (kişisel veri GitHub'a çıkabilir): {target}")
    target.mkdir(parents=True, exist_ok=False)
    os.chmod(target, 0o700)
    manifest = {"alindi": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"), "tablolar": {}}
    for table in list_tables(conn):
        manifest["tablolar"][table] = export_table(conn, table, target / f"{table}.csv")
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    return manifest


def main(argv: list[str]) -> None:
    load_env()
    target = Path(argv[0]).expanduser() if argv else default_target()
    conn = psycopg.connect(require("DATABASE_URL"), prepare_threshold=None)
    conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ  # tüm tablolar aynı anlık görüntüden
    conn.read_only = True  # SALT OKUNUR
    manifest = run_backup(conn, target)
    total = sum(manifest["tablolar"].values())
    print(f"yedek alındı: {target}  ({len(manifest['tablolar'])} tablo, {total} satır)")
    for table, n in manifest["tablolar"].items():
        print(f"  {table}: {n}")


if __name__ == "__main__":
    main(sys.argv[1:])
