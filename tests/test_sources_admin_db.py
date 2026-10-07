"""Kaynak yönetiminin SQL'leri gerçek PostgreSQL ile (CI'de `db-test`; yerelde TEST_DATABASE_URL yoksa atlanır). Mantık: tests/test_sources_admin.py."""
import json

import pytest

from application import source_export, sources_cmd

pytestmark = pytest.mark.db


def add(conn, platform, name, url, status="aktif", priority=10):
    return conn.execute("INSERT INTO sources (platform, name, url, status, priority) VALUES (%s,%s,%s,%s,%s) RETURNING id::text AS id",
                        (platform, name, url, status, priority)).fetchone()["id"]


def test_list_toggle_add_and_export_against_the_real_schema(db, tmp_path):
    c = db.conn
    c.execute("DELETE FROM sources")  # şema tohumundaki kaynaklar yerine bilinen küçük bir liste
    kktcar = add(c, "web", "KKTCar", "https://kktcar.com/en/search/results")
    add(c, "web", "KibrisArabaAl", "https://kibrisarabaal.com/")
    add(c, "instagram", "kibris.car", "https://www.instagram.com/kibris.car/", priority=11)
    add(c, "facebook", "Pazar", "https://www.facebook.com/groups/469402498541872/", status="deneme", priority=None)
    lid = c.execute("INSERT INTO listings (source_id, source_item_id, is_active) VALUES (%s,'a',TRUE) RETURNING id", (kktcar,)).fetchone()["id"]
    c.execute("INSERT INTO alerts (listing_id, tier, chat_id) VALUES (%s,'guclu','1')", (lid,))

    rows = sources_cmd._rows(db, ("web", "instagram", "facebook"))
    by = {r["name"]: r for r in rows}
    assert by["KKTCar"]["strong_30d"] == 1 and isinstance(by["KKTCar"]["id"], str) and by["Pazar"]["priority"] is None

    assert sources_cmd.toggle(db, kktcar, False)[0]
    assert c.execute("SELECT status FROM sources WHERE id::text=%s", (kktcar,)).fetchone()["status"] == "pasif"
    assert "eklendi" in sources_cmd.add(db, "ig:yeni.galeri")
    new = c.execute("SELECT platform, name, url, kind, status, priority, discovered_by FROM sources WHERE name='yeni.galeri'").fetchone()
    assert new == {"platform": "instagram", "name": "yeni.galeri", "url": "https://www.instagram.com/yeni.galeri/", "kind": "ilan_sayfasi",
                   "status": "aktif", "priority": 12, "discovered_by": "sahip"}
    assert "istek olarak kaydedildi" in sources_cmd.add(db, "web:biarabacik.com")
    assert c.execute("SELECT status FROM sources WHERE name='biarabacik.com'").fetchone()["status"] == "aday"
    assert json.loads(db.get_state("src:opened:instagram"))  # günlük ray kaydı bot_state'e yazıldı
    sources_cmd._insert(db, sources_cmd.parse_source_link("https://www.instagram.com/yeni.galeri/"), "pasif")  # aynı adres: çift satır yok
    assert c.execute("SELECT count(*) AS n, min(status) AS s FROM sources WHERE url='https://www.instagram.com/yeni.galeri/'").fetchone() == {"n": 1, "s": "pasif"}
    sources_cmd._set_status(db, new_id := c.execute("SELECT id::text AS id FROM sources WHERE name='yeni.galeri'").fetchone()["id"], "aktif")
    assert sources_cmd._row(db, new_id)["status"] == "aktif"

    path = tmp_path / "kaynaklar.json"
    assert source_export.export_social_sources(db, str(path))
    doc = json.loads(path.read_text())
    assert [(i["anahtar"], i["durum"]) for i in doc["kaynaklar"]] == [
        ("fb:469402498541872", "aktif"), ("ig:kibris.car", "aktif"), ("ig:yeni.galeri", "aktif")]
