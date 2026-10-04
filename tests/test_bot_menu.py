"""Telegram komut menüsü: yalnız 6 görünür komut, bir kez yazılır (sahibin kararı 03.10.2026)."""
from application import bot_menu


class Repo:
    def __init__(self, state=None):
        self.state = state or {}

    def get_state(self, k, default=None):
        return self.state.get(k, default)

    def set_state(self, k, v):
        self.state[k] = v


def test_menu_is_written_once_per_version_and_lists_only_the_decided_commands(monkeypatch):
    calls = []
    monkeypatch.setattr(bot_menu, "api", lambda token, method, **kw: calls.append((method, kw)))
    repo = Repo()
    assert bot_menu.ensure_menu(repo, "t") is True
    method, kw = calls[0]
    assert method == "setMyCommands" and [c["command"] for c in kw["commands"]] == ["durum", "son", "fiyat", "satti", "dur", "basla"]
    assert all(c["description"] for c in kw["commands"])
    assert bot_menu.ensure_menu(repo, "t") is False and len(calls) == 1  # aynı sürüm: yeniden yazılmaz
    repo.state[bot_menu.STATE_KEY] = "eski"
    assert bot_menu.ensure_menu(repo, "t") is True and len(calls) == 2  # sürüm değişince yeniden


def test_a_failed_telegram_call_does_not_mark_the_menu_as_written(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("ağ yok")
    monkeypatch.setattr(bot_menu, "api", boom)
    repo = Repo()
    try:
        bot_menu.ensure_menu(repo, "t")
    except RuntimeError:
        pass
    assert bot_menu.STATE_KEY not in repo.state  # başarısızsa sonraki turda yeniden denenir
