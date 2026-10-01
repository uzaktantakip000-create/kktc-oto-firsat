from application import discovery


class Repo:
    def __init__(self, mentions, known=()):
        self.mentions, self.known, self.state, self.added = mentions, set(known), {}, []

    def mentioned_handles(self, days, min_posts):
        return self.mentions

    def known_instagram_handles(self):
        return self.known

    def get_state(self, k, default=None):
        return self.state.get(k, default)

    def set_state(self, k, v):
        self.state[k] = v

    def alert_recent(self, key, hours):
        return key in self.state

    def mark_alerted(self, key):
        self.state[key] = "x"


def test_candidates_skip_known_junk_and_decided():
    repo = Repo([{"handle": "yenigaleri", "n": 5}, {"handle": "kibris.car", "n": 9}, {"handle": "gmail.com", "n": 4},
                 {"handle": "p", "n": 3}, {"handle": "eskigaleri", "n": 3}, {"handle": "siteadi.com", "n": 3}], known={"kibris.car"})
    repo.state["disc:decided:eskigaleri"] = "gec"
    assert [c["handle"] for c in discovery.candidates(repo)] == ["yenigaleri"]


def test_weekly_message_has_buttons_and_is_sent_once(monkeypatch):
    sent = []
    monkeypatch.setattr(discovery, "api", lambda token, method, **kw: sent.append(kw))
    repo = Repo([{"handle": "yenigaleri", "n": 5}])
    assert discovery.send_discovery(repo, "t", "1") == 1
    assert "disc:ekle:yenigaleri" in str(sent[0]["reply_markup"]) and "disc:gec:yenigaleri" in str(sent[0]["reply_markup"])
    assert discovery.send_discovery(repo, "t", "1") == 0 and len(sent) == 1  # haftada bir


def test_decide_records_choice(monkeypatch):
    repo = Repo([])
    assert "geçildi" in discovery.decide(repo, "gec", "x") and repo.state["disc:decided:x"] == "gec"
    monkeypatch.setattr(discovery.sources_cmd, "add_instagram", lambda r, h: "Eklendi: x")
    monkeypatch.setattr(discovery.sources_cmd, "change_status", lambda r, h, st: "x taramaya alındı")
    assert "taramaya alındı" in discovery.decide(repo, "ekle", "x")
