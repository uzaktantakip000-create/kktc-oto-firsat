"""KibrisArabaAl izin kapısı (application/kaa_gate, 21.10.2026 izin meclisi): kapı kapalıyken HİÇBİR yoldan siteye istek yok; açıkken
dürüst kimlik, robots.txt, kota, aralık, 403/429/503'te 24 saat bekleme ve 3 bekleme sonrası kalıcı durma; /durum ve alarmlar susar.
Gerçek ağ YOK: her istemci sahte, httpx'in kendisi de bu dosyada patlatılır (bir şey kaçarsa test kırılır)."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from application import collect_kibrisarabaal as kaa
from application import health, kaa_gate, liveness, source_alarm, status
from application.evaluate import Evaluated
from domain.comparables import Market
from domain.profit import Confidence, ProfitResult, Tier
from entrypoints import admin_cli, cron_collect, tick
from infrastructure.collectors import kibrisarabaal as site

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc)
REAL = datetime.now(timezone.utc)  # saati kendi okuyan yollar (cron_collect, canlılık kontrolü) için kayıtlar buna göre kurulur
HTML = (Path(__file__).parent / "fixtures/kibrisarabaal_detail_demio.html").read_text()
SOURCE = {"id": "s1", "name": "KibrisArabaAl", "url": "https://kibrisarabaal.com/"}
ROBOTS_OK = "User-agent: *\nAllow: /\n"
REAL_NEW_CLIENT = site.new_client  # fikstür patlatmadan önceki gerçek kurucu (yalnız nesne kurulur, istek atılmaz)


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """Hiçbir test gerçek ağa çıkamaz: httpx'in gönderimi ve KAA istemci kurucusu patlar (sahte istemci verilmeyen yol yakalanır)."""
    def boom(*a, **k):
        raise AssertionError("gerçek ağ isteği denendi")
    monkeypatch.setattr(httpx.Client, "send", boom)
    monkeypatch.setattr(site, "new_client", boom)
    monkeypatch.setattr(kaa, "gbp_rate", lambda currency: 1.0)


class Exploding:
    """Kapı kapalı testlerinin istemcisi: herhangi bir çağrı testi kırar."""

    def __getattr__(self, name):
        raise AssertionError(f"kapı kapalıyken istemci kullanıldı: {name}")


class Resp:
    def __init__(self, status, url, text="", headers=None):
        self.status_code, self.url, self.text, self.headers = status, url, text, headers or {}

    def raise_for_status(self):
        if not 200 <= self.status_code < 300:  # httpx gibi: yönlendirme de hata
            req = httpx.Request("GET", self.url)
            raise httpx.HTTPStatusError(str(self.status_code), request=req, response=httpx.Response(self.status_code, request=req))


def sitemap(*ids):
    return "<urlset>" + "".join(f"<url><loc>https://kibrisarabaal.com/ilan/{i}-x</loc><lastmod>2026-10-20T10:00:00+00:00</lastmod></url>"
                                for i in ids) + "</urlset>"


class FakeSite:
    """Sahte site: adres -> (durum, metin, başlıklar). Bilinmeyen ilan adresi demio sayfasını döner."""

    def __init__(self, robots=(200, ROBOTS_OK), ids=(101, 102, 103), detail=None, sitemap_status=200):
        self.robots, self.ids, self.detail, self.sitemap_status, self.urls = robots, ids, detail or {}, sitemap_status, []

    def get(self, url, timeout=30):
        self.urls.append(url)
        if url == site.ROBOTS:
            return Resp(self.robots[0], url, self.robots[1], self.robots[2] if len(self.robots) > 2 else None)
        if url == site.SITEMAP:
            return Resp(self.sitemap_status, url, sitemap(*self.ids))
        for key, (code, headers) in self.detail.items():
            if f"/ilan/{key}-" in url:
                return Resp(code, url, "", headers)
        return Resp(200, url, HTML)


class Clock:
    def __init__(self, now=NOW):
        self.now, self.slept = now, []

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += timedelta(seconds=s)


class Repo:
    def __init__(self, state=None, known=(), sources=None, alarm_rows=None, stale_rows=None, volume_rows=None):
        self.state, self.known = dict(state or {}), set(known)
        self.upserts, self.checked, self.marked, self.missing = [], [], [], []
        self.sources_rows, self.alarm_rows = sources or [SOURCE], alarm_rows or []
        self.stale_rows, self.volume_rows = stale_rows or [], volume_rows or []

    def get_state(self, key, default=None):
        return self.state.get(key, default)

    def set_state(self, key, value):
        self.state[key] = value

    def state_with_prefix(self, prefix):
        return {k[len(prefix):]: v for k, v in self.state.items() if k.startswith(prefix)}

    def known_item_ids(self, source_id):
        return self.known

    def upsert_listing(self, source_id, item_id, data):
        self.upserts.append(item_id)
        return True

    def mark_checked(self, *a, **k):
        self.checked.append(1)

    def count_recent(self, source_id):
        return 0

    def deactivate_missing(self, source_id, present):
        self.missing.append(set(present))
        return 0

    def sources(self, platform, statuses):
        return self.sources_rows

    def alert_recent(self, key, hours):
        return key in self.marked

    def mark_alerted(self, key):
        self.marked.append(key)

    def alarm_sources(self):
        return self.alarm_rows

    def stale_sources(self):
        return self.stale_rows

    def source_volume(self):
        return self.volume_rows

    def apply_refresh(self, listing_id, old, data):
        raise AssertionError("kapı kapalıyken ilan yenilenmemeli")


def run(repo, client, clock=None, **kw):
    clock = clock or Clock()
    return kaa.collect_kibrisarabaal(repo, SOURCE, now=clock(), client=client, sleep=clock.sleep, clock=clock, **kw)


def ev(listing_id="k1"):
    l = dict(id=listing_id, url=f"https://kibrisarabaal.com/ilan/{listing_id[1:] or 1}-toyota-vitz", source_item_id=listing_id[1:] or "1",
             price_gbp=5000.0, price_amount=5000.0, currency="GBP")
    return Evaluated(l, Market(5, 8000, 7000, 9000, 1, 0.0, 80_000), ProfitResult(7600, 2600, 0.52, Confidence.MEDIUM, Tier.STRONG), [], [], [])


def open_gate(monkeypatch):
    monkeypatch.setenv("KAA_IZIN", "1")
    monkeypatch.setenv("KKTC_RUNNER", "vps")
    monkeypatch.setenv("BOT_CONTACT_EMAIL", "bot@example.org")


# --- 1. kapı kapalı: sıfır istek ---

CLOSED_CASES = {
    "izin yok (varsayılan)": ({}, {}),
    "izin var ama GitHub/yerel (VPS değil)": ({"KAA_IZIN": "1", "BOT_CONTACT_EMAIL": "bot@example.org", "KKTC_RUNNER": ""}, {}),
    "izin 1 değil": ({"KAA_IZIN": "evet", "KKTC_RUNNER": "vps", "BOT_CONTACT_EMAIL": "bot@example.org"}, {}),
    "iletişim adresi yok": ({"KAA_IZIN": "1", "KKTC_RUNNER": "vps"}, {}),
    "tarayıcı kimliği taklidi": ({"KAA_IZIN": "1", "KKTC_RUNNER": "vps", "BOT_CONTACT_EMAIL": "bot@example.org",
                                  "KAA_USER_AGENT": "Mozilla/5.0 (Windows NT 10.0) Chrome/120"}, {}),
    "engel beklemesi": ({"KAA_IZIN": "1", "KKTC_RUNNER": "vps", "BOT_CONTACT_EMAIL": "bot@example.org"},
                        {kaa_gate.PAUSE_KEY: (REAL + timedelta(hours=5)).isoformat()}),
    "kalıcı durdurma": ({"KAA_IZIN": "1", "KKTC_RUNNER": "vps", "BOT_CONTACT_EMAIL": "bot@example.org"},
                        {kaa_gate.HALT_KEY: REAL.isoformat()}),
    "robots.txt yasak (önbellek)": ({"KAA_IZIN": "1", "KKTC_RUNNER": "vps", "BOT_CONTACT_EMAIL": "bot@example.org"},
                                    {kaa_gate.ROBOTS_KEY: json.dumps({"at": REAL.isoformat(), "status": 200,
                                                                      "text": "User-agent: KKTCOtoBot\nDisallow: /\n"})}),
}


@pytest.mark.parametrize("case", list(CLOSED_CASES))
def test_closed_gate_makes_zero_requests_from_every_path(monkeypatch, case, capsys):
    env, state = CLOSED_CASES[case]
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    if "KKTC_RUNNER" not in env:
        monkeypatch.delenv("KKTC_RUNNER", raising=False)
    repo = Repo(state)
    assert kaa_gate.idle_reason(repo) is not None
    stats = kaa.collect_kibrisarabaal(repo, SOURCE, client=Exploding())  # toplayıcı
    assert stats.skipped and stats.requests == 0 and repo.upserts == [] and repo.checked == []
    assert liveness.recheck_before_send(repo, [ev()], kaa_client=Exploding()) != []  # bildirim öncesi kontrol: istek yok, ilan geçer
    assert liveness.recheck_before_send(repo, [ev()]) != []  # istemci verilmese de kurulmaz (new_client patlardı)
    assert cron_collect.run("kibrisarabaal", repo) == []  # zamanlayıcı işi: ne hata ne başarı sayacı
    assert "fail:KibrisArabaAl" not in repo.state
    monkeypatch.setenv("DATABASE_URL", "postgresql://sahte")
    monkeypatch.setattr(admin_cli, "Repository", lambda dsn: repo)
    monkeypatch.setattr("infrastructure.fx.frankfurter.use_store", lambda repo: None)
    monkeypatch.setattr("sys.argv", ["admin_cli", "kaa"])
    admin_cli.backfill_kaa()  # elle geçmiş doldurma da kapıdan geçer
    assert "KibrisArabaAl okunmadı" in capsys.readouterr().out
    with pytest.raises(kaa_gate.GateClosed):
        with kaa_gate.open_client(repo, client=Exploding()):
            pass


def test_github_turn_never_reads_even_with_permission(monkeypatch):
    open_gate(monkeypatch)
    monkeypatch.delenv("KKTC_RUNNER")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert kaa_gate.idle_reason(Repo(), NOW) == "yalnız sunucuda okunur, burada istek atılmıyor" and not kaa_gate.gate_open()


def test_polite_client_itself_refuses_when_the_gate_closes(monkeypatch):
    """İkinci kilit: istemci kurulduktan sonra kapı kapanırsa (ortam değişirse) get yine istek atmaz."""
    web = kaa_gate.PoliteClient(Repo(), Exploding())
    with pytest.raises(kaa_gate.GateClosed):
        web.get(site.SITEMAP)


def test_missing_contact_is_logged_once(monkeypatch, capsys):
    monkeypatch.setenv("KAA_IZIN", "1")
    monkeypatch.setenv("KKTC_RUNNER", "vps")
    repo = Repo()
    for _ in range(3):
        assert "BOT_CONTACT_EMAIL" in kaa.collect_kibrisarabaal(repo, SOURCE, now=NOW, client=Exploding()).skipped
    assert capsys.readouterr().out.count("BOT_CONTACT_EMAIL") == 1


# --- 2. dürüst kimlik ---

def test_user_agent_names_the_bot_and_its_contact(monkeypatch):
    open_gate(monkeypatch)
    assert kaa_gate.user_agent() == "KKTCOtoBot/1.0 (+mailto:bot@example.org)"
    h = kaa_gate.headers()
    assert h["User-Agent"].startswith("KKTCOtoBot/") and "bot@example.org" in h["User-Agent"] and h["From"] == "bot@example.org"
    assert "Authorization" not in h


def test_user_agent_override_and_access_token(monkeypatch):
    open_gate(monkeypatch)
    monkeypatch.setenv("KAA_USER_AGENT", "KKTCOtoBot/1.0 (izinli; +mailto:bot@example.org)")
    monkeypatch.setenv("KAA_ACCESS_TOKEN", "sahte-anahtar")
    assert kaa_gate.headers()["User-Agent"] == "KKTCOtoBot/1.0 (izinli; +mailto:bot@example.org)"
    assert kaa_gate.headers()["Authorization"] == "sahte-anahtar"
    monkeypatch.setenv("KAA_TOKEN_HEADER", "X-Api-Key")
    assert kaa_gate.headers()["X-Api-Key"] == "sahte-anahtar" and "Authorization" not in kaa_gate.headers()
    monkeypatch.setenv("KAA_USER_AGENT", "Mozilla/5.0 Chrome/120")  # tarayıcı taklidi kabul edilmez
    assert kaa_gate.user_agent() is None and kaa_gate.headers() is None


def test_bad_contact_is_rejected(monkeypatch):
    open_gate(monkeypatch)
    for bad in ("", "adres-yok", "a@b", "a b@c.org"):
        monkeypatch.setenv("BOT_CONTACT_EMAIL", bad)
        assert kaa_gate.user_agent() is None


def test_client_is_plain_httpx_without_proxies_or_browser_fingerprint():
    c = REAL_NEW_CLIENT({"User-Agent": "KKTCOtoBot/1.0 (+mailto:bot@example.org)"})  # yalnız nesne kurulur, istek ATILMAZ
    try:
        assert type(c) is httpx.Client and c.trust_env is False and c.follow_redirects is False
        assert c.headers["User-Agent"].startswith("KKTCOtoBot/")
    finally:
        c.close()


def test_kaa_code_never_uses_browserlike_or_scrapling():
    """KibrisArabaAl'a tarayıcı parmak izli istemci (infrastructure/http/browserlike, Scrapling, curl_cffi) ASLA: engel aşılmaz."""
    import ast
    root = Path(__file__).resolve().parent.parent
    banned = ("browserlike", "scrapling", "curl_cffi", "patchright", "playwright")
    for rel in ("infrastructure/collectors/kibrisarabaal.py", "application/kaa_gate.py", "application/collect_kibrisarabaal.py",
                "application/liveness.py"):
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        names = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        names += [f"{n.module}.{a.name}" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names]
        assert not [m for m in names if any(b in m.lower() for b in banned)], rel


# --- 3. robots.txt ---

def test_robots_disallow_means_no_request(monkeypatch):
    open_gate(monkeypatch)
    web = FakeSite(robots=(200, "User-agent: KKTCOtoBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n"))
    repo = Repo()
    stats = run(repo, web)
    assert web.urls == [site.ROBOTS] and stats.skipped and stats.requests == 1  # yalnız robots.txt istendi
    web2 = FakeSite()
    assert run(repo, web2).skipped and web2.urls == []  # önbellek (24 saat): robots.txt bile yeniden istenmez
    assert kaa_gate.idle_reason(repo, NOW) == "robots.txt bize izin vermiyor, istek atılmıyor"


def test_robots_disallowed_listing_is_never_requested(monkeypatch):
    open_gate(monkeypatch)
    web = FakeSite(robots=(200, "User-agent: *\nDisallow: /ilan/102-\n"), ids=(101, 102))
    stats = run(Repo(), web)
    assert not any("/ilan/102-" in u for u in web.urls) and any("/ilan/101-" in u for u in web.urls) and stats.new == 1


def test_robots_401_disallows_everything_and_5xx_skips_the_round(monkeypatch):
    open_gate(monkeypatch)
    web = FakeSite(robots=(401, ""))
    assert run(Repo(), web).skipped and web.urls == [site.ROBOTS]
    web = FakeSite(robots=(500, ""))
    with pytest.raises(kaa_gate.RobotsUnavailable):  # tur HATA (sürerse kaynak alarmı); önbelleğe yazılmaz
        run(repo := Repo(), web)
    assert web.urls == [site.ROBOTS] and kaa_gate.ROBOTS_KEY not in repo.state
    web = FakeSite(robots=(404, ""))  # robots.txt yok: serbest
    assert run(Repo(), web).new == 3


def test_robots_is_read_at_most_once_a_day(monkeypatch):
    open_gate(monkeypatch)
    repo, clock = Repo(), Clock()
    run(repo, web := FakeSite(), clock)
    assert web.urls.count(site.ROBOTS) == 1
    clock.now += timedelta(hours=23)
    run(repo, web2 := FakeSite(), clock)
    assert site.ROBOTS not in web2.urls
    clock.now += timedelta(hours=2)
    run(repo, web3 := FakeSite(), clock)
    assert web3.urls[0] == site.ROBOTS


def test_crawl_delay_at_least_ten_seconds_also_after_the_sitemap(monkeypatch):
    open_gate(monkeypatch)
    clock = Clock()
    run(Repo(), FakeSite(ids=(101, 102)), clock)
    assert clock.slept == [10.0, 10.0, 10.0]  # robots -> harita -> ilan -> ilan: her aralık ≥ 10 sn (haritadan sonra da)
    clock = Clock()
    run(Repo(), FakeSite(robots=(200, "User-agent: *\nCrawl-delay: 25\n"), ids=(101,)), clock)
    assert clock.slept == [25.0, 25.0]  # robots.txt'in istediği daha uzunsa o


def test_pause_between_runs_counts_across_processes(monkeypatch):
    """Aralık bot_state'te: canlılık kontrolü toplayıcıdan hemen sonra gelse de 10 sn beklenir."""
    open_gate(monkeypatch)
    repo, clock = Repo(), Clock()
    run(repo, FakeSite(ids=()), clock)
    clock.slept.clear()
    with kaa_gate.open_client(repo, client=FakeSite(), sleep=clock.sleep, clock=clock) as web:
        site.fetch_detail(web, site.Entry("https://kibrisarabaal.com/ilan/101-x", "101", None))
    assert clock.slept == [10.0]


# --- 4. hacim ---

def test_each_listing_once_and_no_rereading_of_known_listings(monkeypatch):
    open_gate(monkeypatch)
    repo = Repo()
    web = FakeSite(ids=(101, 102, 103))
    assert run(repo, web).new == 3
    repo.known |= {"101", "102", "103"}
    web2 = FakeSite(ids=(101, 102, 103, 104))
    clock = Clock(NOW + timedelta(hours=1))
    stats = run(repo, web2, clock)
    assert web2.urls == [site.SITEMAP, "https://kibrisarabaal.com/ilan/104-x"] and stats.new == 1 and stats.requests == 2
    assert not hasattr(kaa, "refresh_active")  # aktif ilan yenilemesi (eski trafiğin ~%90'ı) yok


def test_hourly_quota(monkeypatch):
    open_gate(monkeypatch)
    repo = Repo({kaa_gate.HOUR_KEY: f"{NOW:%Y-%m-%dT%H}|57"})
    web = FakeSite(ids=(101, 102, 103, 104))
    stats = run(repo, web)
    assert len(web.urls) == 3 and stats.requests == 3 and "kota" in stats.stopped  # 57 + 3 = 60: robots, harita, bir ilan
    assert repo.state[kaa_gate.HOUR_KEY] == f"{NOW:%Y-%m-%dT%H}|60" and repo.state[f"kaa_req:{NOW:%Y-%m-%d}"] == "3"
    web2 = FakeSite()
    assert "kota" in run(repo, web2).skipped and web2.urls == []
    later = Clock(NOW + timedelta(hours=1))  # yeni saat: sayaç sıfırdan
    repo.known |= {"101", "102", "103", "104"}
    web3 = FakeSite(ids=(101, 102, 103, 104, 105))
    assert run(repo, web3, later).new == 1 and web3.urls == [site.SITEMAP, "https://kibrisarabaal.com/ilan/105-x"]


def test_daily_quota(monkeypatch):
    open_gate(monkeypatch)
    repo = Repo({f"kaa_req:{NOW:%Y-%m-%d}": "300"})
    web = FakeSite()
    assert "kota" in run(repo, web).skipped and web.urls == []
    tomorrow = Clock(NOW + timedelta(days=1))
    assert run(repo, FakeSite(ids=(101,)), tomorrow).new == 1


def test_counter_write_failure_means_no_request(monkeypatch):
    open_gate(monkeypatch)

    class BrokenRepo(Repo):
        def set_state(self, key, value):
            if key.startswith("kaa_req"):
                raise RuntimeError("veritabanı yok")
            super().set_state(key, value)

    web = FakeSite()
    with pytest.raises(RuntimeError, match="veritabanı yok"):
        run(BrokenRepo(), web)
    assert web.urls == []


def test_schedule_is_every_30_minutes_by_day_and_2_hours_by_night():
    assert tick.SCHEDULE["kibrisarabaal"] == (30, 120)


# --- 5. engel ---

def test_403_on_a_detail_page_stops_the_run_and_pauses_24_hours(monkeypatch):
    open_gate(monkeypatch)
    sent = []
    monkeypatch.setattr(health, "notify_owner", lambda repo, key, text, repeat_hours=12, **k: sent.append((key, text, repeat_hours)) or True)
    web = FakeSite(ids=(101, 102, 103), detail={103: (403, None)})  # en yeni ilan önce okunur: ilk ilan sayfası 403
    repo = Repo()
    stats = run(repo, web)
    assert web.urls == [site.ROBOTS, site.SITEMAP, "https://kibrisarabaal.com/ilan/103-x"]  # 102 ve 101 istenmedi
    assert stats.skipped and "403" in stats.skipped and repo.upserts == [] and repo.checked == []
    assert repo.state[kaa_gate.PAUSE_KEY] == (NOW + timedelta(hours=24)).isoformat()
    assert [(k, h) for k, _, h in sent] == [("kaa_blocked", 30 * 24)] and "24 saat" in sent[0][1]
    web2 = FakeSite()
    assert run(repo, web2, Clock(NOW + timedelta(hours=23))).skipped and web2.urls == []  # beklemede: sıfır istek
    repo.state[kaa_gate.PAUSE_KEY] = (REAL + timedelta(hours=23)).isoformat()  # zamanlayıcı işi saati kendi okur
    assert cron_collect.run("kibrisarabaal", repo) == [] and "fail:KibrisArabaAl" not in repo.state  # bekleme alarm sayılmaz


@pytest.mark.parametrize("code", [429, 503])
def test_429_and_503_also_block_and_retry_after_is_honoured(monkeypatch, code):
    open_gate(monkeypatch)
    monkeypatch.setattr(health, "notify_owner", lambda *a, **k: True)
    repo = Repo()
    web = FakeSite(ids=(101,), detail={101: (code, {"Retry-After": str(3 * 24 * 3600)})})
    run(repo, web)
    assert repo.state[kaa_gate.PAUSE_KEY] == (NOW + timedelta(days=3)).isoformat()  # site 3 gün dedi: 24 saatten uzun, ona uyulur


def test_sitemap_or_robots_403_also_blocks(monkeypatch):
    open_gate(monkeypatch)
    monkeypatch.setattr(health, "notify_owner", lambda *a, **k: True)
    repo = Repo()
    web = FakeSite(robots=(403, ""))
    assert run(repo, web).skipped and web.urls == [site.ROBOTS] and kaa_gate.PAUSE_KEY in repo.state
    repo = Repo()
    web = FakeSite(sitemap_status=403)
    assert run(repo, web).skipped and web.urls == [site.ROBOTS, site.SITEMAP] and kaa_gate.PAUSE_KEY in repo.state


def test_three_pauses_still_blocked_stop_for_good_until_a_human_reopens(monkeypatch):
    open_gate(monkeypatch)
    sent = []
    monkeypatch.setattr(health, "notify_owner", lambda repo, key, text, repeat_hours=12, **k:
                        (key not in repo.marked) and (repo.mark_alerted(key) or sent.append(text) or True))
    repo, when = Repo(), NOW
    for _ in range(4):  # ilk engel + 3 bekleme sonrası hâlâ engel
        assert kaa_gate.idle_reason(repo, when) is None
        run(repo, FakeSite(sitemap_status=403), Clock(when))
        when += timedelta(hours=24, minutes=1)
    assert repo.state[kaa_gate.HALT_KEY] and "onay" in kaa_gate.idle_reason(repo, when + timedelta(days=60))
    assert len(sent) == 1  # sahibe 30 günde tek mesaj
    web = FakeSite()
    assert run(repo, web, Clock(when + timedelta(days=60))).skipped and web.urls == []  # kendiliğinden yeniden denemez
    kaa_gate.reopen(repo)  # insan onayı (admin_cli kaa-ac)
    assert kaa_gate.idle_reason(repo, when) is None and run(repo, FakeSite(), Clock(when)).new == 3


def test_a_successful_sitemap_resets_the_block_streak(monkeypatch):
    open_gate(monkeypatch)
    monkeypatch.setattr(health, "notify_owner", lambda *a, **k: True)
    repo = Repo()
    run(repo, FakeSite(sitemap_status=403))
    assert repo.state[kaa_gate.STREAK_KEY] == "1"
    run(repo, FakeSite(ids=()), Clock(NOW + timedelta(hours=25)))
    assert repo.state[kaa_gate.STREAK_KEY] == "0"


def test_liveness_403_stops_checking_and_pauses(monkeypatch):
    open_gate(monkeypatch)
    monkeypatch.setattr(health, "notify_owner", lambda *a, **k: True)
    monkeypatch.setattr(kaa_gate.time, "sleep", lambda s: None)
    repo = Repo()
    web = FakeSite(detail={1: (403, None), 2: (200, None)})
    out = liveness.recheck_before_send(repo, [ev("k1"), ev("k2")], kaa_client=web)
    assert len(out) == 2  # doğrulanamadı diye fırsat kaçmaz
    assert web.urls == [site.ROBOTS, "https://kibrisarabaal.com/ilan/1-toyota-vitz"] and kaa_gate.PAUSE_KEY in repo.state


def test_retry_after_parsing():
    assert site.retry_after("120") == timedelta(seconds=120)
    assert site.retry_after("Wed, 21 Oct 2026 12:00:00 GMT", NOW) == timedelta(hours=3)
    assert site.retry_after("bozuk") is None and site.retry_after(None) is None
    assert site.retry_after("9" * 30) == timedelta(days=366)  # taşmaz
    assert site.retry_after("Wed, 21 Oct 2026 08:00:00 GMT", NOW) is None  # geçmiş


# --- 6. /durum, sabah nabzı, alarmlar ---

class StatusRepo(Repo):
    def __init__(self, rows, **kw):
        super().__init__({"tick:last": NOW.isoformat()}, **kw)
        self.rows, self.conn = rows, self

    def execute(self, sql, params=None):
        rows = self.rows if "FROM sources s" in sql else [
            {"strong": 0, "est": 0} if "FROM alerts" in sql else {"total": 10, "ok": 5} if "LATERAL" in sql else {"n": 0}]

        class R:
            def fetchall(_):
                return rows

            def fetchone(_):
                return rows[0]
        return R()

    def shadow_summary(self, platform="facebook", hours=24):
        return None


def row(name, url, hours):
    return dict(name=name, platform="web", url=url, status="aktif", alert_level="yesil", hours_since_check=hours, new_24h=0, fresh_n=0)


KAA_ROW = row("KibrisArabaAl", "https://kibrisarabaal.com/", 264.0)  # 10.10'dan beri taranmadı


def test_status_shows_a_neutral_waiting_line_and_no_delay_while_the_gate_is_closed():
    repo = StatusRepo([row("KKTCar", "https://www.kktcar.com/", 0.2), KAA_ROW])
    text = status.build_status(repo, NOW)
    assert "⏸ Site: KibrisArabaAl · izin bekleniyor (10.10'dan beri engelli, istek atılmıyor)" in text
    assert "GECİKMİŞ" not in text and "gecikme" not in text and "✅ Sistem çalışıyor" in text
    assert status.late_sources(repo, NOW) == []
    beat = status.build_heartbeat(repo, NOW)
    assert "gecikme" not in beat


def test_status_still_flags_kaa_as_late_once_the_gate_is_open(monkeypatch):
    open_gate(monkeypatch)
    repo = StatusRepo([KAA_ROW])
    text = status.build_status(repo, NOW)
    assert "⚠️ GECİKMİŞ — Site: KibrisArabaAl" in text and "⚠️ 1 yerde gecikme" in text


def test_status_shows_the_block_pause(monkeypatch):
    open_gate(monkeypatch)
    repo = StatusRepo([KAA_ROW])
    repo.state[kaa_gate.PAUSE_KEY] = (NOW + timedelta(hours=10)).isoformat()
    text = status.build_status(repo, NOW)
    assert "⏸ Site: KibrisArabaAl · site engelledi, istek atılmıyor (bekleme bitişi 21.10 22:00)" in text and "GECİKMİŞ" not in text


def test_no_source_alarm_or_recovery_message_while_gated(monkeypatch):
    out = []
    monkeypatch.setattr(source_alarm, "notify_owner", lambda repo, key, text, repeat_hours=12: out.append(text) or True)
    alarm_rows = [dict(id="s1", name="KibrisArabaAl", platform="web", url="https://kibrisarabaal.com/", hours_since_check=264.0)]
    repo = Repo({"fail:KibrisArabaAl": "900", "failmsg:KibrisArabaAl": "HTTPStatusError: 403", "srcalarm:KibrisArabaAl": ""},
                alarm_rows=alarm_rows)
    assert source_alarm.check_source_alarms(repo) == 0 and out == []
    repo.state["srcalarm:KibrisArabaAl"] = "1"  # eski alarm duruyor: kapı kapalıyken "tekrar çalışıyor" da denmez
    assert source_alarm.check_source_alarms(repo) == 0 and out == []
    open_gate(monkeypatch)  # kapı açıkken gerçek arıza yine alarm verir
    repo.state["srcalarm:KibrisArabaAl"] = ""
    assert source_alarm.check_source_alarms(repo) == 1 and "KibrisArabaAl" in out[0]


def test_no_health_warning_for_kaa_while_gated():
    stale = [dict(id="s1", name="KibrisArabaAl", platform="web", url="https://kibrisarabaal.com/", created_at=None, listings_7d=0,
                  hours_since_check=264.0),
             dict(id="s2", name="KKTCar", platform="web", url="https://www.kktcar.com/", created_at=None, listings_7d=3, hours_since_check=30.0)]
    volume = [dict(id="s1", name="KibrisArabaAl", url="https://kibrisarabaal.com/", per_day=90.0, last_new=NOW - timedelta(days=11))]
    repo = Repo(stale_rows=stale, volume_rows=volume)
    assert [k for k, _ in health.source_problems(repo)] == ["stale:s2"]
    assert health.volume_problems(repo, NOW) == []
