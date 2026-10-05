"""/son komutu, anlık kaynak alarmı ve haftalık karne (sahte repo, ağ yok)."""
import json
from datetime import datetime, timezone

from application import health, history_cmd, report, source_alarm


class FakeRepo:
    def __init__(self, state=None, sources=None, opps=None, sent=None, fb=None, marks=None, names=None):
        self.state, self.sources, self.opps = state or {}, sources or [], opps or []
        self.sent, self.fb, self.marks, self.names = sent or {}, fb or {}, marks or {}, names or []
        self.marked = []

    # bot_state
    def get_state(self, k, default=None):
        return self.state.get(k, default)

    def set_state(self, k, v):
        self.state[k] = v

    def state_with_prefix(self, prefix):
        return {k[len(prefix):]: v for k, v in self.state.items() if k.startswith(prefix)}

    def alert_recent(self, key, hours):
        return key in self.marked

    def mark_alerted(self, key):
        self.marked.append(key)

    # sorgular
    def alarm_sources(self):
        return self.sources

    def recent_opportunities(self, limit=10):
        return self.opps[:limit]

    def week_alert_counts(self, days=7):
        return self.sent

    def week_feedback_counts(self, days=7):
        return self.fb

    def alert_marks_since(self, prefix, days=7):
        return self.marks.get(prefix, [])

    def source_names(self, ids):
        return self.names


def opp(tier="guclu", fb=None, price=9000.0, median=15800.0, hour=11):
    return dict(id="L", url="https://x.example/ilan/1", year=2017, brand="Mercedes", model="A180", price_gbp=price,
                tier=tier, sent_at=datetime(2026, 10, 2, hour, 20, tzinfo=timezone.utc), median_gbp=median, feedback=fb)


# --- /son ---
def test_son_empty():
    assert history_cmd.last_opportunities(FakeRepo()) == "Henüz fırsat gönderilmedi."


def test_son_lines_feedback_and_url():
    text = history_cmd.last_opportunities(FakeRepo(opps=[opp(fb="satilmis"), opp("tahmini", price=7000.0, median=None, hour=9)]))
    first, second = text.split("\n\n")[1:]
    assert first.startswith("🟢 02.10 14:20 · 2017 Mercedes A180 · £9.000 · %43 ucuz · ✅ satılmış dedin")  # UTC+3
    assert first.endswith("\nhttps://x.example/ilan/1")
    assert second.startswith("🟠 02.10 12:20 · 2017 Mercedes A180 · £7.000 · ❔ düğmeye basılmadı")  # medyan yoksa % yazılmaz


def test_son_unknown_action_and_limit():
    assert "❔ düğmeye basılmadı" in history_cmd.last_opportunities(FakeRepo(opps=[opp(fb=None)]))
    assert history_cmd.last_opportunities(FakeRepo(opps=[opp(fb="pas")] * 15)).count("🟢") == 10


def test_help_mentions_son():
    from application.bot_poll import HELP_OWNER, WELCOME_OWNER
    assert "/son" in HELP_OWNER and "/yardim" in WELCOME_OWNER


# --- anlık kaynak alarmı ---
def src(name="KibrisCars", hours=0.2, url="https://kibriscars.com/x", platform="web", sid="S1"):
    return dict(id=sid, name=name, platform=platform, url=url, hours_since_check=hours)


def sent_messages(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    out = []
    monkeypatch.setattr(health, "api", lambda *a, **kw: out.append(kw["text"]))
    return out


def test_track_counts_and_resets():
    repo = FakeRepo()
    for _ in range(3):
        source_alarm.track_collect(repo, "KibrisCars", "HTTPStatusError: Client error '403 Forbidden' for url")
    assert repo.state["fail:KibrisCars"] == "3" and "403" in repo.state["failmsg:KibrisCars"]
    source_alarm.track_collect(repo, "KibrisCars")
    assert repo.state["fail:KibrisCars"] == "0"


def test_track_never_raises():
    class Broken(FakeRepo):
        def get_state(self, k, default=None):
            raise RuntimeError("db")
    source_alarm.track_collect(Broken(), "X", "hata")
    source_alarm.track_collect(Broken(), "X")


def test_three_failures_alarm_once_then_recovery(monkeypatch):
    out = sent_messages(monkeypatch)
    repo = FakeRepo(state={"fail:KibrisCars": "3", "failmsg:KibrisCars": "HTTPStatusError: Client error '403 Forbidden'"},
                    sources=[src(), src("KKTCar", url="https://kktcar.com/x", sid="S2")])
    assert source_alarm.check_source_alarms(repo) == 1
    assert out == ["⚠️ KibrisCars 3 turdur okunamıyor (hata: 403). Diğer kaynaklar çalışıyor."]
    assert "stale:S1" in repo.marked  # eski 'saattir tarama yok' uyarısı ikinci kez yazmasın
    assert source_alarm.check_source_alarms(repo) == 0 and len(out) == 1  # arıza sürerken tekrar yazmaz
    repo.state["fail:KibrisCars"] = "0"  # bir tur başarılı
    repo.marked.clear()
    assert source_alarm.check_source_alarms(repo) == 1
    assert out[-1] == "✅ KibrisCars tekrar çalışıyor."
    assert source_alarm.check_source_alarms(repo) == 0 and len(out) == 2  # düzelme mesajı da tek


def test_two_failures_is_not_enough(monkeypatch):
    out = sent_messages(monkeypatch)
    assert source_alarm.check_source_alarms(FakeRepo(state={"fail:KibrisCars": "2"}, sources=[src()])) == 0 and out == []


def test_stale_source_alarms_by_its_own_limit(monkeypatch):
    out = sent_messages(monkeypatch)
    repo = FakeRepo(sources=[src("KKTCar", hours=5, url="https://kktcar.com/x"),  # sınır 3 saat
                             src("SahibindenArabaKibris", hours=5, url="https://sahibindenarabakibris.com/x", sid="S2")])  # sınır 6
    assert source_alarm.check_source_alarms(repo) == 1
    assert out == ["⚠️ KKTCar 5 saattir taranamıyor. Diğer kaynaklar çalışıyor."]


def test_stale_without_fail_counter_recovers(monkeypatch):
    out = sent_messages(monkeypatch)
    repo = FakeRepo(sources=[src("KKTCar", hours=5, url="https://kktcar.com/x")])
    source_alarm.check_source_alarms(repo)
    repo.sources = [src("KKTCar", hours=0.1, url="https://kktcar.com/x")]
    repo.marked.clear()
    source_alarm.check_source_alarms(repo)
    assert out[-1] == "✅ KKTCar tekrar çalışıyor."


def test_inactive_or_unknown_source_counter_ignored(monkeypatch):
    out = sent_messages(monkeypatch)
    assert source_alarm.check_source_alarms(FakeRepo(state={"fail:EskiKaynak": "9"}, sources=[src()])) == 0 and out == []


def test_collective_job_alarms(monkeypatch):
    out = sent_messages(monkeypatch)
    repo = FakeRepo(state={"feed:facebook": "on", "fail:Facebook grupları": "4", "failmsg:Facebook grupları": "RuntimeError: Apify süresi doldu"})
    assert source_alarm.check_source_alarms(repo) == 1
    assert "Facebook grupları 4 turdur okunamıyor (hata: RuntimeError)" in out[0]


def test_several_broken_sources_say_so(monkeypatch):
    out = sent_messages(monkeypatch)
    repo = FakeRepo(state={"fail:A": "3", "fail:B": "3"}, sources=[src("A", sid="1"), src("B", sid="2"), src("C", sid="3")])
    source_alarm.check_source_alarms(repo)
    assert len(out) == 2 and all("1 kaynakta daha sorun var" in t for t in out)


def test_error_text_redacted_in_state(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "sekret-deger-12345")
    repo = FakeRepo()
    source_alarm.track_collect(repo, "A", "hata sekret-deger-12345 ile")
    assert "sekret" not in repo.state["failmsg:A"]


# --- haftalık karne ---
def test_karne_full():
    repo = FakeRepo(state={"pb:stats": json.dumps({"rows": 1094, "settled": 900, "suspect": 6, "error": 0.12}),
                           "cfg:est_min_discount_to_lower": "0.75", "cfg:muted_models": "fiat|egea,kia|rio"},
                    sent={"guclu": 5, "tahmini": 3}, fb={"pas": 2, "satilmis": 1, "yanlis_fiyat": 1},
                    marks={"est_off:": ["fiat|egea"], "est_tighten": [""], "guard:": ["S1"]}, names=["KibrisCars"])
    text = "\n".join(report.karne_lines(repo))
    assert "5 tane 🟢 ve 3 tane 🟠" in text
    assert "Düğmeye bastığın: 4 (pas 2" in text and "zaten satılmış 1" in text and "yanlış fiyat 1" in text
    assert "fiat egea" in text and "%75'i" in text and "KibrisCars" in text and "model sayısı (toplam): 2" in text
    assert "1094 model-yıl satırı var, 900 tanesi sağlam, isabet %88" in text
    assert report.NUDGE not in text


def test_karne_nudge_when_no_feedback():
    text = "\n".join(report.karne_lines(FakeRepo(sent={"guclu": 2})))
    assert report.NUDGE in text and "bir şey değiştirmedim" in text and "Değer tablo" not in text


def test_karne_quiet_week_has_no_nudge():
    assert report.NUDGE not in "\n".join(report.karne_lines(FakeRepo()))


def test_karne_bad_book_stats_ignored():
    assert report._book_line(FakeRepo(state={"pb:stats": "bozuk"})) is None
    assert report._book_line(FakeRepo(state={"pb:stats": "{}"})) is None
