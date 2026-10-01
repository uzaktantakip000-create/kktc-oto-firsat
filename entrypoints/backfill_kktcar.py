"""Tek seferlik: kktcar.com geçmiş ilanlarını yerelden doldurur. python -m entrypoints.backfill_kktcar [adet]"""
import sys

from application.collect_kktcar import collect_kktcar
from infrastructure.config import load_env, require
from infrastructure.db.repository import Repository

if __name__ == "__main__":
    load_env()
    repo = Repository(require("DATABASE_URL"))
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 500
    source = repo.conn.execute("SELECT * FROM sources WHERE url LIKE '%kktcar.com%'").fetchone()
    print(collect_kktcar(repo, source, max_new=n))
