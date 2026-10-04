"""🟠 tahmini fırsat: assess_listing, bildirim, arıza freni, yapay zekâ ikinci okuma, öğrenme, kuru deneme.
Değer tablosu (agent A) yerine sahte PriceBook/Estimate kullanılır; estimate_from_book monkeypatch'lenir."""
from datetime import datetime, timezone

import pytest

from application import estimate_guard, evaluate, health, notify, price_book_shadow
from application import llm_reader as lr
from application.evaluate import Evaluated, assess_listing
from domain import decision
from domain.comparables import Market
from domain.llm_read import compare, parse_llm_read
from domain.price_book import BookRow, Estimate, PriceBook
from domain.profit import Confidence, ProfitResult, Tier
from domain.settings import Settings
from tests.test_evaluate import POOL, car
from tests.test_llm_reader import GOOD, TEXT, FakeRepo as LlmRepo, reader
from tests.test_notify import FakeRepo as NotifyRepo, ev as notify_ev, patch_api

S = Settings()
EST = Estimate(value_gbp=10_000, lower_gbp=8_000, method="B", n=30, sellers=12, sigma=0.15)


def book_with(est=EST, monkeypatch=None, **rows):
    monkeypatch.setattr(decision, "estimate_from_book", lambda l, b, s, now=None: est)
    return PriceBook(**rows)


def row(status="oturmus", **kw):
    return BookRow("Toyota", "vitz", "", 2015, 8000, 7000, 9000, 80_000, 12, 6, "A", status, **kw)


# --- assess_listing: 🟠 sınırları ---
def test_fires_exactly_at_080_of_lower_not_above(monkeypatch):
    b = book_with(monkeypatch=monkeypatch)
    a = assess_listing(car("t", 6400), [], S, b)  # 0.80 · 8000 = 6400
    assert a.profit.tier is Tier.ESTIMATED and a.method == "B"
    assert assess_listing(car("t", 6401), [], S, b) is None


def test_estimated_assessment_values(monkeypatch):
    a = assess_listing(car("t", 6000), [], S, book_with(monkeypatch=monkeypatch))
    assert a.profit.exit_price_gbp == pytest.approx(8000 * 0.95)  # çıkış fiyatı değerden değil alt sınırdan
    assert a.profit.profit_gbp == pytest.approx(7600 - 6000 - 300)
    assert a.profit.confidence is Confidence.LOW and a.estimate is EST
    assert (a.market.n, a.market.median_gbp, a.market.low_gbp) == (30, 10_000, 8_000)
    assert "tahmini_az_emsal" in a.gaps


def test_profit_below_minimum_no(monkeypatch):
    est = Estimate(7_500, 6_000, "B", 30, 12, 0.15)  # 0.95·6000 − 4800 − 300 = 600 < 750
    assert assess_listing(car("t", 4800), [], S, book_with(est, monkeypatch)) is None


def test_price_below_40pct_of_value_no(monkeypatch):
    est = Estimate(20_000, 8_000, "B", 30, 12, 0.40)
    assert assess_listing(car("t", 6400), [], S, book_with(est, monkeypatch)) is None  # 6400 < 0.4·20000: yazım hatası


def test_a_market_contradicts_no_but_agreeing_a_fires(monkeypatch):
    b = book_with(monkeypatch=monkeypatch)
    low_pool = [car(f"p{i}", 6000 + i * 100) for i in range(5)]  # A medyanı 6200: fiyat 6000 > 0.85·6200 değil ama 6400>5270
    assert assess_listing(car("t", 6400), low_pool, S, b).profit.tier is not Tier.ESTIMATED
    ok_pool = [car(f"p{i}", 8800 + i * 100) for i in range(5)]  # medyan 9000: 6400 ≤ 0.85·9000
    a = assess_listing(car("t", 6400), ok_pool, S, b)
    assert a.profit.tier is Tier.ESTIMATED and a.method == "B"


def test_eight_or_more_comparables_never_estimated(monkeypatch):
    a = assess_listing(car("t", 5000), POOL, S, book_with(monkeypatch=monkeypatch))
    assert a.method == "A" and a.profit.tier is Tier.STRONG


@pytest.mark.parametrize("over,setting", [
    ({"steering": "LHD"}, {}),
    ({"brand_norm": "Toyota"}, {"blocked_brands": ["Toyota"]}),
    ({}, {"muted_models": ["Toyota|vitz"]}),
    ({}, {"max_buy_gbp": 5000}),
    ({"seller_phone": "905331112233"}, {"blocked_phones": ["905331112233"]}),
    ({"raw_text": "hasarlı araç"}, {}),
    ({"currency_guess": True}, {}),
    ({"karantina_nedeni": "uc_fiyat"}, {}),
])
def test_blockers_no_estimated(monkeypatch, over, setting):
    s = Settings(**setting)
    a = assess_listing(car("t", 6000, **over), [], s, book_with(monkeypatch=monkeypatch))
    assert a is None or a.profit.tier is not Tier.ESTIMATED


def test_feature_off_or_no_book_unchanged(monkeypatch):
    b = book_with(monkeypatch=monkeypatch)
    assert assess_listing(car("t", 6000), [], Settings(estimated_alerts=False), b) is None
    assert assess_listing(car("t", 6000), [], S) is None  # book=None: eski davranış
    a = assess_listing(car("t", 5000), POOL, S)
    b2 = assess_listing(car("t", 5000), POOL, S, None)
    assert a.profit == b2.profit and a.gaps == b2.gaps and a.method == "A" and a.estimate is None


def test_suspect_row_downgrades_green(monkeypatch):
    monkeypatch.setattr(decision, "estimate_from_book", lambda *a: None)
    ok = PriceBook(rows={("Toyota", "vitz", "", 2015): row("oturmus")})
    bad = PriceBook(rows={("Toyota", "vitz", "", 2015): row("supheli", cand_value=9000)})
    assert assess_listing(car("t", 5000), POOL, S, ok).profit.tier is Tier.STRONG
    a = assess_listing(car("t", 5000), POOL, S, bad)
    assert a.profit.tier is Tier.NEGOTIABLE and "deger_supheli" in a.gaps


def test_evaluate_new_saves_method_b_only_for_estimated(monkeypatch):
    from tests.test_evaluate import FakeRepo
    b = book_with(monkeypatch=monkeypatch)
    repo = FakeRepo([car("t", 6000)], [])
    (e,) = evaluate.evaluate_new(repo, S, book=b)
    assert e.profit.tier is Tier.ESTIMATED and e.method == "B"
    saved = repo.saved[0][1]
    assert saved["method"] == "B" and saved["tier"] == "tahmini" and saved["market_median_gbp"] == 10_000
    assert "tahmini_az_emsal" in saved["red_flags"]
    repo2 = FakeRepo([car("t", 5000)], POOL)
    evaluate.evaluate_new(repo2, S, book=b)
    assert "method" not in repo2.saved[0][1]  # A: kolon varsayılanı


def test_load_book_failure_is_none():
    class R:
        pass
    assert evaluate.load_book(R()) is None


# --- bildirim ---
def est_ev(i, price=5000):
    e = notify_ev(i, Tier.ESTIMATED)
    e.listing["price_gbp"] = price
    e.market = Market(30, 7600, 6700, 8600, 1, 0.0)
    e.profit = ProfitResult(6365, 1065, 0.21, Confidence.LOW, Tier.ESTIMATED)
    return e


def test_format_estimated_alert():
    t = notify.format_alert(est_ev(1))
    assert t.splitlines()[0] == "🟠 KONTROL ET — az emsal, kendin de bak"
    assert "📘 Tablo değeri ~£7.600 (en kötü ihtimalle £6.700) → ~%34 ucuz" in t and "kâr (temkinli) ~£1.065" in t and "30 ilanlık eğri" in t
    assert "💡 Neden: az benzer araç var: fiyat model eğrisinden hesaplandı" in t
    assert "🟢" not in t and "GÜÇLÜ" not in t and "Güven" not in t


def test_green_alert_shows_one_market_number_even_when_a_book_row_is_known():
    """Sahibin kararı: mesajda TEK piyasa ortası (iki farklı sayı görünmesin); değer tablosu satırı mesajdan kalktı."""
    e = notify_ev(1)
    e.book_row = row()
    t = notify.format_alert(e)
    assert "📘" not in t and "Değer tablosu" not in t and "📊 Piyasa ortası" in t


def test_send_alerts_tier_filter(monkeypatch):
    sent = patch_api(monkeypatch, {})
    repo = NotifyRepo(["a"])
    assert notify.send_alerts(repo, "t", [notify_ev(1), est_ev(2)], tier=Tier.ESTIMATED) == 1
    assert [k for k in repo.alerts] == [(2, "a")]
    assert sent == ["a"]


def test_burst_brake_sends_one_summary_and_marks_all(monkeypatch):
    sent, owner = [], []
    texts = []

    def fake(token, method, **kw):
        sent.append(kw["chat_id"])
        texts.append(kw["text"])
        return {"message_id": 7}

    monkeypatch.setattr(notify, "api", fake)
    monkeypatch.setattr(health, "notify_owner", lambda repo, key, text, repeat_hours=12: owner.append((key, text)) or True)
    repo = NotifyRepo(["a", "b"])
    evs = [est_ev(i, price=5000 + i) for i in range(S.est_burst_limit + 1)]
    n = notify.send_alerts(repo, "t", evs, tier=Tier.ESTIMATED, s=S)
    assert n == len(evs) and sent == ["a", "b"]  # abone başına TEK mesaj
    assert texts[0].startswith(f"🟠 {len(evs)} tahmini fırsat çıktı; olağan dışı, kontrol ediyorum — en iyi 5:")
    assert texts[0].count("•") == 5
    assert len(repo.alerts) == 2 * len(evs)  # hepsi bildirilmiş sayıldı: tekrar etmez
    assert owner and owner[0][0] == "est_burst"
    sent.clear()
    notify.send_alerts(repo, "t", evs, tier=Tier.ESTIMATED, s=S)
    assert sent == []


def test_exactly_at_limit_sends_individually(monkeypatch):
    sent = patch_api(monkeypatch, {})
    repo = NotifyRepo(["a"])
    evs = [est_ev(i) for i in range(S.est_burst_limit)]
    no_daily_cap = S.model_copy(update={"est_daily_limit": 1000})  # günlük sınır (3) bu testin konusu değil: arıza freni sınırı sınanıyor
    assert notify.send_alerts(repo, "t", evs, max_per_run=100, tier=Tier.ESTIMATED, s=no_daily_cap) == S.est_burst_limit
    assert len(sent) == S.est_burst_limit


def test_pending_alerts_builds_estimated(monkeypatch):
    now = datetime.now(timezone.utc)
    r = dict(id=1, raw_text="x", model="Vitz", market_median_gbp=7600.0, comparables_n=30, market_low_gbp=6700.0,
             market_high_gbp=8600.0, year_span=1, archived_share=0.0, exit_price_gbp=6365.0, profit_gbp=1065.0, profit_pct=21.0,
             confidence="dusuk", red_flags=["tahmini_az_emsal", "ufak masraf var"], method="B", year=2015, brand_norm="Toyota",
             model_norm="vitz", first_seen_at=now, posted_at=None)

    class R:
        def pending_strong(self, hours, tier, rules_version=None):
            assert tier == "tahmini"
            return [r]

    (e,) = evaluate.pending_alerts(R(), tier=Tier.ESTIMATED, book=PriceBook(rows={("Toyota", "vitz", "", 2015): row()}))
    assert e.profit.tier is Tier.ESTIMATED and e.method == "B" and e.warnings == ["ufak masraf var"]
    assert e.book_row is not None


# --- yapay zekâ okuması: sorun alanı ---
SORUN_TEXT = TEXT + "\nAirbag patlak, ön tamponda değişen var"
SORUN = {**GOOD, "sorun": True, "sorun_alinti": "Airbag patlak"}


def test_problem_quote_must_appear_verbatim():
    assert parse_llm_read(SORUN, SORUN_TEXT).problem == "Airbag patlak"
    assert parse_llm_read({**SORUN, "sorun_alinti": "Su basmış araç"}, SORUN_TEXT).problem is None  # uydurma alıntı
    assert parse_llm_read({**SORUN, "sorun": False}, SORUN_TEXT).problem is None
    assert parse_llm_read(GOOD, SORUN_TEXT).problem is None


def test_compare_adds_problem_reason():
    listing = {"price_amount": 7500, "currency": "GBP", "year": 2016, "km": 53000, "brand": "Honda"}
    reasons, price_ok = compare(listing, parse_llm_read(SORUN, SORUN_TEXT))
    assert reasons == ["okuma_sorun"] and price_ok
    assert compare(listing, parse_llm_read(GOOD, SORUN_TEXT))[0] == []


# --- verify_candidates: 🟠 ---
def est_candidate(platform="web", text=SORUN_TEXT, **over):
    listing = {"id": "L1", "platform": platform, "raw_text": text, "price_amount": 7500, "currency": "GBP", "price_gbp": 7500.0,
               "year": 2016, "km": 53000, "brand": "Honda", "steering": None, "extraction_by": "parser"} | over
    return Evaluated(listing, None, ProfitResult(1, 1, 0.3, Confidence.LOW, Tier.ESTIMATED), [], [], [])


def test_estimated_with_hidden_problem_is_downgraded_on_any_platform():
    repo = LlmRepo()
    out = lr.verify_candidates(repo, reader(repo, data=SORUN), [est_candidate("web")])
    assert out == []
    (lid, flags), = repo.downgraded
    assert lid == "L1" and "okuma_sorun" in flags and "sorun: «Airbag patlak»" in flags


def test_estimated_clean_read_passes_with_check():
    repo = LlmRepo()
    (e,) = lr.verify_candidates(repo, reader(repo), [est_candidate("web", text=TEXT)])
    assert e.checks and "gizli sorun" in e.checks[0] and repo.downgraded == []


def test_estimated_price_mismatch_downgraded():
    repo = LlmRepo()
    assert lr.verify_candidates(repo, reader(repo), [est_candidate(text=TEXT, price_amount=4500)]) == []


def test_llm_priced_estimated_needs_confirmation():
    repo = LlmRepo()
    unconfirmed = {**GOOD, "fiyat": None, "fiyat_alinti": None}
    assert lr.verify_candidates(repo, reader(repo, data=unconfirmed), [est_candidate(text=TEXT, extraction_by="llm")]) == []
    assert repo.downgraded[0][1] == ["llm_okudu"]
    repo2 = LlmRepo()
    assert len(lr.verify_candidates(repo2, reader(repo2), [est_candidate(text=TEXT, extraction_by="llm")])) == 1


def test_llm_failure_estimated_goes_with_note_but_llm_priced_does_not():
    repo = LlmRepo()
    (e,) = lr.verify_candidates(repo, reader(repo, data=None, err="http 500"), [est_candidate(text=TEXT)])
    assert lr.UNCHECKED in e.warnings
    repo2 = LlmRepo()
    assert lr.verify_candidates(repo2, reader(repo2, data=None, err="http 500"), [est_candidate(text=TEXT, extraction_by="llm")]) == []
    assert repo2.downgraded[0][1] == ["llm_okudu"]
    (e3,) = lr.verify_candidates(LlmRepo(), None, [est_candidate(text=TEXT)])  # anahtar yok
    assert lr.UNCHECKED in e3.warnings


def test_budget_raised():
    assert lr.DAILY_BUDGET_USD == 0.40


# --- estimate_guard ---
class GuardRepo:
    def __init__(self, by_model=(), recent=()):
        self.by_model, self.recent, self.state = list(by_model), list(recent), {}
        self.after = "unset"

    def est_feedback_by_model(self, days, min_bad):
        return self.by_model

    def est_feedback_recent(self, limit, after=None):
        self.after = after
        return self.recent

    def get_state(self, k, default=None):
        return self.state.get(k, default)

    def set_state(self, k, v):
        self.state[k] = v

    def alert_recent(self, key, hours):
        return f"alert:{key}" in self.state

    def mark_alerted(self, key):
        self.state[f"alert:{key}"] = "2026-10-01 00:00:00+00"


@pytest.fixture
def owner_msgs(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    msgs = []
    monkeypatch.setattr(health, "api", lambda *a, **kw: msgs.append(kw["text"]))
    return msgs


def test_model_with_two_wrong_marks_is_disabled_once(owner_msgs):
    repo = GuardRepo(by_model=[{"brand_norm": "Toyota", "model_norm": "corolla", "bad_n": 2}])
    assert estimate_guard.disable_failing_models(repo) == 1
    assert repo.state["est_disabled"] == "Toyota|corolla" and "Toyota corolla" in owner_msgs[0] and "2 kez" in owner_msgs[0]
    assert estimate_guard.disable_failing_models(repo) == 0 and len(owner_msgs) == 1  # ikinci kez durdurma/haber yok


def test_other_disabled_models_are_kept(owner_msgs):
    repo = GuardRepo(by_model=[{"brand_norm": "Honda", "model_norm": "fit", "bad_n": 3}])
    repo.state["est_disabled"] = "Toyota|corolla"
    estimate_guard.disable_failing_models(repo)
    assert repo.state["est_disabled"] == "Honda|fit,Toyota|corolla"


def test_global_tightening_after_five_of_ten_wrong(owner_msgs):
    repo = GuardRepo(recent=[True] * 5 + [False] * 5)
    assert estimate_guard.tighten_threshold(repo) == 0.75
    assert repo.state["cfg:est_min_discount_to_lower"] == "0.75" and "0.75" not in owner_msgs[0] and "%75" in owner_msgs[0]
    assert estimate_guard.tighten_threshold(repo) is None  # 30 günde bir


def test_no_tightening_when_few_wrong_or_few_votes_or_floor(owner_msgs):
    assert estimate_guard.tighten_threshold(GuardRepo(recent=[True] * 4 + [False] * 6)) is None
    assert estimate_guard.tighten_threshold(GuardRepo(recent=[True] * 5)) is None  # 10 oy yok
    floor = GuardRepo(recent=[True] * 10)
    floor.state["cfg:est_min_discount_to_lower"] = "0.6"
    assert estimate_guard.tighten_threshold(floor) is None
    steady = GuardRepo(recent=[True] * 10)
    steady.state["cfg:est_min_discount_to_lower"] = "0.62"
    assert estimate_guard.tighten_threshold(steady) == 0.60  # tabana kadar


# --- kuru deneme ---
def test_shadow_lists_estimated_candidates_and_counts_a_confirmed(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(decision, "estimate_from_book", lambda l, b, s, now=None: EST)
    fits = []
    book = PriceBook()
    cand = car("t", 6000, first_seen_at=now, brand="Toyota", url="https://x/y", posted_at=None)
    pool = [car(f"p{i}", 8800 + i * 100) for i in range(5)]  # A medyan 9000: 6000 ≤ 0.85·9000 → A doğrular
    rows = price_book_shadow.shadow_candidates([cand, car("u", 9500, first_seen_at=now)], pool, book, S,
                                               lambda *a: fits.append(a), now)
    assert len(rows) == 1 and rows[0].a_confirmed and rows[0].discount == pytest.approx(0.4)
    out = price_book_shadow.format_report(rows, 14, 2)
    assert "14 günde ilk görülen 2 ilandan 1" in out and "A medyanı var" in out and "https://x/y" in out
    assert "seller_phone" not in out


def test_shadow_refits_model_curve_without_the_listing(monkeypatch):
    from domain.price_book import Curve
    now = datetime.now(timezone.utc)
    seen = []
    curve = Curve("Toyota", "vitz", 9.0, -0.08, -0.05, 0.15, 30, 6, 12, 2008, 2020, 250_000, 2026)
    book = PriceBook(curves={("Toyota", "vitz"): curve})
    cand = car("t", 6000, first_seen_at=now)

    def fit(rows, weights, brand, model, ref_year, s, max_sigma):
        seen.append((len(rows), ref_year, [r["id"] for r in rows]))
        return None  # eğri ilansız kurulamıyor

    estimates = []
    monkeypatch.setattr(decision, "estimate_from_book", lambda l, b, s, now=None: estimates.append(b.curve("Toyota", "vitz")) or EST)
    price_book_shadow.shadow_candidates([cand], [cand, *POOL], book, S, fit, now)
    assert seen and "t" not in seen[0][2] and seen[0][0] == len(POOL) and seen[0][1] == 2026
    assert book.curve("Toyota", "vitz") is curve  # asıl tablo değişmedi


def test_ad_check_answers_estimated(monkeypatch):
    from application import ad_check
    from tests.test_ad_check import AD, Repo
    monkeypatch.setattr(ad_check, "gbp_rate", lambda c: 1.0)
    monkeypatch.setattr(ad_check, "load_book", lambda repo: PriceBook())
    monkeypatch.setattr(decision, "estimate_from_book", lambda l, b, s, now=None: EST)
    out = ad_check.handle(Repo(pool=[]), AD, None, None)
    assert "🟠 TAHMİNİ FIRSAT — az emsal, kendin de kontrol et" in out  # /ad_check (iletilen ilan) yanıtı ayrı, ayrıntılı biçim
    assert "Tablo değeri ~£10.000 (en kötü ihtimalle £8.000)" in out and "~%50 ucuz" in out
    assert "En yakın emsaller" not in out


def test_estimate_guard_does_nothing_before_ten_votes(owner_msgs):
    """Sahip kararı (03.10.2026): 10 oydan önce otomatik öğrenme yok (model kapatma ve eşik sıkılaştırma dahil)."""
    class FewVotes(GuardRepo):
        def feedback_votes(self):
            return 9

    repo = FewVotes(by_model=[{"brand_norm": "Toyota", "model_norm": "corolla", "bad_n": 2}], recent=[True] * 10)
    assert estimate_guard.guard_estimates(repo) == (0, None)
    assert "est_disabled" not in repo.state and owner_msgs == []
