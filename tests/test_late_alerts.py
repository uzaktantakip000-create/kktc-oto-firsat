"""/gec (application/late_alerts, 17.11.2026): tazelik süzgecine takılmış 🟢'ler, canlılık kontrolünden geçerse sahibe 🕰 etiketiyle gider."""
from datetime import datetime, timedelta, timezone

import pytest

from application import kaa_gate, late_alerts as la
from application.notify import TelegramError

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def gate_open(monkeypatch):
    monkeypatch.setattr(kaa_gate, "gate_open", lambda *a, **k: True)


def row(i, source="KibrisArabaAl", **over):
    base = {"id": f"L{i}", "url": f"https://www.kibrisarabaal.com/x/{i}", "source_item_id": str(i), "year": 2012, "brand": "Nissan", "model": f"M{i}", "km": 90000,
            "price_amount": 3500.0, "currency": "GBP", "price_gbp": 3500.0, "first_seen_at": NOW - timedelta(days=9),
            "posted_at": NOW - timedelta(days=11), "photo_urls": None, "source_name": source, "platform": "web", "comparables_n": 11,
            "market_median_gbp": 4800.0, "profit_gbp": 900.0, "profit_pct": 27.1, "method": "A", "brand_norm": f"b{i}", "model_norm": f"m{i}",
            "price_changed_at": None}
    return base | over


class Repo:
    def __init__(self, rows):
        self.rows, self.saved = rows, []

    def unnotified_strong(self, rules_version, days, limit):
        return list(self.rows)

    def resurfaced_kktcarabam(self, ids):
        return set()

    def save_alert(self, listing_id, chat_id, tier, msg_id, *, evaluation_id, price_gbp, kind=None):
        self.saved.append((listing_id, chat_id, tier, kind))


def run(rows, alive=None, fail=()):
    sent = []

    def api(token, method, **kw):
        if kw["text"].split("\n")[0] and any(f in kw["text"] for f in fail):
            raise TelegramError("sendMessage", 429, "x")
        sent.append(kw)
        return {"message_id": len(sent)}

    def recheck(repo, cands):
        return [c for c in cands if alive is None or c.listing["id"] in alive]

    repo = Repo(rows)
    return repo, sent, la.send(repo, "t", "42", NOW, api_call=api, recheck=recheck, sleep=lambda s: None)


def test_live_ads_are_sent_with_late_label_and_recorded_once():
    repo, sent, reply = run([row(1), row(2)], alive={"L1"})
    assert len(sent) == 1 and sent[0]["text"].startswith("🕰 GEÇ BİLDİRİM") and "kibrisarabaal.com/x/1" in sent[0]["text"]
    assert repo.saved == [("L1", "42", "guclu", "gec")] and "1 geç bildirim" in reply and "1 tanesi satılmış" in reply


def test_unwatchable_source_is_not_sent():
    """Facebook/KKTCarabam: satıldı mı izlenemiyor, 'hâlâ satışta' doğrulanamaz."""
    repo, sent, reply = run([row(1, source="Bir Grup", platform="facebook", url="https://www.facebook.com/groups/1/posts/1")])
    assert sent == [] and reply == la.NONE_LEFT


def test_failed_send_is_not_recorded_and_max_per_run():
    repo, sent, _ = run([row(1), row(2)], fail=("M1",))
    assert [s["text"].count("M2") > 0 for s in sent] == [True] and [x[0] for x in repo.saved] == ["L2"]
    repo, sent, reply = run([row(i) for i in range(1, 13)])
    assert len(sent) == la.MAX_SEND and "aday daha var" in reply
