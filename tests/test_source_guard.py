from datetime import datetime, timedelta, timezone

from application import health, source_guard
from tests.test_notify import ev
from application import notify


class Repo:
    def __init__(self, failing):
        self.failing, self.levels, self.marked = failing, {}, []
        self.conn = self

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


def test_new_source_gets_label_old_source_does_not():
    e = ev('L1')
    now = datetime.now(timezone.utc)
    e.listing["source_created_at"] = now - timedelta(days=3)
    assert "🆕 Yeni kaynak" in notify.format_alert(e)
    e.listing["source_created_at"] = now - timedelta(days=40)
    assert "🆕" not in notify.format_alert(e)
