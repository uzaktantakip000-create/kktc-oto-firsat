from datetime import datetime, timezone

import pytest

from application import feed_switch, health, status


@pytest.fixture(autouse=True)
def _legacy_apify(monkeypatch):
    monkeypatch.setattr(feed_switch, "LEGACY_APIFY", True)  # bu dosya eski Apify yolunun davranışını da sınar (üretimde emekli, 07.10.2026)


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0]


class FakeRepo:
    def __init__(self, src_rows):
        self.src_rows, self.state, self.marked, self.conn = src_rows, {"tick:last": "2026-10-02T09:00:00+00:00", "fb_spend:2026-10": "1.5", "feed:instagram": "on", "feed:facebook": "on"}, [], self

    def get_state(self, k, default=None):
        return self.state.get(k, default)

    def execute(self, sql, params=None):
        if "FROM sources s" in sql:
            return Rows(self.src_rows)
        if "FROM alerts" in sql:
            return Rows([{"strong": 2, "est": 1, "other": 1}])
        if "LATERAL" in sql:
            return Rows([{"total": 100, "ok": 40}])
        return Rows([{"n": 3}])  # feedback

    def alert_recent(self, key, hours):
        return False

    def mark_alerted(self, key):
        self.marked.append(key)


def src(name, platform="web", url="https://kktcar.com/x", status_="aktif", level="yesil", hours=0.1):
    return dict(name=name, platform=platform, url=url, status=status_, alert_level=level,
                hours_since_check=hours, new_24h=4, fresh_n=10)


NOW = datetime(2026, 10, 2, 9, 5, tzinfo=timezone.utc)


def test_status_counts_and_marks():
    repo = FakeRepo([src("KKTCar"), src("IG", "instagram", "https://instagram.com/a", "deneme", "golge", hours=9),
                     src("Yasak", status_="erisim_reddediyor"), src("Aday", status_="aday")])
    text = status.build_status(repo, NOW)
    assert "SANA HABER VEREN YERLER (1)" in text and "BİLDİRİM VERMEYENLER (1)" in text
    assert "⚠️ 1 yerde gecikme" in text and "⚠️ GECİKMİŞ — Instagram: IG" in text  # IG 9 saattir yok (sınır 6)
    assert "12:05" in text and "%40" in text and "basışların: 3" in text
    assert "Facebook $1.50" in text and "Yasak" not in text and "Aday" not in text


def test_all_good_header_and_never_scanned_flagged():
    assert "✅ Sistem çalışıyor" in status.build_status(FakeRepo([src("KKTCar")]), NOW)
    assert "⚠️ 1 yerde gecikme" in status.build_status(FakeRepo([src("Yeni", hours=None)]), NOW)


def test_morning_status_only_in_morning_window(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    sent = []
    monkeypatch.setattr(health, "api", lambda *a, **kw: sent.append(kw["text"]))
    repo = FakeRepo([src("KKTCar")])
    assert status.send_morning_status(repo, datetime(2026, 10, 2, 14, 0, tzinfo=timezone.utc)) is False and sent == []
    assert status.send_morning_status(repo, datetime(2026, 10, 2, 5, 30, tzinfo=timezone.utc)) is True
    assert sent and sent[0].startswith("✅ Sistem çalışıyor")  # KISA nabız (ayrıntılı rapor yalnız /durum)
    assert "3 yeni ilan tarandı, 2 🟢 ve 1 🟠 gönderildi" in sent[0] and len(sent[0].splitlines()) == 1 and "gecikme" not in sent[0]


def test_heartbeat_mentions_delays_briefly_and_points_to_the_detailed_report():
    text = status.build_heartbeat(FakeRepo([src("KKTCar"), src("Yeni", hours=None)]), NOW)
    assert "⚠️ 1 yerde gecikme var (ayrıntı: /durum)" in text and len(text.splitlines()) == 2
    assert "SANA HABER VEREN" not in text  # eski uzun rapor sabah mesajı değil


def test_source_limits_per_platform():
    assert health.source_limit_hours({"url": "https://www.facebook.com/groups/1", "platform": "facebook"}) == 12
    assert health.source_limit_hours({"url": "https://kibrisarabaal.com", "platform": "web"}) == 3
    assert health.source_limit_hours({"url": "https://kktcarabam.com", "platform": "web"}) == 8  # 2 saatte bir tarama (eskiden 6 saat → 14)


def test_status_uses_book_coverage_when_available(monkeypatch):
    monkeypatch.setattr(status.price_book_job, "coverage", lambda repo, now=None: (60, 20, 100))
    monkeypatch.setattr(status.price_book_job, "summary_line", lambda repo: "📘 Değer tablosu: 400 model-yıl, 300 oturmuş, 4 şüpheli · isabet %88")
    text = status.build_status(FakeRepo([src("KKTCar")]), NOW)
    assert "Fiyatını bildiğim araç: 80 / 100 (%80) — benzer ilanla 60, değer eğrisiyle 20" in text
    assert "• 📘 Değer tablosu: 400 model-yıl, 300 oturmuş, 4 şüpheli · isabet %88" in text
    assert "Fiyatını karşılaştırabildiğim" not in text


def test_status_falls_back_without_book():
    text = status.build_status(FakeRepo([src("KKTCar")]), NOW)  # sahte repoda tablo yok: eski hesap
    assert "Fiyatını karşılaştırabildiğim araç: 40 / 100 (%40)" in text and "📘" not in text
