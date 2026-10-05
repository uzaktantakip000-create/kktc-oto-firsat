from datetime import datetime, timezone

import httpx
import pytest

from application import collect_facebook as cf
from application import llm_reader as lr
from application import notify
from application.evaluate import Evaluated
from infrastructure.collectors.facebook_groups import RawGroupPost
from infrastructure.llm import openrouter
from tests.test_notify import FakeRepo as NotifyRepo, ev as notify_ev, patch_api

TEXT = "2016 Honda Fit 1.3 otomatik\n53.000 km\nFiyat: 7.500£ nakit"
GOOD = {"arac_ilani_mi": True, "marka": "Honda", "model": "Fit", "yil": 2016, "yil_alinti": "2016 Honda Fit",
        "km": 53000, "km_alinti": "53.000 km", "fiyat": 7500, "fiyat_alinti": "Fiyat: 7.500£",
        "direksiyon": None, "pesinat_veya_kredi_devri": False, "satildi": False}


class FakeRepo:
    def __init__(self):
        self.state, self.downgraded, self.rows = {}, [], {}

    def get_state(self, k, default=None):
        return self.state.get(k, default)

    def set_state(self, k, v):
        self.state[k] = v

    def downgrade_evaluation(self, listing_id, flags, evaluation_id=None):
        self.downgraded.append((listing_id, flags))

    def known_item_ids(self, source_id):
        return set()

    def upsert_listing(self, source_id, item_id, data):
        self.rows[(source_id, item_id)] = data
        return True

    def mark_checked(self, *a, **k):
        pass

    def count_recent(self, source_id):
        return 0


def reader(repo, data=GOOD, err=None, cost=0.0005):
    calls = []

    def call(key, model, text):
        calls.append(text)
        return data, err, cost

    r = lr.LlmReader(repo, "k", "m", call=call, now=datetime(2026, 10, 2, tzinfo=timezone.utc))
    r.seen = calls
    return r


def ev(platform="instagram", **over):
    listing = {"id": "L1", "platform": platform, "raw_text": TEXT, "price_amount": 7500, "currency": "GBP", "year": 2016,
               "km": 53000, "brand": "Honda", "steering": None} | over
    return Evaluated(listing, None, None, [], [], [])


def test_clean_candidate_is_kept_with_check_line_and_cached():
    repo = FakeRepo()
    r = reader(repo)
    out = lr.verify_candidates(repo, r, [ev()])
    assert len(out) == 1 and out[0].checks and not repo.downgraded
    lr.verify_candidates(repo, r, [ev()])
    assert len(r.seen) == 1  # ikinci turda önbellekten: tekrar ödenmez


def test_mismatch_downgrades_and_drops_candidate():
    repo = FakeRepo()
    out = lr.verify_candidates(repo, reader(repo), [ev(price_amount=4500)])
    assert out == [] and repo.downgraded == [("L1", ["okuma_fiyat"])]


def test_km_difference_in_safe_direction_only_warns():
    repo = FakeRepo()
    r = reader(repo)
    out = lr.verify_candidates(repo, r, [ev(km=90000)])  # kayıt 90.000, yapay zekâ metinden 53.000 okudu: emsal daha ucuz bantta aranır (temkinli)
    assert len(out) == 1 and not repo.downgraded and lr.KM_READ_WARNING in out[0].warnings and repo.state["verify:L1"] == "ok_km"
    out = lr.verify_candidates(repo, r, [ev(km=90000)])  # önbellekten: uyarı yine eklenir, ikinci okuma ücreti yok
    assert lr.KM_READ_WARNING in out[0].warnings and len(r.seen) == 1


def test_km_recorded_lower_than_read_is_still_blocked():
    """Tehlikeli yön: kayıtta makul ama düşük km (emsal şişer, 'km yüksek' kontrolü susar): 🟡'ye düşer."""
    repo = FakeRepo()
    out = lr.verify_candidates(repo, reader(repo), [ev(km=30000)])  # kayıt 30.000, yapay zekâ metinden 53.000 okudu
    assert out == [] and repo.downgraded == [("L1", ["okuma_km"])]


def test_suspicious_recorded_km_is_only_a_warning_even_if_read_is_higher():
    repo = FakeRepo()
    out = lr.verify_candidates(repo, reader(repo), [ev(km=215)])  # 2016 araçta 215 km zaten "bilinmiyor" sayılıyor
    assert len(out) == 1 and not repo.downgraded and lr.KM_READ_WARNING in out[0].warnings


def test_km_difference_still_blocks_estimated_tier():
    from domain.profit import Tier
    repo = FakeRepo()
    e = ev(km=90000)
    e.profit = type("P", (), {"tier": Tier.ESTIMATED})()
    out = lr.verify_candidates(repo, reader(repo), [e])
    assert out == [] and repo.downgraded and "okuma_km" in repo.downgraded[0][1]


def test_llm_failure_keeps_candidate_with_unchecked_note():
    repo = FakeRepo()
    out = lr.verify_candidates(repo, reader(repo, data=None, err="http 500"), [ev()])
    assert len(out) == 1 and lr.UNCHECKED in out[0].warnings and not repo.downgraded


def test_unreachable_price_is_flagged_not_dropped():
    repo = FakeRepo()
    out = lr.verify_candidates(repo, reader(repo, data={**GOOD, "fiyat": None, "fiyat_alinti": None}), [ev()])
    assert len(out) == 1 and lr.UNCONFIRMED in out[0].warnings


def test_sites_and_missing_reader_are_untouched():
    repo = FakeRepo()
    r = reader(repo)
    assert len(lr.verify_candidates(repo, r, [ev("web")])) == 1 and r.seen == []
    assert len(lr.verify_candidates(repo, None, [ev()])) == 1


def test_daily_budget_stops_reading():
    repo = FakeRepo()
    r = reader(repo, cost=lr.DAILY_BUDGET_USD)
    assert r.read(TEXT) is not None
    assert r.read(TEXT) is None and "bütçe" in r.last_error and len(r.seen) == 1


def test_listing_fields_requires_brand_year_price_and_rejects_sold_or_credit(monkeypatch):
    monkeypatch.setattr(lr, "gbp_rate", lambda c: 1.0)
    from domain.llm_read import parse_llm_read
    ok = lr.listing_fields(parse_llm_read(GOOD, TEXT))
    assert ok["extraction_by"] == "llm" and ok["price_gbp"] == 7500 and ok["brand"] == "Honda"
    assert lr.listing_fields(parse_llm_read({**GOOD, "satildi": True}, TEXT)) is None
    assert lr.listing_fields(parse_llm_read({**GOOD, "pesinat_veya_kredi_devri": True}, TEXT)) is None
    assert lr.listing_fields(parse_llm_read({**GOOD, "fiyat": None, "fiyat_alinti": None}, TEXT)) is None
    assert lr.listing_fields(None) is None


def test_facebook_post_with_two_prices_is_read_by_llm_and_marked(monkeypatch):
    monkeypatch.setattr(lr, "gbp_rate", lambda c: 1.0)
    monkeypatch.setattr(cf, "gbp_rate", lambda c: 1.0)
    repo = FakeRepo()
    text = "2016 Honda Fit otomatik 53.000 km\n7.500£\n8.200£ (takaslı)\nGirne"
    assert cf.listing_data(RawGroupPost("1", "u", None, text, "g"), {"name": "x"}) is None  # kural okuyamıyor
    good = {**GOOD, "yil_alinti": "2016 Honda Fit", "fiyat_alinti": "7.500£"}
    now = datetime(2026, 10, 2, 9, tzinfo=timezone.utc)
    src = [dict(id="G1", name="G", url="https://www.facebook.com/groups/1/", last_checked_at=None)]
    fetch = lambda token, urls, hours, n: ([RawGroupPost("1", "u1", now, text, "https://www.facebook.com/groups/1")], 0.01, 1)
    res = cf.collect_facebook_groups(repo, "t", src, fetch=fetch, now=now, reader=reader(repo, data=good))
    row = repo.rows[("G1", "1")]
    assert res["G"].llm_read == 1 and row["extraction_by"] == "llm" and row["price_gbp"] == 7500


def test_free_text_site_listing_is_verified_but_structured_site_is_not():
    repo = FakeRepo()
    r = reader(repo)
    structured = ev("web", extraction_by="parser")
    assert len(lr.verify_candidates(repo, r, [structured])) == 1 and r.seen == []        # JSON-LD'li site: gerek yok
    free = ev("web", extraction_by="parser_serbest", price_amount=4500)                  # serbest metin: okutulur, fiyat uyuşmuyor
    assert lr.verify_candidates(repo, r, [free]) == [] and len(r.seen) == 1


# --- 🟢 fırsat notu (deal_notes): okuyucuyla aynı günlük bütçe + (ilan, fiyat) başına tek soru ---
DAY = datetime(2026, 10, 2, 9, tzinfo=timezone.utc)
SPEND = "llm_spend:2026-10-02"
SUSPICIOUS = {"gercek_firsat_mi": False, "risk_notlari": ["hasar kaydı olabilir"], "fiyat_yorumu": "ucuz", "sorulacak_sorular": []}


def green(price=5000):
    e = notify_ev(1)
    e.listing.update(raw_text="2015 Toyota Vitz 5.000 STG", price_gbp=price)
    return e


def deal_call(note=SUSPICIOUS, err=None, cost=0.002):
    def call(key, model, text, summary):
        call.seen.append((text, summary))
        if isinstance(note, Exception):
            raise note
        return note, err, cost
    call.seen = []
    return call


def test_deal_note_cost_goes_to_the_shared_daily_budget_and_the_note_is_cached():
    repo, call = FakeRepo(), deal_call()
    assert lr.deal_notes(repo, [green()], "k", "m", call=call, now=DAY) == {1: SUSPICIOUS}
    assert call.seen == [("2015 Toyota Vitz 5.000 STG", "Emsal: 5 ilan, medyan £8000, aralık £7000–£9000")]
    assert repo.state[SPEND] == "0.00200" and reader(repo).spent_today() == 0.002  # okuyucu da aynı harcamayı görür
    again = lr.deal_notes(repo, [green()], "k", "m", call=call, now=DAY)  # sonraki tur: aynı ilan, aynı fiyat
    assert again == {1: SUSPICIOUS} and len(call.seen) == 1 and repo.state[SPEND] == "0.00200"  # önbellekten: soru ve ücret yok
    assert "⚠️ Yapay zekâ şüpheli buldu: hasar kaydı olabilir" in notify.format_alert(green(), again[1])  # önbellekteki not mesaja aynen girer


def test_deal_note_is_asked_again_when_the_price_changes():
    repo, call = FakeRepo(), deal_call()
    lr.deal_notes(repo, [green(5000)], "k", "m", call=call, now=DAY)
    lr.deal_notes(repo, [green(4800)], "k", "m", call=call, now=DAY)
    assert len(call.seen) == 2 and repo.state[SPEND] == "0.00400"
    assert {k for k in repo.state if k.startswith("deal_note:")} == {"deal_note:1:t5000", "deal_note:1:t4800"}


def test_deal_note_budget_exhausted_no_call_and_the_alert_still_goes(monkeypatch):
    repo, call = FakeRepo(), deal_call()
    repo.state[SPEND] = f"{lr.DAILY_BUDGET_USD:.5f}"  # bugünkü bütçe (okuyucuyla ortak) doldu
    notes = lr.deal_notes(repo, [green()], "k", "m", call=call, now=DAY)
    assert notes == {} and call.seen == [] and repo.state == {SPEND: "0.40000"}  # soru yok, önbelleğe 'soruldu' yazılmadı
    sent = patch_api(monkeypatch, {})
    assert notify.send_alerts(NotifyRepo(["a"]), "t", [green()], notes) == 1 and sent == ["a"]  # bildirim notsuz gider
    tomorrow = datetime(2026, 10, 3, 9, tzinfo=timezone.utc)
    assert lr.deal_notes(repo, [green()], "k", "m", call=call, now=tomorrow) == {1: SUSPICIOUS}  # ertesi gün yeni bütçe: sorulur


def test_deal_note_failure_is_asked_once_and_never_raises():
    repo, call = FakeRepo(), deal_call(note=None, err="http 500", cost=0.0)
    assert lr.deal_notes(repo, [green()], "k", "m", call=call, now=DAY) == {}
    assert repo.state["deal_note:1:t5000"] == "null"
    assert lr.deal_notes(repo, [green()], "k", "m", call=call, now=DAY) == {} and len(call.seen) == 1  # hata da "soruldu" sayılır
    boom = deal_call(note=RuntimeError("model yok"))
    assert lr.deal_notes(FakeRepo(), [green()], "k", "m", call=boom, now=DAY) == {}  # çağrı patlarsa: not yok, hata fırlamaz

    class DeadRepo(FakeRepo):
        def get_state(self, k, default=None):
            raise RuntimeError("veritabanı yok")

    assert lr.deal_notes(DeadRepo(), [green()], "k", "m", call=call, now=DAY) == {}


def test_check_deal_caps_tokens_masks_phone_and_returns_cost(monkeypatch):
    """Fırsat notu çağrısı: max_tokens tavanı var, telefon maskelenir, (veri, hata, maliyet) döner (maliyet yanıtta yoksa okuyucuyla aynı
    tahmini maliyet); ağ/HTTP hatası istisna değil kısa neden ve 0 ücrettir."""
    body = {"choices": [{"message": {"content": 'Not: {"gercek_firsat_mi": true}'}}], "usage": {"cost": 0.0012}}
    sent, reply = [], {"status": 200, "body": body}

    class Resp:
        def __init__(self):
            self.status_code = reply["status"]

        def json(self):
            return reply["body"]

    def post(url, headers=None, json=None, timeout=None):
        sent.append(json)
        if reply["status"] is None:
            raise httpx.ConnectError("bağlanamadı")
        return Resp()

    monkeypatch.setattr(openrouter.httpx, "post", post)
    assert openrouter.check_deal("k", "m", "Vitz, tel 0533 123 45 67", "Emsal: 9 ilan") == ({"gercek_firsat_mi": True}, None, 0.0012)
    assert sent[0]["max_tokens"] == openrouter.CHECK_MAX_TOKENS == 1500
    assert "0533" not in sent[0]["messages"][0]["content"] and "[tel]" in sent[0]["messages"][0]["content"]
    reply["body"] = {"choices": [{"message": {"content": "JSON yok"}}]}
    assert openrouter.check_deal("k", "m", "x", "y") == (None, "json yok", openrouter.CALL_COST_USD)
    reply["status"] = 429
    assert openrouter.check_deal("k", "m", "x", "y") == (None, "http 429", 0.0)
    reply["status"] = None
    assert openrouter.check_deal("k", "m", "x", "y") == (None, "ConnectError", 0.0)
