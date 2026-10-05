"""Toplama güvenilirliği: tick süresi/log, Apify maliyet kaydı, KKTCar/KibrisArabaal hata yakalama ve yenileme, Facebook görsel alanı."""
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from application import collect_facebook as cf
from application import collect_instagram as ci
from application import collect_kibrisarabaal as ckaa
from application import collect_kktcar as ckk
from entrypoints import tick
from infrastructure.collectors import apify_run, facebook_groups as fg, instagram_apify as ig
from infrastructure.collectors.apify_run import ApifyRunError
from infrastructure.collectors.facebook_groups import RawGroupPost, parse_item
from infrastructure.collectors.kibrisarabaal import parse_detail

NOW = datetime(2026, 10, 2, 9, tzinfo=timezone.utc)


# ---------- tick ----------
class StateRepo:
    def __init__(self):
        self.state, self.order = {}, []

    def set_state(self, k, v):
        self.state[k] = v
        self.order.append(("state", k))

    def get_state(self, k, d=None):
        return self.state.get(k, d)


def test_tick_state_written_only_after_job_finished():
    repo = StateRepo()

    def runner(job, r):
        assert f"tick:{job}" not in r.state  # iş çalışırken henüz yazılmamış
        r.order.append(("run", job))
        return []

    tick.run_batch(["kktcar"], repo, NOW, 0.0, [], runner=runner, clock=lambda: 0.0)
    assert repo.order == [("run", "kktcar"), ("state", "tick:kktcar")]


def test_tick_handled_error_still_marks_job_and_keeps_going(capsys):
    repo, errors = StateRepo(), []

    def runner(job, r):
        if job == "kktcar":
            raise RuntimeError("boom")
        return [(job, "x")]

    tick.run_batch(["kktcar", "mezunum"], repo, NOW, 0.0, errors, runner=runner, clock=lambda: 0.0)
    assert "tick:kktcar" in repo.state and "tick:mezunum" in repo.state
    assert errors[0][0] == "kktcar" and errors[1] == ("mezunum", "x")
    assert "iş mezunum: 0 sn" in capsys.readouterr().out  # her iş için süre satırı


def test_tick_skips_slow_job_without_enough_time_and_does_not_mark_it(capsys):
    repo = StateRepo()
    clock = iter([tick.TICK_BUDGET_S - 5 * 60, tick.TICK_BUDGET_S - 5 * 60]).__next__  # 5 dk kaldı: Facebook'a yetmez
    ran = []
    tick.run_batch(["facebook"], repo, NOW, 0.0, [], runner=lambda j, r: ran.append(j) or [], clock=clock)
    assert ran == [] and "tick:facebook" not in repo.state  # bir sonraki turda çalışır
    assert "atlandı" in capsys.readouterr().out


def test_tick_has_time_thresholds_and_facebook_first():
    assert tick.has_time(11 * 60, "facebook") and not tick.has_time(9 * 60, "facebook")
    assert tick.has_time(7 * 60, "instagram") and not tick.has_time(5 * 60, "instagram")
    assert tick.has_time(1, "kktcar")  # hızlı siteler her zaman çalışır
    assert tick.SLOW_JOBS[0] == "facebook"
    # en kötü durum süreleri bütçeye sığar: Facebook (çalıştırma + bekleme payı) en az payın altında kalmalı
    worst_fb = (fg.RUN_TIMEOUT + apify_run.GRACE).total_seconds() + cf.LLM_BUDGET_S + 75
    assert worst_fb <= tick.MIN_LEFT_S["facebook"]


def test_workflow_unbuffered_and_timeout():
    wf = (Path(__file__).parent.parent / ".github/workflows/tick.yml").read_text()
    assert 'PYTHONUNBUFFERED: "1"' in wf and "timeout-minutes: 20" in wf and "cancel-in-progress: false" in wf


def test_ci_workflow_least_privilege():
    """ci.yml (05.10.2026): iş akışı geneli yalnız okuma; dala yazma izni yalnız `live`'ı ilerleten `yayin` işinde. pip ile bağımlılık kuran
    işler checkout jetonunu diske bırakmaz (persist-credentials: false): bozulmuş bir paket `live`'a yazamasın. (YAML kütüphanesi yok: satır okunur.)"""
    text = (Path(__file__).parent.parent / ".github/workflows/ci.yml").read_text()
    lines = [re.sub(r"(^|\s+)#.*$", "", ln) for ln in text.splitlines()]  # yorumlar atılır
    top = lines[:lines.index("jobs:")]
    assert top[top.index("permissions:") + 1] == "  contents: read" and not any(ln.endswith(": write") for ln in top)
    jobs, name = {}, None
    for ln in lines[lines.index("jobs:") + 1:]:
        m = re.fullmatch(r"  ([\w-]+):", ln)
        if m:
            name = m.group(1)
            jobs[name] = []
        elif name and ln.strip():
            jobs[name].append(ln.strip())
    assert set(jobs) == {"test", "bildir", "db-test", "yayin"}
    checkouts = {name: sum(ln.startswith("- uses: actions/checkout@") for ln in body) for name, body in jobs.items()}
    assert checkouts == {"test": 1, "bildir": 0, "db-test": 1, "yayin": 1}
    for name, body in jobs.items():
        assert [ln for ln in body if ln.endswith(": write")] == (["contents: write"] if name == "yayin" else []), name
        assert body.count("persist-credentials: false") == (0 if name == "yayin" else checkouts[name]), name  # yayin jetonla git push yapar
    assert any(ln.startswith("run: git push origin") for ln in jobs["yayin"])


# ---------- Apify maliyet kaydı ----------
class FakeRunClient:
    def __init__(self, finished=None, wait_error=None, abort_cost=0.07):
        self.finished, self.wait_error, self.abort_cost, self.aborted = finished, wait_error, abort_cost, False

    def wait_for_finish(self, wait_duration=None):
        if self.wait_error:
            raise self.wait_error
        return self.finished

    def abort(self):
        self.aborted = True
        return SimpleNamespace(status="ABORTED", usage_total_usd=self.abort_cost, usage_usd=None)


class FakeActor:
    def start(self, **kw):
        return SimpleNamespace(id="r1", status="RUNNING", usage_total_usd=None, usage_usd=None)


class FakeApify:
    def __init__(self, run_client, items=None, dataset_error=None):
        self.rc, self.items, self.dataset_error = run_client, items or [], dataset_error

    def actor(self, name):
        return FakeActor()

    def run(self, run_id):
        return self.rc

    def dataset(self, ds):
        outer = self

        class DS:
            def iterate_items(self):
                if outer.dataset_error:
                    raise outer.dataset_error
                yield from outer.items
        return DS()


def done(cost, status="SUCCEEDED"):
    return SimpleNamespace(id="r1", status=status, usage_total_usd=cost, usage_usd=None, default_dataset_id="d")


def test_run_actor_returns_finished_run_and_logs_cost(capsys):
    client = FakeApify(FakeRunClient(finished=done(0.2985)))
    run = apify_run.run_actor(client, "a/b", {}, run_timeout=timedelta(minutes=5), label="facebook")
    assert run.usage_total_usd == 0.2985 and "maliyet=$0.2985" in capsys.readouterr().out


def test_run_actor_wait_failure_carries_cost_and_aborts():
    rc = FakeRunClient(wait_error=httpx.ReadTimeout("x"), abort_cost=0.07)
    with pytest.raises(ApifyRunError) as e:
        apify_run.run_actor(FakeApify(rc), "a/b", {}, run_timeout=timedelta(minutes=5), min_cost=0.008)
    assert rc.aborted and e.value.cost_usd == 0.07


def test_run_actor_unfinished_run_is_aborted_and_costed():
    rc = FakeRunClient(finished=SimpleNamespace(id="r1", status="RUNNING", usage_total_usd=0.03, usage_usd=None), abort_cost=0.05)
    with pytest.raises(ApifyRunError) as e:
        apify_run.run_actor(FakeApify(rc), "a/b", {}, run_timeout=timedelta(minutes=5), min_cost=0.008)
    assert rc.aborted and e.value.cost_usd == 0.05


def test_run_cost_prefers_known_usage_and_falls_back_to_minimum():
    assert apify_run.run_cost(SimpleNamespace(usage_total_usd=None, usage_usd=0.4), 0.0) == 0.4
    assert apify_run.run_cost(SimpleNamespace(usage_total_usd=None, usage_usd=None), 0.008) == 0.008


def test_facebook_dataset_read_failure_still_reports_cost(monkeypatch):
    client = FakeApify(FakeRunClient(finished=done(0.21, "TIMED-OUT")), dataset_error=httpx.ReadTimeout("x"))
    monkeypatch.setattr(fg, "ApifyClient", lambda token: client)
    with pytest.raises(ApifyRunError) as e:
        fg.fetch_group_posts("tok", ["https://www.facebook.com/groups/1"], 2, 40)
    assert e.value.cost_usd == 0.21


def test_instagram_dataset_read_failure_still_reports_cost(monkeypatch):
    client = FakeApify(FakeRunClient(finished=done(0.02)), dataset_error=httpx.ReadTimeout("x"))
    monkeypatch.setattr(ig, "ApifyClient", lambda token: client)
    with pytest.raises(ApifyRunError) as e:
        ig.fetch_posts("tok", ["a"], "2026-10-02")
    assert e.value.cost_usd == 0.02


class SpendRepo:
    def __init__(self):
        self.state = {"fb_spend:2026-10": "1.0"}

    def get_state(self, k, d=None):
        return self.state.get(k, "1.0" if k.startswith("ig_spend") else d)

    def set_state(self, k, v):
        self.state[k] = v

    def known_item_ids(self, sid):
        return set()


def test_facebook_failed_run_cost_is_recorded_then_error_propagates():
    repo = SpendRepo()

    def fetch(token, urls, hours, max_items):
        raise ApifyRunError("süre", 0.25)

    src = [dict(id="G", name="G", url="https://www.facebook.com/groups/1/", last_checked_at=None)]
    with pytest.raises(ApifyRunError):
        cf.collect_facebook_groups(repo, "tok", src, fetch=fetch, now=datetime(2026, 10, 2, tzinfo=timezone.utc))
    assert float(repo.state["fb_spend:2026-10"]) == pytest.approx(1.25)


def test_instagram_second_call_failure_keeps_first_call_cost(monkeypatch):
    repo = SpendRepo()
    calls = []

    def fetch(token, usernames, newer_than, limit=20):
        calls.append(usernames)
        if len(calls) == 2:
            raise ApifyRunError("süre", 0.03)
        got = ig.PostList()
        got.cost_usd = 0.02
        return got

    monkeypatch.setattr(ci, "fetch_posts", fetch)
    srcs = [dict(id="A", name="A", url="https://www.instagram.com/a/", cursor="2026-10-01T10:00:00"),
            dict(id="B", name="B", url="https://www.instagram.com/b/", cursor=None)]
    with pytest.raises(ApifyRunError):
        ci.collect_sources(repo, "tok", srcs)
    key = f"ig_spend:{datetime.now(timezone.utc):%Y-%m}"
    assert float(repo.state[key]) == pytest.approx(1.05)  # 1.0 önceki + 0.02 (ilk çağrı) + 0.03 (başarısız ikinci)


# ---------- Facebook görsel alanı (02.10.2026 gerçek veri şekli) ----------
def fb_item(**extra):
    return {"url": "https://www.facebook.com/groups/1/permalink/123/", "legacyId": "123", "text": "Satılık araç",
            "inputUrl": "https://www.facebook.com/groups/1/", "time": "2026-10-02T09:00:00.000Z", **extra}


def test_item_image_reads_attachments_photo_image_uri_not_page_url():
    item = fb_item(attachments=[{"__typename": "Photo", "thumbnail": "https://scontent-muc2-1.xx.fbcdn.net/t.jpg",
                                 "photo_image": {"uri": "https://scontent-muc2-1.xx.fbcdn.net/full.jpg", "height": 960, "width": 720},
                                 "id": "1", "url": "https://www.facebook.com/photo.php?fbid=1"}])
    assert fg.item_image(item) == "https://scontent-muc2-1.xx.fbcdn.net/full.jpg"
    assert parse_item(item).image_url == "https://scontent-muc2-1.xx.fbcdn.net/full.jpg"


def test_item_image_falls_back_to_thumbnail_and_never_uses_facebook_page_link():
    only_thumb = fb_item(attachments=[{"__typename": "Photo", "thumbnail": "https://scontent.fbcdn.net/t.jpg",
                                       "url": "https://www.facebook.com/photo.php?fbid=1"}])
    assert fg.item_image(only_thumb) == "https://scontent.fbcdn.net/t.jpg"
    only_page = fb_item(attachments=[{"__typename": "Photo", "url": "https://www.facebook.com/photo.php?fbid=1"}])
    assert fg.item_image(only_page) is None  # sayfa bağlantısı görsel değildir (eskiden bu indirilmeye çalışılıyordu)
    assert fg.item_image(fb_item()) is None


# ---------- Facebook km tamamlama / okuma bütçesi ----------
GROUP = "https://www.facebook.com/groups/1"
SOURCES = [dict(id="G1", name="Grup", url=GROUP + "/", last_checked_at=None)]
WITH_PRICE_NO_KM = "Satılık 2015 Toyota Vitz otomatik Girne temiz araç 5.500 STG"


class KmReader:
    last_error = None

    def __init__(self, km=90000):
        self.km, self.calls = km, 0

    def read(self, text):
        self.calls += 1
        return SimpleNamespace(is_car=True, km=self.km)

    def read_image(self, *a):
        return None


class FbRepo:
    def __init__(self):
        self.state, self.rows = {"fb_spend:2026-10": "0"}, {}

    def get_state(self, k, d=None):
        return self.state.get(k, d)

    def set_state(self, k, v):
        self.state[k] = v

    def upsert_listing(self, sid, item_id, data):
        new = (sid, item_id) not in self.rows
        self.rows.setdefault((sid, item_id), data)
        return new

    def mark_checked(self, *a, **k):
        pass

    def count_recent(self, sid):
        return 0

    def known_item_ids(self, sid):
        return {i for (s, i) in self.rows if s == sid}


def run_fb(monkeypatch, posts, reader):
    monkeypatch.setattr(cf, "gbp_rate", lambda c: 1.0)
    repo = FbRepo()
    res = cf.collect_facebook_groups(repo, "tok", SOURCES, fetch=lambda *a: (posts, 0.05, len(posts)),
                                     now=datetime(2026, 10, 2, tzinfo=timezone.utc), reader=reader, fetch_image=lambda u: None)
    return repo, res["Grup"]


def fb_post(i, text):
    return RawGroupPost(i, f"{GROUP}/permalink/{i}/", NOW, text, GROUP)


def test_missing_km_is_completed_by_llm_for_new_priced_post(monkeypatch):
    reader = KmReader()
    repo, st = run_fb(monkeypatch, [fb_post("1", WITH_PRICE_NO_KM)], reader)
    row = repo.rows[("G1", "1")]
    assert row["km"] == 90000 and row["price_gbp"] == 5500.0 and row["extraction_by"] == "parser_serbest"
    assert st.km_read == 1 and reader.calls == 1


def test_km_not_read_when_text_already_has_km_or_post_known(monkeypatch):
    reader = KmReader()
    run_fb(monkeypatch, [fb_post("1", WITH_PRICE_NO_KM.replace("Girne", "Girne 80.000 km"))], reader)
    assert reader.calls == 0  # km zaten yazıda: yapay zekâya gidilmez


def test_llm_stage_respects_time_budget(monkeypatch):
    monkeypatch.setattr(cf, "LLM_BUDGET_S", -1)
    reader = KmReader()
    repo, st = run_fb(monkeypatch, [fb_post("1", WITH_PRICE_NO_KM)], reader)
    assert reader.calls == 0 and repo.rows[("G1", "1")]["km"] is None and st.km_read == 0  # ilan yine kaydedilir, yalnız km tamamlanmaz


# ---------- KKTCar: tek ilanın zaman aşımı turu durdurmaz ----------
class Entry:
    def __init__(self, slug):
        self.slug, self.url, self.lastmod = slug, f"https://kktcar.com/{slug}", None


class KkRepo:
    def __init__(self):
        self.touched, self.refresh = [], []

    def stale_active(self, sid, hours, limit):
        return [dict(id=i, url=f"https://kktcar.com/{i}", source_item_id=i) for i in ("a", "b")]

    def touch(self, i):
        self.touched.append(i)

    def apply_refresh(self, i, row, data):
        self.refresh.append(i)
        return None


def test_kktcar_refresh_survives_http_error(monkeypatch):
    def fetch_detail(client, entry):
        if entry.slug == "a":
            raise httpx.ReadTimeout("x")
        return {"price_amount": 5000.0, "currency": "GBP"}

    monkeypatch.setattr(ckk.kktcar, "fetch_detail", fetch_detail)
    monkeypatch.setattr(ckk.kktcar, "polite_sleep", lambda: None)
    monkeypatch.setattr(ckk, "gbp_rate", lambda c: 1.0)
    repo, stats = KkRepo(), ckk.KktcarStats()
    ckk.refresh_active(repo, {"id": "S"}, None, stats)
    assert repo.touched == ["a"] and repo.refresh == ["b"] and stats.refreshed == 1


class KkCollectRepo(KkRepo):
    def known_item_ids(self, sid):
        return set()

    def upsert_listing(self, *a):
        return True

    def mark_checked(self, *a, **k):
        pass

    def count_recent(self, sid):
        return 0

    def get_state(self, k, d=None):
        return d

    def set_state(self, k, v):
        pass

    def stale_active(self, *a):
        return []


def test_kktcar_new_listing_timeout_counts_as_failed_and_read_rate_guard_still_trips(monkeypatch):
    entries = [Entry(f"s{i}") for i in range(6)]
    monkeypatch.setattr(ckk.kktcar, "fetch_sitemap", lambda c: entries)
    monkeypatch.setattr(ckk.kktcar, "new_client", lambda: httpx.Client())
    monkeypatch.setattr(ckk.kktcar, "polite_sleep", lambda: None)

    def boom(client, entry):
        raise httpx.ConnectTimeout("x")

    monkeypatch.setattr(ckk.kktcar, "fetch_detail", boom)
    with pytest.raises(RuntimeError, match="okunamadı"):  # tur çökmez, okuma oranı koruması uyarı yükseltir
        ckk.collect_kktcar(KkCollectRepo(), {"id": "S", "name": "KKTCar"})


# ---------- KibrisArabaal: yenileme ve fiyatsız ilan ----------
HTML = (Path(__file__).parent / "fixtures/kibrisarabaal_detail_demio.html").read_text()
NO_PRICE_HTML = HTML.replace('"price":"7450.00","priceCurrency":"GBP"',
                             '"priceSpecification":{"@type":"UnitPriceSpecification","price":"0","priceCurrency":"TRY",'
                             '"description":"Fiyat için satıcıyla iletişime geçin"}')


def test_price_on_request_listing_has_no_price_by_design():
    # canlı sayfa (02.10.2026): fiyatı gizleyen satıcılar JSON-LD'ye price "0" yazar: parse boşluğu değil, ilanın kendisi fiyatsız
    d = parse_detail(NO_PRICE_HTML)
    assert d["price_amount"] is None and d["urgency_signals"] == ["fiyatsiz"] and d["brand"] == "Mazda"


class KaaRepo:
    def __init__(self, n=3):
        self.rows = [dict(id=f"id{i}", url=f"https://kibrisarabaal.com/ilan/{i}-x", source_item_id=str(i)) for i in range(n)]
        self.touched, self.applied = [], []

    def stale_active(self, sid, hours, limit):
        return self.rows[:limit]

    def touch(self, i):
        self.touched.append(i)

    def apply_refresh(self, i, row, data):
        self.applied.append((i, data.get("price_gbp"), data.get("is_active")))
        return "fiyat" if data.get("price_gbp") == 100.0 else "pasif" if data.get("is_active") is False else None


def test_kaa_refresh_updates_price_deactivates_and_survives_errors(monkeypatch):
    seq = iter([{"price_amount": 100.0, "currency": "GBP"}, {"is_active": False, "urgency_signals": ["kaldirildi"]},
                httpx.ReadTimeout("x")])

    def fetch_detail(client, entry):
        v = next(seq)
        if isinstance(v, Exception):
            raise v
        return v

    monkeypatch.setattr(ckaa.kibrisarabaal, "fetch_detail", fetch_detail)
    monkeypatch.setattr(ckaa.kibrisarabaal, "polite_sleep", lambda: None)
    monkeypatch.setattr(ckaa, "gbp_rate", lambda c: 1.0)
    repo, stats = KaaRepo(), ckaa.KaaStats()
    ckaa.refresh_active(repo, {"id": "S"}, None, stats)
    assert stats.refreshed == 3 and stats.price_changes == 1 and stats.went_inactive == 1 and stats.refresh_failed == 1
    assert repo.touched == ["id2"]  # okunamayan: kaldırıldı SAYILMAZ, yalnızca sırası kayar


def test_kaa_refresh_stops_at_time_limit_and_limit(monkeypatch):
    monkeypatch.setattr(ckaa.kibrisarabaal, "fetch_detail", lambda c, e: {"price_amount": 5.0, "currency": "GBP"})
    monkeypatch.setattr(ckaa.kibrisarabaal, "polite_sleep", lambda: None)
    monkeypatch.setattr(ckaa, "gbp_rate", lambda c: 1.0)
    stats = ckaa.KaaStats()
    ckaa.refresh_active(KaaRepo(30), {"id": "S"}, None, stats, limit=25)
    assert stats.refreshed == 25
    stats = ckaa.KaaStats()
    ckaa.refresh_active(KaaRepo(30), {"id": "S"}, None, stats, max_seconds=-1)
    assert stats.refreshed == 0  # süre dolmuşsa tur uzamaz
