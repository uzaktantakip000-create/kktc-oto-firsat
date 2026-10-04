from datetime import datetime, timedelta, timezone

from application import health, source_guard
from tests.test_notify import ev
from application import notify


class Repo:
    def __init__(self, failing, votes=10):
        self.failing, self.levels, self.marked, self.votes = failing, {}, [], votes
        self.conn = self

    def feedback_votes(self):
        return self.votes

    def sources_failing_feedback(self):
        return self.failing

    def set_alert_level(self, sid, level):
        self.levels[sid] = level

    def alert_recent(self, key, hours):
        return False

    def mark_alerted(self, key):
        self.marked.append(key)


def test_failing_source_is_demoted_and_owner_told(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    sent = []
    monkeypatch.setattr(health, "api", lambda *a, **kw: sent.append(kw["text"]))
    repo = Repo([{"id": "S1", "name": "Kötü Hesap", "n": 10, "bad_n": 4}])
    assert source_guard.demote_failing_sources(repo) == 1
    assert repo.levels == {"S1": "sari"} and "Kötü Hesap" in sent[0] and "/kaynak_seviye" in sent[0]


def test_no_failing_source_changes_nothing():
    repo = Repo([])
    assert source_guard.demote_failing_sources(repo) == 0 and repo.levels == {}


def test_new_source_gets_no_label_any_more():
    e = ev('L1')
    now = datetime.now(timezone.utc)
    e.listing["source_created_at"] = now - timedelta(days=3)
    assert "🆕" not in notify.format_alert(e)  # 🆕 etiketi kaldırıldı (02.10.2026)
    e.listing["source_created_at"] = now - timedelta(days=40)
    assert "🆕" not in notify.format_alert(e)


def test_no_automatic_demotion_before_ten_votes(monkeypatch):
    """Sahip kararı (03.10.2026): 10 oydan önce otomatik öğrenme yok; 1-2 yanlış basış bir kaynağı sessizce kapatmasın."""
    monkeypatch.setattr(health, "api", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("sahibe mesaj gitmemeli")))
    repo = Repo([{"id": "S1", "name": "Kötü Hesap", "n": 10, "bad_n": 4}], votes=9)
    assert source_guard.demote_failing_sources(repo) == 0 and repo.levels == {}
    assert Repo([], votes=10).feedback_votes() == 10  # tam 10 oy: kapı açık (yukarıdaki test)
