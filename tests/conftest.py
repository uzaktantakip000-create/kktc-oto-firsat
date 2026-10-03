"""Gerçek PostgreSQL gerektiren testler (`@pytest.mark.db`) için ortak düzen.
GÜVENLİK: yalnızca `TEST_DATABASE_URL` okunur; `DATABASE_URL`'e (gerçek veritabanı) ASLA bakılmaz. Adres yerel (localhost/127.0.0.1/::1)
değilse ya da veritabanı adında "test" geçmiyorsa testler ATLANIR (test veritabanının şeması her testte silinip yeniden kurulur)."""
import os
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from infrastructure.db.repository import Repository

MIGRATIONS = Path(__file__).resolve().parent.parent / "infrastructure" / "db" / "migrations"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def pytest_configure(config):
    config.addinivalue_line("markers", "db: gerçek PostgreSQL gerektirir (yalnızca TEST_DATABASE_URL; yerel ve adında 'test' geçen veritabanı)")


def safe_test_dsn(env=None) -> str | None:
    """TEST_DATABASE_URL, yalnızca yerel bir sunucudaki adında 'test' geçen veritabanını gösteriyorsa döner; aksi halde None."""
    dsn = (env if env is not None else os.environ).get("TEST_DATABASE_URL")
    if not dsn:
        return None
    try:
        info = conninfo_to_dict(dsn)
    except Exception:
        return None
    if info.get("host") not in LOCAL_HOSTS or "test" not in (info.get("dbname") or "").lower():
        return None
    return dsn


@pytest.fixture
def db():
    """Her test için sıfırdan kurulmuş şema (migration 001–018). Repository (autocommit) döner."""
    dsn = safe_test_dsn()
    if not dsn:
        pytest.skip("TEST_DATABASE_URL yok ya da yerel bir test veritabanı değil (gerçek veritabanına dokunulmaz)")
    repo = Repository(dsn)
    repo.conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
    repo.conn.execute("CREATE SCHEMA public")
    for migration in sorted(MIGRATIONS.glob("*.sql")):
        repo.conn.execute(migration.read_text())
    yield repo
    repo.conn.close()
