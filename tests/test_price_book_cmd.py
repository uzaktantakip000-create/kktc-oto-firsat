from datetime import datetime, timezone

import pytest

from application import price_book_cmd as cmd
from domain.price_book import BookRow, PriceBook


def row(brand, model, year, value, status="oturmus", method="A", variant="", **kw):
    base = dict(low_gbp=value * 0.95, high_gbp=value * 1.07, ref_km=120_000, n=12, sellers=7)
    return BookRow(brand, model, variant, year, value, **{**base, **kw}, method=method, status=status)


def make_book():
    b = PriceBook()
    for r in (row("Toyota", "corolla", 2013, 6900), row("Toyota", "corolla", 2014, 7600, sales_n=1, sales_median_gbp=7200.0),
              row("Toyota", "corolla", 2015, 8300, status="supheli", cand_value=9800.0),
              row("Toyota", "corolla", 2018, 12000, status="ince", method="B"),
              row("Toyota", "corolla", 2014, 7900, variant="1.6", n=6),
              row("Mercedes-Benz", "c", 2014, 15000), row("Suzuki", "swift", 2014, 6800, method="C")):
        b.rows[(r.brand_norm, r.model_norm, r.variant, r.year)] = r
    return b


class FakeStore:
    book, sales_list, added = make_book(), [], []

    def __init__(self, conn):
        pass

    def load_book(self):
        return FakeStore.book

    def add_sale(self, sale):
        FakeStore.added.append(sale)
        FakeStore.sales_list.append(sale)

    def sales(self, days=365):
        return FakeStore.sales_list


class FakeRepo:
    conn = None

    def __init__(self, pool=(), decisions=(), fail=False):
        self.pool, self.decisions, self.fail, self.asked = list(pool), list(decisions), fail, []

    def market_pool(self, days=120):
        return self.pool

    def current_decisions(self, brand, model, year, limit=3):
        self.asked.append((brand, model, year))
        if self.fail:
            raise RuntimeError("db yok")
        rows = [d for d in self.decisions if (d["brand"], d["model"], d["year"]) == (brand, model, year)]
        return [{**d, "total": len(rows)} for d in rows[:limit]]


def dec(price, km, median, n=10, method="A", year=2014, brand="Toyota", model="corolla"):
    """Bir ilanın son kararı (evaluations): bildirim mesajındaki 'piyasa ortası' market_median_gbp'dir."""
    return {"brand": brand, "model": model, "year": year, "km": km, "price_gbp": price, "market_median_gbp": median,
            "comparables_n": n, "method": method, "tier": "guclu"}


def listing(year, km, price, url, **kw):
    return {"brand_norm": "Toyota", "model_norm": "corolla", "year": year, "km": km, "price_gbp": price, "url": url,
            "is_active": True, "duplicate_of": None, **kw}


@pytest.fixture(autouse=True)
def patch(monkeypatch):
    FakeStore.book, FakeStore.sales_list, FakeStore.added = make_book(), [], []
    monkeypatch.setattr(cmd, "PriceBookStore", FakeStore)
    monkeypatch.setattr(cmd, "gbp_rate", lambda cur: {"GBP": 1.0, "TRY": 0.02, "USD": 0.8, "EUR": 0.85}[cur])


POOL = [listing(2014, 118_000, 7500, "https://x/a"), listing(2012, 90_000, 6000, "https://x/b"),
        listing(2014, 150_000, 7000, "https://x/c"), listing(2014, 100_000, 7100, "https://x/d", is_active=False),
        listing(2016, 80_000, 9000, "https://x/e"), {**listing(2014, 120_000, 5000, "https://x/f"), "model_norm": "yaris"}]


@pytest.mark.parametrize("args", [" corolla 2014", " toyota corolla 2014", " corola 2014", " Toyota Corolla 2014", " toyota corola 2014"])
def test_fiyat_matches_name_variants(args):
    out = cmd.fiyat_reply(FakeRepo(POOL), args)
    assert "📘 Toyota Corolla 2014 — £7.600 (aralık £7.220–8.132) · 120 bin km için" in out
    assert "12 ilan · 7 satıcı · ✅ oturmuş · yöntem: benzer ilanlar" in out
    assert "Senin girdiğin gerçek satışlar: 1" in out
    assert "Corolla 2013" in out and "Corolla 2015" in out and "⚠️ şüpheli (bekleyen £9.800)" in out  # yıl ±1
    assert "Corolla 2018" not in out
    assert "Varyant: 1.6 £7.900" in out


def test_fiyat_nearest_listings_by_year_then_km():
    out = cmd.fiyat_reply(FakeRepo(POOL), "corolla 2014")
    near = out.split("En yakın 3 ilan:")[1]
    assert near.index("x/a") < near.index("x/c") < near.index("x/b")  # 2014 (km yakın), 2014, sonra 2012
    assert "x/d" not in near and "x/f" not in near  # pasif ve başka model elenir
    assert near.count("•") == 3


def test_fiyat_methods_and_no_year():
    out = cmd.fiyat_reply(FakeRepo(), "toyota corolla")
    assert "🔸 az veri" in out and "yöntem: değer eğrisi" in out and out.index("2018") < out.index("2013")  # yeniden eskiye
    assert "marka eğrisi (yaklaşık)" in cmd.fiyat_reply(FakeRepo(), "swift 2014")
    assert "En yakın 3 ilan" not in out


def test_fiyat_year_without_row_shows_nearest_and_nomatch_helps():
    out = cmd.fiyat_reply(FakeRepo(), "corolla 2005")
    assert "2005 için tabloda satır yok" in out and "Corolla 2013" in out
    assert "Örnek: /fiyat corolla 2014" in cmd.fiyat_reply(FakeRepo(), "xyzzy 2014")
    assert "Örnek: /fiyat corolla 2014" in cmd.fiyat_reply(FakeRepo(), "")


def test_fiyat_mercedes_short_query():
    assert "Mercedes-Benz C 2014" in cmd.fiyat_reply(FakeRepo(), "mercedes c 2014")
    assert "Mercedes-Benz C 2014" in cmd.fiyat_reply(FakeRepo(), "c 180 2014")


def test_fiyat_caps_lines():
    for y in range(1990, 2012):
        FakeStore.book.rows[("Toyota", "corolla", "", y)] = row("Toyota", "corolla", y, 3000)
    assert cmd.fiyat_reply(FakeRepo(), "corolla").count("📘") == 12


# --- 2.6: /fiyat bildirimdeki "piyasa ortası"nı AYNI karar kaydından gösterir ---
REASON = ("ℹ️ Fark nedeni: 📘 tablo bütün sürümleri ve TL ilanları sayar, hepsini aynı yıla ve 120 bin km'ye çevirir; 📊 yalnız o ilana "
          "benzeyenlere (aynı vites/yakıt/motor, yakın km) bakar, emsal azsa ±2 yıla açılıp iki hesaptan düşüğünü alır. Fırsatı 📊 belirler.")


def test_fiyat_shows_the_alert_median_from_the_same_decision_and_explains_a_big_gap():
    repo = FakeRepo(POOL, [dec(6000, 120_000, 8600), dec(6400, None, 8300, n=9), dec(7000, 90_000, 6700, n=12), dec(7100, 1, 1)])
    out = cmd.fiyat_reply(repo, "corolla 2014")
    assert out.startswith("📘 Değer tablosu (her gece hesaplanır):\n📘 Toyota Corolla 2013")
    block = out.split("📊 ")[1]
    assert block.startswith("Bildirim hesabı (şu an ilanda 4 tane 2014 Toyota Corolla var; 3'ü aşağıda):\n")
    # tablo 7.600: her ilan kendi piyasasıyla; %10'dan çok ayrışana fark yazılır (8.600 = +%13, 6.700 = -%12, 8.300 = +%9 yazılmaz)
    assert "• £6.000 · 120.000 km → piyasa ortası £8.600 (10 emsal) · tablodan %13 yüksek\n" in block
    assert "• £6.400 · km yok → piyasa ortası £8.300 (9 emsal)\n" in block
    assert "• £7.000 · 90.000 km → piyasa ortası £6.700 (12 emsal) · tablodan %12 düşük\n" in block
    assert "£7.100" not in block  # en çok 3 ilan
    assert REASON in out and out.count("ℹ️") == 1  # tek satır neden; hangi rakamın fırsatı belirlediği yazılır
    assert out.index("📊") < out.index(REASON) < out.index("En yakın 3 ilan:")  # mevcut bölümler yerinde
    assert "📘 Toyota Corolla 2014 — £7.600 (aralık £7.220–8.132) · 120 bin km için" in out and repo.asked == [("Toyota", "corolla", 2014)]


def test_fiyat_no_reason_line_when_table_and_decision_agree_within_ten_percent():
    out = cmd.fiyat_reply(FakeRepo(POOL, [dec(6000, 120_000, 8360), dec(6100, 120_000, 6840)]), "corolla 2014")  # 7.600'e göre +%10 / -%10
    assert "• £6.000 · 120.000 km → piyasa ortası £8.360 (10 emsal)\n" in out and "(şu an ilanda 2 tane 2014 Toyota Corolla var):" in out
    assert "tablodan" not in out and "ℹ️" not in out


def test_fiyat_estimate_decision_is_labelled_and_not_used_for_the_gap():
    out = cmd.fiyat_reply(FakeRepo(POOL, [dec(5000, 100_000, 9900, n=6, method="B")]), "corolla 2014")
    assert "• £5.000 · 100.000 km → tablo eğrisi ~£9.900 (az emsal)" in out
    assert "piyasa ortası" not in out and "ℹ️" not in out  # eğri tahmini "piyasa ortası" diye gösterilmez, fark satırı yazılmaz


def test_fiyat_without_current_listings_or_with_db_error_is_unchanged_table_reply():
    plain = cmd.fiyat_reply(FakeRepo(POOL), "corolla 2014")
    assert "📊" not in plain and "ℹ️" not in plain and "En yakın 3 ilan:" in plain
    broken = FakeRepo(POOL, [dec(6000, 120_000, 8600)], fail=True)
    assert cmd.fiyat_reply(broken, "corolla 2014") == plain  # karar kaydı okunamazsa tablo cevabı yine gider
    no_year = FakeRepo(POOL, [dec(6000, 120_000, 8600)])
    out = cmd.fiyat_reply(no_year, "toyota corolla")
    assert no_year.asked == [] and "📊" not in out and "Değer tablosu" not in out  # yıl yoksa eski liste aynen


def test_fiyat_year_missing_in_table_still_shows_decisions_without_gap_line():
    out = cmd.fiyat_reply(FakeRepo((), [dec(2500, 200_000, 3100, n=8, year=2005)]), "corolla 2005")
    assert "2005 için tabloda satır yok" in out
    assert "📊 Bildirim hesabı (şu an ilanda 1 tane 2005 Toyota Corolla var):\n• £2.500 · 200.000 km → piyasa ortası £3.100 (8 emsal)" in out
    assert "ℹ️" not in out  # o yılın tablo satırı yok: kıyas yapılmaz


# --- /satti ---
def sale(args):
    return cmd.record_sale(FakeRepo(), args)


def test_satti_with_and_without_km_and_currencies():
    out = sale(" corolla 2014 120000km 7200")
    assert FakeStore.added[-1] == {"brand_norm": "Toyota", "model_norm": "corolla", "brand": "Toyota", "model": "corolla",
                                   "year": 2014, "km": 120_000, "price_amount": 7200.0, "currency": "GBP", "price_gbp": 7200.0}
    assert "✅ Kaydettim: 2014 Toyota Corolla · 120.000 km · £7.200" in out
    assert "Gerçek satış / tablo değeri: 0,95 (1 satış)" in out and "kullanmamı ister misin" not in out
    sale("corolla 2014 7200")
    assert FakeStore.added[-1]["km"] is None and FakeStore.added[-1]["price_gbp"] == 7200
    sale("corolla 2014 120.000 km £6.900")
    assert FakeStore.added[-1]["km"] == 120_000 and FakeStore.added[-1]["price_gbp"] == 6900
    sale("corolla 2014 120 bin 6900 stg")
    assert FakeStore.added[-1]["km"] == 120_000 and FakeStore.added[-1]["currency"] == "GBP"
    sale("toyota corola 2014 100000 km 350000 TL")
    assert FakeStore.added[-1]["currency"] == "TRY" and FakeStore.added[-1]["price_gbp"] == 7000 and FakeStore.added[-1]["km"] == 100_000
    sale("corolla 2014 $9000")
    assert FakeStore.added[-1]["currency"] == "USD" and FakeStore.added[-1]["price_gbp"] == 7200
    sale("corolla 2014 8000€")
    assert FakeStore.added[-1]["currency"] == "EUR" and FakeStore.added[-1]["price_gbp"] == 6800
    assert len(FakeStore.added) == 7


def test_satti_model_with_digits_in_name():
    FakeStore.book.rows[("Mazda", "cx 5", "", 2016)] = row("Mazda", "cx 5", 2016, 14000)
    sale("mazda cx 5 2016 90000km 13500")
    assert FakeStore.added[-1]["model_norm"] == "cx 5" and FakeStore.added[-1]["year"] == 2016 and FakeStore.added[-1]["price_gbp"] == 13500


def test_satti_rejects_bad_input_without_saving():
    this_year = datetime.now(timezone.utc).year
    assert "Okuyamadım" in sale("corolla 7200") and "Okuyamadım" in sale("corolla 2014") and "Okuyamadım" in sale("")
    assert "tabloda bulamadım" in sale("xyzzy 2014 7200")
    assert "mantıksız" in sale("corolla 2014 100") and "mantıksız" in sale("corolla 2014 300000")
    assert "Okuyamadım" in sale(f"corolla {this_year + 3} 7200")  # gelecek yıl
    assert FakeStore.added == []


def test_satti_fx_failure_is_safe(monkeypatch):
    def boom(cur):
        raise RuntimeError("kur yok")

    monkeypatch.setattr(cmd, "gbp_rate", boom)
    assert "Kur bilgisini" in sale("corolla 2014 5000 tl") and FakeStore.added == []


def test_satti_ratio_line_and_ten_sales_question():
    for _ in range(8):
        sale("corolla 2014 7220")  # 7220/7600 = 0,95
    assert "kullanmamı" not in sale("corolla 2014 7220")  # 9. satış
    out = sale("corolla 2014 7220")  # 10. satış
    assert "(10 satış)" in out and "0,95 yerine 0,95 kullanmamı ister misin? Şimdilik değiştirmiyorum." in out
    FakeStore.sales_list[:] = [{**x, "price_gbp": 7068.0} for x in FakeStore.sales_list]  # 0,93
    assert "Gerçek satış / tablo değeri: 0,93 (11 satış)" in sale("corolla 2014 7068")
    assert "0,95 yerine 0,93 kullanmamı ister misin?" in sale("corolla 2014 7068")


def test_satti_without_book_row_has_no_ratio():
    FakeStore.book.rows[("Honda", "fit", "", 2010)] = row("Honda", "fit", 2010, 4000)
    out = sale("honda fit 2012 4000")
    assert "Kaydettim" in out and "oran hesaplanamadı" in out
