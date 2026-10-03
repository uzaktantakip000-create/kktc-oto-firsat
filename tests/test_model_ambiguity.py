"""Model anahtarı farklı araçları birleştiriyorsa 🟢 yok (geçici yama, İş 9'a kadar) + mükerrerde bilinmeyen model."""
import pytest

from application.evaluate import evaluate_new
from domain.data_gate import GAP_LABELS
from domain.duplicates import same_car
from domain.model_ambiguity import model_ambiguous
from domain.profit import Tier
from tests.test_duplicates import car as dcar
from tests.test_evaluate import FakeRepo, car


def L(brand, model_norm, model="", year=2016):
    return {"brand_norm": brand, "model_norm": model_norm, "model": model, "year": year}


@pytest.mark.parametrize("brand,key,model,year,expected", [
    ("Mazda", "cx", "CX-3", 2017, True), ("Mazda", "cx", "CX-5", 2019, True), ("Mazda", "cx", "CX-30", 2020, True),
    ("Honda", "cr", "CR-V", 2015, True), ("Honda", "cr", "CR-Z", 2012, True),
    ("Volkswagen", "t", "T-Roc", 2020, True), ("Volkswagen", "t", "T-Cross", 2021, True),
    ("Toyota", "yaris", "Yaris Cross", 2019, True),     # adında Cross var: yıl ne olursa olsun
    ("Toyota", "corolla", "Corolla Cross", 2022, True),
    ("Toyota", "yaris", "Yaris", 2021, True),           # 2020+ düz Yaris: havuzu Cross ile karışık
    ("Toyota", "corolla", "Corolla", 2023, True),
    ("Toyota", "yaris", "Yaris", 2012, False),          # eski Yaris: pencerede Cross yok
    ("Toyota", "corolla", "Corolla 1.6", 2014, False),
    ("Toyota", "vitz", "Vitz", 2015, False), ("Mazda", "demio", "Demio", 2014, False), ("Honda", "civic", "Civic", 2016, False),
])
def test_ambiguous_keys(brand, key, model, year, expected):
    assert model_ambiguous(L(brand, key, model, year)) is expected


def test_unknown_values_do_not_crash():
    assert model_ambiguous({}) is False
    assert model_ambiguous({"brand_norm": "Toyota", "model_norm": "yaris", "model": None, "year": None}) is False


def test_cheap_cx_is_not_strong_but_cheap_vitz_is():
    pool = [car(f"p{i}", p, brand_norm="Mazda", model_norm="cx", model="CX-5") for i, p in enumerate([8000, 8200, 8400, 8600, 8800, 9000, 9200, 9400])]
    (ev,) = evaluate_new(FakeRepo([car("t", 5000, brand_norm="Mazda", model_norm="cx", model="CX-3")], pool))
    assert ev.profit.tier is Tier.NEGOTIABLE  # %40 ucuz görünse de 🟢 değil
    ok = [car(f"q{i}", p) for i, p in enumerate([8000, 8200, 8400, 8600, 8800, 9000, 9200, 9400])]
    (ev2,) = evaluate_new(FakeRepo([car("t", 5000)], ok))
    assert ev2.profit.tier is Tier.STRONG  # ayrışık model etkilenmez


def test_gap_is_saved_with_the_evaluation():
    pool = [car(f"p{i}", 8000 + 200 * i, brand_norm="Mazda", model_norm="cx", model="CX-5") for i in range(8)]
    repo = FakeRepo([car("t", 5000, brand_norm="Mazda", model_norm="cx", model="CX-3")], pool)
    evaluate_new(repo)
    saved = repo.saved[0][1]
    assert saved["tier"] == "pazarlik" and "model_belirsiz" in saved["red_flags"]
    assert "model_belirsiz" in GAP_LABELS


# --- mükerrer: bilinmeyen model "aynı model" sayılmaz ---

def test_unknown_model_is_not_merged_by_price_and_km_alone():
    a, b = dcar(1, model_norm=None, seller_phone=None, km=80_250), dcar(2, model_norm=None, seller_phone=None, km=80_250, price_gbp=6100.0)
    assert not same_car(a, b)


def test_unknown_model_with_same_phone_still_merges():
    assert same_car(dcar(1, model_norm=None), dcar(2, model_norm=None, price_gbp=5800.0))


def test_known_model_rules_unchanged():
    assert same_car(dcar(1, seller_phone=None, km=80_250), dcar(2, seller_phone=None, km=80_250, price_gbp=6100.0))


def test_kktcarabam_split_brand_keys_never_alert():
    # KKTCarabam etiketi ilk boşluktan bölünüyor: "Mercedes - Benz GLE" → model "benz", "Land Rover Range Rover" → "rover"
    from domain.model_ambiguity import model_ambiguous
    assert model_ambiguous({"brand_norm": "Mercedes-Benz", "model_norm": "benz", "year": 2019, "model": "- Benz GLE"})
    assert model_ambiguous({"brand_norm": "Land Rover", "model_norm": "rover", "year": 2018, "model": "Rover Evoque"})
    assert not model_ambiguous({"brand_norm": "Mercedes-Benz", "model_norm": "gle", "year": 2019, "model": "GLE"})  # doğru anahtar etkilenmez
