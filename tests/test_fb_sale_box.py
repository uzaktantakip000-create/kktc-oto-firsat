"""Facebook satış kutusu (devir "ek_metin", sosyal oturum 13.10.2026): satış biçimli gönderinin başlık/fiyat/konum satırları gövdenin dışında
durur; okuyucu artık onları ayrı alanda taşır. Fiyat yalnız kutudan geldiyse ilan "fiyat_alani" alır: en fazla 🟡, emsal olmaz, ⚡ yalnız sahibe.
Uydurma gönderiler; ağ yok."""
from datetime import timedelta

import pytest

from application import social_import as si
from application import urgent
from domain.comparables import Market
from domain.data_gate import DATE_SEEN, DATE_UNSURE, FIELD_PRICE, data_gaps
from domain.freetext_parser import body_price
from domain.settings import Settings
from tests.test_social_import import NOW, FakeRepo, _file, _line, _rows
from tests.test_evaluate import NOW as EVAL_NOW
from tests.test_urgent import Repo as UrgentRepo
from tests.test_urgent import collect
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


def test_urgent_box_price_goes_to_everyone_and_only_to_the_owner_under_the_old_rule(monkeypatch):
    """Sahip kararı 22.10.2026 ("bana gelen tüm mesajlar herkese"): fiyatı yalnız satış kutusundan gelen ⚡ de abonelere gider; eski kural
    (doğrulanana dek yalnız sahibe) notify.OWNER_ONLY_KINDS'a urgent.SELECTIVE_KIND eklenince geri gelir."""
    from application import notify

    boxed = ev("kutu", price=7000)
    boxed.listing["urgency_signals"] = [FIELD_PRICE]
    # "normal" başka yıl: aynı model/yıl ±%5 fiyatlı ikinci ilan aynı-araç kilidine takılırdı (20.10.2026); bu test yalnız paylaşımı sınar
    evs = [boxed, ev("normal", price=6900, year=2016)]
    owner, others = [], []
    assert urgent.run(UrgentRepo(), evs, collect(owner), Settings(), EVAL_NOW, share=collect(others)) == 2
    assert len(owner) == 2 and others == owner
    monkeypatch.setattr(notify, "OWNER_ONLY_KINDS", frozenset({urgent.SELECTIVE_KIND}))
    owner, others = [], []
    assert urgent.run(UrgentRepo(), evs, collect(owner), Settings(), EVAL_NOW, share=collect(others)) == 2
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


# --- Facebook "€" (13.10.2026): satış kutusu Kıbrıs'ta fiyatı € gösterir; satıcının para birimi değildir ------------------------------
@pytest.mark.parametrize("amount,currency,gbp", [(9000, "GBP", 9000), (175000, "TRY", 3500), (49999, "GBP", 49999), (50000, "TRY", 1000)])
def test_facebook_euro_is_read_as_a_guessed_sterling_or_lira(monkeypatch, amount, currency, gbp):
    from application import social_ingest
    monkeypatch.setattr(social_ingest, "gbp_rate", lambda c: {"GBP": 1.0, "TRY": 0.02}[c])
    data = {"price_amount": float(amount), "currency": "EUR", "price_gbp": amount * 0.86, "currency_guess": False, "price_raw": f"€{amount:,}"}
    social_ingest.fb_euro(data)
    assert (data["currency"], data["price_gbp"], data["currency_guess"]) == (currency, gbp, True) and data["price_raw"].startswith("€")


def test_other_currencies_are_untouched():
    from application import social_ingest
    data = {"price_amount": 8500.0, "currency": "GBP", "price_gbp": 8500.0, "currency_guess": False}
    social_ingest.fb_euro(data)
    assert data == {"price_amount": 8500.0, "currency": "GBP", "price_gbp": 8500.0, "currency_guess": False}


def test_euro_from_the_box_is_guessed_after_the_field_check(tmp_path, monkeypatch):
    from application import social_ingest
    monkeypatch.setattr(social_ingest, "gbp_rate", lambda c: {"GBP": 1.0, "TRY": 0.02}[c])
    _, data = _imported(tmp_path, "Temiz araç, ilk sahibinden.", "2018 Honda Civic otomatik\n€8,750\nGirne, Cyprus")
    assert (data["currency"], data["price_gbp"], data["currency_guess"]) == ("GBP", 8750, True)
    assert FIELD_PRICE in data["urgency_signals"]  # kutu karşılaştırması asıl tutar/birimle yapıldı


def test_guessed_currency_gets_no_urgent_message():
    guess = ev("tahmin", price=7000)
    guess.listing["currency_guess"] = True
    assert urgent.candidates([guess], Settings(), EVAL_NOW) == []


# --- paylaşım saati (sosyal oturum 14.10.2026: okuyucu zamanı Facebook'un "9 minutes ago" yazısından okumaya başladı) ----------------------
@pytest.mark.parametrize("posted,dated,want", [
    (NOW - timedelta(hours=1), True, NOW - timedelta(hours=1)),
    (None, False, NOW),  # paylaşım saati yok: görülme anı, "paylaşıldı" denmez
    (NOW + timedelta(minutes=5), True, NOW + timedelta(minutes=5)),  # saat kayması payı
    (NOW + timedelta(hours=3), False, NOW),  # görülmeden 3 saat sonra paylaşılmış olamaz: saat dilimi hatası, yok sayılır
])
def test_post_time_is_kept_only_when_plausible(posted, dated, want):
    _, post = si.parse_line(_line(paylasim_utc=posted.isoformat() if posted else None))
    assert (post.posted_at, post.dated) == (want, dated)


def test_undated_post_is_marked_and_dated_one_is_not(tmp_path):
    repo = FakeRepo(_rows())
    _file(tmp_path, NOW, _line("1000000001", paylasim_utc=None), _line("1000000002"))
    si.import_facebook(repo, tmp_path)
    got = {item: data.get("urgency_signals") or [] for (_, item), data in repo.listings.items()}
    assert DATE_SEEN in got["1000000001"] and DATE_SEEN not in got["1000000002"]


@pytest.mark.parametrize("ago,platform,signals,want", [
    (timedelta(minutes=25), "facebook", [], "🕒 25 dk önce paylaşıldı"),
    (timedelta(hours=5, minutes=10), "facebook", None, "🕒 5 saat önce paylaşıldı"),
    (timedelta(days=3, hours=2), "facebook", [], "🕒 3 gün önce paylaşıldı"),
    (timedelta(minutes=25), "facebook", [DATE_SEEN], "🕒 Paylaşım saati okunamadı · 25 dk önce görüldü"),
    (timedelta(days=2), "web", [DATE_SEEN], "🕒 İlan tarihi: 07.10.2026"),
])
def test_message_says_shared_only_when_the_time_was_read(ago, platform, signals, want):
    from application.notify import posted_age_text
    assert posted_age_text(NOW - ago, platform, now=NOW, signals=signals) == want
