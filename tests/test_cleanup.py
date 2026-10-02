import pytest

from domain.normalize import is_car_brand, normalize_brand, normalize_model, reclassify_non_car
from domain.quality import find_quarantine
from domain.red_flags import blocking_flags, warning_flags
from infrastructure.db.repository import Repository


@pytest.mark.parametrize("brand,model,expected", [
    ("Honda", "CBR 1000 RR", "Motosiklet"), ("Honda", "CB 500X", "Motosiklet"), ("Honda", "PCX125", "Motosiklet"),
    ("Honda", "CRF1100L Africa Twin", "Motosiklet"), ("Honda", "NC 750 X", "Motosiklet"), ("Honda", "Forza 300", "Motosiklet"),
    ("BMW", "F 750 GS", "Motosiklet"), ("BMW", "R 1300 GS Trophy", "Motosiklet"), ("BMW", "G 310 R", "Motosiklet"),
    ("BMW", "M 1000 RR Competition", "Motosiklet"), ("BMW", "C 400 X", "Motosiklet"),
    ("Suzuki", "Burgman 400", "Motosiklet"), ("Suzuki", "V-Strom 800 DE", "Motosiklet"), ("Suzuki", "GSX-R 750", "Motosiklet"),
    ("Peugeot", "Speedfight", "Motosiklet"), ("Mercedes", "Actros", "Kamyon & Kamyonet"), ("Honda", "Tekne", "Balikci Teknesi"),
    # arabalar aynen kalır
    ("Honda", "Civic", "Honda"), ("Honda", "CR-V 2.0 Hybrid", "Honda"), ("Honda", "CRX", "Honda"), ("Honda", "Integra", "Honda"),
    ("Honda", "Fit", "Honda"), ("Honda", "N-Box", "Honda"), ("BMW", "F30", "BMW"), ("BMW", "M Serisi M4 Competition", "BMW"),
    ("BMW", "3 Serisi 320i", "BMW"), ("BMW", "X5", "BMW"), ("BMW", "i Serisi i3", "BMW"), ("Suzuki", "Swift", "Suzuki"),
    ("Suzuki", "Vitara", "Suzuki"), ("Suzuki", "Alto Lapin", "Suzuki"), ("Mercedes", "Sprinter", "Mercedes-Benz"),
])
def test_reclassify_via_norm_keys(brand, model, expected):
    got = Repository.norm_keys(brand, model)["brand_norm"]
    assert got == expected
    if expected in ("Motosiklet", "Kamyon & Kamyonet", "Balikci Teknesi"):
        assert not is_car_brand(got)
    else:
        assert is_car_brand(got)


def test_reclassify_keeps_raw_model_norm_and_none_safe():
    assert Repository.norm_keys("Honda", "CBR 1000 RR") == {"brand_norm": "Motosiklet", "model_norm": "cbr"}
    assert reclassify_non_car(None, None) is None
    assert reclassify_non_car("Toyota", "corolla") == "Toyota"
    assert normalize_model("BMW", "M 1000 RR") == "m"  # model_norm aynı, ham modele bakılır


def row(i, price, year=2015, km=80_000):
    return dict(id=i, brand_norm="Toyota", model_norm="vitz", year=year, km=km, price_gbp=price)


def test_placeholder_price_is_quarantined():
    q = find_quarantine([row("a", 1.0), row("b", 7.0), row("c", 8.44), row("d", 4500), row("e", None)], 2026)
    assert q == {"a": "fiyat_yer_tutucu", "b": "fiyat_yer_tutucu", "c": "fiyat_yer_tutucu"}


@pytest.mark.parametrize("text", [
    "araç pert kayıtlı", "ağır hasarlı geçmişi var", "ağır hasar kaydı var", "airbag patlak", "airbagler açık", "airbag yok",
    "vuruk araç", "su basmış", "sel hasarlı", "pert total",
])
def test_new_blocking_words(text):
    assert blocking_flags(text), text


@pytest.mark.parametrize("text", [
    "hasarsız, pert kaydı yok", "pert kaydı yoktur", "pert değil", "ağır hasarsız temiz araç", "ağır hasar kaydı yok",
    "değişen yok boyasız", "airbag sağlam", "su basmamış temiz", "expert onaylı",
])
def test_negations_do_not_block(text):
    flags = blocking_flags(text)
    assert "pert/ağır hasar" not in flags and "airbag açık/patlak" not in flags and "vuruk/su basmış" not in flags, text


def test_degisen_var_is_warning_only():
    assert "değişen var" in warning_flags("2 parça değişen var")
    assert "değişen var" in warning_flags("Değişenli araç")
    assert "değişen var" not in warning_flags("değişen yok")
    assert not blocking_flags("2 parça değişen var")
