from application import ad_check
from tests.test_evaluate import POOL, car


class Repo:
    def __init__(self, pool=POOL):
        self.pool, self.state, self.keys_asked = pool, {}, []

    def market_pool(self, days, keys=None):
        """Gerçek Repository.market_pool gibi: `keys` verilirse yalnız bu (marka, model) çiftleri (SQL: concat_ws('|', marka, model||''))."""
        self.keys_asked.append(keys)
        if keys is None:
            return self.pool
        want = {f"{b}|{m or ''}" for b, m in keys}
        return [r for r in self.pool if f"{r['brand_norm']}|{r['model_norm'] or ''}" in want]

    def blocked_phones(self):
        return []

    def get_state(self, k, default=None):
        return self.state.get(k, default)

    def set_state(self, k, v):
        self.state[k] = v


class Reader:
    last_error = None

    def __init__(self, image_text=None, read=None):
        self.image_text, self._read = image_text, read

    def read_image(self, image, mime="image/jpeg"):
        if self.image_text is None:
            self.last_error = "görselde okunur yazı yok"
        return self.image_text

    def read(self, text):
        return self._read


AD = "2015 Toyota Vitz 80.000 km otomatik\nFiyat: 5.000£ nakit\nGirne"


def test_cheap_car_gets_green_verdict_with_market_and_comps(monkeypatch):
    monkeypatch.setattr(ad_check, "gbp_rate", lambda c: 1.0)
    out = ad_check.handle(Repo(), AD, None, None)
    assert "Okuduğum (kural): 2015 Toyota Vitz" in out and "🟢 GÜÇLÜ FIRSAT" in out
    assert "8 emsal" in out and "En yakın emsaller" in out


def test_expensive_car_is_not_a_deal(monkeypatch):
    monkeypatch.setattr(ad_check, "gbp_rate", lambda c: 1.0)
    out = ad_check.handle(Repo(), AD.replace("5.000£", "9.500£"), None, None)
    assert "❌ Fırsat değil" in out


def test_unreadable_ad_explains_why_and_how_to_resend():
    out = ad_check.handle(Repo(), "2015 Toyota Vitz detaylı bilgi için arayın", None, None)
    assert "Okuyamadım" in out and "fiyatı bulamadım" in out and "Örnek:" in out


def test_no_comparables_says_so(monkeypatch):
    monkeypatch.setattr(ad_check, "gbp_rate", lambda c: 1.0)
    out = ad_check.handle(Repo(pool=[]), AD, None, None)
    assert "yeterli emsal yok" in out


def test_trap_words_block(monkeypatch):
    monkeypatch.setattr(ad_check, "gbp_rate", lambda c: 1.0)
    out = ad_check.handle(Repo(), AD + "\nGümrüksüz araç", None, None)
    assert "🚫 Tuzak işareti" in out


def test_motorcycle_and_absurd_price_are_refused(monkeypatch):
    monkeypatch.setattr(ad_check, "gbp_rate", lambda c: 1.0)
    assert "Okuyamadım" in ad_check.handle(Repo(), "2019 Yamaha raptor 3.000 km 2.500£", None, None)  # marka listesinde yok
    out = ad_check.handle(Repo(), AD.replace("5.000£", "300.000£"), None, None)
    assert "mantıksız" in out


def test_screenshot_text_goes_through_same_path(monkeypatch):
    monkeypatch.setattr(ad_check, "gbp_rate", lambda c: 1.0)
    out = ad_check.handle(Repo(), "", b"jpeg", Reader(image_text=AD))
    assert "🟢 GÜÇLÜ FIRSAT" in out
    out = ad_check.handle(Repo(), "", b"jpeg", Reader(image_text=None))
    assert "Görüntüden yazı okuyamadım" in out
    assert "anahtarı tanımlı değil" in ad_check.handle(Repo(), "", b"jpeg", None)


def test_daily_quota():
    repo = Repo()
    for _ in range(ad_check.MAX_PER_DAY):
        assert "sınırına ulaşıldı" not in ad_check.handle(repo, AD, None, None)
    assert "sınırına ulaşıldı" in ad_check.handle(repo, AD, None, None)


# --- bot yönlendirmesi: yalnızca sahibin komut olmayan mesajı değerlendirilir ---------------------------------
class PollRepo(Repo):
    def __init__(self):
        super().__init__()
        self.conn = self

    def execute(self, sql, params=()):
        class R:
            def fetchone(_):
                return {"status": "onayli"}
        return R()


def _msg(chat, text=None, photo=None):
    return {"chat": {"id": chat}, "from": {"first_name": "x"}, "text": text, **({"photo": photo} if photo else {})}


def test_owner_and_approved_subscriber_texts_are_checked_but_commands_are_not(monkeypatch):
    from application import bot_poll
    monkeypatch.setattr(bot_poll.sources_cmd, "sources_report", lambda repo: "rapor")
    sent, asked = [], []
    monkeypatch.setattr(bot_poll, "api", lambda token, method, **kw: sent.append((method, kw)))
    monkeypatch.setattr(bot_poll.ad_check, "handle", lambda repo, raw, image, reader, **kw: asked.append((raw, image, kw)) or "cevap")
    monkeypatch.setattr(bot_poll.llm_reader, "from_env", lambda repo: None)
    repo = PollRepo()
    bot_poll._handle_message(repo, "tok", "1", _msg(1, "2015 Toyota Vitz 5000£"))
    assert asked == [("2015 Toyota Vitz 5000£", None, {})] and sent[-1][1]["text"] == "cevap"
    bot_poll._handle_message(repo, "tok", "1", _msg(2, "2015 Toyota Vitz 5000£"))  # onaylı abone: o da kontrol ettirir (kendi kotasıyla)
    assert asked[-1] == ("2015 Toyota Vitz 5000£", None, {"subscriber": "2"}) and sent[-1][1]["text"] == "cevap"
    bot_poll._handle_message(repo, "tok", "1", _msg(1, "/kaynaklar"))                 # komut: ilan sayılmaz
    assert len(asked) == 2


def test_owner_price_book_commands_are_routed(monkeypatch):
    from application import bot_poll
    sent = []
    monkeypatch.setattr(bot_poll, "api", lambda token, method, **kw: sent.append(kw["text"]))
    monkeypatch.setattr(bot_poll.price_book_cmd, "fiyat_reply", lambda repo, args: f"fiyat:{args}")
    monkeypatch.setattr(bot_poll.price_book_cmd, "record_sale", lambda repo, args: f"satti:{args}")
    monkeypatch.setattr(bot_poll.settings_store, "set_estimated", lambda repo, args: f"tahmini:{args}")
    monkeypatch.setattr(bot_poll.ad_check, "handle", lambda *a: sent.append("ILAN") or "ilan")
    repo = PollRepo()
    bot_poll._handle_message(repo, "tok", "1", _msg(1, "/fiyat Corolla 2014"))
    bot_poll._handle_message(repo, "tok", "1", _msg(1, "/satti corolla 2014 120000km 7200"))
    bot_poll._handle_message(repo, "tok", "1", _msg(1, "/tahmini kapat"))
    bot_poll._handle_message(repo, "tok", "1", _msg(2, "/fiyat corolla 2014"))  # arkadaş: komut çalışmaz, ama sessiz de kalınmaz
    assert sent == ["fiyat: corolla 2014", "satti: corolla 2014 120000km 7200", "tahmini: kapat", bot_poll.OWNER_ONLY_REPLY]
    assert "/yardim" in bot_poll.WELCOME_OWNER
    assert all(c in bot_poll.HELP_OWNER for c in ("/fiyat", "/satti", "/tahmini", "/son", "/durum"))


def pool_far_km():
    """8 emsal; km'leri iletilen ilanın km'sinden (80.000) %2'den fazla uzak: ikiz sanılmazlar."""
    return [car(f"q{i}", p, km=55_000 + 2_345 * i) for i, p in enumerate([8000, 8200, 8400, 8600, 8800, 9000, 9200, 9400])]


def market_line(out: str) -> str:
    return next(line for line in out.splitlines() if line.startswith("📊 Piyasa"))


def test_forwarded_ad_does_not_count_its_own_database_twin_as_a_comparable(monkeypatch):
    """Sistemin taradığı ilan bota iletilince kendi veritabanı kaydı emsal sayılmaz (aksi halde 8 yerine 9 emsal ve kayık medyan)."""
    monkeypatch.setattr(ad_check, "gbp_rate", lambda c: 1.0)
    ad = AD.replace("5.000£", "8.500£")
    twin = car("twin", 8500)  # aynı marka/model/yıl, aynı km (80.000), aynı fiyat: aynı araç
    base = market_line(ad_check.handle(Repo(pool=pool_far_km()), ad, None, None))
    with_twin = market_line(ad_check.handle(Repo(pool=pool_far_km() + [twin]), ad, None, None))
    assert "8 emsal" in base and with_twin == base  # ikiz görmezden gelinir: cevap, ikizi hiç olmayan duruma eşit


def test_a_different_car_with_the_same_model_is_still_a_comparable(monkeypatch):
    monkeypatch.setattr(ad_check, "gbp_rate", lambda c: 1.0)
    ad = AD.replace("5.000£", "8.500£")
    other = car("other", 8500, km=82_000)  # km farkı 2.000 (>%2): başka araç, emsal sayılır
    out = ad_check.handle(Repo(pool=pool_far_km() + [other]), ad, None, None)
    assert "9 emsal" in market_line(out)


# --- abone ilan kontrolü: ayrı kota, sahibin kişisel ayarları uygulanmaz ---------------------------------------------
def test_subscriber_has_a_separate_daily_quota_and_the_owners_is_untouched(monkeypatch):
    monkeypatch.setattr(ad_check, "gbp_rate", lambda c: 1.0)
    repo = Repo()
    for _ in range(ad_check.MAX_PER_DAY_SUBSCRIBER):
        assert "sınırına ulaşıldı" not in ad_check.handle(repo, AD, None, None, subscriber="77")
    assert "sınırına ulaşıldı" in ad_check.handle(repo, AD, None, None, subscriber="77")
    assert "sınırına ulaşıldı" not in ad_check.handle(repo, AD, None, None, subscriber="88")  # başka abone etkilenmez
    assert "sınırına ulaşıldı" not in ad_check.handle(repo, AD, None, None)                   # sahibin kotası ayrı
    assert sorted(v for k, v in repo.state.items() if k.count(":") == 1) == ["1"]  # sahibin sayacı yalnız kendi 1 kontrolü


def test_owners_personal_settings_do_not_apply_to_a_subscribers_check(monkeypatch):
    monkeypatch.setattr(ad_check, "gbp_rate", lambda c: 1.0)
    repo = Repo()
    repo.state["cfg:blocked_brands"] = "Toyota"  # sahip Toyota'yı istemiyor (kişisel tercih)
    assert "🟢 GÜÇLÜ FIRSAT" not in ad_check.handle(repo, AD, None, None)                      # sahibin kontrolünde tercih uygulanır
    assert "🟢 GÜÇLÜ FIRSAT" in ad_check.handle(repo, AD, None, None, subscriber="77")        # abonede standart kurallar


# --- veritabanı okuma hacmi: iletilen ilan da yalnız kendi (marka, model) çiftinin havuzunu okur ------------------------
class FullPoolRepo(Repo):
    """Eski davranış: anahtar yok sayılır, TÜM havuz döner (karşılaştırma için)."""
    def market_pool(self, days, keys=None):
        self.keys_asked.append(keys)
        return self.pool


def _many(prefix, prices, **kw):
    return [car(f"{prefix}{i}", p, **kw) for i, p in enumerate(prices)]


MIXED_POOL = (
    POOL + [car("twin", 8500)]  # Toyota vitz + iletilen ilanın veritabanındaki ikizi
    + _many("y", [14000, 14200, 14400, 14600, 14800, 15000, 15200, 15400], model_norm="yaris", model="Yaris", year=2021, km=30_000)  # CROSS_KEYS
    + _many("c", [12000, 12200, 12400, 12600, 12800, 13000, 13200, 13400], brand_norm="Mazda", model_norm="cx", model="CX")  # MIXED_KEYS
    + _many("t", [9000, 9200, 9400, 9600, 9800, 10000, 10200, 10400], brand_norm="Ford", model_norm="transit", model="Transit")  # MIXED_KEYS
    + _many("n", [8000, 8200, 8400, 8600, 8800, 9000, 9200, 9400], model_norm=None, model=None)  # modeli bilinmeyen Toyota
    + _many("fg", [7000, 7200, 7400, 7600, 7800, 8000, 8200, 8400], brand_norm="Honda", model_norm="fit", model="Fit", currency="GBP")
    + _many("ft", [6000, 6100, 6200, 6300], brand_norm="Honda", model_norm="fit", model="Fit", currency="TRY")  # TL emsal: yalnız Honda fit'te
)
CASES = [  # (marka, model, yıl, km, £ fiyat, para birimi)
    ("Toyota", "Vitz", 2015, 80_000, 5000, "GBP"),     # 🟢 + en yakın emsaller (havuzda başka anahtarlarda TL ilan var)
    ("Toyota", "Vitz", 2015, 80_000, 8500, "GBP"),     # ikizi havuzda: kendi emsali sayılmaz
    ("Toyota", "Vitz", 2015, 80_000, 5000, "TRY"),     # TL ilan
    ("Toyota", "Yaris", 2021, 30_000, 9000, "GBP"),    # Yaris/Yaris Cross karışık anahtar (CROSS_KEYS)
    ("Mazda", "CX", 2015, 80_000, 8000, "GBP"),        # CX-3/CX-5/CX-30 karışık anahtar (MIXED_KEYS)
    ("Ford", "Transit", 2015, 80_000, 6000, "GBP"),    # MIXED_KEYS
    ("Toyota", None, 2015, 80_000, 5000, "GBP"),       # model bilinmiyor (model_norm None)
    ("Toyota", "Foobarx", 2015, 80_000, 5000, "GBP"),  # bilinmeyen model: emsal yok
    ("Honda", "Fit", 2015, 80_000, 4500, "GBP"),       # anahtarın kendisinde TL emsal var
]


def _ad(brand, model, year, km, price, currency):
    return {"brand": brand, "model": model, "year": year, "km": km, "fuel": None, "transmission": "otomatik", "steering": None,
            "price_amount": price, "currency": currency, "currency_guess": False, "price_gbp": float(price), "raw_text": "x", "engine_l": None}


def test_forwarded_ad_reads_only_its_own_model_pool_and_the_answer_is_identical(monkeypatch):
    """Çıkış kotası: iletilen ilan tüm emsal havuzunu değil yalnız kendi (marka, model) çiftini okur (evaluate.pool_keys). Cevap ve karar
    (seviye, piyasa sayıları, emsal kimlikleri, nedenler) tüm havuzla birebir aynı: karışık model (MIXED/CROSS_KEYS), modeli bilinmeyen,
    bilinmeyen model, TL ilan, ikiz ve 🟠 yolu (değer tablosu) dahil. Farklı olabilecek TEK alan `Market.gbp_only`: yalnız kanıt bayrağıdır
    (cevapta görünmez; başka anahtardaki TL ilan "önce £-yalnız dene" adımını açar ama sayılar aynı kalır; taranan ilanda da böyledir)."""
    from dataclasses import replace

    from domain import decision as decision_mod
    from domain.price_book import Estimate, PriceBook
    from domain.settings import Settings
    from infrastructure.db.repository import Repository

    monkeypatch.setattr(ad_check, "gbp_rate", lambda c: 1.0)
    est = Estimate(value_gbp=10_000, lower_gbp=8_000, method="B", n=30, sellers=12, sigma=0.15)
    decisions = []
    real_decide = ad_check.decide

    def spy(listing, pool, s, book=None, **kw):
        d = real_decide(listing, pool, s, book, **kw)
        decisions.append(None if d is None else (d.profit, replace(d.market, gbp_only=False), d.blocking, d.warnings, d.gaps, d.method))
        return d

    monkeypatch.setattr(ad_check, "decide", spy)
    monkeypatch.setattr(decision_mod, "estimate_from_book", lambda l, b, s, now=None: est)
    seen_tiers = set()
    for with_book in (False, True):
        monkeypatch.setattr(ad_check, "load_book", (lambda repo: PriceBook()) if with_book else (lambda repo: None))
        for case in CASES:
            monkeypatch.setattr(ad_check, "read_ad", lambda text, reader, c=case: (_ad(*c), "kural", None))
            narrow, full = Repo(pool=MIXED_POOL), FullPoolRepo(pool=MIXED_POOL)
            decisions.clear()
            got = ad_check.analyze_text(narrow, "x", None, Settings())
            want = ad_check.analyze_text(full, "x", None, Settings())
            assert got == want, case
            assert len(decisions) == 2 and decisions[0] == decisions[1], case
            seen_tiers.add(None if decisions[0] is None else decisions[0][0].tier.value)
            keys = Repository.norm_keys(case[0], case[1])
            assert narrow.keys_asked == [[(keys["brand_norm"], keys["model_norm"])]] and full.keys_asked == narrow.keys_asked, case
    assert {None, "guclu", "pazarlik", "tahmini"} <= seen_tiers  # vakalar gerçekten farklı yollardan geçti (emsal yok, 🟢, 🟡, 🟠)
