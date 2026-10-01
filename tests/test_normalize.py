from domain.normalize import fold, normalize_brand, normalize_model


def test_brand():
    assert normalize_brand("Bmw") == "BMW"
    assert normalize_brand("Mercedes") == "Mercedes-Benz"
    assert normalize_brand("Ni̇ssan") == "Nissan"
    assert normalize_brand("toyota") == "Toyota"
    assert normalize_brand("Vauxhall") == "Opel"


def test_mercedes_classes():
    for m in ("C 180", "C180", "C 200", "C 220d"):
        assert normalize_model("Mercedes-Benz", m) == "c"
    assert normalize_model("Mercedes-Benz", "E Serisi") == "e"
    assert normalize_model("Mercedes-Benz", "GLC 220d") == "glc"
    assert normalize_model("Mercedes-Benz", "CLA 180") == "cla"
    assert normalize_model("Mercedes-Benz", "A180d AMG Line") == "a"


def test_bmw_series():
    assert normalize_model("BMW", "520d M Sport") == "5"
    assert normalize_model("BMW", "116i") == "1"
    assert normalize_model("BMW", "1.18i") == "1"
    assert normalize_model("BMW", "X5") == "x5"
    assert normalize_model("BMW", "320i Cabrio") == "3"


def test_generic():
    assert normalize_model("Toyota", "vıtz") == "vitz"
    assert normalize_model("Suzuki", "Swift Style Paket") == "swift"
    assert normalize_model("Ford", "Transit Connect") == "transit connect"
    assert fold("Çıkışlı") == "cikisli"


def test_mercedes_benz_split_across_brand_and_model():
    assert normalize_model("Mercedes-Benz", "Benz C200") == "c"
    assert normalize_model("Mercedes-Benz", "Mercedes Benz E220") == "e"


def test_land_rover_families_not_mixed():
    assert normalize_model("Land Rover", "Rover Evoque") == "evoque"
    assert normalize_model("Land Rover", "Range Rover Sport") == "range rover sport"
    assert normalize_model("Land Rover", "Range Rover Vogue") == "range rover"
    assert normalize_model("Land Rover", "Discovery Sport") == "discovery sport"
    assert normalize_model("Land Rover", "Discovery 4") == "discovery"


def test_brand_hyphen_spacing_and_non_car_categories():
    from domain.normalize import is_car_brand, normalize_brand
    assert normalize_brand("Mercedes - Benz") == normalize_brand("Mercedes-Benz") == normalize_brand("Mercedes") == "Mercedes-Benz"
    assert is_car_brand("Toyota") and not is_car_brand("Yamaha") and not is_car_brand("Surat Teknesi") and not is_car_brand(None)
