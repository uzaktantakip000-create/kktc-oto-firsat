"""Kesik Facebook metni (devir "metin_kesik", sosyal oturum 2b13520, 31.10.2026): "See more" açılmayan gönderide metin sözcük/sayı ortasında
bitebilir; okuyucu sondaki "…"yı siler. Metnin son kısmına bağlı fiyat kullanılmaz (ilan fiyatsız kalır); erken yazılmış fiyat ve kesik
olmayan metin eskisi gibi. Uydurma gönderiler; ağ yok."""
import pytest

from application import social_import as si
from domain.freetext_parser import CUT_TAIL, price_in_cut_tail
from tests.test_social_import import NOW, FakeRepo, _file, _line, _rows

EARLY = "2015 Toyota Corolla fiyat 6.500 STG\nAraç çok temiz, bakımları yeni yapıldı, lastikler yeni, muayenesi var, takas"
LATE = "2015 Toyota Corolla 120.000 km, araç çok temiz, bakımları yeni yapıldı\nFiyat 12.500 STG"


def _imported(tmp_path, text, **over):
    repo = FakeRepo(_rows())
    _file(tmp_path, NOW, _line(text=text, **over))
    rep = si.import_facebook(repo, tmp_path)
    (data,) = repo.listings.values() if repo.listings else (None,)
    return rep, data


@pytest.mark.parametrize("text, want", [
    (EARLY, False),  # fiyat başta: son 30 karakter atılsa da aynı
    (LATE, True),  # fiyat en sonda: "12.500" + kesik birim/"bin" olabilir
    ("2015 Toyota Corolla temiz", False),  # fiyat yok
    ("Fiyat 6.500 STG", True),  # kısa metin: hepsi son kısım
])
def test_price_in_cut_tail(text, want):
    assert price_in_cut_tail(text) is want


def test_tail_is_measured_after_trailing_space():
    text = "2015 Toyota Corolla temiz araç bakımlı muayeneli lastikleri yeni fiyat 6.500 STG"
    assert price_in_cut_tail(text + "   \n") is True
    assert price_in_cut_tail(text + " " + "x" * CUT_TAIL) is False  # fiyattan sonra 30+ karakter: fiyat kesilen yere bağlı değil


def test_cut_text_with_price_at_the_end_is_saved_without_price(tmp_path):
    rep, data = _imported(tmp_path, LATE, metin_kesik=True)
    assert (data["brand"], data["year"], data["km"]) == ("Toyota", 2015, 120_000)
    assert data["price_amount"] is None and data["price_gbp"] is None and "fiyatsiz" in data["urgency_signals"]
    assert rep.priceless == 1


def test_cut_text_with_early_price_keeps_the_price(tmp_path):
    _, data = _imported(tmp_path, EARLY, metin_kesik=True)
    assert data["price_amount"] == 6500 and "fiyatsiz" not in (data.get("urgency_signals") or [])


@pytest.mark.parametrize("over", [{}, {"metin_kesik": False}, {"metin_kesik": "true"}])
def test_text_not_marked_cut_is_unchanged(tmp_path, over):
    _, data = _imported(tmp_path, LATE, **over)  # alan yok/yanlış/dize: eski satır gibi (yalnız JSON true kesik sayılır)
    assert data["price_amount"] == 12_500


def test_cut_text_price_from_the_sale_box_is_kept(tmp_path):
    _, data = _imported(tmp_path, "2015 Toyota Corolla 120.000 km, araç çok temiz, bakımları yeni", metin_kesik=True,
                        ek_metin="£12,500\nGirne, Cyprus")
    assert data["price_amount"] == 12_500  # kutu satırları kesilmez; fiyat gövdeden gelmiyor


def test_parse_line_reads_the_flag():
    _, post = si.parse_line(_line(metin_kesik=True))
    assert post.truncated is True
    _, post = si.parse_line(_line())
    assert post.truncated is False
