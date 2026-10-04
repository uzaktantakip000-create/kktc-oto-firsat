"""Karar kaydı (migration 019, Adım 5b-1): evaluations satırına rules_version/satıcı sayısı/alt çeyrek/tablo değeri/nedenler/kanıt yazılır.
Yalnız YAZMA: karar ve okuma yolları bu alanlara bağlı değildir; kayıt kurulamazsa değerlendirme yine kaydedilir."""
import json
import uuid

from application import evaluate as evaluate_module
from application.decision_record import MAX_COMPARABLE_IDS, decision_record, settings_digest
from application.evaluate import evaluate_new
from domain.decision import Decision
from domain.price_book import BookRow, Estimate, PriceBook
from domain.comparables import Market
from domain.profit import Confidence, ProfitResult, Tier
from domain.settings import RULES_VERSION, Settings
from tests.test_evaluate import POOL, FakeRepo, car


def saved(repo):
    return repo.saved[0][1]


def test_strong_evaluation_carries_the_full_decision_record():
    repo = FakeRepo([car("t", 5000)], POOL)
    evaluate_new(repo)
    ev = saved(repo)
    assert ev["rules_version"] == RULES_VERSION and ev["tier"] == "guclu"
    assert ev["saticilar_n"] == 8 and ev["alt_ceyrek_gbp"] is not None and ev["nedenler"] is None
    e = ev["evidence"]
    assert e["yontem"] == "A" and e["fiyat_gbp"] == 5000 and e["km_bilinmiyor"] is False and e["gbp_only"] is False
    assert len(e["emsal_ids"]) == 8 and e["medyan_km"] == 80_000 and e["medyan_yil"] == 2015.0
    json.dumps(e)  # JSON'a çevrilebilir


def test_downgraded_evaluation_records_the_reasons_but_no_comparable_ids():
    repo = FakeRepo([car("t", 5000, currency_guess=True)], POOL)
    evaluate_new(repo)
    ev = saved(repo)
    assert ev["tier"] == "pazarlik" and ev["nedenler"] == ["para_birimi_tahmin"]
    assert "emsal_ids" not in ev["evidence"]  # satır boyutu: kimlik listesi yalnız 🟢/🟠'da


def test_unknown_km_is_recorded_in_the_evidence():
    repo = FakeRepo([car("t", 5000, km=None)], POOL)
    evaluate_new(repo)
    assert saved(repo)["evidence"]["km_bilinmiyor"] is True


def test_implausible_price_row_also_carries_the_rules_version():
    repo = FakeRepo([car("t", 15)], POOL)
    evaluate_new(repo)
    assert saved(repo)["rules_version"] == RULES_VERSION


def test_a_failing_record_never_blocks_the_evaluation(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kayıt kurulamadı")
    monkeypatch.setattr(evaluate_module, "decision_record", boom)
    repo = FakeRepo([car("t", 5000)], POOL)
    (ev,) = evaluate_new(repo)
    assert ev.profit.tier is Tier.STRONG  # karar ve bildirim yolu etkilenmez
    row = saved(repo)
    assert row["tier"] == "guclu" and row["rules_version"] == RULES_VERSION and "evidence" not in row


def test_uuid_comparable_ids_become_strings_and_are_capped():
    pool = [car(uuid.uuid4(), 8000 + i * 10) for i in range(40)]
    repo = FakeRepo([car(uuid.uuid4(), 5000)], pool)
    evaluate_new(repo)
    ids = saved(repo)["evidence"]["emsal_ids"]
    assert len(ids) == MAX_COMPARABLE_IDS and all(isinstance(i, str) for i in ids)
    json.dumps(saved(repo)["evidence"])


def test_settings_digest_changes_with_the_decision_inputs_and_never_contains_phone_numbers():
    base = settings_digest(Settings())
    assert settings_digest(Settings()) == base
    assert settings_digest(Settings(max_buy_gbp=9000)) != base and settings_digest(Settings(blocked_brands=["Fiat"])) != base
    assert settings_digest(Settings(blocked_phones=["+905551112233"])) != base  # sayı değişir...
    rec_s = Settings(blocked_phones=["+905551112233"])
    repo = FakeRepo([car("t", 5000)], POOL)
    evaluate_new(repo, rec_s)
    assert "905551112233" not in json.dumps(saved(repo), default=str)  # ...ama numara kayda girmez


def test_estimated_decision_leaves_unknown_market_fields_null_not_zero():
    est = Estimate(9000, 7500, "B", 14, 6, 0.12, BookRow("Toyota", "vitz", "", 2015, 8800, 7000, 9500, 80_000, 12, 6, "A", "oturmus"))
    market = Market(14, 9000, 7500, 10800, 1, 0.0)  # decision.py'nin yapay 🟠 piyasası: satıcı sayısı 0, alt çeyrek yok
    a = Decision(market, ProfitResult(7125, 1500, 0.3, Confidence.LOW, Tier.ESTIMATED), [], [], ["tahmini_az_emsal"], "", "B", est)
    rec = decision_record(a, car("t", 5000), None, Settings())
    assert rec["saticilar_n"] is None and rec["alt_ceyrek_gbp"] is None and rec["tablo_degeri_gbp"] == 8800
    assert rec["evidence"]["tahmin"]["alt"] == 7500 and rec["evidence"]["tablo"]["durum"] == "oturmus" and "emsal_ids" not in rec["evidence"]


def test_book_value_is_recorded_for_direct_decisions_too():
    book = PriceBook(rows={("Toyota", "vitz", "", 2015): BookRow("Toyota", "vitz", "", 2015, 8700, 8000, 9400, 80_000, 12, 6, "A", "oturmus")})
    repo = FakeRepo([car("t", 5000)], POOL)
    evaluate_new(repo, book=book)
    assert saved(repo)["tablo_degeri_gbp"] == 8700


def test_widened_market_records_both_medians_and_the_year_span():
    narrow = [car(f"n{i}", 7000 + i * 100, year=2015) for i in range(5)]
    wide = [car(f"w{i}", 9000 + i * 100, year=2013) for i in range(5)]
    repo = FakeRepo([car("t", 5000)], narrow + wide)
    evaluate_new(repo)
    e = saved(repo)["evidence"]
    assert e["yil_araligi"] == 2 and e["medyan_dar"] == 7200 and e["medyan_genis"] > 8000
