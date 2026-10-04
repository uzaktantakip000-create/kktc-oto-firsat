"""Model anahtarı kuralları (Adım 6a): gerçek ham adlardan oluşan altın dosya + okunur örnekler + araç/yardımcı kontrolleri."""
import csv
from pathlib import Path

import pytest

from domain.model_keys import model_key
from infrastructure.db.repository import Repository

GOLDEN = list(csv.DictReader((Path(__file__).parent / "fixtures" / "model_golden.csv").open(encoding="utf-8", newline="")))


def keys(brand, model):
    k = Repository.norm_keys(brand, model)
    return k["brand_norm"], k["model_norm"]


def test_golden_file_is_big_and_anonymous():
    assert len(GOLDEN) >= 1200 and {"brand", "model", "brand_norm", "model_norm"} == set(GOLDEN[0])
    assert all(len(r["model"]) <= 80 for r in GOLDEN)


def test_every_real_name_maps_to_its_golden_key():
    """Kural değişirse (ya da yanlışlıkla bozulursa) hangi gerçek ad hangi anahtardan hangisine geçti açıkça görünür."""
    wrong = [(r["brand"], r["model"], (r["brand_norm"], r["model_norm"] or None), keys(r["brand"], r["model"]))
             for r in GOLDEN if keys(r["brand"], r["model"]) != (r["brand_norm"], r["model_norm"] or None)]
    assert wrong == [], wrong[:10]


@pytest.mark.parametrize("brand,model,expected", [
    ("Mazda", "CX-5 2.2 Skyactiv-D", ("Mazda", "cx-5")), ("Mazda", "CX-3", ("Mazda", "cx-3")), ("Mazda", "/ CX3", ("Mazda", "cx-3")),
    ("Mazda", "CX-30", ("Mazda", "cx-30")), ("Mazda", "Demio 1.3i", ("Mazda", "demio")), ("Mazda", "2 1.5 Sky-G", ("Mazda", "2")),
    ("Honda", "Fit Aria", ("Honda", "fit aria")), ("Honda", "Fit 1.3", ("Honda", "fit")), ("Honda", "CR-V 2.0i", ("Honda", "cr-v")),
    ("Honda", "CR-Z GT", ("Honda", "cr-z")), ("Honda", "ZR-V", ("Honda", "zr-v")), ("Honda", "HR-V", ("Honda", "hr-v")),
    ("Volkswagen", "T-Roc", ("Volkswagen", "t-roc")), ("Volkswagen", "T-Cross", ("Volkswagen", "t-cross")),
    ("Toyota", "Yaris Cross", ("Toyota", "yaris cross")), ("Toyota", "Yaris 1.5 Hybrid", ("Toyota", "yaris")),
    ("Toyota", "Corolla  Cross Cross", ("Toyota", "corolla cross")), ("Toyota", "Corolla Axio", ("Toyota", "axio")),
    ("Toyota", "Corolla 1.8 Hybrid", ("Toyota", "corolla")), ("Toyota", "C-HR 1.8 Hybrid", ("Toyota", "c-hr")),
    ("Mitsubishi", "L 200 4x4", ("Mitsubishi", "l200")), ("Mitsubishi", "Lancer Evo 8", ("Mitsubishi", "lancer evo")),
    ("Mitsubishi", "Lancer 1.5", ("Mitsubishi", "lancer")), ("Nissan", "X-Trail", ("Nissan", "x-trail")),
    ("Mercedes-Benz", "GLE Coupe 400 d", ("Mercedes-Benz", "gle coupe")), ("Mercedes-Benz", "GLE 300 d AMG", ("Mercedes-Benz", "gle")),
    ("Mercedes - Benz", "- Benz GLE", ("Mercedes-Benz", "gle")), ("Mercedes", "/ C180", ("Mercedes-Benz", "c")),
    ("Mercedes-Benz", "C Serisi C 180", ("Mercedes-Benz", "c")), ("BMW", "Z Serisi Z4", ("BMW", "z4")),
    ("BMW", "M Serisi M4 Competition", ("BMW", "m4")), ("BMW", "320i Cabrio", ("BMW", "3")),
    ("Ford", "Transit Custom", ("Ford", "transit custom")), ("Ford", "Transit", ("Ford", "transit")),
    ("Peugeot", "307 CC", ("Peugeot", "307 cc")), ("Peugeot", "307 Cabrio", ("Peugeot", "307 cc")), ("Peugeot", "307 SW", ("Peugeot", "307 sw")),
    ("Mini", "Cooper Cabrio", ("Mini", "cooper cabrio")), ("Mini", "Cooper S 1.6", ("Mini", "cooper")),
    ("Land", "Rover Range Rover Evoque", ("Land Rover", "evoque")), ("Land", "Rover Range Rover", ("Land Rover", "range rover")),
])
def test_family_keys(brand, model, expected):
    assert keys(brand, model) == expected


def test_no_rule_means_old_first_word_behavior_and_nothing_is_lost():
    assert model_key("Toyota", "vitz 1.3") is None and keys("Toyota", "Vitz 1.3") == ("Toyota", "vitz")
    assert model_key(None, "anything") is None and model_key("Unknown Brand", "x y") is None
    assert keys("Toyota", "") == ("Toyota", None) and keys("Toyota", None) == ("Toyota", None)
