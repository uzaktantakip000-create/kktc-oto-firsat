"""KibrisArabaAl (kibrisarabaal.com) izin kapısı ve nazik istemci (21.10.2026, izin meclisi bulguları).
10.10.2026'dan beri site botu 403 ile engelliyor; engel AŞILMAZ, tek meşru yol sitenin izni. İzin gelene kadar kapı KAPALI: hiçbir yerden
(toplayıcı, bildirim öncesi canlılık kontrolü, admin_cli kaa, GitHub turları) siteye TEK istek gitmez. Kapı ancak üç şartla açık:
KAA_IZIN=1, VPS'te (runner_gate.on_vps; GitHub turları hiç okumaz) ve engel beklemesi/durdurma yok. Açıkken her istek PoliteClient'tan geçer:
- dürüst kimlik: "KKTCOtoBot/1.0 (+mailto:<BOT_CONTACT_EMAIL>)" + From başlığı (adres yoksa ÇALIŞMAZ), düz httpx, proxy yok (trust_env=False);
  KAA_USER_AGENT tam değiştirir ama tarayıcı kimliği (Mozilla/...) olamaz; site anahtar verirse KAA_ACCESS_TOKEN (+ KAA_TOKEN_HEADER);
- robots.txt günde en çok bir kez okunur (bot_state 'kaa_robots'): can_fetch("KKTCOtoBot") ve max(10 sn, Crawl-delay) aralık;
  401/403 = her şey yasak, 5xx = bu tur yok;
- kota: saatte 60, günde 300 istek (bot_state 'kaa_req:<tarih>'); sayaç yazılamazsa istek de yok;
- 403/429/503 (hangi adres olursa): Blocked -> tur hemen durur, 24 saat (Retry-After daha uzunsa o kadar) istek yok; sahibe 30 günde
  tek mesaj; 3 bekleme bitip site hâlâ engelliyse kalıcı durur (insan açar: admin_cli kaa-ac). sources.status'a otomatik yazılmaz.
Kapı kapalıyken KibrisArabaAl /durum'da nötr "⏸" satırıdır: gecikme sayılmaz, kaynak alarmı/sağlık uyarısı vermez (idle_reason)."""
import json
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

import httpx

from application.runner_gate import on_vps
from domain.kktc_time import to_kktc
from infrastructure.collectors import kibrisarabaal
from infrastructure.collectors.kibrisarabaal import AGENT, ROBOTS, SITEMAP, Blocked

GATE_ENV = "KAA_IZIN"  # "1" = sahip izin geldiğini söyledi (yalnız VPS ortam dosyasında)
CONTACT_ENV = "BOT_CONTACT_EMAIL"  # sitenin bize ulaşacağı adres: User-Agent ve From başlığında
UA_ENV = "KAA_USER_AGENT"  # isteğe bağlı: sitenin istediği kimlik (tam değiştirir)
TOKEN_ENV, TOKEN_HEADER_ENV = "KAA_ACCESS_TOKEN", "KAA_TOKEN_HEADER"  # site anahtar verirse; başlık adı yoksa Authorization
BLOCKED_SINCE = "10.10"  # /durum metni: engelin başladığı gün

MIN_DELAY_S = 10.0  # iki istek arası en az (robots.txt daha uzununu isterse o)
MAX_WAIT_S = 60.0  # tur içinde bundan uzun beklenmez: istek sonraki tura kalır (tick bütçesi 13 dk)
HOUR_MAX, DAY_MAX = 60, 300  # istek kotası (UTC saat / gün)
ROBOTS_TTL = timedelta(hours=24)
ROBOTS_MAX_CHARS = 50_000
PAUSE = timedelta(hours=24)
MAX_PAUSE = timedelta(days=30)  # Retry-After bundan uzunsa kırpılır (bozuk/abartılı başlık kaydı sonsuza kilitlemesin)
HALT_AFTER = 3  # bu kadar bekleme bitip site hâlâ engelliyse kalıcı dur
WARN_REPEAT_H = 30 * 24  # sahibe engel mesajı en çok 30 günde bir

PAUSE_KEY = "kaa_blocked_until"  # UTC ISO; bu ana kadar istek yok (eski 2 saatlik beklemenin anahtarı, aynı ad)
STREAK_KEY = "kaa_block_streak"  # üst üste engel sayısı; site haritası okununca 0
HALT_KEY = "kaa_halted"  # doluysa (UTC ISO) kalıcı durdu: yalnız insan temizler (admin_cli kaa-ac)
ROBOTS_KEY = "kaa_robots"  # {"at", "status", "text"}
LAST_REQ_KEY = "kaa_last_req"  # son isteğin anı: aralık süreçler arası da korunur (tick + canlılık kontrolü)
REQ_PREFIX = "kaa_req:"  # kaa_req:<YYYY-MM-DD> = o günün istek sayısı
HOUR_KEY = "kaa_req_saat"  # "<YYYY-MM-DDTHH>|n": bu saatin istek sayısı (tek satır, her saat üzerine yazılır)
LOG_KEY = "kaa_idle_log"  # son yazılan "neden okunmuyor" (aynı neden günlüğe bir kez yazılır)
_REDIRECTS = (301, 302, 303, 307, 308)
_EMAIL = re.compile(r"[^@\s<>()\"]+@[^@\s<>()\"]+\.[^@\s<>()\"]+")


class Deferred(Exception):
    """İstek bu tur atılmadı (kota, uzun aralık, robots.txt okunamadı): hata değil, sonraki turda devam."""


class GateClosed(Deferred):
    """Kapı kapalı: siteye istek YOK."""


class Disallowed(Exception):
    """robots.txt bu adresi bize yasaklıyor: istenmez."""


class RobotsUnavailable(RuntimeError):
    """robots.txt 5xx/ağ hatası/yabancı yönlendirme: bu tur siteye istek yok (tur HATA sayılır: sürerse kaynak alarmı)."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def is_kaa_url(url: str | None) -> bool:
    """Kaynak/ilan satırı KibrisArabaAl'ın mı (sources.url, listings.url)."""
    return "kibrisarabaal.com" in (url or "")


def _kaa_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "kibrisarabaal.com" or host.endswith(".kibrisarabaal.com")


def contact() -> str | None:
    email = (os.environ.get(CONTACT_ENV) or "").strip()
    return email if _EMAIL.fullmatch(email) else None


def user_agent() -> str | None:
    """Dürüst kimlik; iletişim adresi yoksa ya da KAA_USER_AGENT tarayıcı taklidiyse None (çalışılmaz)."""
    email = contact()
    if not email:
        return None
    override = (os.environ.get(UA_ENV) or "").strip()
    if override:
        return None if "mozilla/" in override.lower() else override
    return f"{AGENT}/1.0 (+mailto:{email})"


def headers() -> dict[str, str] | None:
    ua = user_agent()
    if ua is None:
        return None
    out = {"User-Agent": ua, "From": contact()}
    token = (os.environ.get(TOKEN_ENV) or "").strip()
    if token:
        out[(os.environ.get(TOKEN_HEADER_ENV) or "").strip() or "Authorization"] = token
    return out


def gate_open() -> bool:
    """Yalnız ortam: KAA_IZIN=1 ve VPS. Kayıtsız hızlı bakış (haftalık rapordaki "satıldı mı izlenebilir" etiketi gibi)."""
    return os.environ.get(GATE_ENV) == "1" and on_vps()


def _when(raw: str | None) -> datetime | None:
    try:
        when = datetime.fromisoformat(raw) if raw else None
    except (TypeError, ValueError):
        return None
    return when if when is not None and when.tzinfo is not None else None


def paused_until(repo, now: datetime | None = None) -> datetime | None:
    """Engel beklemesi sürüyorsa bitiş anı; kayıt yok/bozuk/saat dilimsiz/geçmiş ya da akıl dışı ileri tarihli ise None."""
    now = now or _utcnow()
    until = _when(repo.get_state(PAUSE_KEY))
    return until if until is not None and now < until <= now + MAX_PAUSE + timedelta(minutes=5) else None


def _cached_robots(repo, now: datetime) -> tuple[int, str] | None:
    try:
        data = json.loads(repo.get_state(ROBOTS_KEY) or "null")
        at, status, text = _when(data["at"]), int(data["status"]), str(data.get("text") or "")
    except (TypeError, ValueError, KeyError):
        return None
    if at is None or not (now - ROBOTS_TTL < at <= now + timedelta(minutes=5)):
        return None
    return status, text


def idle_reason(repo, now: datetime | None = None) -> str | None:
    """None: KibrisArabaAl şu an okunabilir. Metin: neden okunmuyor (/durum'daki nötr satır; gecikme ve alarm sayılmaz).
    Kayıt okunamazsa okunmuyor sayılır: emin olunamayan durumda siteye istek gitmez."""
    now = now or _utcnow()
    if os.environ.get(GATE_ENV) != "1":
        return f"izin bekleniyor ({BLOCKED_SINCE}'dan beri engelli, istek atılmıyor)"
    if not on_vps():
        return "yalnız sunucuda okunur, burada istek atılmıyor"
    if contact() is None:
        return "izin açık ama iletişim adresi (BOT_CONTACT_EMAIL) eksik, istek atılmıyor"
    if user_agent() is None:
        return "KAA_USER_AGENT tarayıcı kimliği taklidi gibi (kabul edilmez), istek atılmıyor"
    try:
        if repo.get_state(HALT_KEY):
            return "site üst üste engelledi, durduruldu; yeniden denemek için onay gerekiyor (istek atılmıyor)"
        until = paused_until(repo, now)
        if until is not None:
            return f"site engelledi, istek atılmıyor (bekleme bitişi {to_kktc(until):%d.%m %H:%M})"
        cached = _cached_robots(repo, now)
    except Exception as e:
        return f"durum kaydı okunamadı ({type(e).__name__}), istek atılmıyor"
    if cached is not None and not kibrisarabaal.robots_policy(*cached).can_fetch(AGENT, SITEMAP):
        return "robots.txt bize izin vermiyor, istek atılmıyor"
    return None


def log_idle(repo, reason: str) -> None:
    """Okunmama nedenini günlüğe yazar; aynı neden bir kez (her turda tekrar yazılmaz). Kayıt hatası turu bozmaz."""
    try:
        if repo.get_state(LOG_KEY) == reason:
            return
        repo.set_state(LOG_KEY, reason)
    except Exception:
        pass
    print(f"KibrisArabaAl: {reason}", flush=True)


def mark_reachable(repo) -> None:
    """Site haritası okundu: üst üste engel sayacı sıfırlanır (kalıcı durdurma 3 ARDIŞIK beklemeden sonradır)."""
    try:
        if (repo.get_state(STREAK_KEY) or "0") != "0":
            repo.set_state(STREAK_KEY, "0")
        if repo.get_state(LOG_KEY):
            repo.set_state(LOG_KEY, "")  # yeniden okunmazsa nedeni yine bir kez yazılsın
    except Exception as e:
        print("KibrisArabaAl: engel sayacı sıfırlanamadı:", type(e).__name__)


def _int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def on_blocked(repo, err: Blocked, now: datetime | None = None) -> datetime:
    """403/429/503: 24 saat (Retry-After daha uzunsa o, en çok 30 gün) istek yok; üst üste HALT_AFTER bekleme sonrası yine engelse
    kalıcı durdurma. Sahibe en çok 30 günde bir mesaj (health.notify_owner 'kaa_blocked'). Dönen: bekleme bitişi."""
    now = now or _utcnow()
    wait = max(PAUSE, min(err.retry_after, MAX_PAUSE)) if err.retry_after else PAUSE
    until = now + wait
    streak = 1
    try:
        streak = _int(repo.get_state(STREAK_KEY)) + 1
        repo.set_state(PAUSE_KEY, until.isoformat())
        repo.set_state(STREAK_KEY, str(streak))
        if streak > HALT_AFTER:
            repo.set_state(HALT_KEY, now.isoformat())
    except Exception as e:  # kayıt yazılamazsa: bu tur zaten durdu; sonraki tur yine tek istekle engeli görür
        print("KibrisArabaAl: engel beklemesi yazılamadı:", type(e).__name__)
    halted = streak > HALT_AFTER
    print(f"KibrisArabaAl: HTTP {err.status}, " + ("üst üste engel: kalıcı durduruldu" if halted else
          f"{wait.total_seconds() / 3600:.0f} saat istek yok ({until:%d.%m %H:%M} UTC)"), flush=True)
    text = ("⚠️ KibrisArabaAl erişimimizi " + ("yine " if streak > 1 else "") + f"engelledi (HTTP {err.status}). "
            + (f"Üst üste {streak} kez engel: denemeyi tamamen durdurdum, yeniden denemek için onayın gerekiyor."
               if halted else
               f"Siteye {wait.total_seconds() / 3600:.0f} saat hiç istek atmıyorum (bekleme bitişi {to_kktc(until):%d.%m %H:%M}). "
               f"Engel sürerse {HALT_AFTER} beklemeden sonra tamamen dururum.")
            + " Engeli aşmaya çalışmıyorum; durum /durum'da. Bu uyarıyı 30 günde bir gönderirim.")
    try:
        from application.health import notify_owner  # health bu modülü içe aktarıyor: döngü olmasın diye burada
        notify_owner(repo, "kaa_blocked", text, repeat_hours=WARN_REPEAT_H)
    except Exception as e:
        print("KibrisArabaAl engel uyarısı gönderilemedi:", type(e).__name__)
    return until


def reopen(repo) -> None:
    """İnsan onayıyla kalıcı durdurmayı ve beklemeyi kaldırır (admin_cli kaa-ac). Kapı (KAA_IZIN) ayrıca açık olmalı."""
    for key in (HALT_KEY, PAUSE_KEY, STREAK_KEY, LOG_KEY):
        repo.set_state(key, "")


class PoliteClient:
    """Siteye giden HER isteğin tek yolu (toplayıcı, canlılık kontrolü). `inner`: düz httpx istemcisi (testlerde sahte).
    Her get: kapı (ortam) -> robots.txt (izin + aralık) -> kota -> aralık beklemesi -> sayaç -> istek -> 403/429/503 ise Blocked.
    Yönlendirme izlenmez (her adım ayrı, sayılan istek: kibrisarabaal.fetch_detail)."""

    def __init__(self, repo, inner, sleep=None, clock=None):
        self.repo, self.inner = repo, inner
        self.sleep, self.clock = sleep or time.sleep, clock or _utcnow  # varsayılanlar çağrı anında çözülür (testler değiştirebilsin)
        self.requests = 0  # bu oturumda siteye gerçekten giden istek
        self._rp = None
        self._delay = MIN_DELAY_S
        self._robots_failed: str | None = None

    def get(self, url: str, timeout: float = 30):
        if not gate_open():
            raise GateClosed("kapı kapalı")
        if not _kaa_host(url):
            raise ValueError("KibrisArabaAl dışı adres")
        if not self._robots().can_fetch(AGENT, url):
            raise Disallowed(urlparse(url).path)
        return self._send(url, timeout)

    def _robots(self):
        if self._rp is not None:
            return self._rp
        if self._robots_failed:
            raise Deferred(self._robots_failed)  # bu oturumda robots.txt bir kez denendi: tekrar istenmez
        cached = _cached_robots(self.repo, self.clock())
        status, text = cached if cached is not None else self._fetch_robots()
        self._rp = kibrisarabaal.robots_policy(status, text)
        self._delay = max(MIN_DELAY_S, kibrisarabaal.crawl_delay(self._rp))
        return self._rp

    def _fetch_robots(self) -> tuple[int, str]:
        url = ROBOTS
        try:
            for _ in range(kibrisarabaal.MAX_HOPS + 1):
                r = self._send(url, 30)
                if r.status_code not in _REDIRECTS:
                    break
                url = urljoin(url, (getattr(r, "headers", None) or {}).get("location") or "")
                if not _kaa_host(url):
                    raise RobotsUnavailable("robots.txt başka siteye yönleniyor: bu tur istek yok")
            else:
                raise RobotsUnavailable("robots.txt yönlendirme döngüsü: bu tur istek yok")
            if r.status_code >= 500:
                raise RobotsUnavailable(f"robots.txt HTTP {r.status_code}: bu tur istek yok")
        except httpx.HTTPError as e:
            self._robots_failed = f"robots.txt okunamadı ({type(e).__name__}): bu tur istek yok"
            raise RobotsUnavailable(self._robots_failed) from None
        except RobotsUnavailable as e:
            self._robots_failed = str(e)
            raise
        text = r.text[:ROBOTS_MAX_CHARS] if r.status_code == 200 else ""
        self.repo.set_state(ROBOTS_KEY, json.dumps({"at": self.clock().isoformat(), "status": r.status_code, "text": text}))
        return r.status_code, text

    def _send(self, url: str, timeout: float):
        now = self.clock()
        day_key, hour_tag = f"{REQ_PREFIX}{now:%Y-%m-%d}", f"{now:%Y-%m-%dT%H}"
        day_n = _int(self.repo.get_state(day_key))
        tag, _, n = (self.repo.get_state(HOUR_KEY) or "").partition("|")
        hour_n = _int(n) if tag == hour_tag else 0
        if day_n >= DAY_MAX or hour_n >= HOUR_MAX:
            raise Deferred(f"istek kotası doldu (bugün {day_n}/{DAY_MAX}, bu saat {hour_n}/{HOUR_MAX})")
        last = _when(self.repo.get_state(LAST_REQ_KEY))
        if last is not None:
            wait = self._delay - max(0.0, (now - last).total_seconds())
            if wait > MAX_WAIT_S:
                raise Deferred(f"robots.txt aralığı ({self._delay:.0f} sn) bu turu aşıyor")
            if wait > 0:
                self.sleep(wait)
        now = self.clock()
        # sayaç ve an İSTEKTEN ÖNCE yazılır: yazılamazsa istek de gitmez (hata yükselir); başarısız istek de sayılır
        self.repo.set_state(day_key, str(day_n + 1))
        self.repo.set_state(HOUR_KEY, f"{hour_tag}|{hour_n + 1}")
        self.repo.set_state(LAST_REQ_KEY, now.isoformat())
        self.requests += 1
        r = self.inner.get(url, timeout=timeout)
        kibrisarabaal.raise_if_blocked(r)
        return r


@contextmanager
def open_client(repo, client=None, sleep=None, clock=None):
    """Kapı AÇIKKEN siteye giden tek yol. Kapı kapalıysa GateClosed (çağıran zaten idle_reason'a bakmış olmalı: bu ikinci kilit).
    `client` verilirse (testler) o sarılır ve kapatılmaz; yoksa düz httpx istemcisi açılır (kibrisarabaal.new_client)."""
    clock = clock or _utcnow
    if idle_reason(repo, clock()) is not None:
        raise GateClosed("kapı kapalı")
    own = client is None
    inner = client if client is not None else kibrisarabaal.new_client(headers())
    try:
        yield PoliteClient(repo, inner, sleep=sleep, clock=clock)
    finally:
        if own:
            inner.close()
