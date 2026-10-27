"""Botun aktarıcısı (application/social_import): devir dosyası -> ilan satırı. Satırlar elle kurulur; okuyucu tarafı (yazıcı) burada yok.
Sözleşme örneği tests/fixtures/social/devir_ornek.jsonl ortaktır: okuyucunun yazıcı testi de (test_social_handoff, okuyucu dalı) aynı
dosyaya karşı doğrular. Uydurma grup/gönderi kimlikleri."""
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from application import social_import as si

ORNEK = Path(__file__).parent / "fixtures" / "social" / "devir_ornek.jsonl"
FIELDS = {"surum", "platform", "anahtar", "gonderi", "url", "metin", "paylasim_utc", "goruldu_utc", "foto_url",
          "satici_gonderi_14g", "satici_grup_14g"}  # son ikisi isteğe bağlı (satıcı radarı); eski satırlarda yoktur
NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
GROUP = "1234567890"
OTHER = "5550001112"
CAR = "2015 Toyota Corolla\n120.000 km\nFiyat 6.500 STG"


def _line(pid: str = "1000000001", group: str = GROUP, text: str = CAR, **over) -> str:
    doc = {"surum": 1, "platform": "facebook", "anahtar": f"fb:{group}", "gonderi": pid,
           "url": f"https://www.facebook.com/groups/{group}/posts/{pid}/", "metin": text,
           "paylasim_utc": (NOW - timedelta(hours=1)).isoformat(), "goruldu_utc": NOW.isoformat(), "foto_url": None}
    return json.dumps(doc | over, ensure_ascii=False)


def _file(dir: Path, day: datetime, *lines: str) -> Path:
    path = dir / f"facebook-{day:%Y%m%d}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.writelines(line + "\n" for line in lines)
    return path


class FakeRepo:
    def __init__(self, rows):
        self.rows, self.state, self.listings, self.checked = rows, {}, {}, []

    def sources(self, platform, statuses):
        return [r for r in self.rows if r["platform"] == platform and r["status"] in statuses]

    def get_state(self, key, default=None):
        return self.state.get(key, default)

    def set_state(self, key, value):
        self.state[key] = value

    def upsert_listing(self, source_id, item_id, data):
        new = (source_id, item_id) not in self.listings
        self.listings.setdefault((source_id, item_id), data)
        return new

    def known_item_ids(self, source_id):
        return {i for s, i in self.listings if s == source_id}

    def mark_checked(self, source_id, cursor, last_post_at, listings_7d=None):
        self.checked.append((source_id, last_post_at, listings_7d))

    def count_recent(self, source_id):
        return len(self.known_item_ids(source_id))


def _rows(status: str = "aktif"):
    return [{"id": "src-1", "platform": "facebook", "status": status, "url": f"https://www.facebook.com/groups/{GROUP}/", "name": "Grup A"},
            {"id": "src-2", "platform": "facebook", "status": "disari", "url": f"https://www.facebook.com/groups/{OTHER}/", "name": "Grup B"}]


def test_shared_sample_follows_contract():
    lines = ORNEK.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5
    for line in lines:
        doc = json.loads(line)
        assert set(doc) == FIELDS and doc["surum"] == si.VERSION and doc["platform"] == si.PLATFORM
        key, post = si.parse_line(line)
        assert key == doc["anahtar"] and post.post_id == doc["gonderi"] and post.text == doc["metin"]


def test_import_of_shared_sample(tmp_path):
    shutil.copy(ORNEK, tmp_path / f"facebook-{NOW:%Y%m%d}.jsonl")
    repo = FakeRepo(_rows())
    rep = si.import_facebook(repo, tmp_path)
    # 3 satır açık grupta (2 ilan + 1 koltuk), 1 kapalı grupta, 1 bilinmeyen grupta
    assert (rep.lines, rep.posts, rep.new_listings, rep.sources) == (5, 3, 2, 1)
    assert rep.not_listing == {"marka_yok": 1}
    assert rep.skipped == {"kaynak_kapali": 1, "bilinmeyen_anahtar": 1}
    car = repo.listings[("src-1", "1000000001")]
    assert (car["brand"], car["model"], car["year"], car["km"], car["price_amount"], car["currency"]) == \
        ("Toyota", "Corolla", 2015, 120000, 6500, "GBP")
    assert car["url"] == f"https://www.facebook.com/groups/{GROUP}/posts/1000000001/"
    assert car["posted_at"] == NOW - timedelta(hours=1)
    civic = repo.listings[("src-1", "pfbid0OrnekKimlik1234567890")]
    assert (civic["brand"], civic["year"], civic["price_amount"]) == ("Honda", 2012, 4900)
    assert civic["posted_at"] == NOW  # paylaşım zamanı yoktu: ilk görülme
    assert repo.checked == [("src-1", NOW, 2)]
    assert json.loads(repo.state[si.STATE_KEY]) == {"dosya": f"facebook-{NOW:%Y%m%d}.jsonl", "satir": 5}


def test_partial_last_line_waits_for_next_turn(tmp_path):
    path = _file(tmp_path, NOW, _line("1000000001"))
    with open(path, "ab") as f:
        f.write(b'{"surum": 1, "platform": "faceb')  # okuyucu yazmayı sürdürüyor
    lines, cur, pending = si.read_new_lines(tmp_path, ("", 0))
    assert (len(lines), cur, pending) == (1, (path.name, 1), False)
    with open(path, "ab") as f:
        f.write(b'ook"}\n')
    lines, cur, _ = si.read_new_lines(tmp_path, cur)
    assert len(lines) == 1 and cur == (path.name, 2)


def test_cursor_moves_across_days_and_respects_limit(tmp_path):
    day1 = _file(tmp_path, NOW, _line("1000000001"), _line("1000000002"))
    later = NOW + timedelta(days=1)
    _file(tmp_path, later, _line("1000000003"))
    lines, cur, pending = si.read_new_lines(tmp_path, ("", 0), limit=2)
    assert (len(lines), cur, pending) == (2, (day1.name, 2), True)
    lines, cur, pending = si.read_new_lines(tmp_path, cur, limit=2)
    assert (len(lines), cur, pending) == (1, (f"facebook-{later:%Y%m%d}.jsonl", 1), False)


def test_import_limit_leaves_rest_for_next_turn(tmp_path):
    _file(tmp_path, NOW, *(_line(f"100000000{i}") for i in range(1, 6)))
    repo = FakeRepo(_rows())
    rep = si.import_facebook(repo, tmp_path, limit=2)
    assert (rep.lines, rep.pending) == (2, True) and "kalan var" in rep.summary()[0]
    si.import_facebook(repo, tmp_path, limit=2)
    rep = si.import_facebook(repo, tmp_path, limit=2)
    assert (rep.lines, rep.pending) == (1, False) and len(repo.listings) == 5


@pytest.mark.parametrize("line,why", [
    ("{yarım", "bozuk_satir"),
    ("[1, 2]", "bozuk_satir"),
    (json.dumps({"surum": 2}), "surum"),
    (json.dumps({"surum": 1, "platform": "instagram"}), "platform"),
    (_line(group="../x"), "alan"),
    (_line(pid="abc"), "alan"),
    (_line(text=None), "alan"),
])
def test_bad_lines_are_skipped_with_reason(line, why):
    assert si.parse_line(line) == why


def test_link_is_rebuilt_and_only_cdn_photo_kept():
    key, post = si.parse_line(_line(url="https://evil.example/x", foto_url="https://evil.example/a.jpg"))
    assert key == f"fb:{GROUP}"
    assert post.url == f"https://www.facebook.com/groups/{GROUP}/posts/1000000001/"
    assert post.image_url is None
    _, post = si.parse_line(_line(foto_url="https://scontent.fxyz1-1.fna.fbcdn.net/v/a.jpg"))
    assert post.image_url == "https://scontent.fxyz1-1.fna.fbcdn.net/v/a.jpg"


def test_missing_post_time_falls_back_to_first_seen():
    seen = NOW - timedelta(days=10)  # 14 günlük birikim ilk aktarımda "şimdi" sayılmamalı
    assert si.parse_line(_line(paylasim_utc=None, goruldu_utc=seen.isoformat()))[1].posted_at == seen
    assert si.parse_line(_line(paylasim_utc="dün", goruldu_utc=seen.isoformat()))[1].posted_at == seen
    assert si.parse_line(_line(paylasim_utc="2026-10-09T11:00:00", goruldu_utc=seen.isoformat()))[1].posted_at == seen  # saat dilimsiz
    assert si.parse_line(_line())[1].posted_at == NOW - timedelta(hours=1)
    assert si.parse_line(_line(paylasim_utc=None, goruldu_utc=None))[1].posted_at is None


def test_deneme_source_is_open_and_closed_duplicate_loses(tmp_path):
    _file(tmp_path, NOW, _line("1000000001"))
    rows = [{"id": "src-old", "platform": "facebook", "status": "pasif", "url": f"https://www.facebook.com/groups/{GROUP}/", "name": "Eski"},
            {"id": "src-1", "platform": "facebook", "status": "deneme", "url": f"https://www.facebook.com/groups/{GROUP}", "name": "Grup A"}]
    repo = FakeRepo(rows)
    assert si.import_facebook(repo, tmp_path).new_listings == 1
    assert list(repo.listings) == [("src-1", "1000000001")]


def test_second_run_reads_nothing_and_replay_is_harmless(tmp_path):
    _file(tmp_path, NOW, _line("1000000001"))
    repo = FakeRepo(_rows())
    si.import_facebook(repo, tmp_path)
    assert si.import_facebook(repo, tmp_path).lines == 0
    repo.state.clear()  # imleç kaybolsa bile aynı satırlar yeni ilan üretmez
    assert si.import_facebook(repo, tmp_path).new_listings == 0


def test_missing_dir_is_quiet(tmp_path):
    rep = si.import_facebook(FakeRepo(_rows()), tmp_path / "yok")
    assert rep.lines == 0 and rep.summary() == ["sosyal devir: yeni satır yok"]


def test_seller_radar_becomes_seller_type_and_bad_values_are_ignored(tmp_path):
    """Satıcı radarı (sosyal oturum, 10.10.2026): telefonun 14 günde geçtiği FARKLI gönderi sayısı ≥3 → galeri, 1-2 → bireysel; yoksa dokunulmaz."""
    assert si.parse_line(_line(satici_gonderi_14g=3, satici_grup_14g=2))[1].seller_posts_14d == 3
    for bad in ("3", -1, 0, True, 3.5, None, 10**9):
        assert si.parse_line(_line(satici_gonderi_14g=bad))[1].seller_posts_14d is None, bad
    assert si.parse_line(_line())[1].seller_posts_14d is None  # eski satır: alan yok
    _file(tmp_path, NOW, _line("1000000011", satici_gonderi_14g=5), _line("1000000012", satici_gonderi_14g=1), _line("1000000013"))
    repo = FakeRepo(_rows())
    si.import_facebook(repo, tmp_path)
    kinds = {pid: repo.listings[("src-1", pid)].get("seller_type") for pid in ("1000000011", "1000000012", "1000000013")}
    assert kinds == {"1000000011": "galeri", "1000000012": "bireysel", "1000000013": None}


def test_page_lines_are_read_with_a_link_built_from_the_page_key():
    """Facebook sayfası (22.10.2026): "fbp:<sayfa>" satırı okunur; bağlantı anahtar + kimlikten kurulur (dosyadaki adres yalnız tam reel
    biçimindeyse kullanılır), kaynak eşlemesi kayıtlı sayfa adresinden."""
    doc = json.loads(_line(pid="2000000001")) | {"anahtar": "fbp:kibrisaraba", "url": "https://evil.example/x"}
    key, post = si.parse_line(json.dumps(doc))
    assert key == "fbp:kibrisaraba" and post.source_key == "kibrisaraba"
    assert post.url == "https://www.facebook.com/kibrisaraba/posts/2000000001/"
    for raw in ("https://www.facebook.com/reel/2000000001/", "https://www.facebook.com/kibrisaraba/reel/2000000001/"):
        assert si.parse_line(json.dumps(doc | {"url": raw}))[1].url == "https://www.facebook.com/reel/2000000001/"
    assert si.parse_line(json.dumps(doc | {"anahtar": "fbp:Kibris-Araba"})) == "alan"
    rows = [{"id": "p1", "platform": "facebook", "url": "https://www.facebook.com/KIBRISARABA/", "status": "aktif"}]
    assert set(si.source_map(rows)) == {"fbp:kibrisaraba"}


def test_sharer_name_of_a_shared_photo_post_never_enters_the_listing_text():
    """27.10.2026 (gizlilik): "+16 Photos from <ad>'s post" başlığı içe aktarmada silinir; ilanın kendi metni kalır."""
    from application.social_import import strip_sharer
    text = "2020 Mercedes GLC220d 69.000 KM'de +16 Photos from Örnek Kişi's post\nabc123"
    assert "Örnek Kişi" not in strip_sharer(text) and "GLC220d 69.000 KM" in strip_sharer(text)
    assert strip_sharer("Photos from Ali Veli's post") == ""
    assert strip_sharer("+3 Photos from Ali Veli’s post") == ""  # kıvrık kesme işareti
    assert strip_sharer("2015 Honda Fit 7.500 STG") == "2015 Honda Fit 7.500 STG"


def test_sharer_name_in_the_extra_text_never_reaches_the_extra_text_or_raw_text():
    """Paylaşan satırı "ek_metin"de de gelir (27.10.2026): ad ne ek metne ne de değerlendirilen raw_text'e girer; kutunun öbür satırları kalır."""
    from application.social_ingest import raw_group_post
    from application.social_port import SocialSource
    for sharer in ("Photos from Örnek Kişi's post", "Photos from Örnek Kişi’s post"):
        doc = json.loads(_line("1000000021")) | {"ek_metin": f"+10\n{sharer}\nabc\n£4.900"}
        key, post = si.parse_line(json.dumps(doc))
        assert "Örnek" not in post.extra_text and "Kişi" not in post.extra_text and post.extra_text == "abc\n£4.900"
        source = SocialSource("facebook", "123456789", "https://www.facebook.com/groups/123456789", "fb-01")
        raw = raw_group_post(post, source)
        assert "Örnek" not in raw.text and "Kişi" not in raw.text and "abc" in raw.text
    assert si.parse_line(json.dumps(json.loads(_line("1000000022")) | {"ek_metin": 5}))[1].extra_text is None  # dize değil: eskisi gibi


def test_sale_box_price_is_counted_per_kktc_day(tmp_path):
    """27.10.2026: satış kutusu fiyatı 24.10'dan beri gelmiyordu, bot fark etmedi. Okunan gönderi satırı ve "ek_metin"i fiyatlı olan, aktarım
    anının KKTC gününe sayılır (bilinmeyen gruptaki satır da: sayaç okuyucunun çıktısını ölçer); bozuk satır sayılmaz."""
    _file(tmp_path, NOW, _line("1000000001", ek_metin="2018 Honda Civic\n€6,500\nGirne"), _line("1000000002", ek_metin="Girne, Cyprus"),
          _line("1000000003"), _line("1000000004", group=OTHER, ek_metin="TRY650,000"), "{bozuk")
    repo = FakeRepo(_rows())
    si.import_facebook(repo, tmp_path, now=NOW)
    assert json.loads(repo.state[si.BOX_KEY + "2026-10-09"]) == {"lines": 4, "priced": 2}
    _file(tmp_path, NOW, _line("1000000005", ek_metin="£4.900"))
    si.import_facebook(repo, tmp_path, now=NOW + timedelta(hours=2))
    assert json.loads(repo.state[si.BOX_KEY + "2026-10-09"]) == {"lines": 5, "priced": 3}
    _file(tmp_path, NOW, _line("1000000006", ek_metin="£4.900"))
    si.import_facebook(repo, tmp_path, now=datetime(2026, 10, 9, 22, 30, tzinfo=timezone.utc))  # KKTC 10.10 01:30
    assert json.loads(repo.state[si.BOX_KEY + "2026-10-10"]) == {"lines": 1, "priced": 1}
    assert not si.box_has_price("2018 Honda Civic otomatik\n+6\nGirne") and not si.box_has_price(None)


def test_group_sale_listing_keeps_facebooks_own_listing_link():
    """27.10.2026: Facebook satış ilanlarını "Buy and sell" sekmesine taşıdı; okuyucu kimliği ve bağlantıyı /commerce/listing/<kimlik>/ yazar.
    Bağlantı yalnız kimlikle birebir aynıysa kullanılır; başka adres gelirse grup gönderisi bağlantısı kurulur (eskisi gibi)."""
    from application.social_import import parse_line
    base = {"surum": 1, "platform": "facebook", "anahtar": "fb:123456789", "gonderi": "28834308402858918",
            "metin": "2015 Honda Fit 7.500 STG", "paylasim_utc": None, "goruldu_utc": "2026-10-27T12:00:00+00:00"}
    _, post = parse_line(json.dumps({**base, "url": "https://www.facebook.com/commerce/listing/28834308402858918/"}))
    assert post.url == "https://www.facebook.com/commerce/listing/28834308402858918/"
    _, other = parse_line(json.dumps({**base, "url": "https://evil.example/commerce/listing/28834308402858918/"}))
    assert other.url == "https://www.facebook.com/groups/123456789/posts/28834308402858918/"
