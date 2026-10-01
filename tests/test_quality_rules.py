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


def test_alert_shows_age_and_asks_about_customs():
    e = ev("1", posted=datetime.now(timezone.utc) - timedelta(hours=5))
    e.listing["platform"] = "instagram"
    text = notify.format_alert(e)
    assert "5 saat önce paylaşıldı" in text and "Gümrük/plaka/evrak durumu ilanda yazmıyor" in text
    e.listing["raw_text"] = "Gümrüklü araç"
    assert "Gümrük/plaka/evrak durumu" not in notify.format_alert(e)


def test_suspicious_low_km_is_treated_as_unknown():
    from domain.comparables import effective_km
    assert effective_km({"km": 370, "year": 2016}) is None   # 370 = 370.000 yazılmış olabilir
    assert effective_km({"km": 370, "year": 2026}) == 370     # yeni araçta makul
    assert effective_km({"km": 98000, "year": 2012}) == 98000
    from domain.data_gate import data_gaps
    from domain.comparables import Market
    m = Market(8, 10000, 9000, 11000, 1, 0.0, 120000, 9500)
    assert "km_yok" in data_gaps({"km": 370, "year": 2016, "model_norm": "x"}, m)
