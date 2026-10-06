import json
from datetime import datetime, timedelta, timezone
from random import Random

import pytest

from application import collect_facebook as cf
from application import collect_instagram as ci
from application import social_run as sr
from application.social_port import Cursor, DailyCap, FetchResult, SocialPost, SocialSource, SocialStop, SourceError, Unreachable
from domain.social_brake import Severity, Signal

NOW = datetime(2026, 10, 5, 9, tzinfo=timezone.utc)  # 12:00 KKTC
IP = "203.0.113.7"  # belgeleme aralığı (gerçek değil)
CAR = "Satılık 2013 Toyota Auris 1.6 benzin otomatik 98.000 km 7.250 STG"


@pytest.fixture(autouse=True)
def no_fx(monkeypatch):
    monkeypatch.setattr(cf, "gbp_rate", lambda c: 1.0)
    monkeypatch.setattr(ci, "gbp_rate", lambda c: 1.0)


def src(i, priority=None):
    return SocialSource("facebook", f"90{i}", f"https://www.facebook.com/groups/90{i}/", f"fb-{i}", slug=f"gizli-grup-{i}",
                        priority=priority if priority is not None else i)


def posts(key, *ids, text=CAR):
    return [SocialPost("facebook", key, pid, f"https://www.facebook.com/groups/{key}/posts/{pid}/", NOW, text) for pid in ids]


class Store:
    def __init__(self, state=None):
        self.state = dict(state or {})

    def get_state(self, key, default=None):
        return self.state.get(key, default)

    def set_state(self, key, value):
        self.state[key] = value


class Sink:
    def __init__(self):
        self.records = []

    def record_post(self, alias, post, data, reason):
        self.records.append((alias, post.post_id, reason))
        return reason is None

    def upsert_listing(self, source_id, item_id, data):
        raise AssertionError

    def known_item_ids(self, source_id):
        return set()


class Fetcher:
    """script: kaynak anahtarı -> FetchResult ya da fırlatılacak hata."""

    platform = "facebook"

    def __init__(self, script=None, ip=IP):
        self.script, self.ip = script or {}, ip
        self.calls, self.closed, self.egress_calls = [], 0, 0

    def check_egress(self):
        self.egress_calls += 1
        return self.ip

    def fetch_new(self, source, cursor, max_posts):
        self.calls.append((source.key, cursor, max_posts))
        got = self.script.get(source.key)
        if isinstance(got, BaseException):
            raise got
        return got or FetchResult(posts=[], cursor=cursor, seen=3, requests=1)

    def close(self):
        self.closed += 1


def run(fetcher, sources, store=None, sink=None, now=NOW, expected_ip=IP, log=None, sleeps=None, **kw):
    store = store if store is not None else Store()
    lines = log if log is not None else []
    rep = sr.run_cycle("facebook", fetcher, sources, store, sink or Sink(), now=now, sleep=(sleeps.append if sleeps is not None else lambda s: None),
                       rng=Random(3), expected_ip=expected_ip, log=lines.append, **kw)
    return rep, store


def started(store, days=10):
    store.state["social:started_at:facebook"] = (NOW - timedelta(days=days)).isoformat()
    return store


def test_cursor_saved_and_used_next_time_and_listing_written():
    c1 = Cursor("p2", NOW)
    f = Fetcher({"901": FetchResult(posts("901", "p2", "p1"), c1, seen=5, requests=2)})
    sink = Sink()
    rep, store = run(f, [src(1)], sink=sink)
    assert rep.status == sr.OK and rep.sources[0].stats.new == 2 and rep.sources[0].seen == 5
    assert json.loads(store.state["social:cursor:facebook:901"]) == {"post_id": "p2", "posted_at": NOW.isoformat()}
    assert f.calls[0][1] == Cursor() and f.calls[0][2] == sr.MAX_POSTS  # ilk okuma: boş imleç
    assert sink.records == [("fb-1", "p2", None), ("fb-1", "p1", None)]
    f2 = Fetcher()
    run(f2, [src(1)], store=store, now=NOW + timedelta(hours=5))
    assert f2.calls[0][1] == c1  # kaldığı yerden
    assert store.state["social:started_at:facebook"] == NOW.isoformat()  # ilk tur zamanı değişmez
    hb = json.loads(store.state["social:heartbeat:facebook"])
    assert hb["sources"] == 1 and hb["status"] == sr.OK


def test_empty_cursor_from_fetcher_does_not_erase_known_cursor():
    store = Store({"social:cursor:facebook:901": json.dumps({"post_id": "p9", "posted_at": NOW.isoformat()})})
    run(Fetcher({"901": FetchResult([], Cursor(), seen=4)}), [src(1)], store=store)
    assert json.loads(store.state["social:cursor:facebook:901"])["post_id"] == "p9"


def test_hard_brake_refuses_without_network():
    store = Store({"social:brake:facebook": json.dumps({"signal": "checkpoint", "reason": "doğrulama", "at": NOW.isoformat()})})
    f = Fetcher()
    rep, _ = run(f, [src(1)], store=store)
    assert rep.status == sr.SKIP_BRAKE and f.egress_calls == 0 and f.calls == [] and f.closed == 1
    assert "social:next_after:facebook" not in store.state and "social:started_at:facebook" not in store.state
    garbage = Store({"social:brake:facebook": "bozuk"})
    assert run(Fetcher(), [src(1)], store=garbage)[0].status == sr.SKIP_BRAKE  # okunamayan fren de frendir


def test_soft_pause_refuses_until_it_expires():
    store = Store({"social:paused_until:facebook": (NOW + timedelta(hours=1)).isoformat()})
    f = Fetcher()
    assert run(f, [src(1)], store=store)[0].status == sr.SKIP_PAUSE and f.calls == []
    rep, _ = run(f, [src(1)], store=store, now=NOW + timedelta(hours=2))
    assert rep.status == sr.OK and len(f.calls) == 1


def test_ip_mismatch_is_hard_and_nothing_read():
    f = Fetcher(ip="198.51.100.9")
    rep, store = run(f, [src(1), src(2)])
    assert rep.status == sr.STOPPED and rep.brake.signal is Signal.IP_CHANGED and rep.brake.severity is Severity.HARD
    assert f.calls == [] and f.closed == 1
    assert json.loads(store.state["social:brake:facebook"])["signal"] == "ip_changed"
    assert sr.current_block(store, "facebook", NOW).kind == "fren"


def test_missing_expected_ip_stops_before_asking_egress():
    f = Fetcher()
    rep, store = run(f, [src(1)], expected_ip="  ")
    assert rep.brake.signal is Signal.IP_CHANGED and f.egress_calls == 0 and f.calls == []
    assert store.state["social:brake:facebook"]


def test_source_error_skips_only_that_source_and_is_counted_then_cleared():
    f = Fetcher({"901": SourceError("grup bulunamadı https://www.facebook.com/groups/gizli-grup-1/ (901)")})
    store = started(Store())
    rep, _ = run(f, [src(1), src(2)], store=store)
    assert rep.status == sr.OK and sorted(c[0] for c in f.calls) == ["901", "902"]  # sıra karışık
    err = json.loads(store.state["social:source_errors:facebook:901"])
    assert err["count"] == 1 and "gizli-grup" not in err["detail"] and "901" not in err["detail"]
    run(f, [src(1), src(2)], store=store)
    assert json.loads(store.state["social:source_errors:facebook:901"])["count"] == 2  # üst üste
    assert "social:cursor:facebook:901" not in store.state
    run(Fetcher(), [src(1), src(2)], store=store)
    assert store.state["social:source_errors:facebook:901"] == ""


def test_social_stop_persists_soft_pause_and_stops_cycle():
    f = Fetcher({"902": SocialStop(Signal.RATE_LIMITED, "429")})
    store = started(Store())
    srcs = [src(1, 1), src(2, 2), src(3, 3)]
    rep, _ = run(f, srcs, store=store)
    assert rep.status == sr.STOPPED and rep.brake.severity is Severity.SOFT
    assert f.calls[-1][0] == "902" and f.closed == 1  # fren sonrası başka kaynak okunmaz
    assert store.state["social:paused_until:facebook"] == (NOW + timedelta(hours=48)).isoformat()
    assert len(json.loads(store.state["social:soft_history:facebook"])) == 1
    assert not store.state.get("social:brake:facebook")
    assert json.loads(store.state["social:heartbeat:facebook"])["status"] == sr.STOPPED


def test_second_soft_within_7_days_becomes_hard():
    store = started(Store({"social:soft_history:facebook": json.dumps([(NOW - timedelta(days=3)).isoformat()])}))
    rep, _ = run(Fetcher({"901": SocialStop(Signal.RATE_LIMITED)}), [src(1)], store=store)
    assert rep.brake.severity is Severity.HARD and json.loads(store.state["social:brake:facebook"])["signal"] == "rate_limited"
    assert len(json.loads(store.state["social:soft_history:facebook"])) == 2
    assert sr.resume(store, "facebook") is True and sr.current_block(store, "facebook", NOW) is None
    assert len(json.loads(store.state["social:soft_history:facebook"])) == 2  # resume geçmişi silmez


def test_all_sources_empty_is_soft_anomaly_but_single_empty_is_not():
    empty = {k: FetchResult([], Cursor(), seen=0) for k in ("901", "902")}
    rep, store = run(Fetcher(empty), [src(1), src(2)], store=started(Store()))
    assert rep.brake.signal is Signal.EMPTY_ANOMALY and rep.brake.severity is Severity.SOFT
    assert store.state["social:paused_until:facebook"]
    rep, _ = run(Fetcher({"901": FetchResult([], Cursor(), seen=0)}), [src(1)], store=started(Store()))
    assert rep.brake is None and rep.status == sr.OK
    mixed = {"901": FetchResult([], Cursor(), seen=0), "902": FetchResult([], Cursor(), seen=2)}
    assert run(Fetcher(mixed), [src(1), src(2)], store=started(Store()))[0].brake is None


def test_daily_cap_stops_quietly():
    f = Fetcher({"901": DailyCap()})
    store = started(Store())
    rep, _ = run(f, [src(1, 1), src(2, 2), src(3, 3)], store=store)
    assert rep.status == sr.CAPPED and rep.brake is None and f.calls[-1][0] == "901" and f.closed == 1  # sonrası okunmaz
    assert not store.state.get("social:brake:facebook") and not store.state.get("social:paused_until:facebook")
    assert store.state["social:next_after:facebook"]


def test_unexpected_error_still_closes_and_schedules():
    f = Fetcher({"901": RuntimeError("beklenmedik")})
    store = Store()
    with pytest.raises(RuntimeError):
        run(f, [src(1)], store=store)
    assert f.closed == 1 and store.state["social:next_after:facebook"]  # hata da olsa sonraki tur zamanı yazılır (sık deneme yok)
    assert json.loads(store.state["social:heartbeat:facebook"])["status"] == sr.ERROR


def test_ingest_failure_keeps_cursor_and_continues(monkeypatch):
    calls = []

    def boom(platform, posts, source, sink, **kw):
        calls.append(source.alias)
        if source.alias == "fb-1":
            raise ValueError("parser")
        return sr.IngestStats(fetched=len(posts))

    monkeypatch.setattr(sr, "ingest", boom)
    f = Fetcher({"901": FetchResult(posts("901", "a"), Cursor("a", NOW), seen=1),
                 "902": FetchResult(posts("902", "b"), Cursor("b", NOW), seen=1)})
    rep, store = run(f, [src(1), src(2)], store=started(Store()))
    assert sorted(calls) == ["fb-1", "fb-2"] and "social:cursor:facebook:901" not in store.state
    assert json.loads(store.state["social:cursor:facebook:902"])["post_id"] == "b"
    assert json.loads(store.state["social:source_errors:facebook:901"])["detail"] == "işleme hatası (ValueError)"


def test_slow_start_and_sleeps_between_sources():
    srcs = [src(i) for i in range(1, 6)]
    sleeps = []
    f = Fetcher()
    rep, store = run(f, srcs, sleeps=sleeps)
    assert len(f.calls) == 3 and {c[0] for c in f.calls} == {"901", "902", "903"} and rep.slow_start and rep.interval_h == 4
    assert len(sleeps) == 2 and all(180 <= s <= 360 for s in sleeps)
    nxt = datetime.fromisoformat(store.state["social:next_after:facebook"])
    assert NOW + timedelta(hours=3, minutes=45) <= nxt <= NOW + timedelta(hours=4, minutes=15)
    f2 = Fetcher()
    rep, _ = run(f2, srcs, store=started(Store(), days=8))
    assert len(f2.calls) == 5 and rep.interval_h == 2 and not rep.slow_start


def test_only_aliases_in_logs_and_report():
    secret_err = SourceError("Grup bulunamadı: https://www.facebook.com/groups/gizli-grup-2/ kimlik 902 gizli-grup-2")
    f = Fetcher({"901": FetchResult(posts("901", "a"), Cursor("a", NOW), seen=1), "902": secret_err,
                 "903": SocialStop(Signal.CHECKPOINT, "https://www.facebook.com/checkpoint/")})
    log = []
    rep, _ = run(f, [src(1, 1), src(2, 2), src(3, 3)], store=started(Store()), log=log)
    text = "\n".join(log + rep.lines() + rep.summary())
    for s in (src(1), src(2), src(3)):
        assert s.key not in text and s.slug not in text and s.url not in text
    assert "fb-2" in text and "facebook.com" not in text


def test_proxy_down_ends_cycle_without_brake_and_without_reading():
    class Down(Fetcher):
        def check_egress(self):
            raise Unreachable("çıkış IP'si okunamadı: ConnectError http://proxy.example.net:8000")

    f = Down()
    rep, store = run(f, [src(1), src(2)], store=started(Store()))
    assert rep.status == sr.UNREACHABLE and rep.brake is None and f.calls == [] and f.closed == 1
    assert not store.state.get("social:brake:facebook") and not store.state.get("social:paused_until:facebook")
    assert "proxy.example.net" not in rep.note and "social:next_after:facebook" in store.state  # sık denemeye dönmez


def test_three_source_errors_in_row_end_cycle_without_brake():
    script = {f"90{i}": SourceError("okunamadı") for i in range(1, 5)}
    f = Fetcher(script)
    rep, store = run(f, [src(i) for i in range(1, 6)], store=started(Store()))
    assert rep.status == sr.ERRORS_IN_ROW and rep.brake is None and len(f.calls) == sr.MAX_ERRORS_IN_ROW
    assert not store.state.get("social:brake:facebook") and f.closed == 1


def test_two_source_errors_never_end_the_cycle():
    f = Fetcher({"901": SourceError("x"), "902": SourceError("y")})  # 4 kaynaktan yalnız 2'si hatalı: 3'lük seri olamaz
    rep, _ = run(f, [src(1), src(2), src(3), src(4)], store=started(Store()))
    assert rep.status == sr.OK and len(f.calls) == 4 and sum(1 for r in rep.sources if r.error) == 2


# ---------------------------------------------------------------- durum dosyası satırı

def _hb(status, new=3, errors=0):
    return json.dumps({"at": NOW.isoformat(), "sources": 2, "seen": 9, "new": new, "errors": errors, "status": status})


def test_status_entry_none_before_first_cycle():
    assert sr.status_entry(Store(), "instagram", NOW) is None


def test_status_entry_healthy_cycle_has_utc_iso_times():
    nxt = NOW + timedelta(hours=4)
    st = Store({"social:heartbeat:instagram": _hb(sr.CAPPED, new=5, errors=1), "social:next_after:instagram": nxt.isoformat()})
    assert sr.status_entry(st, "instagram", NOW) == {
        "son_tur_utc": "2026-10-05T09:00:00+00:00", "sonuc": "tamam", "fren_nedeni": None,
        "sonraki_tur_utc": "2026-10-05T13:00:00+00:00", "yeni_ilan": 5, "kaynak_hatasi": 1}


@pytest.mark.parametrize("status", [sr.ERROR, sr.UNREACHABLE, sr.ERRORS_IN_ROW])
def test_status_entry_failed_cycle_is_hata(status):
    assert sr.status_entry(Store({"social:heartbeat:facebook": _hb(status)}), "facebook", NOW)["sonuc"] == "hata"


def test_status_entry_brake_masks_ip_and_pause_is_fren():
    brake = json.dumps({"signal": "ip_changed", "reason": "çıkış IP'si 198.51.100.9, beklenen 203.0.113.7", "at": NOW.isoformat()})
    e = sr.status_entry(Store({"social:brake:facebook": brake}), "facebook", NOW)
    assert e["sonuc"] == "fren" and "<ip>" in e["fren_nedeni"] and "198.51" not in e["fren_nedeni"] and e["son_tur_utc"] is None
    paused = Store({"social:paused_until:instagram": (NOW + timedelta(hours=48)).isoformat(), "social:heartbeat:instagram": _hb(sr.STOPPED)})
    e = sr.status_entry(paused, "instagram", NOW)
    assert e["sonuc"] == "fren" and "duraklama" in e["fren_nedeni"]
    assert sr.status_entry(Store({"social:heartbeat:instagram": _hb(sr.STOPPED)}), "instagram", NOW)["sonuc"] == "tamam"  # resume sonrası
