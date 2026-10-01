from domain.red_flags import blocking_flags, urgency_signals, warning_flags


def test_blocking():
    assert "hasarlı" in blocking_flags("Hafif hasarlı araç, kazalıdır")
    assert "as is / parça" in blocking_flags("Çıkma motor takıldı")
    assert blocking_flags("Motor ve şanzıman sorunsuzdur, temizdir") == []


def test_warning_and_urgency():
    assert "modifiyeli" in warning_flags("Stage 2 yapıldı 370 HP")
    assert urgency_signals("MEZUN OLDUM, ACİL satılık, pazarlık payı var") == ["acil", "mezun/ayrılıyor", "pazarlık"]


def test_clean_text():
    assert blocking_flags("Full servis bakımlı, takas yok") == []
