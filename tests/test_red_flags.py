from domain.red_flags import blocking_flags, cost_unknown, urgency_signals, warning_flags


def test_blocking():
    assert "hasarlı" in blocking_flags("Hafif hasarlı araç, kazalıdır")
    assert "as is / parça" in blocking_flags("Çıkma motor takıldı")
    assert blocking_flags("Motor ve şanzıman sorunsuzdur, temizdir") == []


def test_warning_and_urgency():
    assert "modifiyeli" in warning_flags("Stage 2 yapıldı 370 HP")
    assert urgency_signals("MEZUN OLDUM, ACİL satılık, pazarlık payı var") == ["acil", "mezun/ayrılıyor", "pazarlık"]


def test_clean_text():
    assert blocking_flags("Full servis bakımlı, takas yok") == []


def test_unquantified_cost():
    """24.10.2026: tutarı yazmayan masraf 🟢'yi en fazla 🟡 yapar; küçültücü/tanımlayıcısı olan masraf yalnız uyarı."""
    assert cost_unknown("2013 Mercedes C Serisi ARACIN MASRAFLARI VARDIR. FİYAT PAZARLIKSIZDIR.")
    assert cost_unknown("Sahibinden satılık, aracın masrafı var, fiyat sabit")
    for minor in ("ufak tefek masrafları vardır", "kozmetik masrafı vardır", "ön düzen masrafı vardır", "25000 tl gibi masrafı vardır",
                  "kaporta masrafı var", "masrafsız, servis bakımlı", "hiçbir masrafı yoktur"):
        assert not cost_unknown(minor), minor
    assert warning_flags("ufak tefek masrafı var") == ["ufak masraf var"]
