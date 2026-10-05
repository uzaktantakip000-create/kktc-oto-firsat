"""Model yılı tavanı = bu yıl + 1 (sabit 2027 değil): 2026'da davranış eskisiyle aynı (2027 tavan), 2027'de 2028 modeli kabul edilir."""
from datetime import date, datetime, timezone

from application import price_book_cmd
from domain.freetext_parser import diagnose, parse_freetext
from domain.llm_read import parse_llm_read
from domain.model_year import max_model_year
from domain.quality import find_quarantine

D2026 = date(2026, 10, 5)
D2027 = date(2027, 9, 1)


def test_ceiling_is_this_year_plus_one():
    assert max_model_year(D2026) == 2027 and max_model_year(D2027) == 2028
    assert max_model_year(datetime(2026, 12, 31, 23, 0, tzinfo=timezone.utc)) == 2027  # datetime da olur (date alt sınıfı)
    assert max_model_year(2026) == 2027 and max_model_year(2027) == 2028  # yıl (int) de verilebilir
    assert max_model_year() == datetime.now(timezone.utc).year + 1


def test_freetext_parser_ceiling_follows_the_date():
    text = "2028 Toyota Corolla 5.000 STG"
    assert parse_freetext(text, today=D2026) is None  # 2026'da 2028 modeli hâlâ reddedilir
    assert parse_freetext("2027 Toyota Corolla 5.000 STG", today=D2026).year == 2027  # tavan dahil
    assert parse_freetext(text, today=D2027).year == 2028
    assert parse_freetext(text, max_year=2028, today=D2026).year == 2028  # açık max_year bugünü ezer
    assert diagnose("Toyota Corolla 2028 5000£", today=D2026) == "yil_yok"
    assert diagnose("Toyota Corolla 2028 5000£", today=D2027) == "ok"


def test_freetext_parser_reads_2030s_years_while_the_ceiling_still_rejects_future_ones():
    """Yıl kalıbı eskiden 2029'da bitiyordu (20[0-2]x): 2030'da 2031 modeli hiç okunamazdı. Kalıp 2039'a kadar; tavan (bu yıl + 1) aynen."""
    d2030 = date(2030, 10, 5)
    assert parse_freetext("2031 Toyota Corolla 5.000 STG", today=d2030).year == 2031  # tavan dahil
    assert parse_freetext("2030 Toyota Corolla 5.000 STG", today=d2030).year == 2030
    assert parse_freetext("2032 Toyota Corolla 5.000 STG", today=d2030) is None  # tavanın üstü
    assert diagnose("Toyota Corolla 2031 5000£", today=d2030) == "ok"
    assert parse_freetext("2031 Toyota Corolla 5.000 STG", today=D2026) is None  # 2026'da eskisi gibi reddedilir
    assert diagnose("Toyota Corolla 2031 5000£", today=D2026) == "yil_yok"
    assert parse_freetext("2031 Toyota Corolla 2015 5.000 STG", today=D2026).year == 2015  # geleceğe dönük sayı atlanır, gerçek yıl okunur


def test_llm_read_year_ceiling_follows_the_date():
    text = "2028 Honda Fit 1.3 otomatik 53.000 km 7.500£"
    data = {"arac_ilani_mi": True, "yil": 2028, "yil_alinti": "2028 Honda Fit"}
    assert parse_llm_read(data, text, today=D2026).year is None
    assert parse_llm_read(data, text, today=D2027).year == 2028
    assert parse_llm_read(data | {"yil": 2027, "yil_alinti": "2027"}, "2027 Honda Fit", today=D2026).year == 2027


def test_quarantine_year_ceiling_follows_this_year():
    rows = [dict(id="a", brand_norm="Toyota", model_norm="vitz", year=2028, km=None, price_gbp=20_000),
            dict(id="b", brand_norm="Toyota", model_norm="vitz", year=2027, km=None, price_gbp=20_000)]
    assert find_quarantine(rows, 2026) == {"a": "yil_supheli"}
    assert find_quarantine(rows, 2027) == {}


def test_sale_command_year_ceiling_follows_this_year():
    assert price_book_cmd._parse_sale("corolla 2028 7200", 2026) is None
    assert price_book_cmd._parse_sale("corolla 2028 7200", 2027)["year"] == 2028
