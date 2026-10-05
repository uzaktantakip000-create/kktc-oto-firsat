"""Eksik veri kapısı sonrası eklenen davranışlar: emsal satırları, yapay zekâ şüphesi, canlılık, özet, kaynak komutları, kilit."""
from datetime import datetime, timedelta, timezone

from application import bot_poll, digest, liveness, notify, sources_cmd
from application.audit import audit_text
from application.evaluate import Evaluated
from domain.comparables import Market
from domain.profit import Confidence, ProfitResult, Tier

NOW = datetime.now(timezone.utc)


def ev(i="L1", url="https://www.kktcar.com/x"):
    l = dict(id=i, first_seen_at=NOW, posted_at=None, year=2015, brand="Toyota", model="Vitz", km=1000,
             transmission="otomatik", steering="RHD", location="Girne", source_name="x", price_gbp=5000,
             currency_guess=False, currency="GBP", seller_phone=None, url=url, source_item_id="s1",
             price_amount=5000, is_active=True)
    return Evaluated(l, Market(5, 8000, 7000, 9000, 1, 0.0, 80_000), ProfitResult(7600, 2600, 0.52, Confidence.MEDIUM, Tier.STRONG), [], [], [])


def test_comparable_list_is_no_longer_in_the_message():
    """Sahibin kararı (03.10.2026): mesaj kısa; emsal listesi gösterilmez (piyasa ortası + emsal sayısı yeter)."""
    text = notify.format_alert(ev())
    assert "En yakın emsaller" not in text and "(5 emsal)" in text


def test_ai_doubt_line_shown_first_among_notes():
    note = {"gercek_firsat_mi": False, "risk_notlari": ["fiyat çok düşük, hasar olabilir"]}
    text = notify.format_alert(ev(), note)
    assert "⚠️ Yapay zekâ şüpheli buldu: fiyat çok düşük" in text and "🔎" not in text
    ok = notify.format_alert(ev(), {"gercek_firsat_mi": True, "risk_notlari": ["kontrol et"]})
    assert "şüpheli" not in ok and "🔎" not in ok  # yapay zekâ olumluysa mesaj kısa kalır; yalnız ŞÜPHE uyarısı gösterilir


class LiveRepo:
    def __init__(self, result):
        self.result, self.calls = result, 0

    def apply_refresh(self, listing_id, old, data):
        self.calls += 1
        assert isinstance(old["price_gbp"], float)
        return self.result


class FakeClient:
    def __init__(self, status=200):
        self.status = status

    def get(self, url, timeout=30):
        return type("R", (), {"status_code": self.status, "text": "x"})()


def fake_fetch(monkeypatch, data):
    monkeypatch.setattr(liveness.kktcar, "parse_detail", lambda html: data)
    monkeypatch.setattr(liveness.kktcar, "polite_sleep", lambda: None)
    monkeypatch.setattr(liveness, "_gbp", lambda d: 5000.0)


def test_sold_or_repriced_listing_not_sent(monkeypatch):
    fake_fetch(monkeypatch, {"price_amount": 5000, "currency": "GBP"})
    for result in ("pasif", "fiyat"):
        assert liveness.recheck_before_send(LiveRepo(result), [ev()], client=FakeClient()) == []


def test_removed_page_not_sent_but_server_error_still_sent(monkeypatch):
    fake_fetch(monkeypatch, None)
    assert liveness.recheck_before_send(LiveRepo(None), [ev()], client=FakeClient(404)) == []
    assert len(liveness.recheck_before_send(LiveRepo(None), [ev()], client=FakeClient(503))) == 1


def test_removed_page_still_waits_politely(monkeypatch):
    """404/410 ile atlanan ilan da siteye istek attı: her ilan sonrası nazik bekleme (404'te atlanmamalı)."""
    fake_fetch(monkeypatch, None)
    sleeps = []
    monkeypatch.setattr(liveness.kktcar, "polite_sleep", lambda: sleeps.append(1))
    assert liveness.recheck_before_send(LiveRepo(None), [ev("a"), ev("b")], client=FakeClient(404)) == []
    assert len(sleeps) == 2


def test_unchanged_or_unreadable_listing_still_sent(monkeypatch):
    fake_fetch(monkeypatch, {"price_amount": 5000, "currency": "GBP"})
    assert len(liveness.recheck_before_send(LiveRepo(None), [ev()], client=FakeClient())) == 1
    fake_fetch(monkeypatch, None)  # sayfa okunamadı: doğrulanamadı diye fırsat kaçmasın
    assert len(liveness.recheck_before_send(LiveRepo("pasif"), [ev()], client=FakeClient())) == 1


def test_instagram_and_kktcarabam_skip_recheck(monkeypatch):
    fake_fetch(monkeypatch, {"price_amount": 1, "currency": "GBP"})
    repo = LiveRepo("pasif")
    evs = [ev("a", "https://www.instagram.com/p/x/"), ev("b", "https://www.kktcarabam.com/y")]
    assert liveness.recheck_before_send(repo, evs, client=FakeClient()) == evs and repo.calls == 0


def row(i, flags=(), first_seen=NOW):
    return dict(id=i, url=f"https://x/{i}", year=2015, brand="Toyota", model="Vitz", km=None, location="Girne",
                first_seen_at=first_seen, posted_at=None, price_gbp=5000.0, source_name="KKTCar", comparables_n=9,
                exit_price_gbp=7600.0, profit_gbp=2300.0, profit_pct=46.0, confidence="orta", red_flags=list(flags),
                price_changed_at=None)


def test_digest_explains_why_not_green_and_skips_stale():
    text, items = digest.build_digest([row("a", ["km_yok"]), row("b", first_seen=NOW - timedelta(days=5))], NOW)
    assert [r["id"] for r in items] == ["a"]
    assert "km yazmıyor" in text and "🟢 değil" in text and "https://x/a" in text
    assert digest.build_digest([row("b", first_seen=NOW - timedelta(days=5))], NOW) == ("", [])


def test_instagram_username_parsing():
    p = sources_cmd.parse_instagram_username
    assert p("https://www.instagram.com/Kibris.Car/?hl=tr") == "kibris.car"
    assert p("@araba_kktc") == "araba_kktc" and p(" araba_kktc ") == "araba_kktc"
    assert p("https://www.instagram.com/p/ABC123/") is None  # gönderi bağlantısı hesap değil
    assert p("a") is None and p("") is None and p("kötü ad!") is None


def test_audit_text_has_link_and_price():
    r = dict(year=2015, brand="Toyota", model="Vitz", km=80_000, transmission="otomatik", fuel="benzin",
             price_gbp=5200.0, price_raw="5.200 STG", source_name="x", url="https://a/b,c")
    t = audit_text(1, 10, r)
    assert "£5.200" in t and "https://a/b,c" in t and "80.000 km" in t


class CbConn:
    def __init__(self, approved=("friend",)):
        self.sql, self.approved = [], approved

    def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split())[:30])
        return self

    def fetchone(self):
        return {"x": 1} if self.sql[-1].startswith("SELECT 1 FROM subscribers") else None


class CbRepo:
    def __init__(self, votes=10):
        self.conn = CbConn()
        self.state = {}
        self.votes = votes

    def feedback_votes(self):
        return self.votes

    def mark_sold(self, listing_id):
        self.conn.sql.append("UPDATE listings SET is_active (mark_sold)")

    def block_seller_of(self, listing_id, reason):
        self.conn.sql.append("BLOCK " + reason)
        return True

    def pas_count(self, listing_id):
        return "Toyota", "vitz", 3

    def get_state(self, k, default=None):
        return self.state.get(k, default)

    def set_state(self, k, v):
        self.state[k] = v


def test_sold_button_deactivates_only_for_owner(monkeypatch):
    monkeypatch.setattr(bot_poll, "_answer", lambda *a, **k: None)
    for sender, expect in (("owner", True), ("friend", False)):
        repo = CbRepo()
        bot_poll._handle_callback(repo, "t", "owner", {"id": "1", "from": {"id": sender}, "data": "fb:satilmis:L1"})
        assert any(s.startswith("INSERT INTO feedback") for s in repo.conn.sql)
        assert any(s.startswith("UPDATE listings SET is_active") for s in repo.conn.sql) is expect


def test_feedback_from_unapproved_user_is_ignored(monkeypatch):
    monkeypatch.setattr(bot_poll, "_answer", lambda *a, **k: None)

    class NoSubs(CbConn):
        def fetchone(self):
            return None

    repo = CbRepo()
    repo.conn = NoSubs()
    bot_poll._handle_callback(repo, "t", "owner", {"id": "1", "from": {"id": "stranger"}, "data": "fb:yanlis_fiyat:L1"})
    assert not any(q.startswith("INSERT INTO feedback") for q in repo.conn.sql)
    repo = CbRepo()  # onaylı abone ama denetim cevabı sadece sahipten kabul edilir
    bot_poll._handle_callback(repo, "t", "owner", {"id": "1", "from": {"id": "friend"}, "data": "fb:audit_yanlis:L1"})
    assert not any(q.startswith("INSERT INTO feedback") for q in repo.conn.sql)


def test_digest_drops_items_that_do_not_fit_message():
    rows = [row(str(i), ["km_yok"]) for i in range(8)]
    for r in rows:
        r["location"] = "x" * 300
        r["url"] = "https://x/" + "y" * 600
    text, shown = digest.build_digest(rows, NOW)
    assert len(text) <= digest.LIMIT and 0 < len(shown) < 8


def test_owner_decisions_flow_back_into_the_system(monkeypatch):
    sent = []
    monkeypatch.setattr(bot_poll, "_answer", lambda *a, **k: None)
    monkeypatch.setattr(bot_poll, "api", lambda token, method, **kw: sent.append((method, kw)))
    repo = CbRepo()
    bot_poll._handle_callback(repo, "t", "owner", {"id": "1", "from": {"id": "owner"}, "data": "fb:kusurlu:L1"})
    assert "BLOCK kusurlu" in repo.conn.sql                       # kusurlu/sahte -> satıcı kara listede
    repo = CbRepo()
    bot_poll._handle_callback(repo, "t", "owner", {"id": "1", "from": {"id": "friend"}, "data": "fb:kusurlu:L1"})
    assert not any(q.startswith("BLOCK") for q in repo.conn.sql)  # arkadaşın basışı kara liste yapmaz
    repo = CbRepo()
    bot_poll._handle_callback(repo, "t", "owner", {"id": "1", "from": {"id": "owner"}, "data": "fb:pas:L1"})
    ask = [kw for m, kw in sent if m == "sendMessage"]
    assert ask and "3 kez 'pas'" in ask[-1]["text"] and "mute:evet:Toyota|vitz" in str(ask[-1]["reply_markup"])
    n = len(sent)
    repo_few = CbRepo(votes=9)  # 10 oydan önce "kusurlu/sahte" yalnız KAYIT: satıcı kara listeye alınmaz
    bot_poll._handle_callback(repo_few, "t", "owner", {"id": "1", "from": {"id": "owner"}, "data": "fb:kusurlu:L1"})
    assert not any(q.startswith("BLOCK") for q in repo_few.conn.sql) and any("INSERT INTO feedback" in q for q in repo_few.conn.sql)
    bot_poll._handle_callback(repo, "t", "owner", {"id": "1", "from": {"id": "owner"}, "data": "fb:pas:L2"})
    assert len(sent) == n                                         # aynı model için ikinci kez sorulmaz
    bot_poll._handle_callback(repo, "t", "owner", {"id": "2", "from": {"id": "owner"}, "data": "mute:evet:Toyota|vitz"})
    assert repo.state["cfg:muted_models"] == "Toyota|vitz"


# --- KibrisArabaAl: bildirimden önce canlılık kontrolü (Adım 8) ---

KAA_URL = "https://kibrisarabaal.com/ilan/1234-toyota-vitz"


def fake_kaa(monkeypatch, data):
    monkeypatch.setattr(liveness.kibrisarabaal, "fetch_detail", lambda client, entry: data)
    monkeypatch.setattr(liveness.kibrisarabaal, "polite_sleep", lambda: None)
    monkeypatch.setattr(liveness, "_kaa_gbp", lambda d: 5000.0)


def test_kaa_sold_removed_or_repriced_listing_not_sent(monkeypatch):
    fake_kaa(monkeypatch, {"is_active": False, "urgency_signals": ["satildi"]})
    repo = LiveRepo("pasif")
    assert liveness.recheck_before_send(repo, [ev("k", KAA_URL)], kaa_client=FakeClient()) == [] and repo.calls == 1  # pasifleşir de
    fake_kaa(monkeypatch, {"price_amount": 4000, "currency": "GBP"})
    assert liveness.recheck_before_send(LiveRepo("fiyat"), [ev("k", KAA_URL)], kaa_client=FakeClient()) == []


def test_kaa_unchanged_or_unreadable_listing_still_sent(monkeypatch):
    fake_kaa(monkeypatch, {"price_amount": 5000, "currency": "GBP"})
    assert len(liveness.recheck_before_send(LiveRepo(None), [ev("k", KAA_URL)], kaa_client=FakeClient())) == 1
    fake_kaa(monkeypatch, None)  # okunamadı (geçici hata/şablon): doğrulanamadı diye fırsat kaçmasın, 'kaldırıldı' da yazılmaz
    repo = LiveRepo("pasif")
    assert len(liveness.recheck_before_send(repo, [ev("k", KAA_URL)], kaa_client=FakeClient())) == 1 and repo.calls == 0


def test_mixed_sources_are_checked_independently_and_other_sources_skipped(monkeypatch):
    fake_fetch(monkeypatch, {"price_amount": 5000, "currency": "GBP"})  # KKTCar: değişmedi
    fake_kaa(monkeypatch, {"is_active": False, "urgency_signals": ["kaldirildi"]})  # KAA: kaldırılmış
    repo = LiveRepo("pasif")
    evs = [ev("kk"), ev("kaa", KAA_URL), ev("ig", "https://www.instagram.com/p/x/")]
    out = liveness.recheck_before_send(repo, evs, client=FakeClient(), kaa_client=FakeClient())
    assert [e.listing["id"] for e in out] == ["ig"]  # kk: apply_refresh 'pasif' döndü (sahte) → düştü; kaa düştü; ig atlandı
