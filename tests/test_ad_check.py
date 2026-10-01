from application import ad_check
from tests.test_evaluate import POOL


class Repo:
    def __init__(self, pool=POOL):
        self.pool, self.state = pool, {}

    def market_pool(self, days):
        return self.pool

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


def test_owner_plain_text_is_checked_but_friend_text_and_commands_are_not(monkeypatch):
    from application import bot_poll
    monkeypatch.setattr(bot_poll.sources_cmd, "sources_report", lambda repo: "rapor")
    sent, asked = [], []
    monkeypatch.setattr(bot_poll, "api", lambda token, method, **kw: sent.append((method, kw)))
    monkeypatch.setattr(bot_poll.ad_check, "handle", lambda repo, raw, image, reader: asked.append((raw, image)) or "cevap")
    monkeypatch.setattr(bot_poll.llm_reader, "from_env", lambda repo: None)
    repo = PollRepo()
    bot_poll._handle_message(repo, "tok", "1", _msg(1, "2015 Toyota Vitz 5000£"))
    assert asked == [("2015 Toyota Vitz 5000£", None)] and sent[-1][1]["text"] == "cevap"
    bot_poll._handle_message(repo, "tok", "1", _msg(2, "2015 Toyota Vitz 5000£"))  # arkadaş: değerlendirilmez
    bot_poll._handle_message(repo, "tok", "1", _msg(1, "/kaynaklar"))                 # komut: ilan sayılmaz
    assert len(asked) == 1
