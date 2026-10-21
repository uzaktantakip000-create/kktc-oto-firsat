"""⚡ ACİL SATICI (application/urgent, 11.10.2026; sahip: "insanlar acil satıyor… en erken bulan kazanır"). Sahte değerlendirmeler; ağ yok;
ilanlar uydurma."""
import json
from datetime import timedelta

import pytest

from application import urgent
from application.evaluate import Evaluated
from domain.comparables import Market
from domain.profit import Confidence, ProfitResult, Tier
from domain.red_flags import urgency_signals
from domain.settings import Settings
from tests.test_evaluate import NOW, car


def ev(i="a", price=7600, median=9000, n=12, text="2015 Toyota Vitz İhtiyaçtan Acil", tier=Tier.NONE, blocking=(), **kw):
    listing = car(i, price, raw_text=text, url=f"https://kibrisarabaal.com/ilan/{i}/", source_name="KibrisArabaAl", location="Girne",
                  first_seen_at=NOW - timedelta(minutes=20), posted_at=NOW - timedelta(hours=1), seller_type="bireysel", platform="web",
                  currency="GBP", price_amount=price) | kw
    market = Market(n, median, median * 0.8, median * 1.2, 1, 0.0)
    profit = ProfitResult(median * 0.95, 0, 0, Confidence.MEDIUM, tier)
    return Evaluated(listing, market, profit, list(blocking), [], urgency_signals(text))


class Repo:
    def __init__(self, sent=()):
        self.state = {}
        self.sent = list(sent)  # veritabanındaki son ⚡'lerin parmak izi (Repository.urgent_sent satırları)
        self.asked = []  # kilit sorgusu ne zaman/hangi kimliklerle soruldu (veritabanı çıkışı)

    def get_state(self, k, default=None):
        return self.state.get(k, default)

    def set_state(self, k, v):
        self.state[k] = v

    def urgent_sent(self, ids, days):
        self.asked.append((list(ids), days))
        return [r for r in self.sent if str(r["id"]) in ids]


def collect(out):
    """send/share sahtesi: yalnız metni toplar (gerçeği: notify.send_urgent / send_to_subscribers; düğme + alerts kaydı)."""
    return lambda listing, text: out.append(text)


def remembered(repo, out):
    """Sahibe gönderim sahtesi: metni toplar ve gönderilen ilanı veritabanı gibi hatırlar (alerts 'acil' satırı → urgent_sent)."""
    def send(listing, text):
        out.append(text)
        repo.sent.append(fingerprint(listing))
    return send


def fingerprint(listing, sent_at=NOW, **kw):
    return {k: listing.get(k) for k in ("id", "brand_norm", "model_norm", "year", "seller_phone", "price_gbp")} | {"sent_at": sent_at} | kw


@pytest.mark.parametrize("text,why", [("2015 Toyota Vitz İhtiyaçtan Acil", "acil / ihtiyaçtan"),
                                      ("Leaving the island, quick sale", "acil / ihtiyaçtan, ayrılıyor / mezun"),
                                      ("adadan ayrılıyorum", "ayrılıyor / mezun"), ("nakit ihtiyacından satılık", "nakit ihtiyacı / borç")])
def test_urgent_individual_seller_ten_percent_under_market_gets_an_instant_message(text, why):
    (picked,) = urgent.candidates([ev(text=text)], Settings(), NOW)
    msg = urgent.message(picked, Settings(), NOW)
    assert msg.startswith("⚡ ACİL SATICI · piyasanın %16 altında\n2015 Toyota Vitz · 80.000 km · £7.600 · KibrisArabaAl · Girne")
    assert f"🗣 İlanda satıcının acelesi yazıyor: {why}" in msg
    assert "📊 Piyasa ortası £9.000 (12 emsal) · %20 kâr için en çok £6." in msg and "pazarlıkla" in msg
    assert "🕐 İlk görüldü: 20 dk önce" in msg and msg.endswith("https://kibrisarabaal.com/ilan/a/")


@pytest.mark.parametrize("kw", [
    dict(text="2015 Toyota Vitz temiz, pazarlık payı var"),  # pazarlık acele değildir
    dict(price=8200),  # piyasanın %10 altında değil
    dict(n=4),  # emsal az
    dict(price=4000),  # piyasanın yarısından ucuz: yanlış fiyat
    dict(seller_type="galeri"),
    dict(shadow=True),  # gölge kaynak
    dict(tier=Tier.STRONG),  # 🟢 zaten 🟢 olarak gidiyor
    dict(blocking=["hasarlı"]),
    dict(first_seen_at=NOW - timedelta(hours=7)),  # taze değil
    dict(brand_norm="Nissan"),  # /istemiyorum
    dict(duplicate_of="kanonik"),  # mükerrer bulma bunu başka ilanın kopyası saydı: aynı-araç kilidinin ilk kuralı (20.10.2026)
    dict(currency="EUR", price_amount=8800),  # €8.800 ≈ £7.600: ucuzluk yalnız kurdan, aynı rakam STG olsa piyasa ortası (13.10.2026)
])
def test_not_urgent_not_cheap_dealer_shadow_green_trap_or_old_gets_nothing(kw):
    s = Settings(blocked_brands=["Nissan"])
    assert urgent.candidates([ev(**kw)], s, NOW) == []


def test_cheapest_first_once_per_listing_and_daily_cap(monkeypatch):
    repo, sent = Repo(), []
    evs = [ev("a", price=8000), ev("b", price=7000), ev("c", price=7500)]
    assert urgent.run(repo, evs, collect(sent), Settings(), NOW) == 3
    assert [m.split("\n")[0] for m in sent] == ["⚡ ACİL SATICI · piyasanın %22 altında", "⚡ ACİL SATICI · piyasanın %17 altında",
                                                "⚡ ACİL SATICI · piyasanın %11 altında"]
    assert urgent.run(repo, evs, collect(sent), Settings(), NOW) == 0 and len(sent) == 3  # ilan başına bir kez
    monkeypatch.setattr(urgent, "MAX_PER_DAY", 4)
    assert urgent.run(repo, [ev("d"), ev("e")], collect(sent), Settings(), NOW) == 1  # günde en çok 4
    assert json.loads(repo.state[urgent.SENT_KEY]) == ["b", "c", "a", "d"]


def test_a_eur_price_that_is_cheap_even_read_as_sterling_still_counts():
    (picked,) = urgent.candidates([ev(currency="EUR", price_amount=7600)], Settings(), NOW)  # £7.600 olarak da piyasanın %16 altında
    assert picked.listing["currency"] == "EUR"


def test_tl_priced_listing_shows_the_original_price():
    msg = urgent.message(ev(currency="TRY", price_amount=480000), Settings(), NOW)
    assert "£7.600 (ilanda 480.000 TRY)" in msg


@pytest.mark.parametrize("text,signals", [("İhtiyaçtan Acil, Masrafsız, Servis Bakımlı", ["acil"]), ("borçsuz temiz araç", []),
                                          ("Türkiye'ye dönüş nedeniyle satılık", ["mezun/ayrılıyor"]),
                                          ("borçtan dolayı zararına", ["nakit/borç"]), ("servis bakımlı", []),
                                          ("katalitik dönüştürücü yeni", []), ("kesin dönüş, acil", ["acil", "mezun/ayrılıyor"]),
                                          ("Türkiye’ye dönüş sebebiyle acil satılıktır!", ["acil", "mezun/ayrılıyor"]),  # ilk ⚡ (Fiesta)
                                          ("tayin dolayısıyla", ["mezun/ayrılıyor"]), ("dönüş sebebiyle", ["mezun/ayrılıyor"])])
def test_urgency_reader(text, signals):
    assert urgency_signals(text) == signals


def test_every_flash_goes_to_everyone_and_the_old_rules_are_one_line_away(monkeypatch):
    """Sahip 22.10.2026: "bana gelen tüm mesajlar botu takip eden herkese aynı şekilde gitsin" → sahibe giden HER ⚡ abonelere de gider
    (eskiden yalnız pazarlığı az olan). Varsayılan piyasa: orta £9.000 → sınır £6.850."""
    from application import notify

    evs = [ev("yakin", price=7000), ev("uzak", price=8000)]  # £150 (%2) ve £1.150 (%14) pazarlık
    owner, others = [], []
    assert urgent.run(Repo(), evs, collect(owner), Settings(), NOW, share=collect(others)) == 2
    assert len(owner) == 2 and others == owner and "pazarlıkla £1.150 inmesi gerekir" in others[1]
    # sahip "bunlar gitmesin" derse tek satır: OWNER_ONLY_KINDS'a SELECTIVE_KIND → 11.10 kuralı ("mantıklıysa herkese gönder"):
    # %20 sınırına inmek için gereken pazarlık fiyatın en çok %10'u ise abonelere de
    monkeypatch.setattr(notify, "OWNER_ONLY_KINDS", frozenset({urgent.SELECTIVE_KIND}))
    owner, others = [], []
    assert urgent.run(Repo(), evs, collect(owner), Settings(), NOW, share=collect(others)) == 2
    assert len(owner) == 2 and others == [owner[0]] and "pazarlıkla £150 inmesi gerekir" in others[0]
    monkeypatch.setattr(notify, "OWNER_ONLY_KINDS", frozenset({urgent.KIND}))  # hiçbir ⚡ abonelere gitmez
    owner, others = [], []
    assert urgent.run(Repo(), evs, collect(owner), Settings(), NOW, share=collect(others)) == 2 and len(owner) == 2 and others == []
    assert urgent.worth_sharing(ev(price=7600), Settings()) and not urgent.worth_sharing(ev(price=7700), Settings())  # £750 = %9,9; £850 = %11
    fiesta = ev(price=2922.2, median=4058.49, n=16)  # ilk ⚡ (11.10 19:50): £122 pazarlık = %4
    assert urgent.worth_sharing(fiesta, Settings()) and urgent.ceiling(fiesta, Settings()) == 2800
    owner = []
    assert urgent.run(Repo(), [ev("a", price=7000)], collect(owner), Settings(), NOW) == 1 and len(owner) == 1  # share verilmezse yalnız sahip


def test_shared_text_reaches_every_approved_subscriber_but_the_owner(monkeypatch):
    from application import notify

    calls = []

    class SubRepo:
        conn = None

        def approved_subscribers(self):
            return [{"chat_id": "1"}, {"chat_id": "2"}, {"chat_id": "3"}]

    def api(token, method, **kw):
        calls.append((kw["chat_id"], kw.get("disable_notification")))
        if kw["chat_id"] == "3":
            raise notify.TelegramError("sendMessage", 400, "bad")

    monkeypatch.setattr(notify, "api", api)
    day = NOW.replace(hour=9)  # KKTC 12:00
    assert notify.send_to_subscribers(SubRepo(), "t", "⚡", skip_chat="1", now=day) == 1  # 3'ün hatası öbürünü durdurmaz
    assert calls == [("2", None), ("3", None)]
    calls.clear()
    notify.send_to_subscribers(SubRepo(), "t", "⚡", skip_chat=1, now=NOW.replace(hour=22))  # KKTC 01:00: sessiz
    assert calls == [("2", True), ("3", True)]


# --- aynı-araç kilidi (20.10.2026; Swift dersi: tek araç Facebook gruplarında yeniden paylaşıldı, sahibe 11 ⚡ gitti) ------------------------
# Sentetik Swift: fiyat TL, sterlin karşılığı uydurma kurla (×0,0185); km ve telefon yok; kopyalar birbirine bağlı değil (duplicate_of yok).
SWIFT = dict(brand_norm="Suzuki", model_norm="swift", model="Swift", year=2021, km=None, platform="facebook", currency="TRY")


def swift(i, price_try=430_000, **kw):
    return ev(i, price=round(price_try * 0.0185, 2), median=10_500, text="2021 Suzuki Swift acil satılık", **(SWIFT | {"price_amount": price_try} | kw))


def test_a_repost_of_a_car_that_already_got_a_flash_in_the_last_week_gets_nothing():
    first = swift("ilk")
    repo, sent = Repo(sent=[fingerprint(first.listing, sent_at=NOW - timedelta(days=1))]), []
    repo.state[urgent.SENT_KEY] = json.dumps(["ilk"])
    assert urgent.run(repo, [swift("kopya", 420_000)], collect(sent), Settings(), NOW) == 0 and sent == []  # %2,3 ucuz kopya
    assert json.loads(repo.state[urgent.SENT_KEY]) == ["ilk"]  # kilitlenen "işlendi" yazılmaz: kilit kendini zincirleme uzatmaz
    assert not any(k.startswith("acil:gun:") for k in repo.state)  # günlük kotayı da harcamaz
    assert repo.asked == [(["ilk"], urgent.LOCK_DAYS)]  # kilidin belleği: işlenmiş ⚡ listesi, 7 gün


def test_four_copies_in_one_run_give_one_message_the_cheapest():
    """18.10 05:50 UTC: aynı turda 4 kopya, 4 ⚡. Artık tek ⚡ (en ucuz kopya); sonraki turdaki beşinci kopya da kilide takılır."""
    repo, sent = Repo(), []
    copies = [swift("g1", 430_000), swift("g2", 420_000), swift("g3", 425_000), swift("g4", 430_000)]
    assert urgent.run(repo, copies, remembered(repo, sent), Settings(), NOW) == 1
    assert len(sent) == 1 and "(ilanda 420.000 TRY)" in sent[0]
    assert json.loads(repo.state[urgent.SENT_KEY]) == ["g2"]
    assert urgent.run(repo, [swift("g5", 425_000)], remembered(repo, sent), Settings(), NOW) == 0 and len(sent) == 1


@pytest.mark.parametrize("price,locked", [(10_500, True), (9_500, True), (10_000, True), (10_501, False), (9_499, False)])
def test_five_percent_boundary_is_inclusive(price, locked):
    before = car("eski", 10_000)
    assert (urgent.same_car(car("yeni", price), before) == "arac") is locked


def test_other_year_other_model_or_an_old_flash_do_not_lock():
    first = swift("ilk")
    for other, prev_at in [(swift("yil", year=2020), NOW - timedelta(days=1)),  # başka yıl
                           (swift("model", model_norm="baleno", model="Baleno"), NOW - timedelta(days=1)),  # başka model
                           (swift("hafta"), NOW - timedelta(days=7, minutes=1))]:  # son ⚡ 7 günden eski
        repo, sent = Repo(sent=[fingerprint(first.listing, sent_at=prev_at)]), []
        repo.state[urgent.SENT_KEY] = json.dumps(["ilk"])
        assert urgent.run(repo, [other], collect(sent), Settings(), NOW) == 1, other.listing["id"]
    repo = Repo(sent=[fingerprint(first.listing, sent_at=NOW - timedelta(days=6, hours=23))])
    repo.state[urgent.SENT_KEY] = json.dumps(["ilk"])
    assert urgent.run(repo, [swift("kopya")], collect([]), Settings(), NOW) == 0  # pencere içinde


def test_same_seller_phone_locks_even_for_another_car_but_short_or_missing_phones_do_not():
    corolla = car("eski", 5_000, brand_norm="Toyota", model_norm="corolla", year=2012, seller_phone="+90 533 111 22 33")
    assert urgent.same_car(car("yeni", 7_600, seller_phone="905331112233"), corolla) == "telefon"  # yazım farkı: yalnız rakamlar
    assert urgent.same_car(car("yeni", 7_600, seller_phone="905331112234"), corolla) is None
    assert urgent.same_car(car("yeni", 7_600, seller_phone=None), corolla) is None  # telefon yalnız birinde: araç kuralı (başka araç)
    assert urgent.same_car(car("yeni", 7_600, seller_phone="1234"), corolla | {"seller_phone": "1234"}) is None  # 7 haneden kısa
    repo, sent = Repo(sent=[fingerprint(corolla)]), []
    repo.state[urgent.SENT_KEY] = json.dumps(["eski"])
    assert urgent.run(repo, [ev("vitz", seller_phone="+90 533 111 2233")], collect(sent), Settings(), NOW) == 0 and sent == []


def test_lock_is_not_queried_without_a_new_candidate_or_when_the_daily_cap_is_full():
    """Veritabanı çıkışı: kilit sorgusu yalnız gönderilebilecek yeni aday varken."""
    repo = Repo()
    repo.state[urgent.SENT_KEY] = json.dumps(["a"])
    assert urgent.run(repo, [ev("a")], collect([]), Settings(), NOW) == 0 and repo.asked == []
    repo.state[urgent.DAY_KEY.format(urgent.to_kktc(NOW).date().isoformat())] = str(urgent.MAX_PER_DAY)
    assert urgent.run(repo, [ev("b")], collect([]), Settings(), NOW) == 0 and repo.asked == []


def test_if_the_lock_cannot_be_read_nothing_is_sent_and_nothing_is_marked():
    class Broken(Repo):
        def urgent_sent(self, ids, days):
            raise RuntimeError("veritabanı")

    repo, sent = Broken(), []
    with pytest.raises(RuntimeError):
        urgent.run(repo, [ev("a")], collect(sent), Settings(), NOW)
    assert sent == [] and urgent.SENT_KEY not in repo.state  # sonraki tur yeniden dener


# --- ⚡ düğmeleri ve kaydı (20.10.2026; sahip: "Facebook fırsatlarında neden oylama yok?") -------------------------------------------------
class TgRepo(Repo):
    """Gönderim yolu sahtesi: onaylı aboneler (sahip dahil), alerts kaydı, abone durdurma."""

    def __init__(self, subs=("own", "s1", "s2"), save_failures=0):
        super().__init__()
        self.subs, self.saved, self.save_failures, self.conn = list(subs), [], save_failures, self

    def approved_subscribers(self):
        return [{"chat_id": c} for c in self.subs]

    def save_alert(self, listing_id, chat_id, tier, msg_id, *, evaluation_id, price_gbp):
        if self.save_failures:
            self.save_failures -= 1
            raise RuntimeError("yazılamadı")
        self.saved.append((listing_id, chat_id, tier, msg_id, evaluation_id, price_gbp))

    def execute(self, sql, params):
        return None


def tg(monkeypatch, fail=()):
    from application import notify
    calls = []

    def api(token, method, **kw):
        if kw["chat_id"] in fail:
            raise notify.TelegramError(method, 400, "x")
        calls.append(kw)
        return {"message_id": 100 + len(calls)}

    monkeypatch.setattr(notify, "api", api)
    return calls


def wired(repo):
    """cron_evaluate.deliver'daki bağlantının aynısı (sahip 'own')."""
    from application import notify
    return (lambda listing, text: notify.send_urgent(repo, "t", "own", listing, text),
            lambda listing, text: notify.send_to_subscribers(repo, "t", text, skip_chat="own", listing=listing))


def votes_of(markup):
    return [b["callback_data"] for row in markup["inline_keyboard"] for b in row if "callback_data" in b]


def test_every_flash_message_has_vote_buttons_and_is_recorded_per_chat(monkeypatch):
    calls, repo = tg(monkeypatch), TgRepo()
    send, share = wired(repo)
    near = ev("yakin", price=7000, evaluation_id="e-1")  # £150 pazarlık: herkese
    assert urgent.run(repo, [near], send, Settings(), NOW, share=share) == 1
    assert [c["chat_id"] for c in calls] == ["own", "s1", "s2"]
    for c in calls:  # sahip ve abonelerin hepsinde 🟢 ile aynı 👍/👎
        assert votes_of(c["reply_markup"]) == ["fb:ilgilendim:yakin", "fb:yanlis_fiyat:yakin"]
        assert not any("url" in b for row in c["reply_markup"]["inline_keyboard"] for b in row)  # telefon yok: WhatsApp yok
    assert repo.saved == [("yakin", "own", "acil", 101, "e-1", 7000.0), ("yakin", "s1", "acil", 102, "e-1", 7000.0),
                          ("yakin", "s2", "acil", 103, "e-1", 7000.0)]


def test_flash_with_a_valid_phone_gets_the_whatsapp_button_and_far_ones_now_reach_everyone(monkeypatch):
    from application import notify

    calls, repo = tg(monkeypatch), TgRepo()
    send, share = wired(repo)
    far = ev("uzak", price=8000, seller_phone="+905331112233")  # £1.150 pazarlık: 22.10.2026'dan beri herkese (eskiden yalnız sahibe)
    assert urgent.run(repo, [far], send, Settings(), NOW, share=share) == 1
    assert [c["chat_id"] for c in calls] == ["own", "s1", "s2"]
    for c in calls:  # herkeste aynı WhatsApp + 👍/👎 düğmeleri; her sohbet kendi 'acil' kaydıyla
        rows = c["reply_markup"]["inline_keyboard"]
        assert rows[0][0]["url"].startswith("https://wa.me/905331112233?text=")
        assert votes_of(c["reply_markup"]) == ["fb:ilgilendim:uzak", "fb:yanlis_fiyat:uzak"]
    assert [(s[1], s[2]) for s in repo.saved] == [("own", "acil"), ("s1", "acil"), ("s2", "acil")]
    # 11.10 kuralı geri açılırsa (OWNER_ONLY_KINDS'a SELECTIVE_KIND) yalnız sahibe gider ve yalnız sahip için kaydedilir
    monkeypatch.setattr(notify, "OWNER_ONLY_KINDS", frozenset({urgent.SELECTIVE_KIND}))
    calls.clear()
    repo = TgRepo()
    send, share = wired(repo)
    assert urgent.run(repo, [far], send, Settings(), NOW, share=share) == 1
    assert [c["chat_id"] for c in calls] == ["own"] and [(s[1], s[2]) for s in repo.saved] == [("own", "acil")]


def test_a_failed_subscriber_send_is_not_recorded_and_others_still_are(monkeypatch):
    tg(monkeypatch, fail={"s1"})
    repo = TgRepo()
    send, share = wired(repo)
    assert urgent.run(repo, [ev("yakin", price=7000)], send, Settings(), NOW, share=share) == 1
    assert [s[1] for s in repo.saved] == ["own", "s2"]


def test_unrecorded_flash_leaves_its_own_trace_that_never_counts_as_a_green(monkeypatch):
    """alerts yazılamazsa (iki deneme) 🟢 yolundaki gibi yedek iz kalır, ama ⚡'e özel anahtarda: `pending_strong`/`alert_exists` bunu
    "🟢 bu sohbete gitti" sanıp aynı ilanın sonraki 🟢'sini yutmasın."""
    from infrastructure.db.repository import unsaved_alert_key
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    tg(monkeypatch)
    repo = TgRepo(subs=("own",), save_failures=2)
    send, share = wired(repo)
    assert urgent.run(repo, [ev("a", price=7000)], send, Settings(), NOW, share=share) == 1
    assert unsaved_alert_key("a", "own", "acil") in repo.state and unsaved_alert_key("a", "own") not in repo.state
    assert unsaved_alert_key("a", "own", "acil") == "sent:unsaved:acil:a:own" and unsaved_alert_key("a", "own", "guclu") == "sent:unsaved:a:own"


def test_flash_shows_up_in_the_history_command_with_its_own_mark():
    from application import history_cmd
    row = dict(tier="acil", sent_at=NOW, year=2021, brand="Suzuki", model="Swift", price_gbp=7770.0, median_gbp=10_500.0, feedback=None,
               url=None)
    assert history_cmd._line(row).startswith("⚡ ") and history_cmd._line(row | {"tier": "guclu"}).startswith("🟢 ")
