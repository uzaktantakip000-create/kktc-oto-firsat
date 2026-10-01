from domain.steering import steering_from_text


def test_explicit_mentions():
    assert steering_from_text("Sol direksiyon, temiz araç") == "LHD"
    assert steering_from_text("Sağ Direksiyon otomatik") == "RHD"
    assert steering_from_text("LHD import") == "LHD"


def test_unknown_or_conflicting_is_none():
    assert steering_from_text("M Sport direksiyon, çok fonksiyonlu direksiyon") is None
    assert steering_from_text("sol direksiyon ya da sağ direksiyon yapılır") is None
    assert steering_from_text(None) is None
