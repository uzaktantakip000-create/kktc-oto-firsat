"""Taze TL fiyatı (20.10.2026; domain/data_gate.tl_fresh, domain/decision._fresh_tl_strong, Settings.tl_fresh_days/tl_fresh_factor).
Sahip: KibrisArabaAl 2011 March 185.000 TL "gerçekten güzel bir fırsat" ama `tl_fiyat` (TL ilan en fazla 🟡) yüzünden 🟡 kaldı. Ölçüm: taze TL
ilan £ piyasasının ~0,91'inde (eski/tarihsiz TL ~0,80). Kural: yayın tarihi güvenilir ve ≤4 günlük TL ilan, £ ilan gibi kurulan piyasanın
0,89 katına göre BÜTÜN 🟢 kapılarından geçerse 🟢; geçmezse ve öbür TL ilanlarda eskisi gibi en fazla 🟡."""
from datetime import timedelta

from application.decision_record import decision_record
from application.evaluate import evaluate_new
from domain.data_gate import DATE_SEEN, DATE_UNSURE, GAP_LABELS, tl_fresh
from domain.decision import decide
from domain.profit import Tier
from domain.settings import Settings
from tests.test_evaluate import NOW, POOL, FakeRepo, car

S = Settings()


_ROUND = object()


def tl(price, days=1.0, amount=_ROUND, **kw):
    """Taze TL ilan: £ fiyatı `price`, TL tutarı yuvarlak (kur ~65; `amount` verilirse o), yayın `days` gün önce."""
    base = {"currency": "TRY", "price_amount": round(price * 65 / 1000) * 1000 if amount is _ROUND else amount,
            "posted_at": NOW - timedelta(days=days)}
    return car("t", price, **(base | kw))


# --- tazelik ---
def test_fresh_needs_try_reliable_recent_date_and_human_amount():
    assert tl_fresh(tl(5000), S, NOW)
    assert tl_fresh(tl(5000, days=3.9), S, NOW)
    assert not tl_fresh(tl(5000, days=4.1), S, NOW)  # eski: fiyat bugünkü kurla konmamış olabilir
    assert not tl_fresh(car("t", 5000, currency="TRY", price_amount=325_000, posted_at=None), S, NOW)  # tarih yok
    assert not tl_fresh(tl(5000, currency_guess=True), S, NOW)  # para birimi tahmin (Facebook ≥60.000 → TL)
    assert not tl_fresh(tl(5000, currency="GBP"), S, NOW)
    assert not tl_fresh(tl(5000, urgency_signals=[DATE_SEEN]), S, NOW)  # paylaşım saati okunamadı: posted_at = ilk görülme
    assert not tl_fresh(tl(5000, urgency_signals=[DATE_UNSURE]), S, NOW)
    assert not tl_fresh(tl(5000), S, None)  # karar anı yok: eski kural
    assert not tl_fresh(tl(5000), Settings(tl_fresh_days=0), NOW)  # kural kapalı


def test_site_converted_or_placeholder_amounts_are_not_fresh():
    assert tl_fresh(tl(5000, amount=89_999), S, NOW) and tl_fresh(tl(5000, amount=162_500), S, NOW)  # insan eliyle yazılmış tutar
    assert not tl_fresh(tl(5000, amount=417_136), S, NOW)  # sitenin başka para biriminden çevirdiği tutar
    assert not tl_fresh(tl(5000, amount=1_234_567), S, NOW)  # yer tutucu
    assert not tl_fresh(tl(5000, amount=None), S, NOW) and not tl_fresh(car("t", 5000, currency="TRY", posted_at=NOW), S, NOW)


# --- karar ---
def test_fresh_tl_far_below_market_is_strong_on_scaled_market():
    d = decide(tl(5000), POOL, S, now=NOW)
    assert d.profit.tier is Tier.STRONG and d.gaps == []
    assert d.market.tl_factor == S.tl_fresh_factor and abs(d.market.median_gbp - 8700 * S.tl_fresh_factor) < 1
    assert d.market.p25_gbp < 8400 * S.tl_fresh_factor  # alt çeyrek de çarpıldı
    assert any(w.startswith("fiyat TL (yeni ilan)") for w in d.warnings)  # mesajda neden yazar
    assert abs(d.profit.profit_gbp - (8700 * S.tl_fresh_factor * S.quick_sale_factor - 5000 - S.fixed_cost_gbp)) < 1  # rakamlar muhafazakâr piyasadan


def test_stale_or_undated_tl_stays_at_most_yellow():
    for l in (tl(5000, days=6), car("t", 5000, currency="TRY", price_amount=325_000, posted_at=None)):
        d = decide(l, POOL, S, now=NOW)
        assert d.profit.tier is Tier.NEGOTIABLE and "tl_fiyat" in d.gaps and d.market.tl_factor is None


def test_tl_discount_is_applied_before_the_strong_gates():
    """£ piyasasına göre %28 kâr (🟢) ama 0,89 katında %14: taze TL ilan 🟢 OLMAZ, eskisi gibi 🟡 (tl_fiyat; rakamlar eski piyasadan)."""
    d = decide(tl(6200), POOL, S, now=NOW)
    assert d.profit.tier is Tier.NEGOTIABLE and "tl_fiyat" in d.gaps and d.market.tl_factor is None and d.market.median_gbp == 8700
    assert decide(tl(6200), POOL, Settings(tl_fresh_factor=1.0), now=NOW).profit.tier is Tier.STRONG  # katsayı olmasa 🟢 olurdu


def test_fresh_tl_market_excludes_stale_tl_comparables():
    """Eski TL emsaller ucuz görünür ve yıl kaydırır (March 2011: üç TL 2012 emsali 'emsal_yili_yeni' yaptı): taze TL'nin piyasası £ ilan gibi
    önce TL emsalsiz kurulur (Adım 6c)."""
    stale = [car(f"s{i}", 5200, currency="TRY", year=2016) for i in range(6)]
    d = decide(tl(5000), POOL + stale, S, now=NOW)
    assert d.profit.tier is Tier.STRONG and d.market.gbp_only and d.market.median_year == 2015
    old = decide(tl(5000, days=6), POOL + stale, S, now=NOW)  # eski yol: TL emsaller karışır, en fazla 🟡
    assert old.profit.tier is not Tier.STRONG and not old.market.gbp_only


def test_other_strong_gates_still_apply_to_fresh_tl():
    assert decide(tl(5000, raw_text="motor arızalı hasarlı"), POOL, S, now=NOW).profit.tier is Tier.NONE  # engel kelime
    assert decide(tl(5000, extraction_by="llm"), POOL, S, now=NOW).profit.tier is Tier.NEGOTIABLE  # yapay zekâ okuması
    few = POOL[:5]  # 3-7 emsal: 🟢 yok
    assert decide(tl(5000), few, S, now=NOW).profit.tier is not Tier.STRONG
    assert decide(tl(5000, km=200_000), POOL, S, now=NOW).profit.tier is not Tier.STRONG  # km'si yakın emsal yok (km_yakin_emsal_az)


def test_record_and_evaluate_new_carry_the_tl_factor():
    l = tl(5000)
    d = decide(l, POOL, S, now=NOW)
    assert decision_record(d, l, None, S)["evidence"]["tl_katsayi"] == S.tl_fresh_factor
    repo = FakeRepo([tl(5000)], POOL)
    (ev,) = evaluate_new(repo, S, book=None)
    assert ev.profit.tier is Tier.STRONG
    saved = repo.saved[0][1]
    assert saved["tier"] == "guclu" and saved["evidence"]["tl_katsayi"] == S.tl_fresh_factor
    assert any(f.startswith("fiyat TL (yeni ilan)") for f in saved["red_flags"])


def test_gap_label_explains_both_cases():
    assert "TL" in GAP_LABELS["tl_fiyat"] and "eski" in GAP_LABELS["tl_fiyat"] and "yeni" in GAP_LABELS["tl_fiyat"]
