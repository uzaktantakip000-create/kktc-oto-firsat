"""/kaynaklar: okuması kapalı sosyal medya kaynakları '✅ taranıyor' gibi görünmez (05.10.2026)."""
from datetime import datetime, timezone

from application import sources_cmd

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


def row(name, platform, status="aktif", checked_h=1):
    return {"name": name, "platform": platform, "status": status, "alert_level": "yesil",
            "last_checked_at": datetime(2026, 10, 5, 12 - checked_h, tzinfo=timezone.utc) if checked_h < 12 else datetime(2026, 10, 2, tzinfo=timezone.utc),
            "listings_7d": 5, "strong_30d": 1, "active_n": 7, "parsed_pct": 80}


class Repo:
    def __init__(self, rows, state=None):
        self.rows, self.state, self.conn = rows, state or {}, self

    def execute(self, sql, params=()):
        return self

    def fetchall(self):
        return self.rows

    def get_state(self, k, default=None):
        return self.state.get(k, default)


def test_paused_social_sources_are_not_listed_as_scanned():
    rows = [row("KKTCar", "web"), row("ARABA KIBRIS", "instagram", checked_h=99), row("Grup", "facebook", checked_h=99), row("Eski", "instagram", "aday")]
    out = sources_cmd.sources_report(Repo(rows), NOW)  # feed:* anahtarı yok: sosyal medya kapalı
    assert "Taranan kaynaklar (1)" in out and "KKTCar" in out
    assert "✅ ARABA KIBRIS" not in out and "✅ Grup" not in out
    assert "Şu an taranmıyor: Instagram (1 kaynak), Facebook (1 kaynak)" in out
    assert "Taranmayanlar" in out and "Eski" in out  # aday kaynaklar eskisi gibi listelenir


def test_social_sources_are_listed_as_scanned_again_when_the_switch_is_on():
    rows = [row("KKTCar", "web"), row("ARABA KIBRIS", "instagram")]
    out = sources_cmd.sources_report(Repo(rows, {"feed:instagram": "on"}), NOW)
    assert "Taranan kaynaklar (2)" in out and "✅ ARABA KIBRIS" in out and "Şu an taranmıyor" not in out
