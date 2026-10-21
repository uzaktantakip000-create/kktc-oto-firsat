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


@pytest.fixture(autouse=True)
def _no_real_social_status_file(monkeypatch):
    """Sosyal okuyucunun GERÇEK durum dosyası (VPS'te var) hiçbir testi etkilemesin: yol var olmayan bir yere çevrilir (sosyal testler kendi geçici dosyasını kurar)."""
    from application import selfwatch
    monkeypatch.setattr(selfwatch, "SOCIAL_STATUS_PATH", "/yok/kktc-social-durum/durum.json")


KAA_ENV = ("KAA_IZIN", "BOT_CONTACT_EMAIL", "KAA_USER_AGENT", "KAA_ACCESS_TOKEN", "KAA_TOKEN_HEADER")


@pytest.fixture(autouse=True)
def _kaa_gate_closed(monkeypatch):
    """KibrisArabaAl izin kapısı (application/kaa_gate) her testte KAPALI başlar: geliştiricinin ortamında KAA_IZIN/BOT_CONTACT_EMAIL olsa
    bile hiçbir test siteye istek atamaz. Kapıyı açan testler `kaa_open`'ı ister ve yalnız sahte istemci kullanır."""
    for name in KAA_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def kaa_open(monkeypatch):
    """Kapı açık: izin + VPS + iletişim adresi (uydurma adres; gerçek değil)."""
    monkeypatch.setenv("KAA_IZIN", "1")
    monkeypatch.setenv("KKTC_RUNNER", "vps")
    monkeypatch.setenv("BOT_CONTACT_EMAIL", "bot@example.org")


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
    """Her test için sıfırdan kurulmuş şema (migration 001–020). Repository (autocommit) döner."""
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
