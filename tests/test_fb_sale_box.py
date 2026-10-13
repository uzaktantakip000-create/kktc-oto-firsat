"""Facebook satış kutusu (devir "ek_metin", sosyal oturum 13.10.2026): satış biçimli gönderinin başlık/fiyat/konum satırları gövdenin dışında
durur; okuyucu artık onları ayrı alanda taşır. Fiyat yalnız kutudan geldiyse ilan "fiyat_alani" alır: en fazla 🟡, emsal olmaz, ⚡ yalnız sahibe.
Uydurma gönderiler; ağ yok."""
from datetime import timedelta

import pytest

from application import social_import as si
from application import urgent
from domain.comparables import Market
from domain.data_gate import DATE_UNSURE, FIELD_PRICE, data_gaps
from domain.freetext_parser import body_price
from domain.settings import Settings
from tests.test_social_import import NOW, FakeRepo, _file, _line, _rows
from tests.test_evaluate import NOW as EVAL_NOW
from tests.test_urgent import Repo as UrgentRepo
from tests.test_urgent import ev

BOX = "2018 Honda Civic otomatik\n£8,750\nGirne, Cyprus"


def _imported(tmp_path, text, extra):
    repo = FakeRepo(_rows())
    _file(tmp_path, NOW, _line(text=text, ek_metin=extra))
    rep = si.import_facebook(repo, tmp_path)
    (data,) = repo.listings.values() if repo.listings else (None,)
    return rep, data


def test_price_only_in_the_sale_box_is_read_but_marked(tmp_path):
    rep, data = _imported(tmp_path, "Temiz araç, ilk sahibinden. Bakımları yeni yapıldı.", BOX)
    assert (data["brand"], data["year"], data["price_amount"], data["currency"]) == ("Honda", 2018, 8750, "GBP")
    assert FIELD_PRICE in data["urgency_signals"] and rep.field_price == 1
    assert "fiyatı satış kutusundan" in rep.summary()[0]


def test_same_price_in_the_body_is_a_normal_listing(tmp_path):
    _, data = _imported(tmp_path, "2018 Honda Civic otomatik, fiyat 8.750 STG", BOX)
    assert data["price_amount"] == 8750 and FIELD_PRICE not in (data.get("urgency_signals") or [])


def test_body_price_with_a_hint_wins_over_a_different_box_price(tmp_path):
    """Kutuda yer tutucu (£1 değil, makul ama farklı tutar), gövdede "fiyat" ipuçlu gerçek tutar: gövde kazanır, işaret yok."""
    _, data = _imported(tmp_path, "Honda Civic 2018\nFiyat: 9.500 STG", "2018 Honda Civic\n£7,000")
    assert data["price_amount"] == 9500 and FIELD_PRICE not in (data.get("urgency_signals") or [])


def test_two_unhinted_prices_mean_no_price(tmp_path):
    _, data = _imported(tmp_path, "Honda Civic 2018 9.500 STG", "2018 Honda Civic\n£7,000")
    assert data["price_amount"] is None and "fiyatsiz" in data["urgency_signals"]  # çelişen tutar: fiyat alınmaz (fiyatsız ilan)


@pytest.mark.parametrize("value,want", [
    (None, None), (42, None), ("", None), ("\n \n", None),
    ("ab\ncd", "ab\ncd"),
    ("\n".join(f"satır {i}" for i in range(12)), "\n".join(f"satır {i}" for i in range(8))),  # en çok 8 satır
    ("kısa\n" + "x" * 121, "kısa"),  # sınırdan uzun satır atılır
    ("£8,750\x00gizli\nGirne", "Girne"),  # yazdırılamayan karakterli satır atılır
    ("s\u034f\ne\u034f\nS\u034f\n" * 3 + "2018 Honda Civic\n£8,750", "2018 Honda Civic\n£8,750"),  # harf harf bölünmüş zaman yazısı (13.10)
    ("·\n£\n9\nGirne", "Girne"),  # 2'den az harf/rakam: gürültü
])
def test_extra_text_is_validated(value, want):
    assert si._extra_text(value) == want


def test_line_without_box_is_unchanged():
    _, post = si.parse_line(_line())
    assert post.extra_text is None and "\n" + "None" not in post.text


def test_body_price_follows_the_parser_rule():
    assert body_price("2015 Toyota Corolla\nFiyat 6.500 STG\n0533 123 45 67") == (6500, "GBP")
    assert body_price("Temiz araç") is None and body_price("") is None


def test_box_price_never_gives_green_alone():
    listing = {"model_norm": "civic", "urgency_signals": [FIELD_PRICE], "km": 80000, "year": 2018, "price_gbp": 8750}
    gaps = data_gaps(listing, Market(12, 11000, 9000, 13000, 2, 0.0))
    assert FIELD_PRICE in gaps
    assert FIELD_PRICE not in data_gaps(listing | {"urgency_signals": ["foto_fiyat"]}, Market(12, 11000, 9000, 13000, 2, 0.0))


def test_urgent_box_price_goes_only_to_the_owner():
    owner, others = [], []
    boxed = ev("kutu", price=7000)
    boxed.listing["urgency_signals"] = [FIELD_PRICE]
    assert urgent.run(UrgentRepo(), [boxed, ev("normal", price=6900)], owner.append, Settings(), EVAL_NOW, share=others.append) == 2
    assert len(owner) == 2 and len(others) == 1 and others[0] != next(m for m in owner if "/kutu/" in m)


# --- devir "tazelik" (sosyal oturum 13.10.2026): tarihsiz ve imlece ulaşılmadan okunan gönderi anlık bildirim almaz ---------------------
@pytest.mark.parametrize("value,want", [("kesin", "kesin"), ("imlec", "imlec"), ("belirsiz", "belirsiz"), ("baska", None), (1, None), (None, None)])
def test_freshness_field_is_read_strictly(value, want):
    _, post = si.parse_line(_line(tazelik=value))
    assert post.freshness == want


@pytest.mark.parametrize("text", ["2015 Toyota Corolla\nFiyat 6.500 STG", "2015 Toyota Corolla temiz"])  # fiyatlı ve fiyatsız ilan
def test_unsure_date_marks_the_listing_and_sure_ones_do_not(tmp_path, text):
    repo = FakeRepo(_rows())
    _file(tmp_path, NOW, _line("1000000001", text=text, tazelik="belirsiz"), _line("1000000002", text=text, tazelik="imlec"))
    si.import_facebook(repo, tmp_path)
    got = {item: data.get("urgency_signals") or [] for (_, item), data in repo.listings.items()}
    assert DATE_UNSURE in got["1000000001"] and DATE_UNSURE not in got["1000000002"]


def test_unsure_date_is_never_fresh_for_any_instant_message():
    from application import send_gate
    from application.notify import is_fresh
    seen = EVAL_NOW - timedelta(minutes=5)
    assert is_fresh(seen, None, now=EVAL_NOW, platform="facebook")
    assert not is_fresh(seen, None, now=EVAL_NOW, platform="facebook", signals=["acil", DATE_UNSURE])
    listing = {"first_seen_at": seen, "posted_at": None, "platform": "facebook", "urgency_signals": [DATE_UNSURE]}
    assert not send_gate._fresh(type("E", (), {"listing": listing})())  # 🟢 kapısı
    unsure = ev("eski", price=7000)
    unsure.listing["urgency_signals"] = [DATE_UNSURE]
    assert urgent.candidates([unsure], Settings(), EVAL_NOW) == []  # ⚡
