import json
import stat
from datetime import datetime, timedelta, timezone

import pytest

from application.social_port import SocialPost
from infrastructure.social_files import FileStateStore, JsonlTrialSink, SourcesFileError, delete_old_trial_files, load_sources

NOW = datetime(2026, 10, 5, 9, tzinfo=timezone.utc)
HEADER = "platform,key,url,alias,slug,priority,default_steering,active\n"


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_state_store_roundtrip_atomic_and_private(tmp_path):
    path = tmp_path / "facebook.state.json"
    s = FileStateStore(path)
    assert s.get_state("a") is None and s.get_state("a", "x") == "x"
    s.set_state("social:brake:facebook", '{"signal": "checkpoint"}')
    s.set_state("social:cursor:facebook:1", "{}")
    assert FileStateStore(path).get_state("social:brake:facebook") == '{"signal": "checkpoint"}'  # yeni nesne dosyadan okur
    assert mode(path) == 0o600
    assert [p.name for p in tmp_path.iterdir()] == ["facebook.state.json"]  # geçici dosya kalmaz
    assert s.keys("social:cursor:") == ["social:cursor:facebook:1"]


def test_corrupt_state_file_is_not_silently_reset(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{bozuk")
    with pytest.raises(RuntimeError, match="bozuk"):
        FileStateStore(path).get_state("x")
    with pytest.raises(RuntimeError):
        FileStateStore(path).set_state("x", "1")
    assert path.read_text() == "{bozuk"  # fren kaybolmasın: dosyaya dokunulmaz


def post(pid, text="2013 Toyota Auris 7.250 STG", image=None):
    return SocialPost("facebook", "111", pid, f"https://www.facebook.com/groups/111/posts/{pid}/", NOW, text, image_url=image)


def test_trial_sink_writes_one_line_per_post(tmp_path):
    sink = JsonlTrialSink(tmp_path / "trial", "facebook", lambda: NOW)
    data = {"brand": "Toyota", "model": "Auris", "year": 2013, "km": None, "price_amount": 7250.0, "currency": "GBP",
            "price_gbp": 7250.0, "extraction_by": "parser_serbest", "seller_phone": "905330000000"}
    assert sink.record_post("fb-a", post("1", image="https://cdn.example/a.jpg"), data, None) is True
    assert sink.record_post("fb-a", post("2", "bahçe seti"), None, "arac_degil") is False
    assert sink.record_post("fb-a", post("1"), data, None) is False  # aynı gönderi ikinci kez yazılmaz
    path = tmp_path / "trial" / "facebook-20261005.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 2 and mode(path) == 0o600
    r = rows[0]
    assert set(r) == {"platform", "alias", "post_id", "url", "posted_at", "seen_at", "has_image", "image_url", "outcome", "reason",
                      "brand", "model", "year", "km", "price_amount", "currency", "price_gbp", "extraction_by", "text"}
    assert (r["alias"], r["outcome"], r["reason"], r["has_image"], r["brand"]) == ("fb-a", "ilan", None, True, "Toyota")
    assert r["posted_at"] == NOW.isoformat() and "seller_phone" not in r
    assert (rows[1]["outcome"], rows[1]["reason"], rows[1]["brand"]) == ("ilan_degil", "arac_degil", None)
    again = JsonlTrialSink(tmp_path / "trial", "facebook", lambda: NOW + timedelta(days=1))
    assert again.known_item_ids("111") == {"1", "2"}  # bilinenler önceki dosyalardan
    assert JsonlTrialSink(tmp_path / "trial", "instagram", lambda: NOW).known_item_ids("x") == set()  # platformlar ayrı


def test_trial_sink_upsert_listing_signature(tmp_path):
    sink = JsonlTrialSink(tmp_path, "instagram", lambda: NOW, aliases={"galeri_a": "ig-a"})
    assert sink.upsert_listing("galeri_a", "A1", {"url": "u", "raw_text": "t", "photo_urls": ["https://cdn.example/x.jpg"],
                                                  "brand": "Mazda", "extraction_by": "parser"}) is True
    assert sink.upsert_listing("galeri_a", "A1", {}) is False
    assert sink.upsert_listing("galeri_a", "A2", {"url": "u", "extraction_by": None}) is False  # okunamayan: ilan değil
    rows = [json.loads(x) for x in (tmp_path / "instagram-20261005.jsonl").read_text().splitlines()]
    assert [(r["alias"], r["outcome"], r["image_url"]) for r in rows] == [("ig-a", "ilan", "https://cdn.example/x.jpg"),
                                                                         ("ig-a", "ilan_degil", None)]


def test_half_written_line_does_not_break_known_ids(tmp_path):
    (tmp_path / "facebook-20261004.jsonl").write_text('{"post_id": "9"}\n{"post_id": "1')
    assert JsonlTrialSink(tmp_path, "facebook", lambda: NOW).known_item_ids(None) == {"9"}


def test_delete_old_trial_files_by_name_date(tmp_path):
    for name in ("facebook-20260920.jsonl", "instagram-20260921.jsonl", "compare-facebook-20260901-1030.json",
                 "facebook-20260921.jsonl", "facebook-20261005.jsonl", "notlar.txt"):
        (tmp_path / name).write_text("x")
    assert delete_old_trial_files(tmp_path, 14, NOW) == 2  # 21.09 tam 14 gün: kalır
    assert sorted(p.name for p in tmp_path.iterdir()) == ["facebook-20260921.jsonl", "facebook-20261005.jsonl",
                                                          "instagram-20260921.jsonl", "notlar.txt"]
    assert delete_old_trial_files(tmp_path / "yok", 14, NOW) == 0


def write_csv(tmp_path, body):
    p = tmp_path / "sources.csv"
    p.write_text(HEADER + body)
    return p


def test_load_sources_filters_platform_and_inactive(tmp_path):
    p = write_csv(tmp_path, "# yorum satırı\n"
                            "facebook,111,https://www.facebook.com/groups/111/,fb-a,ornek-a,2,LHD,1\n"
                            "facebook,222,https://www.facebook.com/groups/222/,fb-b,,1,,evet\n"
                            "facebook,333,https://www.facebook.com/groups/333/,fb-c,,3,,0\n"
                            "\n"
                            "instagram,Galeri_X,https://www.instagram.com/galeri_x/,ig-x,,,,1\n")
    fb = load_sources(p, "facebook")
    assert [(s.key, s.alias, s.priority, s.default_steering, s.slug) for s in fb] == [("111", "fb-a", 2, "LHD", "ornek-a"),
                                                                                       ("222", "fb-b", 1, None, None)]
    ig = load_sources(p, "instagram")
    assert [(s.platform, s.key, s.priority) for s in ig] == [("instagram", "galeri_x", 100)]
    assert all(s.source_id is None for s in fb + ig)


def test_load_sources_errors_are_clear_and_do_not_echo_values(tmp_path):
    p = write_csv(tmp_path, "facebook,gizli-grup,https://www.facebook.com/groups/gizli-grup/,fb-a,,1,,1\n"
                            "facebook,444,https://example.com/x,fb-b,,1,,1\n"
                            "facebook,555,https://www.facebook.com/groups/555/,fb-b,,x,SOL,1\n"
                            "facebook,666,https://www.facebook.com/groups/666/,666,,1,,belki\n")
    with pytest.raises(SourcesFileError) as e:
        load_sources(p, "facebook")
    msg = str(e.value)
    assert "satır 2: key" in msg and "satır 3: url" in msg and "satır 4: priority" in msg and "satır 5: active" in msg
    assert "gizli-grup" not in msg and "example.com" not in msg


def test_load_sources_missing_file_columns_duplicates(tmp_path):
    with pytest.raises(SourcesFileError, match="kaynak dosyası yok"):
        load_sources(tmp_path / "yok.csv", "facebook")
    bad = tmp_path / "b.csv"
    bad.write_text("platform,key,url\nfacebook,1,https://www.facebook.com/groups/1/\n")
    with pytest.raises(SourcesFileError, match="sütun eksik: alias"):
        load_sources(bad, "facebook")
    dup = write_csv(tmp_path, "facebook,111,https://www.facebook.com/groups/111/,fb-a,,1,,1\n"
                              "facebook,111,https://www.facebook.com/groups/111/,fb-b,,1,,1\n")
    with pytest.raises(SourcesFileError, match="aynı kimlik"):
        load_sources(dup, "facebook")
    with pytest.raises(SourcesFileError, match="bilinmeyen platform"):
        load_sources(dup, "tiktok")
    alias_is_key = write_csv(tmp_path, "facebook,111,https://www.facebook.com/groups/111/,111,,1,,1\n")
    with pytest.raises(SourcesFileError, match="alias"):
        load_sources(alias_is_key, "facebook")
