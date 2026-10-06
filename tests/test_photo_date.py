"""KKTCarabam kapak fotoğrafı adresindeki yükleme anı (`domain/photo_date.py`) ve onun tazeliği YALNIZ SIKILAŞTIRAN kullanımı
(`notify.photo_stale` / `notify.is_fresh(photo_urls=...)`). Gerçek veri: tests/fixtures/kktcarabam_list.html (liste kartları)."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from application import notify
from domain.photo_date import kktcarabam_photo_time
from infrastructure.collectors.kktcarabam import parse_list

FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


def photo(when: datetime, host="www.kktcarabam.com", name="img-1-6abe346a6b863-270_200.jpg") -> str:
    """Adresteki yol KKTC saatidir (UTC+3): `when` (UTC) anına denk gelen yolu üretir."""
    local = when + timedelta(hours=3)
    return f"https://{host}/uploads/images/{local:%Y/%m/%d/%H}/{name}"


def test_real_list_cards_carry_the_upload_hour_in_kktc_time_converted_to_utc():
    cards = {c.item_id: c for c in parse_list((FIX / "kktcarabam_list.html").read_text())}
    assert cards["263802"].photo_url.endswith("/uploads/images/2026/10/01/13/4a8babb0-04f4-4317-8524-ecf662e5d701-6abe346a6b863-270_200.jpg")
    assert cards["263802"].photo_at == datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)  # 13:00 KKTC (UTC+3) = 10:00 UTC
    assert cards["259938"].photo_at == datetime(2026, 9, 9, 18, 0, tzinfo=timezone.utc)  # 21:00 KKTC
    assert cards["182968"].photo_at == datetime(2025, 5, 19, 14, 0, tzinfo=timezone.utc)  # 17:00 KKTC
    assert cards and all(c.photo_url and c.photo_at for c in cards.values())  # fixture'daki her kartın tarihi okunur


@pytest.mark.parametrize("src", [None, "", "   ", "https://www.kktcarabam.com/uploads/images/yok.jpg", "data:image/gif;base64,R0lGOD",
                                 "https://cdn.example.com/uploads/images/2026/10/01/13/a.jpg", "http://[bozuk"])
def test_parse_list_card_without_a_usable_photo_still_parses_with_no_date(src):
    img = f'<img src="{src}" alt="">' if src is not None else ""
    html = (f'<a href="https://www.kktcarabam.com/263802-toyota-yaris-lefkosa-benzin-otomatik" class="ilan mb-3"><div class="resim">{img}'
            '<span>Toyota Yaris</span></div><h3>2024 Model Otomatik Toyota Yaris</h3><div class="fiyat">9.999 GBP</div></a>')
    [card] = parse_list(html)
    assert (card.item_id, card.label, card.price_text) == ("263802", "Toyota Yaris", "9.999 GBP")  # kartın geri kalanı etkilenmez
    assert card.photo_at is None


def test_parse_list_relative_photo_address_is_completed_with_the_site_address():
    html = ('<a href="/263802-toyota-yaris-lefkosa-benzin-otomatik" class="ilan mb-3"><div class="resim">'
            '<img src="/uploads/images/2026/10/01/13/a-6abe346a6b863-270_200.jpg"><span>Toyota Yaris</span></div><h3>x</h3></a>')
    [card] = parse_list(html)
    assert card.photo_url == "https://www.kktcarabam.com/uploads/images/2026/10/01/13/a-6abe346a6b863-270_200.jpg"
    assert card.photo_at == datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("url", [
    None, "", 42, ["x"], "bozuk",
    "https://www.kktcarabam.com/uploads/images/2026/13/01/13/a.jpg",   # 13. ay
    "https://www.kktcarabam.com/uploads/images/2026/02/30/13/a.jpg",   # 30 Şubat
    "https://www.kktcarabam.com/uploads/images/2026/10/01/25/a.jpg",   # 25. saat
    "https://www.kktcarabam.com/uploads/images/2014/12/31/12/a.jpg",   # 2015'ten önce
    "https://www.kktcarabam.com/uploads/images/0000/00/00/00/a.jpg",
    "https://www.kktcarabam.com/uploads/images/2026/10/01/a.jpg",      # saat klasörü yok
    "https://www.kktcarabam.com/uploads/images/2026/10/01/13",         # dosya yolu yok
    "https://www.kktcarabam.com/images/2026/10/01/13/a.jpg",           # başka klasör
    "https://evil.com/uploads/images/2026/10/01/13/a.jpg",             # başka site
    "https://kktcarabam.com.evil.com/uploads/images/2026/10/01/13/a.jpg",
    "https://www.instagram.com/p/abc/uploads/images/2026/10/01/13/a.jpg",
    "https://scontent.cdninstagram.com/v/t51.2885-15/123456_n.jpg?stp=dst-jpg",
])
def test_photo_time_is_none_for_missing_or_garbage_addresses(url):
    assert kktcarabam_photo_time(url, now=NOW) is None


def test_photo_time_bounds_2015_and_one_day_into_the_future():
    ok = "https://www.kktcarabam.com/uploads/images/{}/a.jpg"
    assert kktcarabam_photo_time(ok.format("2015/01/01/03"), now=NOW) == datetime(2015, 1, 1, 0, 0, tzinfo=timezone.utc)  # tam sınır
    assert kktcarabam_photo_time(ok.format("2015/01/01/02"), now=NOW) is None  # UTC'ye çevrilince 2015'ten önce
    assert kktcarabam_photo_time(ok.format("2026/10/04/15"), now=NOW) == datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)  # tam bir gün ileri: kabul
    assert kktcarabam_photo_time(ok.format("2026/10/04/16"), now=NOW) is None  # bir günü aştı: bozuk
    assert kktcarabam_photo_time(ok.format("2030/01/01/00"), now=NOW) is None
    assert kktcarabam_photo_time("HTTPS://WWW.KKTCARABAM.COM/uploads/images/2026/10/01/13/A.JPG", now=NOW) == datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)
    assert kktcarabam_photo_time(ok.format("2026/10/01/13") + "?v=2", now=NOW) == datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)
    assert kktcarabam_photo_time(ok.format("2026/10/01/13"), now=NOW).tzinfo is timezone.utc  # saat dilimli (UTC)


def test_hour_is_read_as_a_fixed_utc_plus_3_so_a_late_hour_rolls_into_the_same_utc_day_and_midnight_into_the_previous_one():
    ok = "https://www.kktcarabam.com/uploads/images/2026/10/01/{}/a.jpg"
    assert kktcarabam_photo_time(ok.format("23"), now=NOW) == datetime(2026, 10, 1, 20, 0, tzinfo=timezone.utc)
    assert kktcarabam_photo_time(ok.format("00"), now=NOW) == datetime(2026, 9, 30, 21, 0, tzinfo=timezone.utc)  # bir önceki UTC günü


def test_photo_stale_only_when_the_upload_is_older_than_the_window_before_the_reference():
    ref = NOW
    assert notify.photo_stale([photo(ref - timedelta(hours=40))], ref)
    assert not notify.photo_stale([photo(ref - timedelta(hours=30))], ref)
    assert not notify.photo_stale([photo(ref - timedelta(hours=36))], ref)  # tam pencere: eski sayılmaz (sınır dahil değil)
    assert notify.photo_stale([photo(ref - timedelta(hours=37))], ref)
    assert notify.photo_stale([photo(ref - timedelta(hours=40))], ref, fresh_hours=24)
    for nothing in (None, [], [None], [""], ["bozuk"], [photo(ref - timedelta(days=9), host="evil.com")], (), "x"):
        assert notify.photo_stale(nothing, ref) is False  # ipucu yoksa/okunamıyorsa HİÇBİR ŞEY değişmez
    assert not notify.photo_stale([photo(ref + timedelta(days=3))], ref)  # gelecekteki (bozuk) tarih sıkılaştırmaz


# --- is_fresh: fotoğraf tarihi tazeliği yalnız sıkılaştırır ---
FRESH_SEEN = NOW - timedelta(hours=1)


def test_old_photo_makes_an_otherwise_fresh_listing_not_fresh():
    old = [photo(NOW - timedelta(days=20))]
    assert notify.is_fresh(FRESH_SEEN, None, NOW)  # fotoğraf bilgisi olmadan taze
    assert not notify.is_fresh(FRESH_SEEN, None, NOW, photo_urls=old)
    assert not notify.is_fresh(FRESH_SEEN, NOW - timedelta(days=1), NOW, photo_urls=old)  # tarihi belli ve taze olsa da fotoğraf eskiyse taze değil
    assert notify.is_fresh(FRESH_SEEN, None, NOW, photo_urls=[photo(NOW - timedelta(hours=20))])  # pencere içi fotoğraf: taze


def test_old_photo_is_not_rescued_by_a_recent_price_change_like_the_number_rule():
    changed = NOW - timedelta(hours=2)
    old = [photo(NOW - timedelta(days=20))]
    assert notify.is_fresh(NOW - timedelta(days=3), None, NOW, price_changed_at=changed)  # fotoğrafsız: fiyat değişikliği taze yapar (eskisi gibi)
    assert not notify.is_fresh(NOW - timedelta(days=3), None, NOW, price_changed_at=changed, photo_urls=old)


@pytest.mark.parametrize("photo_urls", [None, [], [""], ["bozuk"], [photo(NOW - timedelta(hours=2))], [photo(NOW - timedelta(days=9), host="evil.com")],
                                        [photo(NOW + timedelta(days=9))]])
def test_missing_garbage_or_recent_photo_never_changes_the_verdict(photo_urls):
    """Fotoğraf tarihi tazeliği ASLA artırmaz: her girdide sonuç, fotoğrafsız sonuçla aynıdır."""
    for first_seen, posted, price_changed in [(FRESH_SEEN, None, None), (NOW - timedelta(hours=40), None, None), (FRESH_SEEN, NOW - timedelta(days=10), None),
                                              (FRESH_SEEN, NOW - timedelta(days=2), None), (NOW - timedelta(days=5), None, NOW - timedelta(hours=3)),
                                              (NOW - timedelta(hours=40), NOW - timedelta(days=9), None)]:
        assert notify.is_fresh(first_seen, posted, NOW, price_changed_at=price_changed, photo_urls=photo_urls) \
            == notify.is_fresh(first_seen, posted, NOW, price_changed_at=price_changed)


def test_recent_photo_does_not_rescue_a_listing_that_is_stale_by_the_existing_rules():
    recent = [photo(NOW - timedelta(hours=1))]
    assert not notify.is_fresh(NOW - timedelta(hours=40), None, NOW, photo_urls=recent)  # ilk görülme 36 saatten eski
    assert not notify.is_fresh(FRESH_SEEN, NOW - timedelta(days=10), NOW, photo_urls=recent)  # ilan tarihi 4 günden eski
    assert not notify.is_fresh(FRESH_SEEN, NOW - timedelta(hours=60), NOW, platform="instagram", photo_urls=recent)  # sosyal medya 48 saat


def test_non_kktcarabam_listing_with_photos_is_unaffected():
    """Başka kaynakların fotoğraf adresleri (Instagram CDN, PazarKibris vb.) eskiyse de tazelik değişmez."""
    cdn = ["https://scontent-ams4-1.cdninstagram.com/v/t51.2885-15/2026/10/01/13/abc_n.jpg?stp=dst-jpg_e35",
           "https://pazarkibris.com/uploads/images/2020/01/01/10/car.jpg"]
    assert notify.is_fresh(FRESH_SEEN, None, NOW, photo_urls=cdn)
    assert notify.is_fresh(FRESH_SEEN, None, NOW, platform="instagram", photo_urls=cdn)
