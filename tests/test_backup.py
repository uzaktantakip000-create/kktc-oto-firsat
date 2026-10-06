"""Adım 3a: yerel yedek aracı. Kişisel veri (telefon, ilan metni) içerdiği için git deposuna YAZMAYI reddeder; salt okunur dışa aktarır.
Gözetimsiz kullanım (VPS haftalık zamanlayıcı): --dizin/--sakla, saklama (yalnız aracın kendi klasörleri), başarıda bot_state.backup_last_ok,
başarısızlıkta sıfırdan farklı çıkış + eski yedeklere dokunmama. Sahte bağlantı/depo; gerçek PostgreSQL testleri `db` işaretli (yalnız TEST_DATABASE_URL)."""
import json
import os
import stat
import types
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from entrypoints import backup
from tests.conftest import safe_test_dsn


class FakeCopy:
    def __init__(self, chunks):
        self.chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(self.chunks)


class FakeCursor:
    def __init__(self, data):
        self.data, self.queries = data, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def copy(self, query):
        self.queries.append(query.as_string(None) if hasattr(query, "as_string") else str(query))
        return FakeCopy(self.data)


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0]


class FakeConn:
    def __init__(self):
        self.executed = []
        self.closed = False

    def close(self):
        self.closed = True

    def cursor(self):
        return FakeCursor([b"id,name\n", b"1,a\n", b"2,b\n"])

    def execute(self, query, *a):
        text = query if isinstance(query, str) else repr(query)
        self.executed.append(text)
        if "information_schema" in text:
            return Result([("listings",), ("alerts",)])
        return Result([(2,)])


def test_refuses_to_write_inside_a_git_repository(tmp_path):
    repo = tmp_path / "proje"
    (repo / ".git").mkdir(parents=True)
    assert backup.inside_git_repo(repo / "yedek")  # depo içi: reddedilir
    assert not backup.inside_git_repo(tmp_path / "baska-yer")
    with pytest.raises(SystemExit, match="git deposunun içinde"):
        backup.run_backup(FakeConn(), repo / "yedek")
    assert not (repo / "yedek").exists()  # hiçbir şey yazılmadı


def test_exports_every_table_with_a_manifest_and_private_permissions(tmp_path):
    target = tmp_path / "KKTC-yedek" / "x"
    manifest = backup.run_backup(FakeConn(), target, datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc))
    assert manifest["tablolar"] == {"listings": 2, "alerts": 2}
    assert (target / "listings.csv").read_text() == "id,name\n1,a\n2,b\n"
    assert json.loads((target / "manifest.json").read_text())["tablolar"]["alerts"] == 2
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o700  # yalnız sahibi okuyabilir


def test_default_target_is_outside_the_repo_and_timestamped():
    t = backup.default_target(datetime(2026, 10, 3, 12, 30, 5, tzinfo=timezone.utc))
    assert t.name == "20261003-123005" and t.parent.name == "KKTC-yedek"


# --- gözetimsiz kullanım: izinler, saklama, durum anahtarı, hata yolları ------------------------------------------------------------

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
SECRET_DSN = "postgresql://kullanici:GIZLISIFRE123@127.0.0.1:1/yok"


def make_backup(parent, name, tables=("listings", "alerts"), *, complete=True, mtime=None, extra=None):
    d = parent / name
    d.mkdir(parents=True)
    for t in tables:
        (d / f"{t}.csv").write_text("id\n1\n")
    if complete:
        (d / "manifest.json").write_text(json.dumps({"alindi": "x", "tablolar": {t: 1 for t in tables}}))
    if extra:
        (d / extra).write_text("benim dosyam")
    if mtime is not None:
        os.utime(d, (mtime, mtime))
    return d


def names(parent):
    return sorted(p.name for p in parent.iterdir())


def test_backup_files_are_private_and_new_parent_folder_too(tmp_path):
    target = tmp_path / "yeni-ust" / "20261006-120000"
    backup.run_backup(FakeConn(), target, NOW)
    assert stat.S_IMODE(os.stat(target.parent).st_mode) == 0o700  # bu aracın kurduğu üst klasör
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o700
    files = sorted(target.iterdir())
    assert [f.name for f in files] == ["alerts.csv", "listings.csv", "manifest.json"]
    assert all(stat.S_IMODE(os.stat(f).st_mode) == 0o600 for f in files)  # telefon numarası içerir: yalnız sahibi


def test_existing_parent_folder_permissions_are_left_alone(tmp_path):
    parent = tmp_path / "var"
    parent.mkdir(mode=0o750)
    os.chmod(parent, 0o750)
    backup.run_backup(FakeConn(), parent / "20261006-120000", NOW)
    assert stat.S_IMODE(os.stat(parent).st_mode) == 0o750


def test_backup_name_pattern():
    assert backup.parse_backup_name("20261006-120000") == datetime(2026, 10, 6, 12, 0, 0)
    for bad in ["notlar", "20261006-1200", "20261006-120000.bak", "20261340-120000", "2026-10-06", "x20261006-120000", ""]:
        assert backup.parse_backup_name(bad) is None


def test_retention_keeps_newest_n_and_touches_only_its_own_folders(tmp_path):
    parent = tmp_path / "yedekler"
    for day in range(1, 7):
        make_backup(parent, f"2026100{day}-000000")
    (parent / "notlar").mkdir()
    (parent / "notlar" / "onemli.txt").write_text("silme")
    (parent / "readme.txt").write_text("silme")
    make_backup(parent, "20260930-000000", extra="benim.txt")  # adı kalıba uyar ama içinde başkasının dosyası var
    make_backup(parent, "20260999-000000")  # geçersiz tarih: kalıba uymaz
    outside = make_backup(tmp_path, "disarda")
    (parent / "20260929-000000").symlink_to(outside, target_is_directory=True)  # kalıba uyan ama BAĞLANTI olan ad
    removed, left = backup.apply_retention(parent, 4, now=NOW)
    assert removed == ["20261001-000000", "20261002-000000"]
    assert left == ["20260930-000000"]  # içinde yabancı dosya: silinmedi, bildirildi
    assert names(parent) == ["20260929-000000", "20260930-000000", "20260999-000000", "20261003-000000", "20261004-000000",
                             "20261005-000000", "20261006-000000", "notlar", "readme.txt"]
    assert (parent / "20260930-000000" / "benim.txt").exists() and (parent / "20260930-000000" / "listings.csv").exists()
    assert (parent / "notlar" / "onemli.txt").exists()
    assert names(outside) == ["alerts.csv", "listings.csv", "manifest.json"]  # bağlantının gösterdiği yere dokunulmadı


def test_retention_with_fewer_backups_than_keep_deletes_nothing(tmp_path):
    parent = tmp_path / "y"
    make_backup(parent, "20261001-000000")
    make_backup(parent, "20261002-000000")
    assert backup.apply_retention(parent, 4, now=NOW) == ([], [])
    assert len(names(parent)) == 2


def test_retention_ignores_incomplete_folders_when_counting_and_cleans_only_stale_ones(tmp_path):
    parent = tmp_path / "y"
    for day in (1, 2, 3):
        make_backup(parent, f"2026100{day}-000000")
    make_backup(parent, "20261004-000000", complete=False, mtime=NOW.timestamp() - 600)  # 10 dk önce: çalışan yedek olabilir
    make_backup(parent, "20260920-000000", complete=False, mtime=NOW.timestamp() - 7 * 3600)  # yarım kalmış eski yedek
    removed, left = backup.apply_retention(parent, 3, now=NOW)
    assert removed == ["20260920-000000"] and left == []
    assert names(parent) == ["20261001-000000", "20261002-000000", "20261003-000000", "20261004-000000"]  # tam olanların hepsi kaldı


def test_retention_never_removes_the_backup_just_taken(tmp_path):
    parent = tmp_path / "y"
    make_backup(parent, "20300101-000000")  # saat sapması: "gelecek" adlı klasörler
    make_backup(parent, "20300102-000000")
    make_backup(parent, "20261006-120000")  # az önce alınan (adı en eski sıralanır)
    removed, _ = backup.apply_retention(parent, 2, current="20261006-120000", now=NOW)
    assert removed == []  # en yeni 2 + current korunur
    assert (parent / "20261006-120000").is_dir()


def test_retention_needs_at_least_one(tmp_path):
    with pytest.raises(ValueError):
        backup.apply_retention(tmp_path, 0)


def test_remove_backup_dir_refuses_subfolders_and_unknown_files(tmp_path):
    d = make_backup(tmp_path, "20261001-000000")
    (d / "alt").mkdir()
    assert not backup.remove_backup_dir(d) and (d / "listings.csv").exists()  # alt klasör: hiçbir şey silinmedi
    (d / "alt").rmdir()
    (d / "fotograf.jpg").write_text("x")
    assert not backup.remove_backup_dir(d) and (d / "listings.csv").exists()
    (d / "fotograf.jpg").unlink()
    assert backup.remove_backup_dir(d) and not d.exists()


class FailingConn(FakeConn):
    """İkinci tabloda bağlantı kopar: ilk tablonun CSV'si diskte kalmış olur."""

    def __init__(self, error=None):
        super().__init__()
        self.cursors = 0
        self.error = error or psycopg.OperationalError("bağlantı koptu")

    def cursor(self):
        self.cursors += 1
        if self.cursors == 2:
            raise self.error
        return super().cursor()


def test_failed_run_backup_removes_its_partial_folder_and_leaves_older_backups(tmp_path):
    parent = tmp_path / "y"
    old = make_backup(parent, "20261001-000000")
    target = parent / "20261006-120000"
    with pytest.raises(psycopg.OperationalError):
        backup.run_backup(FailingConn(), target, NOW)
    assert not target.exists()  # yarım klasör (kişisel veri) kalmadı
    assert names(parent) == ["20261001-000000"] and (old / "manifest.json").exists()


def test_existing_target_is_never_deleted_when_it_cannot_be_created(tmp_path):
    target = make_backup(tmp_path, "20261006-120000")
    with pytest.raises(FileExistsError):
        backup.run_backup(FakeConn(), target, NOW)
    assert (target / "listings.csv").exists() and (target / "manifest.json").exists()


@pytest.fixture
def fake_env(monkeypatch):
    monkeypatch.setattr(backup, "load_env", lambda *a, **k: None)  # çalışma klasöründeki .env (gerçek veritabanı) hiç okunmasın
    monkeypatch.setenv("DATABASE_URL", SECRET_DSN)


def patch_db(monkeypatch, conn=None, *, set_error=None, readback=None, snapshot_error=None):
    """Veritabanı olmadan: yedek bağlantısı ve durum deposu sahte. Dönen: yazılan durum sözlüğü, kurulan Repository sayısı, açılan bağlantılar.
    readback verilirse get_state hep onu döner (yeni bağlantıdan doğrulama başarısızlığı için)."""
    state, made, conns = {}, [], []

    def open_snapshot(dsn):
        assert dsn == SECRET_DSN
        if snapshot_error:
            raise snapshot_error
        conns.append(conn or FakeConn())
        return conns[-1]

    class FakeRepo:
        def __init__(self, dsn):
            made.append(dsn)
            self.conn = types.SimpleNamespace(close=lambda: None)

        def set_state(self, key, value):
            if set_error:
                raise set_error
            state[key] = value

        def get_state(self, key, default=None):
            return readback if readback is not None else state.get(key, default)

    monkeypatch.setattr(backup, "open_snapshot", open_snapshot)
    monkeypatch.setattr(backup, "Repository", FakeRepo)
    return state, made, conns


def test_unattended_success_writes_state_key_applies_retention_and_reports_size_and_time(tmp_path, monkeypatch, capsys, fake_env):
    parent = tmp_path / "yedekler"
    for y in (2020, 2021, 2022):
        make_backup(parent, f"{y}0101-000000")
    state, made, conns = patch_db(monkeypatch)
    before = datetime.now(timezone.utc).replace(microsecond=0)
    assert backup.main(["--dizin", str(parent), "--sakla", "2"]) == 0
    assert state.keys() == {"backup_last_ok"}  # tam anahtar adı: sabah durum kodu bunu okur
    stamp = datetime.fromisoformat(state["backup_last_ok"])
    assert stamp.utcoffset() == timedelta(0) and before <= stamp <= datetime.now(timezone.utc)
    kept = names(parent)
    assert len(kept) == 2 and "20220101-000000" in kept  # yeni yedek + en yeni eski; öbürleri silindi
    [new] = [n for n in kept if n != "20220101-000000"]
    assert conns[0].closed
    out = capsys.readouterr()
    assert "yedek alındı" in out.out and "boyut:" in out.out and "süre:" in out.out and " sn" in out.out
    assert "2 eski yedek silindi" in out.out and f"backup_last_ok yazıldı: {state['backup_last_ok']}" in out.out
    assert out.err == ""
    assert stat.S_IMODE(os.stat(parent / new).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(parent / new / "listings.csv").st_mode) == 0o600


def test_unattended_failure_exits_nonzero_keeps_older_backups_and_writes_no_key(tmp_path, monkeypatch, capsys, fake_env):
    parent = tmp_path / "yedekler"
    for y in (2020, 2021, 2022, 2023):
        make_backup(parent, f"{y}0101-000000")
    state, made, _ = patch_db(monkeypatch, FailingConn(psycopg.OperationalError(f"koptu: {SECRET_DSN}")))
    assert backup.main(["--dizin", str(parent), "--sakla", "1"]) == 1
    assert names(parent) == ["20200101-000000", "20210101-000000", "20220101-000000", "20230101-000000"]  # saklama çalışmadı, yarım klasör de yok
    assert state == {} and made == []  # backup_last_ok YAZILMADI (durum deposu hiç kurulmadı)
    err = capsys.readouterr().err
    assert "YEDEK ALINAMADI" in err and "GIZLISIFRE123" not in err  # bağlantı adresindeki parola hata satırına sızmaz


def test_unattended_database_down_exits_nonzero_and_changes_nothing(tmp_path, monkeypatch, capsys, fake_env):
    parent = tmp_path / "yedekler"
    make_backup(parent, "20200101-000000")
    state, made, _ = patch_db(monkeypatch, snapshot_error=psycopg.OperationalError(f"bağlanılamadı {SECRET_DSN}"))
    assert backup.main(["--dizin", str(parent), "--sakla", "1"]) == 1
    assert names(parent) == ["20200101-000000"] and state == {} and made == []
    assert "GIZLISIFRE123" not in capsys.readouterr().err


def test_missing_database_url_exits_nonzero(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(backup, "load_env", lambda *a, **k: None)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert backup.main(["--dizin", str(tmp_path / "y")]) == 1
    assert "DATABASE_URL" in capsys.readouterr().err and not (tmp_path / "y").exists()


def test_state_key_write_failure_is_an_error_but_the_backup_stays(tmp_path, monkeypatch, capsys, fake_env):
    parent = tmp_path / "yedekler"
    for y in (2020, 2021):
        make_backup(parent, f"{y}0101-000000")
    state, _, _ = patch_db(monkeypatch, set_error=psycopg.OperationalError(f"yazılamadı {SECRET_DSN}"))
    assert backup.main(["--dizin", str(parent), "--sakla", "2"]) == 1  # sabah durum kodu yedeği göremeyeceği için başarısız sayılır
    kept = names(parent)
    assert state == {} and len(kept) == 2 and "20210101-000000" in kept  # yeni yedek diskte; saklama yine işledi
    [new] = [n for n in kept if n != "20210101-000000"]
    assert (parent / new / "manifest.json").exists()
    err = capsys.readouterr().err
    assert "backup_last_ok yazılamadı" in err and "GIZLISIFRE123" not in err


def test_state_key_must_read_back_from_a_new_connection(tmp_path, monkeypatch, capsys, fake_env):
    patch_db(monkeypatch, readback="baska-deger")
    assert backup.main(["--dizin", str(tmp_path / "y")]) == 1
    assert "beklenen değer değil" in capsys.readouterr().err


def test_manual_mode_is_unchanged_no_state_write_no_deletion(tmp_path, monkeypatch, capsys, fake_env):
    parent = tmp_path / "KKTC-yedek"
    for y in (2020, 2021, 2022):
        make_backup(parent, f"{y}0101-000000")
    state, made, conns = patch_db(monkeypatch)
    assert backup.main([str(parent / "elle")]) == 0
    assert names(parent) == ["20200101-000000", "20210101-000000", "20220101-000000", "elle"]  # hiçbir eski yedek silinmedi
    assert state == {} and made == []  # elle yedek veritabanına HİÇ yazmaz
    assert (parent / "elle" / "manifest.json").exists() and "boyut:" in capsys.readouterr().out


def test_cli_rejects_bad_option_combinations(tmp_path):
    for argv in (["--sakla", "4"], ["--dizin", str(tmp_path), "--sakla", "0"], ["--dizin", str(tmp_path), "--sakla", "-3"],
                 [str(tmp_path / "x"), "--dizin", str(tmp_path)], ["--bilinmeyen"]):
        with pytest.raises(SystemExit) as e:
            backup.parse_args(argv)
        assert e.value.code == 2, argv
    args = backup.parse_args(["--dizin", "/var/lib/kktc-bot/backups", "--sakla", "4"])
    assert args.dizin == "/var/lib/kktc-bot/backups" and args.sakla == 4 and args.hedef is None
    assert backup.parse_args([]).dizin is None and backup.parse_args(["/x"]).hedef == "/x"


def test_unattended_refuses_a_git_repository_and_writes_nothing(tmp_path, monkeypatch, capsys, fake_env):
    repo = tmp_path / "proje"
    (repo / ".git").mkdir(parents=True)
    state, made, _ = patch_db(monkeypatch)
    assert backup.main(["--dizin", str(repo / "yedekler"), "--sakla", "4"]) == 1
    assert not (repo / "yedekler").exists() and state == {} and made == []
    assert "git deposunun içinde" in capsys.readouterr().err


def test_human_size():
    assert backup.human_size(5_055_434) == "4,8 MB" and backup.human_size(2048) == "2 KB" and backup.human_size(10) == "1 KB"


# --- gerçek PostgreSQL (yalnız TEST_DATABASE_URL; yerel, adında 'test' geçen veritabanı) -----------------------------------------------

@pytest.fixture
def real_env(monkeypatch, db):
    dsn = safe_test_dsn()
    monkeypatch.setattr(backup, "load_env", lambda *a, **k: None)
    monkeypatch.setenv("DATABASE_URL", dsn)
    return dsn


def state_value(dsn):
    with psycopg.connect(dsn, autocommit=True) as c:  # yeni bağlantı
        row = c.execute("SELECT value FROM bot_state WHERE key = 'backup_last_ok'").fetchone()
    return row[0] if row else None


@pytest.mark.db
def test_real_database_unattended_backup_writes_a_verifiable_key(tmp_path, real_env, db, capsys):
    db.conn.execute("INSERT INTO sources (platform, name, url, status) VALUES ('web', 'Deneme', 'https://test.example/yedek', 'aktif')")
    assert state_value(real_env) is None
    n_sources = db.conn.execute("SELECT count(*) AS n FROM sources").fetchone()["n"]  # migration'lar da kaynak ekler
    parent = tmp_path / "yedekler"
    make_backup(parent, "20200101-000000")
    make_backup(parent, "20210101-000000")
    assert backup.main(["--dizin", str(parent), "--sakla", "2"]) == 0
    stamp = datetime.fromisoformat(state_value(real_env))
    assert stamp.utcoffset() == timedelta(0) and abs(datetime.now(timezone.utc) - stamp) < timedelta(minutes=2)
    [new] = [n for n in names(parent) if n > "20250101"]
    manifest = json.loads((parent / new / "manifest.json").read_text())
    assert manifest["tablolar"]["sources"] == n_sources > 1 and "bot_state" in manifest["tablolar"]  # backup_last_ok yedekten SONRA yazılır
    assert "Deneme" in (parent / new / "sources.csv").read_text()
    assert len(names(parent)) == 2 and "20210101-000000" in names(parent)
    assert all(stat.S_IMODE(os.stat(f).st_mode) == 0o600 for f in (parent / new).iterdir())
    assert "boyut:" in capsys.readouterr().out


@pytest.mark.db
def test_real_database_failed_backup_leaves_no_key_and_older_backups(tmp_path, real_env, db, monkeypatch, capsys):
    parent = tmp_path / "yedekler"
    make_backup(parent, "20200101-000000")
    real_export = backup.export_table
    calls = []

    def flaky(conn, table, path):
        calls.append(table)
        if len(calls) == 3:
            raise psycopg.OperationalError("tablo okunamadı")
        return real_export(conn, table, path)

    monkeypatch.setattr(backup, "export_table", flaky)
    assert backup.main(["--dizin", str(parent), "--sakla", "1"]) == 1
    assert names(parent) == ["20200101-000000"]  # yarım yeni klasör silindi, eski yedek duruyor
    assert state_value(real_env) is None
