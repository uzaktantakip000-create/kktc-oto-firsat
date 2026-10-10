from datetime import datetime, timedelta, timezone

import pytest

from application import notify
from application.evaluate import evaluate_new
from domain.comparables import find_market
from domain.profit import Tier
from domain.red_flags import blocking_flags, customs_stated, plate_flags
from tests.test_comparables import NOW, TARGET, row
from tests.test_evaluate import FakeRepo, POOL, car
from tests.test_notify import ev


# --- emsal kuralları -------------------------------------------------------------------------------------------
def test_archived_listing_not_known_sold_is_not_a_comparable():
    pool = [row(i, 6000 + i * 100) for i in range(3)] + [row(9, 9000, is_active=False, urgency_signals=["arsiv"])]
    assert find_market(TARGET, pool, now=NOW).n == 3 and find_market(TARGET, pool, now=NOW).high_gbp == 6200


def test_sold_archive_listing_is_a_comparable():
    pool = [row(i, 6000 + i * 100) for i in range(2)] + [row(9, 6200, is_active=False, urgency_signals=["satildi"])]
    assert find_market(TARGET, pool, now=NOW).n == 3


def test_active_listing_older_than_60_days_is_dropped_but_sold_one_kept_until_90():
    old = NOW - timedelta(days=70)
    pool = [row(i, 6000 + i * 100, ref_date=old) for i in range(3)]
    assert find_market(TARGET, pool, now=NOW) is None  # 70 gündür satılamamış
    sold = [row(i, 6000 + i * 100, ref_date=old, is_active=False, urgency_signals=["satildi"]) for i in range(3)]
    assert find_market(TARGET, sold, now=NOW).n == 3


def test_single_dealer_is_not_a_market():
    pool = [row(i, 6000 + i * 100, seller_phone="905330000001") for i in range(6)]
    assert find_market(TARGET, pool, now=NOW) is None
    mixed = pool[:2] + [row(10, 6300, seller_phone="905330000002"), row(11, 6400)]
    assert find_market(TARGET, mixed, now=NOW).n == 4


def test_quartile_is_exposed():
    pool = [row(i, p) for i, p in enumerate([6000, 6200, 6400, 6600, 6800])]
    assert find_market(TARGET, pool, now=NOW).p25_gbp == 6200


# --- 🟢 için ucuz çeyrek ve plaka -----------------------------------------------------------------------------
def test_strong_needs_cheapest_quarter():
    (e1,) = evaluate_new(FakeRepo([car("t", 5500)], POOL))
    assert e1.profit.tier is Tier.STRONG  # alt çeyreğin altında
    # emsallerin alt kuyruğu çok ucuz: medyandan %20+ ucuz ama alt çeyreğin üstünde -> 🟡
    pool = [car(f"p{i}", p) for i, p in enumerate([4500, 5000, 5200, 9000, 9100, 9200, 9300, 9400, 9500])]
    repo = FakeRepo([car("t", 6800)], pool)
    (e2,) = evaluate_new(repo)
    assert e2.profit.tier is Tier.NEGOTIABLE and "ucuz_ceyrek_degil" in repo.saved[0][1]["red_flags"]


def test_tr_plate_caps_at_yellow():
    repo = FakeRepo([car("t", 5000, raw_text="TR plaka temiz araç")], POOL)
    (ev_,) = evaluate_new(repo)
    assert ev_.profit.tier is Tier.NEGOTIABLE and "plaka_uyari" in repo.saved[0][1]["red_flags"]


# --- USD/EUR: satıcı STG yerine yanlış para birimi seçmiş olabilir (10.10.2026 yanlış 🟢: 2014 Auris "10.500 USD", aynı araç başka sitede £10.500) ---
USD_RATE, EUR_RATE = 0.7564, 0.8691


def test_usd_price_that_is_a_normal_price_as_gbp_gets_no_alert():
    # POOL medyanı 8.700: 8.500 USD (£6.429) çevrilince 🟢, ama aynı rakam STG olsa sıradan fiyat -> bildirim yok
    as_gbp_control = FakeRepo([car("t", round(8500 * USD_RATE, 2))], POOL)
    assert evaluate_new(as_gbp_control)[0].profit.tier is Tier.STRONG
    repo = FakeRepo([car("t", round(8500 * USD_RATE, 2), currency="USD", price_amount=8500)], POOL)
    evs = evaluate_new(repo)
    assert all(e.profit.tier is Tier.NONE for e in evs)
    assert "para_birimi_supheli" in repo.saved[0][1]["red_flags"] and repo.saved[0][1]["tier"] == "yok"


def test_usd_price_that_is_still_cheap_as_gbp_stays_strong():
    repo = FakeRepo([car("t", round(5500 * EUR_RATE, 2), currency="EUR", price_amount=5500)], POOL)
    (e,) = evaluate_new(repo)
    assert e.profit.tier is Tier.STRONG and "para_birimi_supheli" not in repo.saved[0][1]["red_flags"]


def test_usd_price_only_yellow_as_gbp_caps_at_yellow():
    repo = FakeRepo([car("t", round(6900 * USD_RATE, 2), currency="USD", price_amount=6900)], POOL)
    (e,) = evaluate_new(repo)  # STG okunursa %15 kâr: en fazla 🟡
    assert e.profit.tier is Tier.NEGOTIABLE and "para_birimi_supheli" in repo.saved[0][1]["red_flags"]


def test_gbp_and_try_prices_are_not_touched_by_the_currency_check():
    (e,) = evaluate_new(FakeRepo([car("t", 5000, currency="GBP", price_amount=5000)], POOL))
    assert e.profit.tier is Tier.STRONG
    repo = FakeRepo([car("t", 5000, currency="TRY", price_amount=5000 / 0.0185)], POOL)  # TL'nin kendi kuralı var (tl_fiyat: en fazla 🟡)
    evaluate_new(repo)
    assert "para_birimi_supheli" not in repo.saved[0][1]["red_flags"]


# --- gümrük / evrak filtresi ----------------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["Gümrüksüz araç", "GÜMRÜKSÜZ", "evrakı yok", "evraksız", "evrak eksik", "ICRALIK araç",
                                  "gümrük borcu var", "haciz var", "gümrüğü ödenmedi"])
def test_customs_traps_block(text):
    assert blocking_flags(text) == ["gümrüksüz/evraksız"]


@pytest.mark.parametrize("text", ["Gümrüklü, evrakları tam", "gümrük ödendi", "temiz araç 6.500 STG", "evrak eksiksiz", "2.El (Plakasız) Japonyadan gelme"])
def test_clean_text_not_blocked(text):
    assert blocking_flags(text) == []


def test_customs_stated_and_plate_flags():
    assert customs_stated("Gümrüklü araç") and customs_stated("evrakları tam")
    assert not customs_stated("temiz araç")
    assert plate_flags("TR plakalı") == ["TR/yabancı plaka"] and plate_flags("kktc plaka") == []


def test_blocked_listing_gets_no_tier():
    (e,) = evaluate_new(FakeRepo([car("t", 5000, raw_text="gümrüksüz")], POOL))
    assert e.profit.tier is Tier.NONE


# --- tazelik ve mesaj ------------------------------------------------------------------------------------------
def test_social_post_older_than_48h_gets_no_instant_alert():
    t = datetime.now(timezone.utc)
    assert notify.is_fresh(t, t - timedelta(hours=47), platform="instagram")
    assert not notify.is_fresh(t, t - timedelta(hours=50), platform="facebook")
    assert notify.is_fresh(t, t - timedelta(hours=50), platform=None)  # siteler için bu kural yok


def test_alert_shows_age_and_no_longer_asks_about_customs():
    """Gümrük hatırlatması mesajdan kalktı (sahibin kısa mesaj kararı; gümrüksüz/evraksız ilan zaten engel kelimeyle elenir)."""
    e = ev("1", posted=datetime.now(timezone.utc) - timedelta(hours=5))
    e.listing["platform"] = "instagram"
    text = notify.format_alert(e)
    assert "5 saat önce paylaşıldı" in text and "Gümrük" not in text


def test_suspicious_low_km_is_treated_as_unknown():
    from domain.comparables import effective_km
    assert effective_km({"km": 370, "year": 2016}) is None   # 370 = 370.000 yazılmış olabilir
    from datetime import date
    assert effective_km({"km": 370, "year": 2026}, today=date(2026, 10, 5)) == 370  # yeni araçta makul (saat SABİT: 2028'de 2026 model 2 yaşında olur)
    assert effective_km({"km": 98000, "year": 2012}) == 98000
    from domain.data_gate import data_gaps, km_unknown
    from domain.comparables import Market
    m = Market(8, 10000, 9000, 11000, 1, 0.0, 120000, 9500)
    assert km_unknown({"km": 370, "year": 2016, "model_norm": "x"})
    assert "km_yok" not in data_gaps({"km": 370, "year": 2016, "model_norm": "x"}, m)  # km şüphesi artık engel değil (uyarı)
    assert not km_unknown({"km": 98000, "year": 2012})


def test_credit_takeover_and_promissory_note_phrases_block_but_negations_do_not():
    """Adım 8: kredi devri / senetle satış ilanındaki fiyat araç bedeli değildir (engel); 'senet yok' gibi olumsuzlamalar tetiklemez."""
    from domain.red_flags import blocking_flags
    for text in ("kredi devri ile satılık", "Kredi devir fırsatı", "krediyi devralacak alıcı aranıyor", "senetle satış yapılır",
                 "senet var, peşin değil", "SENETLİ satılık"):
        assert "kredi devri/senet" in blocking_flags(text), text
    for text in ("senet yok, temiz araç", "senet istenmez", "kredisiz temiz araç", "araç krediyle alınmadı", "tek elden, bakımlı"):
        assert "kredi devri/senet" not in blocking_flags(text), text


def test_absurdly_cheap_price_is_flagged_for_every_comparable_count():
    from application.evaluate import evaluate_new
    from tests.test_evaluate import POOL, FakeRepo, car
    repo = FakeRepo([car("t", 3000)], POOL)  # 8 emsal (medyan ~8.700), fiyat medyanın yarısından az
    (ev,) = evaluate_new(repo)
    assert ev.profit.tier.value == "pazarlik" and "fiyat_asiri_dusuk" in repo.saved[0][1]["red_flags"]
