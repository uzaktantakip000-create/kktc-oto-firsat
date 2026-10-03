"""Adım 3a: yerel yedek aracı. Kişisel veri (telefon, ilan metni) içerdiği için git deposuna YAZMAYI reddeder; salt okunur dışa aktarır."""
import json
import os
import stat
from datetime import datetime, timezone

import pytest

from entrypoints import backup


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
