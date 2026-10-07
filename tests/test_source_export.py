"""Sosyal kaynak listesinin VPS'teki sosyal okuyucuya dosyayla aktarımı (07.10.2026; application/source_export)."""
import json
import os
import stat

from application import source_export
from entrypoints import tick


def src(id, platform, name, url, status="aktif", priority=10):
    return {"id": id, "platform": platform, "name": name, "url": url, "status": status, "priority": priority}


def test_export_build_keeps_closed_rows_and_skips_bad_ones():
    rows = [src("i1", "instagram", "kibris.car", "https://www.instagram.com/kibris.car/", priority=11),
            src("i2", "instagram", "eski", "https://www.instagram.com/eski.hesap/", status="pasif", priority=12),
            src("i3", "instagram", "kopya", "https://instagram.com/KIBRIS.CAR", status="pasif", priority=13),
            src("f1", "facebook", "Pazar", "https://www.facebook.com/groups/469402498541872/", status="deneme", priority=None),
            src("f2", "facebook", "Market", "https://www.facebook.com/marketplace/1/cars/"),
            src("w1", "web", "KKTCar", "https://kktcar.com/")]
    items = source_export.build(rows)
    assert [(i["anahtar"], i["durum"]) for i in items] == [("fb:469402498541872", "aktif"), ("ig:kibris.car", "aktif"), ("ig:eski.hesap", "pasif")]
    assert items[0] == {"anahtar": "fb:469402498541872", "platform": "facebook", "grup": "469402498541872",
                        "url": "https://www.facebook.com/groups/469402498541872/", "ad": "Pazar", "oncelik": 99, "durum": "aktif"}
    assert items[1]["kullanici"] == "kibris.car" and items[1]["oncelik"] == 11


class ExportRepo:
    def __init__(self, rows):
        self.rows, self.conn = rows, self

    def execute(self, sql, params=()):
        return self

    def fetchall(self):
        return self.rows


def test_export_writes_atomically_once_and_only_where_the_folder_exists(tmp_path):
    repo = ExportRepo([src("i1", "instagram", "kibris.car", "https://www.instagram.com/kibris.car/")])
    assert source_export.export_social_sources(repo, str(tmp_path / "yok" / "kaynaklar.json")) is False  # VPS değil: dokunma
    path = tmp_path / "kaynaklar.json"
    assert source_export.export_social_sources(repo, str(path)) is True
    doc = json.loads(path.read_text())
    assert doc["surum"] == 1 and [i["anahtar"] for i in doc["kaynaklar"]] == ["ig:kibris.car"]
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o644 and not [p for p in os.listdir(tmp_path) if p.endswith(".tmp")]
    assert source_export.export_social_sources(repo, str(path)) is False  # içerik aynı: yeniden yazılmaz
    repo.rows[0]["status"] = "pasif"
    assert source_export.export_social_sources(repo, str(path)) is True and json.loads(path.read_text())["kaynaklar"][0]["durum"] == "pasif"


def test_tick_export_failure_does_not_break_the_round(monkeypatch, capsys):
    def boom(repo):
        raise RuntimeError("db down")
    monkeypatch.setattr(tick.source_export, "export_social_sources", boom)
    tick.export_sources(object())
    assert "yazılamadı" in capsys.readouterr().out


def test_tick_unit_owns_the_shared_folder():
    unit = open("deploy/bot/kktc-tick.service", encoding="utf-8").read()
    assert "StateDirectory=kktc-kaynaklar" in unit and "StateDirectoryMode=0755" in unit
    assert source_export.EXPORT_PATH == "/var/lib/kktc-kaynaklar/kaynaklar.json"
