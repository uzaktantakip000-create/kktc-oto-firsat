"""Telegram komut menüsü: sahip sohbetine 8 komut, diğer herkese 3 komut; bir kez yazılır (sahibin kararı 03.10 + 05.10.2026)."""
import re

from application import bot_menu, bot_poll


class Repo:
    def __init__(self, state=None):
        self.state = state or {}

    def get_state(self, k, default=None):
        return self.state.get(k, default)

    def set_state(self, k, v):
        self.state[k] = v


def test_menu_has_an_owner_scope_and_a_minimal_default_scope(monkeypatch):
    calls = []
    monkeypatch.setattr(bot_menu, "api", lambda token, method, **kw: calls.append((method, kw)))
    repo = Repo()
    assert bot_menu.ensure_menu(repo, "t", "777") is True
    owner_call, default_call, button_call = calls
    assert owner_call[0] == "setMyCommands" and owner_call[1]["scope"] == {"type": "chat", "chat_id": 777}
    assert [c["command"] for c in owner_call[1]["commands"]] == ["durum", "son", "bul", "fiyat", "satti", "ayarlar", "kaynaklar", "yardim", "dur", "basla"]
    assert default_call[0] == "setMyCommands" and "scope" not in default_call[1]
    assert [c["command"] for c in default_call[1]["commands"]] == ["yardim", "dur", "basla"]  # aboneler yalnız çalışanları görür
    assert button_call == ("setChatMenuButton", {"chat_id": 777, "menu_button": {"type": "commands"}})
    assert bot_menu.ensure_menu(repo, "t", "777") is False and len(calls) == 3  # aynı sürüm+sahip: yeniden yazılmaz
    repo.state[bot_menu.STATE_KEY] = "eski"
    assert bot_menu.ensure_menu(repo, "t", "777") is True and len(calls) == 6    # sürüm değişince yeniden
    assert bot_menu.ensure_menu(repo, "t", "888") is True                        # sahip değişince de yeniden


def test_menu_entries_follow_telegram_rules_and_every_one_has_a_handler():
    for commands in (bot_menu.OWNER_COMMANDS, bot_menu.DEFAULT_COMMANDS):
        names = [c for c, _ in commands]
        assert len(names) == len(set(names)) <= 100
        for name, desc in commands:
            assert re.fullmatch(r"[a-z0-9_]{1,32}", name) and 1 <= len(desc) <= 256
            assert "/" + name in bot_poll.OWNER_COMMANDS | bot_poll.SHARED_COMMANDS  # menüdeki her komutun işleyicisi var
    assert {c for c, _ in bot_menu.DEFAULT_COMMANDS} <= {c.lstrip("/") for c in bot_poll.SHARED_COMMANDS}  # abone menüsünde sahip komutu yok
    for cmd in bot_poll.OWNER_COMMANDS - {"/kaynak_ekle", "/kaynak_seviye", "/kaynak_ac", "/kaynak_kapat"}:  # yardımda listelenmeyenler: yalnız kaynak yönetimi
        assert cmd in bot_poll.HELP_OWNER, cmd


def test_a_failed_telegram_call_does_not_mark_the_menu_as_written(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("ağ yok")
    monkeypatch.setattr(bot_menu, "api", boom)
    repo = Repo()
    try:
        bot_menu.ensure_menu(repo, "t", "777")
    except RuntimeError:
        pass
    assert bot_menu.STATE_KEY not in repo.state  # başarısızsa sonraki turda yeniden denenir


def test_a_failure_in_the_last_call_still_retries_everything(monkeypatch):
    calls = []

    def flaky(token, method, **kw):
        calls.append(method)
        if method == "setChatMenuButton":
            raise RuntimeError("geçici")
    monkeypatch.setattr(bot_menu, "api", flaky)
    repo = Repo()
    try:
        bot_menu.ensure_menu(repo, "t", "777")
    except RuntimeError:
        pass
    assert bot_menu.STATE_KEY not in repo.state and calls == ["setMyCommands", "setMyCommands", "setChatMenuButton"]
