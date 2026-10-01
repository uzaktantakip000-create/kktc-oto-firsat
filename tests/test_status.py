from datetime import datetime, timezone

from application import health, status


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0]


class FakeRepo:
    def __init__(self, src_rows):
        self.src_rows, self.state, self.marked, self.conn = src_rows, {"tick:last": "2026-10-02T09:00:00+00:00", "fb_spend:2026-10": "1.5"}, [], self

    def get_state(self, k, default=None):
        return self.state.get(k, default)

    def execute(self, sql, params=None):
        if "FROM sources s" in sql:
            return Rows(self.src_rows)
        if "FROM alerts" in sql:
            return Rows([{"strong": 2, "other": 1}])
        if "LATERAL" in sql:
            return Rows([{"total": 100, "ok": 40}])
        return Rows([{"n": 3}])  # feedback

    def alert_recent(self, key, hours):
        return False

    def mark_alerted(self, key):
        self.marked.append(key)


def src(name, platform="web", url="https://kktcar.com/x", status_="aktif", level="yesil", hours=0.1):
    return dict(name=name, platform=platform, url=url, status=status_, alert_level=level,
                hours_since_check=hours, new_24h=4, active_n=10)


NOW = datetime(2026, 10, 2, 9, 5, tzinfo=timezone.utc)


def test_status_counts_and_marks():
    repo = FakeRepo([src("KKTCar"), src("IG", "instagram", "https://instagram.com/a", "deneme", "golge", hours=9),
                     src("Yasak", status_="erisim_reddediyor"), src("Aday", status_="aday")])
    text = status.build_status(repo, NOW)
    assert "🔔 1 bildirim açık · 👻 1 gölge" in text and "💤 1 bekleyen · ⛔ 1 yasak" in text
    assert "⚠️ 1 kaynakta gecikme" in text  # IG 9 saattir yok (sınır 6)
    assert "12:05 (KKTC)" in text and "%40" in text and "Geri bildirim: 3/30" in text
    assert "Facebook $1.50/15" in text


def test_all_good_header_and_never_scanned_flagged():
    assert "✅ Her şey yolunda" in status.build_status(FakeRepo([src("KKTCar")]), NOW)
    assert "⚠️ 1 kaynakta gecikme" in status.build_status(FakeRepo([src("Yeni", hours=None)]), NOW)


def test_morning_status_only_in_morning_window(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    sent = []
    monkeypatch.setattr(health, "api", lambda *a, **kw: sent.append(kw["text"]))
    repo = FakeRepo([src("KKTCar")])
    assert status.send_morning_status(repo, datetime(2026, 10, 2, 14, 0, tzinfo=timezone.utc)) is False and sent == []
    assert status.send_morning_status(repo, datetime(2026, 10, 2, 5, 30, tzinfo=timezone.utc)) is True
    assert sent and sent[0].startswith("📊")


def test_source_limits_per_platform():
    assert health.source_limit_hours({"url": "https://www.facebook.com/groups/1", "platform": "facebook"}) == 20
    assert health.source_limit_hours({"url": "https://kibrisarabaal.com", "platform": "web"}) == 3
