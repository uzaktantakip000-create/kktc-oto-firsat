"""🔍 ÇOK UCUZ — ÖNCE TEYİT ET (01.11.2026; sahip: "fırsattı bunlar, ona göre ayarla"): kârı ≥ %20, emsali ≥ 8, 🟢 olmamasının TEK nedeni
fiyatın inanılmayacak kadar ucuz olması (2σ gürültü, emsallerin yarısından düşük) ya da temkinli km varsayımı olan ilan, 🟢 DEĞİL, şüphesi
yazılı ayrı mesajla gider. Öbür 🟢 kapıları aynen geçerlidir. Uydurma ilanlar; ağ yok."""
from dataclasses import replace

import pytest

from domain.decision import cheap_to_verify, decide
from domain.profit import Tier
from domain.settings import Settings
from tests.test_comparables import NOW
from tests.test_evaluate import POOL, car

WIDE = [car(f"w{i}", p) for i, p in enumerate([5000, 6000, 7000, 8000, 9000, 10000, 11000, 12000])]  # dağınık pazar: 2σ kapısı ucuzluğu gürültü sayar


def test_noisy_market_cheap_listing_goes_as_verify_not_green():
    l = car("t", 6000)
    d = decide(l, WIDE, Settings(), now=NOW)
    assert d.profit.tier is Tier.NEGOTIABLE and "fiyat_gurultu" in d.gaps and d.profit.profit_pct >= 0.20
    v = cheap_to_verify(l, WIDE, Settings(), now=NOW)
    assert v is not None and v.profit.tier is Tier.STRONG  # gevşek karar: "fiyat şüphesi dışında temiz"


def test_price_below_half_of_market_goes_as_verify_down_to_the_floor():
    m = decide(car("x", 8000), POOL, Settings(), now=NOW).market
    half = car("t", round(m.median_gbp * 0.45))
    d = decide(half, POOL, Settings(), now=NOW)
    assert d.profit.tier is Tier.NEGOTIABLE and "fiyat_asiri_dusuk" in d.gaps
    assert cheap_to_verify(half, POOL, Settings(), now=NOW) is not None
    typo = car("t", round(m.median_gbp * 0.30))  # eksik rakam (15.000 → 1.500 gibi): bu mesaj da gitmez
    assert cheap_to_verify(typo, POOL, Settings(), now=NOW) is None
    assert cheap_to_verify(half, POOL, Settings(cheap_verify_min_ratio=0.50), now=NOW) is None


def test_real_green_is_not_sent_twice_as_verify():
    l = car("t", 5000)
    assert decide(l, POOL, Settings(), now=NOW).profit.tier is Tier.STRONG
    assert cheap_to_verify(l, POOL, Settings(), now=NOW) is None  # 🟢 yolundan gider


@pytest.mark.parametrize("change", [
    dict(raw_text="aracın masrafları vardır"),  # tutarı yazmayan masraf
    dict(raw_text="motor arızalı"),  # tuzak kelimesi
])
def test_other_green_gates_still_apply(change):
    assert cheap_to_verify(car("t", 6000, **change), WIDE, Settings(), now=NOW) is None


def test_few_comparables_or_low_profit_is_not_sent():
    assert cheap_to_verify(car("t", 6000), WIDE[:5], Settings(), now=NOW) is None  # emsal < 8
    assert cheap_to_verify(car("t", 7400), WIDE, Settings(), now=NOW) is None  # kâr %20'nin altında


def test_verify_message_says_it_is_not_green_and_names_the_doubt():
    from application.evaluate import CHEAP_VERIFY
    from application.notify import format_alert
    from tests.test_notify import ev
    e = replace(ev(1, tier=Tier.NEGOTIABLE), warnings=["fiyat_gurultu"], kind=CHEAP_VERIFY)
    text = format_alert(e)
    assert text.startswith("🔍 ÇOK UCUZ — ÖNCE TEYİT ET · %52 kâr potansiyeli\nℹ️ 🟢 değil")
    assert "🔍 Şüphe: benzerlerin fiyatı çok dağınık" in text and "fiyat_gurultu" not in text
    assert "PAZARLIK FIRSATI" not in text
    plain = format_alert(replace(e, kind=""))  # aynı seviye, işaretsiz: eski 🟡 mesajı
    assert plain.startswith("🟡 PAZARLIK FIRSATI") and "🟡 Neden 🟢 değil: benzerlerin" in plain


def _pending(monkeypatch, specs, verdict=True):
    from application import evaluate
    from tests.test_send_gate import cand
    evs = []
    for i, (pct, flags) in enumerate(specs):
        e = cand(i, tier=Tier.NEGOTIABLE, seen_h=1)
        e = replace(e, profit=replace(e.profit, profit_pct=pct), warnings=list(flags))
        e.listing.update(brand_norm="toyota", model_norm="corolla")
        evs.append(e)
    monkeypatch.setattr(evaluate, "pending_alerts", lambda *a, **k: list(evs))
    monkeypatch.setattr(evaluate, "cheap_to_verify", lambda listing, pool, s, book, now: object() if verdict else None)
    pools = []
    repo = type("R", (), {"get_state": lambda self, k: None, "resurfaced_kktcarabam": lambda self, ids: set(),
                          "market_pool": lambda self, keys: pools.append(keys) or []})()
    out = evaluate.pending_cheap_verify(repo, Settings())
    return [(e.listing["id"], e.kind) for e in out], len(pools)


def test_pending_takes_only_recorded_price_doubts_at_twenty_percent(monkeypatch):
    out, pools = _pending(monkeypatch, [(0.30, ["fiyat_gurultu"]), (0.18, ["fiyat_gurultu"]), (0.30, ["emsal_yili_yeni"]),
                                        (0.40, ["fiyat_asiri_dusuk"]), (0.25, ["km_temkinli"])])
    assert out == [(0, "ucuz_teyit"), (3, "ucuz_teyit"), (4, "ucuz_teyit")] and pools == 1


def test_pending_reads_no_pool_without_candidates(monkeypatch):
    assert _pending(monkeypatch, [(0.30, ["tl_fiyat"])]) == ([], 0)
    assert _pending(monkeypatch, [(0.30, ["fiyat_gurultu"])], verdict=False) == ([], 1)


def _deliver(monkeypatch, pazarlik_24h: int, cheap_24h: int, enabled: bool = True):
    from entrypoints import cron_evaluate
    calls = []
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(cron_evaluate, "pending_alerts", lambda *a, **k: [])
    monkeypatch.setattr(cron_evaluate, "pending_clean_negotiable", lambda repo, s, book=None, platform=None: ["temiz"])
    monkeypatch.setattr(cron_evaluate, "pending_cheap_verify", lambda repo, s, book=None, platform=None: ["ucuz"])
    monkeypatch.setattr(cron_evaluate, "gonderim_kontrol", lambda repo, evs, label: (list(evs), []))
    monkeypatch.setattr(cron_evaluate.urgent, "run", lambda *a, **k: 0)

    def send_alerts(repo, token, evs, notes=None, tier=Tier.STRONG, max_per_run=10, s=None):
        calls.append((list(evs), max_per_run))
        return len(evs)

    monkeypatch.setattr(cron_evaluate, "send_alerts", send_alerts)

    def sent_since(self, tier, hours=24, kind=None):
        if tier != "pazarlik":
            return 0
        return cheap_24h if kind == "ucuz_teyit" else pazarlik_24h  # kind=None: bütün 🟡 (temiz + çok ucuz)

    repo = type("R", (), {"alerts_sent_since": sent_since})()
    s = Settings(cheap_verify_alerts=enabled, estimated_alerts=False)
    cron_evaluate.deliver(repo, "t", "1", s, [], lambda: None)
    return [c for c in calls if c[0]]


def test_verify_has_its_own_daily_limit_and_does_not_use_up_the_negotiable_one(monkeypatch):
    assert _deliver(monkeypatch, 0, 0) == [(["temiz"], 1), (["ucuz"], 3)]
    assert _deliver(monkeypatch, 2, 2) == [(["temiz"], 1), (["ucuz"], 1)]  # bugün 2 🔍 gitti: temiz 🟡 hakkı duruyor
    assert _deliver(monkeypatch, 4, 3) == []  # 1 temiz 🟡 + 3 🔍: ikisi de doldu
    assert _deliver(monkeypatch, 0, 0, enabled=False) == [(["temiz"], 1)]


def test_sent_verify_message_is_recorded_with_its_kind(monkeypatch):
    """İnceleme bulgusu (01.11.2026): kind kayda yazılmazsa 🔍 günlük sınırı hiç dolmaz ve her 🔍 temiz 🟡 hakkını yer. Gerçek
    send_alerts → _record_alert → save_alert yolu."""
    from application import notify
    from application.evaluate import CHEAP_VERIFY
    from tests.test_notify import FakeRepo, ev, patch_api

    class Repo(FakeRepo):
        def save_alert(self, listing_id, chat_id, tier, msg_id, *, evaluation_id, price_gbp, kind=None):
            self.saved_alerts.append({"listing": listing_id, "tier": tier, "kind": kind})

    patch_api(monkeypatch, {})
    repo = Repo(["c1"])
    cheap, plain = replace(ev(1, tier=Tier.NEGOTIABLE), kind=CHEAP_VERIFY), ev(2, tier=Tier.NEGOTIABLE)
    assert notify.send_alerts(repo, "t", [cheap, plain], tier=Tier.NEGOTIABLE) == 2
    assert repo.saved_alerts == [{"listing": 1, "tier": "pazarlik", "kind": "ucuz_teyit"},
                                 {"listing": 2, "tier": "pazarlik", "kind": None}]  # işaretsiz 🟡: eski kayıt (kind gönderilmez)
