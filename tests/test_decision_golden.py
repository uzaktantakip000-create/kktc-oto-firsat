"""Karar altın dosyası (tests/fixtures/decision_golden.json): 49 GERÇEK ilan vakası + her birinin "olması gereken" etiketi.
Etiketler: yesil (🟢 FIRSAT) · kontrol (🟠 KONTROL ET) · yok (bildirim yok). Dosya canlı veriden 03.10.2026'da alındı
(entrypoints/golden_snapshot.py); kimlik bilgisi içermez. Etiketleri iki bağımsız okuyucu yazdı: biri çakışırsa vaka `strict: false`.
Sahip kararı (04.10.2026): km eksik/şüpheli tek başına engel değil (kâr şartı %20 aynı kalır): bu yüzden etiketi yalnızca km yüzünden
'kontrol' olan g17/g18/g19/g21/g48'in olması gereken etiketi `owner_override` ile 'yesil'dir. Bu testler (1) dosyanın bütünlüğünü ve gizliliğini, (2) bugünkü sistemin 'yok olmalı' vakalarda 🟢 üretmediğini, (3) bugünkü
eksiklerin (olması gereken 🟠'ların henüz gelmemesi) yalnızca AZALABİLECEĞİNİ (ratchet) doğrular. AlertPolicy v2 (İş 6) ve model
adı tablosu (İş 8) bu dosyaya karşı ölçülür."""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from application.evaluate import apply_send_floor, evaluate_new
from domain.comparables import find_market
from domain.profit import Tier
from domain.red_flags import BLOCKING, blocking_flags
from domain.settings import Settings
from entrypoints.golden_snapshot import case_pool, case_target
from tests.test_evaluate import FakeRepo

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "decision_golden.json").read_text())
CASES = GOLDEN["cases"]
LABELS = {"yesil", "kontrol", "yok"}
NOW = datetime.now(timezone.utc)
FORBIDDEN_KEYS = {"seller_phone", "phone", "telefon", "url", "raw_text", "seller_handle", "handle", "name", "text"}

# Bugünkü sistemin "olması gerekenden" ayrıştığı STRICT vakalar. Bu liste yalnızca KÜÇÜLEBİLİR: sistem düzeldikçe satır silinir.
# (Bugün hepsi "olması gereken 🟠 KONTROL ET, sistem bildirim yok": 🟠 yeni tasarımla (İş 6) gelecek.)
KNOWN_MISMATCHES = {
    ("g03", "kontrol"), ("g07", "kontrol"), ("g25", "kontrol"), ("g32", "kontrol"),
}


def want(case: dict) -> str:
    """Olması gereken etiket: iki okuyucunun etiketi (`expected`); sahip sonradan karar verdiyse `owner_override` (okuyucu kaydı değişmez)."""
    return (case.get("owner_override") or {}).get("label", case["expected"])

# Engel işaretini (ilan metnindeki anahtar kelime) geri üretmek için örnek ifade: dosyada ilan metni saklanmaz
FLAG_PHRASE = {"pert/ağır hasar": "pert", "airbag açık/patlak": "airbag patlak", "vuruk/su basmış": "vuruk", "hasarlı": "hasarlı",
               "motor/şanzıman sorunlu": "motor sorunlu", "as is / parça": "parça araç", "kira/taksit": "peşinat",
               "gümrüksüz/evraksız": "gümrüksüz"}


def current_label(case: dict) -> str:
    """Bugünkü hattın (evaluate_new + emsal kapısı) bu vaka için kararı. Değer tablosu yok: bugünkü 🟢 yalnızca emsal medyanından gelir;
    🟠 (yöntem B) zaten gönderilmediği için 'yok' sayılır."""
    l = case["listing"]
    text = " ".join(FLAG_PHRASE[f] for f in case["text_flags"]["blocking"])
    listing = {**l, "id": case["id"], "raw_text": text, "first_seen_at": NOW, "ref_date": NOW, "is_active": True, "duplicate_of": None,
               "seller_phone": None, "currency": l["currency"]}
    evs = evaluate_new(FakeRepo([listing], case_pool(GOLDEN, case, NOW)), Settings(), book=None)
    if not evs:
        return "yok"
    return "yesil" if evs[0].profit.tier is Tier.STRONG and apply_send_floor(evs) else "yok"


# --- 1. dosya bütünlüğü ve gizlilik ---

def _walk(o, path=()):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from _walk(v, path + (k,))
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v, path)
    else:
        yield path, o


def test_fixture_is_anonymous():
    for path, value in _walk(GOLDEN):
        assert not set(path) & FORBIDDEN_KEYS, f"yasak alan: {path}"
        if isinstance(value, str):
            assert "http" not in value and "@" not in value, path
            assert not re.search(r"\d{9,}", value), f"telefon benzeri dizi: {path}"
    for rows in GOLDEN["pools"].values():
        assert all(re.fullmatch(r"s\d+", r["seller"]) for r in rows)  # satıcılar yeniden adlandırılmış
    assert all(re.fullmatch(r"g\d\d", c["id"]) for c in CASES)


def test_fixture_shape_and_labels():
    assert 45 <= len(CASES) <= 60 and len({c["id"] for c in CASES}) == len(CASES)
    assert {c["expected"] for c in CASES} == LABELS  # üç etiket de var
    for c in CASES:
        assert c["expected"] in LABELS and isinstance(c["strict"], bool) and len(c["why"]) >= 15
        assert c["second_opinion"]["label"] in LABELS
        assert c["strict"] == (c["expected"] == c["second_opinion"]["label"] and "belirsiz" not in (c["confidence"], c["second_opinion"]["confidence"]))
        assert c["pool_key"] in GOLDEN["pools"] and c["synthetic"] == (c["origin"] == "sentetik")
    assert sum(c["strict"] for c in CASES) >= 35  # çoğu vaka iki okuyucuda da aynı etiket


def test_old_alerts_are_in_the_file():
    assert sum(c["origin"] == "eski_yesil" for c in CASES) == 7 and sum(c["origin"] == "eski_turuncu" for c in CASES) == 3


def test_blocking_phrases_reproduce_flags():
    for c in CASES:
        text = " ".join(FLAG_PHRASE[f] for f in c["text_flags"]["blocking"])
        assert blocking_flags(text) == c["text_flags"]["blocking"], c["id"]
    assert set(FLAG_PHRASE) == set(BLOCKING)


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_market_stats_reproduce_from_snapshot(case):
    """Kayıtlı emsal havuzundan bugünkü find_market aynı piyasa özetini üretir (kod ya da dosya sessizce kaymasın)."""
    m = find_market(case_target(case), case_pool(GOLDEN, case, NOW), Settings(), NOW)
    st = case["stats"]
    if st is None:
        assert m is None
        return
    assert m is not None and m.n == st["n"] and m.year_span == st["year_span"]
    assert abs(m.median_gbp - st["median_gbp"]) < 1 and abs(m.archived_share - st["archived_share"]) < 0.002
    assert abs((case["listing"]["price_gbp"] / m.median_gbp - 1) * 100 - st["price_vs_median_pct"]) < 0.2


# --- 2. bugünkü sistem ---

def test_bad_data_and_blocked_text_never_alert_today():
    for c in CASES:
        if c["origin"] in ("bozuk_veri", "ozel_durum"):
            assert c["expected"] == "yok" and current_label(c) == "yok", c["id"]


def test_system_never_greens_a_case_that_should_not_be_green():
    """Sahibin en çok nefret ettiği hata: yanlış 🟢. Bugünkü hat, olması gereken 'yesil' olmayan hiçbir vakada 🟢 vermez."""
    wrong = [c["id"] for c in CASES if want(c) != "yesil" and current_label(c) == "yesil"]
    assert wrong == []


def test_owner_overrides_are_documented_and_limited():
    """Sahip kararıyla değişen etiketler açıkça kayıtlı (etiket, tarih, neden) ve azdır; okuyucu kaydı (expected/second_opinion) korunur."""
    over = [c for c in CASES if "owner_override" in c]
    assert {c["id"] for c in over} == {"g17", "g18", "g19", "g21", "g48"}
    for c in over:
        o = c["owner_override"]
        assert o["label"] in LABELS and o["date"] == "2026-10-04" and len(o["why"]) >= 30
        assert c["expected"] == "kontrol" and c["second_opinion"]["label"] == "kontrol"  # okuyucu kaydı değişmedi


def test_clean_synthetic_control_is_green_today():
    """Kapı fazla sıkı değil: gerçek havuzda %30 ucuz, temiz ve tablo uyumlu araç 🟢 olabiliyor."""
    g49 = next(c for c in CASES if c["id"] == "g49")
    assert g49["expected"] == "yesil" and g49["strict"] and current_label(g49) == "yesil"


def test_known_mismatches_only_shrink():
    """Ratchet: olması gereken etiketten ayrışan STRICT vakalar KNOWN_MISMATCHES ile birebir aynı olmalı. Yeni ayrışma = gerileme;
    kapanan ayrışma = listeden silinmeli (liste yalnızca küçülür)."""
    now_missing = {(c["id"], want(c)) for c in CASES if c["strict"] and current_label(c) != want(c)}
    assert not (now_missing - KNOWN_MISMATCHES), f"yeni gerileme: {sorted(now_missing - KNOWN_MISMATCHES)}"
    assert not (KNOWN_MISMATCHES - now_missing), f"kapandı, listeden sil: {sorted(KNOWN_MISMATCHES - now_missing)}"
