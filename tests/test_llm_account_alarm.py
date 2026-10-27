"""Yapay zekâ hesap hatası uyarısı (application/llm_reader.account_alarm, 20.10.2026): OpenRouter 19.10'dan beri her çağrıya "http 402" (kredi
bitti) dönüyordu, kimse bilmiyordu (Mezunum'da 4 ilan her turda yeniden çekildi; sosyal 🟢'lerin ikinci okuması yapılmadı). Hesap hatası
sahibe 24 saatte bir yazılır; geçici hatalar (ağ, 429, 5xx) yazılmaz. Uyarı hiçbir okuma yolunu bozmaz.
İstisna (22.10.2026, rate_limit_alarm): saatlerce süren 429 serisi (ücretsiz modelin günlük istek sınırı) kendi anahtarıyla bir kez yazılır."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from application import health
from application import llm_reader as lr
from tests.test_llm_reader import GOOD, TEXT, FakeRepo, deal_call, green, reader

DAY = datetime(2026, 10, 2, 9, tzinfo=timezone.utc)
H, MIN = timedelta(hours=1), timedelta(minutes=1)


@pytest.fixture
def alarms(monkeypatch):
    sent = []
    monkeypatch.setattr(health, "notify_owner", lambda repo, key, text, repeat_hours=0, **kw: sent.append((key, text, repeat_hours)) or True)
    return sent


@pytest.mark.parametrize("err,why", [("http 402", "kredisi bitti"), ("http 401", "anahtarı geçersiz"), ("http 403", "anahtarı reddediliyor"),
                                    ("http 404", "modeli bulunamıyor, kaldırılmış olabilir")])  # 404: model kaldırıldı (27.10.2026)
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


# --- 429 serisi (22.10.2026): ücretsiz modelin günlük istek sınırı dolunca bütün gün her çağrı 429 döner; uyarı yoktu ---

def failing(repo, at, err="http 429"):
    return lr.LlmReader(repo, "k", "m", call=lambda *a: (None, err, 0.0), now=at)


def test_hours_of_429s_warn_the_owner_with_their_own_key(alarms):
    repo = FakeRepo()
    for i in range(5):  # 5 çağrı, 80 dakika: henüz uyarı yok (kısa patlama da olabilir)
        assert failing(repo, DAY + i * 20 * MIN).read("x") is None
    assert alarms == [] and json.loads(repo.state[lr.RATE_KEY]) == {"since": DAY.isoformat(), "n": 5}
    failing(repo, DAY + 2 * H).read("x")  # 2 saattir her çağrı 429
    assert len(alarms) == 1 and alarms[0][0] == "llm_429" and alarms[0][2] == 24  # tekrar sınırı notify_owner'da (24 saat)
    text = alarms[0][1]
    assert "2 saattir her isteği geri çeviriyor (http 429" in text and "üst üste 6 istek" in text
    assert "yalnız kural ayrıştırıcısıyla okunuyor" in text and "kendiliğinden düzelir" in text


def test_a_short_burst_or_a_few_calls_never_warn(alarms):
    burst = FakeRepo()
    for i in range(20):  # dakikalık sınır: bir turda art arda 20 çağrı
        failing(burst, DAY + i * MIN).read("x")
    few = FakeRepo()
    for at in (DAY, DAY + 3 * H, DAY + 6 * H):  # 6 saattir 429 ama yalnız 3 çağrı
        failing(few, at).read("x")
    assert alarms == []


@pytest.mark.parametrize("answer", ["ok", "json yok"])
def test_the_first_answer_clears_the_streak_and_other_errors_leave_it(alarms, answer):
    repo = FakeRepo()
    for i in range(4):
        failing(repo, DAY + i * H).read("x")  # 3 saattir 429, 4 çağrı
    for err in ("http 500", "ReadTimeout", "http 402"):  # sunucu/ağ/hesap hatası: seri ne uzar ne silinir
        failing(repo, DAY + 4 * H, err=err).read("x")
    assert json.loads(repo.state[lr.RATE_KEY])["n"] == 4
    if answer == "ok":
        assert reader(repo).read(TEXT) is not None
    else:  # model cevap verdi ama JSON yok: sınır açık
        failing(repo, DAY + 4 * H, err="json yok").read("x")
    assert repo.state[lr.RATE_KEY] == ""
    for i in range(5):  # yeni seri: 5 çağrı ama 40 dakika
        failing(repo, DAY + 5 * H + i * 10 * MIN).read("x")
    assert [a[0] for a in alarms] == ["llm_hesap"]  # yalnız 402'nin kendi uyarısı


def test_429s_from_deal_notes_and_photo_reads_count_too(alarms):
    repo = FakeRepo()
    for i in range(3):
        lr.deal_notes(repo, [green()], "k", "m", call=deal_call(note=None, err="http 429", cost=0.0), now=DAY + i * H)
    for i in (3, 4):
        lr.LlmReader(repo, "k", "m", image_call=lambda *a: (None, "http 429", 0.0), now=DAY + i * H).read_image(b"jpg")
    assert [a[0] for a in alarms] == ["llm_429"]  # 5. çağrı, 4 saattir
    assert "deal_note:1:t5000" not in repo.state  # 429 notu önbelleğe yazılmadı: sınır açılınca sorulur


def test_a_broken_streak_record_or_failing_alert_never_breaks_reading(monkeypatch, capsys):
    repo = FakeRepo()
    repo.state[lr.RATE_KEY] = "bozuk"
    failing(repo, DAY).read("x")
    assert json.loads(repo.state[lr.RATE_KEY]) == {"since": DAY.isoformat(), "n": 1}  # okunamayan kayıt: yeni seri

    def boom(*a, **k):
        raise RuntimeError("telegram yok")

    monkeypatch.setattr(health, "notify_owner", boom)
    for i in range(1, 5):
        assert failing(repo, DAY + i * H).read("x") is None
    assert "yapay zekâ istek sınırı uyarısı yazılamadı: RuntimeError" in capsys.readouterr().out
