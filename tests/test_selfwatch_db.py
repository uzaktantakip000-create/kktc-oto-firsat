"""Öz-izleme: gerçek `bot_state` tablosuyla uçtan uca (yalnız yerel test veritabanı: tests/conftest.py). Sahte repo testlerinin saydığı davranış
(tekrar sınırı, olay açma/kapama) gerçek `get_state`/`set_state`/`alert_recent`/`mark_alerted` ile de aynı çıkar."""
from datetime import datetime, timedelta, timezone

import pytest

from application import bot_poll, selfwatch
from application.runner_gate import VPS_TICK_KEY
from tests.test_runner_gate import real_ago, where
from tests.test_selfwatch import tg  # noqa: F401  (pytest fixture)


@pytest.mark.db
def test_failover_alert_repeat_limit_and_recovery_on_the_real_table(db, monkeypatch, tg):
    where(monkeypatch, "github")
    seen = real_ago(52)
    db.set_state(VPS_TICK_KEY, seen)
    selfwatch.after_tick(db, False)
    selfwatch.after_tick(db, False)  # GitHub 15 dk sonra yine tarar: tekrar yazmaz
    assert len(tg) == 1 and tg[0].startswith("⚠️ Sunucu turları durdu (son tur 52 dk önce)")
    assert db.get_state(selfwatch.FO_TICK_KEY) == seen
    where(monkeypatch, "vps")
    db.set_state(VPS_TICK_KEY, datetime.now(timezone.utc).isoformat())  # VPS yeniden başarıyla bitirdi
    selfwatch.after_tick(db, True)
    selfwatch.after_tick(db, True)
    assert len(tg) == 2 and tg[1].startswith("✅ Sunucu turları yeniden çalışıyor (kesinti ~52 dk")
    assert db.get_state(selfwatch.FO_TICK_KEY) == ""
    where(monkeypatch, "github")  # VPS taze: yeni uyarı yok
    selfwatch.after_tick(db, False, datetime.now(timezone.utc) + timedelta(minutes=10))
    assert len(tg) == 2


@pytest.mark.db
def test_listener_watch_on_the_real_table(db, monkeypatch, tg):
    where(monkeypatch, "vps")
    base = datetime.now(timezone.utc)
    for minutes in (0, 15):  # dinleyici kalp atışı hiç yok: ilk iki okuma yalnız not
        selfwatch.listener_watch(db, base + timedelta(minutes=minutes))
    assert tg == [] and db.get_state(selfwatch.LISTEN_MISS_KEY).startswith(base.isoformat())
    for minutes in (30, 45, 60):  # 30. dk'da uyarı, sonrakiler tekrar sınırı yüzünden susar
        selfwatch.listener_watch(db, base + timedelta(minutes=minutes))
    assert len(tg) == 1 and tg[0].startswith("⚠️ Telegram'da anında cevap durdu") and db.get_state(selfwatch.LISTEN_DOWN_KEY) == base.isoformat()
    db.set_state(bot_poll.LISTENER_STATE_KEY, (base + timedelta(minutes=69)).isoformat())  # dinleyici döndü
    selfwatch.listener_watch(db, base + timedelta(minutes=70))
    selfwatch.listener_watch(db, base + timedelta(minutes=71))
    assert len(tg) == 2 and tg[1] == "✅ Telegram'da anında cevap yeniden çalışıyor (kesinti ~70 dk)."
    assert db.get_state(selfwatch.LISTEN_MISS_KEY) == "" and db.get_state(selfwatch.LISTEN_DOWN_KEY) == ""
