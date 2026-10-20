"""Yapay zekâ hesap hatası uyarısı (application/llm_reader.account_alarm, 20.10.2026): OpenRouter 19.10'dan beri her çağrıya "http 402" (kredi
bitti) dönüyordu, kimse bilmiyordu (Mezunum'da 4 ilan her turda yeniden çekildi; sosyal 🟢'lerin ikinci okuması yapılmadı). Hesap hatası
sahibe 24 saatte bir yazılır; geçici hatalar (ağ, 429, 5xx) yazılmaz. Uyarı hiçbir okuma yolunu bozmaz."""
from datetime import datetime, timezone

import pytest

from application import health
from application import llm_reader as lr
from tests.test_llm_reader import GOOD, FakeRepo, deal_call, green, reader

DAY = datetime(2026, 10, 2, 9, tzinfo=timezone.utc)


@pytest.fixture
def alarms(monkeypatch):
    sent = []
    monkeypatch.setattr(health, "notify_owner", lambda repo, key, text, repeat_hours=0, **kw: sent.append((key, text, repeat_hours)) or True)
    return sent


@pytest.mark.parametrize("err,why", [("http 402", "kredisi bitti"), ("http 401", "anahtarı geçersiz"), ("http 403", "anahtarı reddediliyor")])
def test_an_account_error_while_reading_alerts_the_owner_with_its_own_key(alarms, err, why):
    r = reader(FakeRepo(), data=None, err=err, cost=0.0)
    assert r.read("2015 Toyota Vitz 6.500 STG") is None and r.last_error == err
    assert len(alarms) == 1 and alarms[0][0] == "llm_hesap" and alarms[0][2] == 24
    assert f"OpenRouter) {why} ({err})" in alarms[0][1] and "kontrol edilmedi" in alarms[0][1]
    if err == "http 402":
        assert "openrouter.ai → Credits" in alarms[0][1]


@pytest.mark.parametrize("err", ["http 500", "http 429", "zaman aşımı", None])
def test_temporary_errors_and_success_never_alert(alarms, err):
    reader(FakeRepo(), data=None if err else GOOD, err=err, cost=0.0).read("2016 Honda Fit 1.3 otomatik\n53.000 km\nFiyat: 7.500£ nakit")
    assert alarms == []


def test_an_account_error_while_reading_a_photo_alerts_too(alarms):
    r = lr.LlmReader(FakeRepo(), "k", "m", image_call=lambda *a: (None, "http 402", 0.0), now=DAY)
    assert r.read_image(b"jpg") is None and r.last_error == "http 402"
    assert [a[0] for a in alarms] == ["llm_hesap"]


def test_an_account_error_while_asking_the_deal_note_alerts_and_the_alert_still_goes_without_a_note(alarms):
    repo, call = FakeRepo(), deal_call(note=None, err="http 402", cost=0.0)
    assert lr.deal_notes(repo, [green()], "k", "m", call=call, now=DAY) == {}
    assert [a[0] for a in alarms] == ["llm_hesap"]


def test_a_failing_owner_alert_never_breaks_reading(monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("telegram yok")

    monkeypatch.setattr(health, "notify_owner", boom)
    assert reader(FakeRepo(), data=None, err="http 402", cost=0.0).read("x") is None
    assert "yapay zekâ hesap uyarısı yazılamadı: RuntimeError" in capsys.readouterr().out
