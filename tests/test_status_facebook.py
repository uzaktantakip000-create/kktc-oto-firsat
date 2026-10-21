"""/durum'da Facebook (21.10.2026): eski Apify yolu emekli (feed_switch.LEGACY_APIFY = False, üretimdeki hâli) ve grupları sunucudaki sosyal
okuyucu okuyor. Rapor Facebook'u "📴 duraklatıldı / bakmıyorum" diye göstermemeli (sahip: "Anlık bu şekilde sebebi nedir? Çalışmayan kısımlar
hala mı var?"); tek blokta grup sayısı, haber verenler, denemedekiler, son 24 saatin yeni ilanı ve okuyucunun durumu."""
from datetime import datetime, timezone

import pytest

from application import feed_switch, selfwatch, status

NOW = datetime(2026, 10, 21, 8, 46, tzinfo=timezone.utc)


def src(name, platform="facebook", level="yesil", status_="aktif", hours=1.0, new=0, fresh=5, url=None):
    return dict(name=name, platform=platform, url=url or f"https://www.facebook.com/groups/{name}/", status=status_, alert_level=level,
                hours_since_check=hours, new_24h=new, fresh_n=fresh)


class Rows:
    def __init__(self, r):
        self.r = r

    def fetchall(self):
        return self.r

    def fetchone(self):
        return self.r[0]


class Repo:
    def __init__(self, rows):
        self.rows, self.state = rows, {"tick:last": "2026-10-21T08:35:00+00:00"}
        self.conn = self

    def execute(self, sql, params=None):
        if "FROM sources s" in sql:
            return Rows(self.rows)
        if "FROM alerts" in sql:
            return Rows([{"strong": 0}])
        if "LATERAL" in sql:
            return Rows([{"total": 10, "ok": 4}])
        return Rows([{"n": 0}])

    def get_state(self, k, default=None):
        return self.state.get(k, default)


@pytest.fixture(autouse=True)
def production(monkeypatch):
    monkeypatch.setattr(feed_switch, "LEGACY_APIFY", False)
    monkeypatch.setattr(status.price_book_job, "coverage", lambda repo, now: None)
    monkeypatch.setattr(status.price_book_job, "summary_line", lambda repo: None)
    monkeypatch.setattr(status, "summary_line", lambda repo: None)


ROWS = [src("KKTCar", "web", url="https://kktcar.com/x", hours=0.2, new=1, fresh=310),
        src("g1", new=20), src("g2", new=13), src("g3", level="golge", new=4),
        src("eski", "instagram", status_="pasif", url="https://instagram.com/x")]


def test_facebook_is_one_line_among_the_alerting_sources_and_never_paused(monkeypatch):
    monkeypatch.setattr(selfwatch, "on_vps", lambda: False)
    text = status.build_status(Repo(ROWS), NOW)
    assert "duraklatıldı" not in text and "DURAKLATILANLAR" not in text and "Bakmıyorum" not in text and "DENEMEDE" not in text
    assert "📣 SANA HABER VEREN YERLER (2)" in text  # KKTCar + Facebook bloğu
    assert ("✅ Facebook: 3 grup, sunucudaki sosyal okuyucu okuyor · 2 grup haber veriyor, 1 grup ilk günlerinde denemede (yalnız ölçülüyor)"
            " · son 24 saatte 37 yeni ilan") in text
    assert "Facebook grubu: g1" not in text  # gruplar tek tek yazılmaz
    assert "✅ Sistem çalışıyor" in text


def test_the_reader_state_from_the_server_is_shown_and_a_problem_is_flagged(monkeypatch):
    from application.selfwatch import SocialPlatform, SocialView
    monkeypatch.setattr(selfwatch, "on_vps", lambda: True)
    monkeypatch.setattr(selfwatch, "read_social", lambda *a, **k: {"ok": 1})
    view = SocialView(None, [SocialPlatform("facebook", "tamam", "Facebook ✅ (son tur 2 saat önce, 33 yeni)", None)])
    monkeypatch.setattr(selfwatch, "judge_social", lambda data, now: view)
    text = status.build_status(Repo(ROWS), NOW)
    assert "   Okuyucu: Facebook ✅ (son tur 2 saat önce, 33 yeni)" in text and "✅ Sistem çalışıyor" in text
    bad = SocialView(None, [SocialPlatform("facebook", "hata", "Facebook ⚠️ hata", "x")])
    monkeypatch.setattr(selfwatch, "judge_social", lambda data, now: bad)
    text = status.build_status(Repo(ROWS), NOW)
    assert "⚠️ Facebook: 3 grup" in text and "Okuyucu: Facebook ⚠️ hata" in text and "1 yerde gecikme ya da arıza var" in text


def test_the_stale_apify_lines_are_not_shown():
    repo = Repo(ROWS)
    repo.state["fb_funnel:2026-10"] = '{"gonderi": 365, "ilan": 28}'
    text = status.build_status(repo, NOW)
    assert "Facebook'ta bu ay 365 gönderiye baktım" not in text and "Apify" not in text
