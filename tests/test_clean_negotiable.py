"""🟡 PAZARLIK FIRSATI (27.10.2026; sahip: "çok az fırsat gönderiliyor"): 🟢'nin bütün kapılarından geçen, kârı %15–20 arası ilan açık etiketle
anında gider, günde en çok `clean_negotiable_daily_limit`. Kapı yeni değil: aynı `decide()` yalnız eşik (%15) ve asgari net kâr (£500) gevşek."""
from domain.decision import clean_negotiable, decide
from domain.profit import Tier
from domain.settings import Settings
from infrastructure.db.repository import _tiers_blocking
from tests.test_comparables import NOW
from tests.test_evaluate import POOL, car


def _cheapest_quartile_price(pct: float) -> float:
    """POOL'da kâr oranı yaklaşık `pct` olan fiyat (çıkış = medyan·0,95 − 300)."""
    m = decide(car("x", 7000), POOL, Settings(), now=NOW).market
    return (m.median_gbp * 0.95 - 300) / (1 + pct)


def test_seventeen_percent_clean_listing_is_a_negotiable_opportunity_not_green():
    l = car("t", round(_cheapest_quartile_price(0.17)))
    d = decide(l, POOL, Settings(), now=NOW)
    assert d.profit.tier is Tier.NEGOTIABLE and 0.15 <= d.profit.profit_pct < 0.20
    clean = clean_negotiable(l, POOL, Settings(), now=NOW)
    assert clean is not None and clean.profit.tier is Tier.STRONG  # "🟢 kalitesinde" (eşik %15'le)


def test_below_fifteen_percent_or_switched_off_is_not_sent():
    l = car("t", round(_cheapest_quartile_price(0.13)))
    assert clean_negotiable(l, POOL, Settings(), now=NOW) is None
    l17 = car("t", round(_cheapest_quartile_price(0.17)))
    assert clean_negotiable(l17, POOL, Settings(clean_negotiable_threshold=0.18), now=NOW) is None


def test_green_gates_still_apply_to_negotiable_opportunity():
    l = car("t", round(_cheapest_quartile_price(0.17)))
    l["raw_text"] = "aracın masrafları vardır"  # tutarı yazmayan masraf: 🟢 olmazdı, 🟡 fırsat da olmaz
    assert clean_negotiable(l, POOL, Settings(), now=NOW) is None
    few = POOL[:5]  # 8'den az emsal: gönderim kapısı
    assert clean_negotiable(car("t", round(_cheapest_quartile_price(0.17))), few, Settings(), now=NOW) is None


def test_negotiable_message_is_blocked_by_an_earlier_green_orange_or_urgent_message():
    assert set(_tiers_blocking("pazarlik")) == {"guclu", "tahmini", "acil", "pazarlik"}
    assert _tiers_blocking("guclu") == ["guclu", "tahmini"]  # tersi değil: 🟡 gitmiş ilan sonradan 🟢 olursa 🟢 gider


def _deliver(monkeypatch, sent_last_24h: int, enabled: bool = True):
    from entrypoints import cron_evaluate
    calls = []
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(cron_evaluate, "pending_alerts", lambda *a, **k: [])
    monkeypatch.setattr(cron_evaluate, "pending_clean_negotiable", lambda repo, s, book=None, platform=None: ["aday"])
    monkeypatch.setattr(cron_evaluate, "gonderim_kontrol", lambda repo, evs, label: (list(evs), []))
    monkeypatch.setattr(cron_evaluate.urgent, "run", lambda *a, **k: 0)

    def send_alerts(repo, token, evs, notes=None, tier=Tier.STRONG, max_per_run=10, s=None):
        calls.append((tier, list(evs), max_per_run))
        return len(evs)

    monkeypatch.setattr(cron_evaluate, "send_alerts", send_alerts)
    repo = type("R", (), {"alerts_sent_since": lambda self, tier, hours=24: sent_last_24h if tier == "pazarlik" else 0})()
    s = Settings(clean_negotiable_alerts=enabled, estimated_alerts=False)
    cron_evaluate.deliver(repo, "t", "1", s, [], lambda: None)
    return [c for c in calls if c[0] is Tier.NEGOTIABLE]


def test_delivery_sends_at_most_the_daily_limit(monkeypatch):
    assert _deliver(monkeypatch, 0) == [(Tier.NEGOTIABLE, ["aday"], 1)]
    assert _deliver(monkeypatch, 1) == []  # bugün zaten bir tane gitti
    assert _deliver(monkeypatch, 0, enabled=False) == []


def test_negotiable_message_explains_itself_without_raw_codes():
    """Canlı kuru deneme (27.10.2026): %30 kârlı ama km'siz 🟡 mesajı "kâr %20'nin altında" diyordu ve ham `km_temkinli` gösteriyordu."""
    from dataclasses import replace
    from application.notify import format_alert
    from tests.test_notify import ev
    e = ev(1, tier=Tier.NEGOTIABLE)
    e = replace(e, warnings=["km_temkinli"], market=replace(e.market, p25_gbp=7200.0))
    text = format_alert(e)
    assert text.startswith("🟡 PAZARLIK FIRSATI · %52 kâr potansiyeli\nℹ️ 🟢 kadar kesin değil")
    assert "🟡 Neden 🟢 değil: km yazmıyor" in text and "km_temkinli" not in text
    assert "%20'nin altında: pazarlıkla" not in text
    assert "en ucuz çeyrek £7.200" in text


def _pending(monkeypatch, cands):
    """Sahte repo, havuz okuması sayılır (Supabase çıkış kotası, 27.10.2026: her turda ~67 aday için ~1 MB çekiliyordu, ~1'i taze)."""
    from application import evaluate
    from tests.test_send_gate import cand
    evs = [cand(i, tier=Tier.NEGOTIABLE, seen_h=h) for i, h in cands]
    for e in evs:
        e.listing.update(brand_norm="toyota", model_norm="corolla")
    monkeypatch.setattr(evaluate, "pending_alerts", lambda *a, **k: list(evs))
    monkeypatch.setattr(evaluate, "clean_negotiable", lambda listing, pool, s, book, now: object())
    pools = []
    repo = type("R", (), {"get_state": lambda self, k: None, "resurfaced_kktcarabam": lambda self, ids: set(),
                          "market_pool": lambda self, keys: pools.append(keys) or []})()
    out = evaluate.pending_clean_negotiable(repo, Settings())
    return [e.listing["id"] for e in out], len(pools)


def test_pool_is_not_read_when_no_candidate_is_fresh(monkeypatch):
    assert _pending(monkeypatch, [(1, 100), (2, 200)]) == ([], 0)  # taze olmayan gönderim kontrolünde zaten elenirdi


def test_only_fresh_candidates_are_rechecked_with_the_pool(monkeypatch):
    assert _pending(monkeypatch, [(1, 100), (2, 1)]) == ([2], 1)
