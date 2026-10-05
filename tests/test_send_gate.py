"""2.3 (eski 4c) gönderim kontrolü: tazelik → emsal kapısı → canlılık → yapay zekâ okuması TEK adımda (application/send_gate.py).

Bölüm A (karakterizasyon, "altın iz"): cron_evaluate.run() uçtan uca çalışır; yalnız dış sınırlar sahtedir (site sayfası, yapay zekâ,
Telegram gönderimi, aday listesi). Kaydedilen iz: hangi sayfa açıldı, yapay zekâ neyi okudu, veritabanına ne yazıldı, send_alerts'e
hangi ilanlar hangi sırayla gitti, log satırları. Bu testler yeniden düzenlemeden ÖNCE eski satır içi kodla yazılıp geçti; yeni kodla
AYNEN geçmeleri davranış farkının 0 olduğunu gösterir.
Bölüm B: gonderim_kontrol'ün döndürdüğü elenme nedenleri (yeni)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from application import evaluate, llm_reader
from application.evaluate import Evaluated
from domain.comparables import Market
from domain.llm_read import LlmRead
from domain.profit import Confidence, ProfitResult, Tier
from entrypoints import cron_evaluate
from infrastructure.collectors import kktcar

NOW = datetime.now(timezone.utc)
BOOK = object()
TIME_KEYS = {"eval:last", "eval:full", "dedupe:full"}  # değerlendirme öncesi zaman damgaları: kapıyla ilgisi yok, izde gösterilmez


def kk(i):
    return f"https://www.kktcar.com/ilan/{i}"


def cand(i, *, tier=Tier.STRONG, n=9, method="A", url=None, platform="web", seen_h=1, posted_h=None, price_changed_h=None,
         extraction_by="parser"):
    l = {"id": i, "first_seen_at": NOW - timedelta(hours=seen_h),
         "posted_at": NOW - timedelta(hours=posted_h) if posted_h is not None else None,
         "price_changed_at": NOW - timedelta(hours=price_changed_h) if price_changed_h is not None else None,
         "platform": platform, "url": url, "price_amount": 5000, "currency": "GBP", "price_gbp": 5000.0, "raw_text": f"ilan {i}",
         "extraction_by": extraction_by, "year": 2015, "km": 90000, "brand": "Toyota", "steering": "RHD", "evaluation_id": f"ev-{i}"}
    return Evaluated(l, Market(n, 8000, 7000, 9000, 1, 0.0), ProfitResult(7600, 2600, 0.5, Confidence.MEDIUM, tier), [], [], [],
                     method=method)


PRICE_OK = LlmRead(is_car=True, price=5000.0, currency="GBP")
PRICE_WRONG = LlmRead(is_car=True, price=9999.0, currency="GBP")


class World:
    """Sahte dış dünya + iz defteri."""

    def __init__(self, green=(), orange=(), dead=(), price_changed=(), llm=None):
        self.cands = {Tier.STRONG: list(green), Tier.ESTIMATED: list(orange)}
        self.dead, self.changed, self.llm = set(dead), set(price_changed), llm or {}
        self.boom = {}  # patlama noktası -> hata: ("pending", seviye) aday listesi, ("site", n) n'inci sayfa istemcisi
        self.trace, self.clients = [], 0

    def log(self, *event):
        self.trace.append(event)

    def maybe_boom(self, point):
        if point in self.boom:
            raise self.boom[point]


class FakeRepo:
    def __init__(self, w):
        self.w, self.state = w, {}

    def get_state(self, key, default=None):
        if key not in TIME_KEYS:
            self.w.log("repo.get_state", key)
        return self.state.get(key, default)

    def set_state(self, key, value):
        if key not in TIME_KEYS:
            self.w.log("repo.set_state", key, value)
        self.state[key] = value

    def apply_refresh(self, listing_id, old, data):
        self.w.log("repo.apply_refresh", listing_id, dict(old), dict(data))
        return "fiyat" if listing_id in self.w.changed else None

    def downgrade_evaluation(self, listing_id, flags, evaluation_id=None):
        self.w.log("repo.downgrade_evaluation", listing_id, list(flags), evaluation_id)

    def expire_unverifiable(self):
        pass

    def release_orphan_duplicates(self):
        return 0

    def purge_personal_data(self):
        return 0, 0, 0


class FakeSite:
    def __init__(self, w):
        self.w = w

    def get(self, url, timeout=None):
        self.w.log("site.get", url)
        return SimpleNamespace(status_code=404 if url in self.w.dead else 200, text=url)

    def close(self):
        self.w.log("site.close")


class FakeReader:
    def __init__(self, w):
        self.w = w

    def read(self, text):
        self.w.log("llm.read", text)
        verdict = self.w.llm[text]
        if isinstance(verdict, Exception):
            raise verdict
        return verdict


def wire(monkeypatch, w, *, llm_configured=True, orange_floor_open=False):
    """cron_evaluate.run()'ı sahte dünyaya bağlar. Kapı adımlarının KENDİSİ (is_fresh, apply_send_floor, recheck_before_send,
    verify_candidates) gerçek koddur; yalnız onların dışarıya açılan uçları sahte."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    if llm_configured:  # fırsat notu (check_deal) için anahtar + model; okuyucu ayrıca sahte
        monkeypatch.setenv("OPENROUTER_API_KEY", "k")
        monkeypatch.setenv("OPENROUTER_MODEL", "m")
    else:
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    nop = lambda *a, **k: None  # noqa: E731
    for name in ("apply_rules_version", "poll_bot", "ensure_menu", "mark_duplicates", "run_maintenance",
                 "demote_failing_sources", "guard_estimates", "send_daily_digest", "send_morning_status", "send_discovery",
                 "send_monthly_audit", "check_fx", "check_sources", "send_weekly_report"):
        monkeypatch.setattr(cron_evaluate, name, nop)
    monkeypatch.setattr("application.price_book_job.run_price_book", nop)
    monkeypatch.setattr(cron_evaluate, "check_source_alarms", lambda repo: w.log("yan_isler"))
    settings = cron_evaluate.Settings()
    monkeypatch.setattr(cron_evaluate, "load_settings", lambda repo: settings)
    monkeypatch.setattr(cron_evaluate, "load_book", lambda repo: BOOK)
    monkeypatch.setattr(cron_evaluate, "evaluate_new", lambda *a, **k: [])

    def pending(repo, tier=Tier.STRONG, book=None):
        w.log("pending_alerts", tier.value, book is BOOK)
        w.maybe_boom(("pending", tier))
        return list(w.cands[tier])

    monkeypatch.setattr(cron_evaluate, "pending_alerts", pending)

    def new_client():
        w.clients += 1
        w.log("site.new_client")
        w.maybe_boom(("site", w.clients))
        return FakeSite(w)

    monkeypatch.setattr(kktcar, "new_client", new_client)
    monkeypatch.setattr(kktcar, "polite_sleep", lambda: w.log("site.sleep"))
    monkeypatch.setattr(kktcar, "parse_detail", lambda html: {"price_amount": 5000, "currency": "GBP"})

    real_from_env = llm_reader.from_env

    def from_env(repo):
        w.log("llm.from_env")
        return FakeReader(w) if llm_configured else real_from_env(repo)

    monkeypatch.setattr(llm_reader, "from_env", from_env)
    monkeypatch.setattr(cron_evaluate, "check_deal",
                        lambda key, model, text, summary: w.log("check_deal", text, summary) or {"gercek_firsat_mi": True})

    def send_alerts(repo, token, evs, notes=None, tier=Tier.STRONG, s=None):
        w.log("send_alerts", tier.value, [(e.listing["id"], tuple(e.warnings), tuple(e.checks)) for e in evs],
              sorted(notes) if notes is not None else None, s is settings)
        return len(evs)

    monkeypatch.setattr(cron_evaluate, "send_alerts", send_alerts)
    if orange_floor_open:  # 🟠 bugün emsal kapısından hiç geçmez; 🟠 açılınca kapının sonraki adımları da aynı kalsın diye kapı burada açılır
        real = evaluate.send_allowed
        monkeypatch.setattr(evaluate, "send_allowed", lambda tier, method, n: tier is Tier.ESTIMATED or real(tier, method, n))
    return FakeRepo(w)


def out_lines(capsys):
    return capsys.readouterr().out.splitlines()


# --- Bölüm A: karakterizasyon (eski ve yeni kodla aynı iz) ---

def golden_world():
    green = [
        cand("g1", n=9, url=kk("g1")),  # gider
        cand("g2", seen_h=40, url=kk("g2")),  # taze değil
        cand("g3", n=7, url=kk("g3")),  # emsal < 8
        cand("g4", n=12, url=kk("g4")),  # sitede kaldırılmış (404)
        cand("g5", n=10, url=kk("g5")),  # fiyatı değişmiş
        cand("g6", n=8, platform="instagram", posted_h=1, url="https://instagram.com/p/g6"),  # yapay zekâ fiyatı farklı okudu
        cand("g7", n=11, extraction_by="parser_serbest", url="https://kktcarabam.com/g7"),  # yapay zekâ doğruladı
        cand("g8", n=9, platform="facebook", posted_h=2),  # yapay zekâ okuyamadı: notla gider
        cand("g9", n=20, method="B", seen_h=60, price_changed_h=2),  # fiyatı yeni değişti (taze) ama değer tablosu yöntemi: emsal kapısı
    ]
    orange = [cand("o1", tier=Tier.ESTIMATED, method="B", n=20, url=kk("o1")), cand("o2", tier=Tier.ESTIMATED, method="B", seen_h=50)]
    llm = {"ilan g6": PRICE_WRONG, "ilan g7": PRICE_OK, "ilan g8": None}
    return World(green, orange, dead={kk("g4")}, price_changed={"g5"}, llm=llm)


OLD = {"price_amount": 5000.0, "currency": "GBP", "price_gbp": 5000.0}
READ = {"price_amount": 5000, "currency": "GBP", "price_gbp": 5000.0}

GOLDEN_GREEN_TRACE = [
    ("pending_alerts", "guclu", True),
    # canlılık: yalnız taze VE emsali yeten KKTCar ilanları (g2 bayat, g3 az emsal: sayfası hiç açılmadı)
    ("site.new_client",),
    ("site.get", kk("g1")), ("repo.apply_refresh", "g1", OLD, READ), ("site.sleep",),
    ("site.get", kk("g4")),  # 404: atlanır (eski davranış: bu dalda bekleme yok)
    ("site.get", kk("g5")), ("repo.apply_refresh", "g5", OLD, READ), ("site.sleep",),
    ("site.close",),
    # yapay zekâ: canlılıktan geçenlerden yalnız sosyal medya / serbest metin olanlar okunur (g1 site ilanı: okunmaz)
    ("llm.from_env",),
    ("repo.get_state", "verify:g6"), ("llm.read", "ilan g6"), ("repo.downgrade_evaluation", "g6", ["okuma_fiyat"], "ev-g6"),
    ("repo.set_state", "verify:g6", "bad"),
    ("repo.get_state", "verify:g7"), ("llm.read", "ilan g7"), ("repo.set_state", "verify:g7", "ok"),
    ("repo.get_state", "verify:g8"), ("llm.read", "ilan g8"),
    ("check_deal", "ilan g1", "Emsal: 9 ilan, medyan £8000, aralık £7000–£9000"),
    ("check_deal", "ilan g7", "Emsal: 11 ilan, medyan £8000, aralık £7000–£9000"),
    ("check_deal", "ilan g8", "Emsal: 9 ilan, medyan £8000, aralık £7000–£9000"),
    ("send_alerts", "guclu", [("g1", (), ()), ("g7", (), (llm_reader.OK_CHECK,)), ("g8", (llm_reader.UNCHECKED,), ())],
     ["g1", "g7", "g8"], False),
]


def test_golden_trace_green_and_orange(monkeypatch, capsys):
    w = golden_world()
    cron_evaluate.run(wire(monkeypatch, w))
    assert w.trace == GOLDEN_GREEN_TRACE + [
        ("pending_alerts", "tahmini", True),
        ("llm.from_env",),  # 🟠: o2 bayat, o1 emsal kapısında kaldı -> canlılık/okuma çağrısı yok (boş listeyle okuyucu yine kurulur)
        ("send_alerts", "tahmini", [], None, True),
        ("yan_isler",),
    ]
    assert out_lines(capsys) == [
        "emsal kapısı (🟢): 2 ilan gönderilmedi (emsal < 8)",
        "emsal kapısı (🟠): 1 ilan gönderilmedi (emsal < 8)",
        "değerlendirilen=0 güçlü=3 bildirilen=3 tahmini_bildirilen=0",
    ]


def test_llm_not_configured_passes_through_and_orange_path_after_the_floor(monkeypatch, capsys):
    """Yapay zekâ anahtarı yok: 🟢 (sosyal medya dahil) okunmadan geçer, not alınmaz; 🟠 kapısı açıkken 🟠 "kontrol edilmedi" notuyla
    gider, fiyatını yapay zekâ okumuş 🟠 ise gitmez (🟡'ye düşer)."""
    w = World(green=[cand("s1", platform="instagram", posted_h=3), cand("s2", n=15)],
              orange=[cand("o1", tier=Tier.ESTIMATED, method="B", n=20, url=kk("o1")),
                      cand("o2", tier=Tier.ESTIMATED, method="B", extraction_by="llm"),
                      cand("o3", tier=Tier.ESTIMATED, method="B", url=kk("o3")),
                      cand("o4", tier=Tier.ESTIMATED, method="B", seen_h=37)],
              dead={kk("o3")})
    cron_evaluate.run(wire(monkeypatch, w, llm_configured=False, orange_floor_open=True))
    assert w.trace == [
        ("pending_alerts", "guclu", True),
        ("llm.from_env",),
        ("send_alerts", "guclu", [("s1", (), ()), ("s2", (), ())], [], False),
        ("pending_alerts", "tahmini", True),
        ("site.new_client",),
        ("site.get", kk("o1")), ("repo.apply_refresh", "o1", OLD, READ), ("site.sleep",),
        ("site.get", kk("o3")),
        ("site.close",),
        ("llm.from_env",),
        ("repo.downgrade_evaluation", "o2", ["llm_okudu"], "ev-o2"), ("repo.set_state", "verify:o2:t5000.0", "bad"),
        ("send_alerts", "tahmini", [("o1", (llm_reader.UNCHECKED,), ())], None, True),
        ("yan_isler",),
    ]
    assert out_lines(capsys) == ["değerlendirilen=0 güçlü=2 bildirilen=2 tahmini_bildirilen=1"]


ORANGE_BOOM = RuntimeError("🟠 patladı")
GREEN_BOOM = RuntimeError("🟢 patladı")


@pytest.mark.parametrize("where", ["aday", "canlilik", "yapay_zeka"])
def test_exception_in_orange_path_does_not_stop_green_or_side_jobs(monkeypatch, capsys, where):
    """🟠 yolu try içinde: nerede patlarsa patlasın 🟢 zaten gitmiştir, hata log'a yazılır, yan işler çalışır, tur hata vermez."""
    w = golden_world()
    w.cands[Tier.ESTIMATED] = [cand("o1", tier=Tier.ESTIMATED, method="B", n=20, url=kk("o1"))]
    if where == "aday":
        w.boom[("pending", Tier.ESTIMATED)] = ORANGE_BOOM
    elif where == "canlilik":
        w.boom[("site", 2)] = ORANGE_BOOM  # 2. sayfa istemcisi = 🟠'nin canlılık kontrolü
    else:
        w.llm["ilan o1"] = ORANGE_BOOM
    cron_evaluate.run(wire(monkeypatch, w, orange_floor_open=True))  # tur hata vermez
    assert w.trace[:len(GOLDEN_GREEN_TRACE)] == GOLDEN_GREEN_TRACE  # 🟢 aynen gitti
    rest = w.trace[len(GOLDEN_GREEN_TRACE):]
    assert rest[0] == ("pending_alerts", "tahmini", True) and rest[-1] == ("yan_isler",)
    assert not any(e[0] == "send_alerts" for e in rest)  # 🟠 gönderimi yok
    assert out_lines(capsys)[-2:] == ["tahmini fırsat gönderimi başarısız: RuntimeError 🟠 patladı",
                                      "değerlendirilen=0 güçlü=3 bildirilen=3 tahmini_bildirilen=0"]


@pytest.mark.parametrize("where", ["aday", "canlilik", "yapay_zeka"])
def test_exception_in_green_path_propagates_as_before(monkeypatch, capsys, where):
    """🟢 yolu try DIŞINDA (eskisi gibi): hata turu durdurur, hiçbir şey gönderilmez, 🟠 ve yan işler çalışmaz, iş akışı hata verir."""
    w = golden_world()
    if where == "aday":
        w.boom[("pending", Tier.STRONG)] = GREEN_BOOM
    elif where == "canlilik":
        w.boom[("site", 1)] = GREEN_BOOM
    else:
        w.llm["ilan g6"] = GREEN_BOOM
    with pytest.raises(RuntimeError, match="🟢 patladı"):
        cron_evaluate.run(wire(monkeypatch, w))
    events = [e[0] for e in w.trace]
    assert "send_alerts" not in events and "yan_isler" not in events  # gönderim yok, 🟠 ve yan işler çalışmadı
    assert ("pending_alerts", "tahmini", True) not in w.trace
    assert out_lines(capsys) == ([] if where == "aday" else ["emsal kapısı (🟢): 2 ilan gönderilmedi (emsal < 8)"])


# --- Bölüm B: gonderim_kontrol'ün kendisi (elenme nedenleri) ---

from application.send_gate import CANLI_DEGIL, EMSAL_AZ, LLM_REDDETTI, TAZE_DEGIL, gonderim_kontrol  # noqa: E402


def ids(pairs):
    return [(ev.listing["id"], reason) for ev, reason in pairs]


def test_gate_returns_sendable_and_reason_per_rejected_listing_in_check_order(monkeypatch, capsys):
    w = golden_world()
    repo = wire(monkeypatch, w)
    sendable, rejected = gonderim_kontrol(repo, w.cands[Tier.STRONG], "🟢")
    assert [ev.listing["id"] for ev in sendable] == ["g1", "g7", "g8"]  # run()'ın send_alerts'e verdiğinin aynısı (Bölüm A)
    assert ids(rejected) == [("g2", TAZE_DEGIL), ("g3", EMSAL_AZ), ("g9", EMSAL_AZ), ("g4", CANLI_DEGIL), ("g5", CANLI_DEGIL),
                             ("g6", LLM_REDDETTI)]
    assert (TAZE_DEGIL, EMSAL_AZ, CANLI_DEGIL, LLM_REDDETTI) == ("taze_degil", "emsal_az", "canli_degil", "llm_reddetti")
    assert w.trace == GOLDEN_GREEN_TRACE[1:-4]  # yan etkiler: run() izinin kapıya düşen kısmı (aday okuma, not ve gönderim hariç)
    assert out_lines(capsys) == ["emsal kapısı (🟢): 2 ilan gönderilmedi (emsal < 8)"]
    # bugünkü kural: taze 🟠'nin hepsi emsal kapısında kalır
    w.trace.clear()
    assert ids(gonderim_kontrol(repo, w.cands[Tier.ESTIMATED], "🟠")[1]) == [("o2", TAZE_DEGIL), ("o1", EMSAL_AZ)]
    assert w.trace == [("llm.from_env",)]
    assert gonderim_kontrol(repo, [], "🟢") == ([], [])


def test_gate_orange_without_llm_reader_unverified_llm_priced_listing_counts_as_llm_rejection(monkeypatch):
    o = [cand("o1", tier=Tier.ESTIMATED, method="B", url=kk("o1")), cand("o2", tier=Tier.ESTIMATED, method="B", extraction_by="llm"),
         cand("o3", tier=Tier.ESTIMATED, method="B", url=kk("o3")), cand("o4", tier=Tier.ESTIMATED, method="B", seen_h=37)]
    w = World(orange=o, dead={kk("o3")})
    sendable, rejected = gonderim_kontrol(wire(monkeypatch, w, llm_configured=False, orange_floor_open=True), o, "🟠")
    assert [ev.listing["id"] for ev in sendable] == ["o1"] and sendable[0].warnings == [llm_reader.UNCHECKED]
    assert ids(rejected) == [("o4", TAZE_DEGIL), ("o3", CANLI_DEGIL), ("o2", LLM_REDDETTI)]


def test_cron_evaluate_has_no_scattered_send_checks_left():
    """Gönderim koşulları yalnız send_gate'te: cron_evaluate tek tek adımları artık doğrudan çağırmaz (yeniden dağılmasın)."""
    for name in ("is_fresh", "apply_send_floor", "recheck_before_send", "llm_reader"):
        assert not hasattr(cron_evaluate, name), name
    assert cron_evaluate.gonderim_kontrol is gonderim_kontrol
