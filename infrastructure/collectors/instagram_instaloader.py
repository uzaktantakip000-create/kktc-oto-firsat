"""Instagram okuyucusu (VPS işçisi), sözleşme: application/social_port.py (build/login).

Herkese açık galeri hesaplarının imleçten YENİ gönderilerinin yalnız meta verisi okunur: kod, tarih, açıklama, ilk görselin
CDN adresi. Görsel/video İNDİRİLMEZ, yorum okunmaz, takip/beğeni/mesaj YOK. Ağır kütüphaneler (instaloader, requests,
playwright) yalnız işlev İÇİNDE import edilir.

Instaloader 4.15.3 (kaynağı okunarak doğrulandı, 05.10.2026):
- Profile.from_username() kullanılmaz: dayandığı api/v1/users/web_profile_info bazı işletme hesaplarında 400
  ("ig_business_category_subvertical"), bazı ağlarda ilk istekte 429 veriyor (GitHub #2724, #2726). Onun yerine
  Profile.get_posts()'un giriş yapılmış dalındaki zaman tüneli sorgusu (doc_id 7898261790222653, kullanıcı adıyla) doğrudan
  kurulur (PR #2743 ile aynı yol): hesap başına 1 istek eksik, bozuk uç yok.
- Gönderi alanları ham düğümden okunur (Post.from_iphone_struct'un okuduğu alanların aynısı): Post.url giriş yapılmışken
  gönderi başına ek i.instagram.com isteği atar, Post.is_pinned 4.15.3'te hep False döner.
- copy_session() proxy'yi ve kancaları kopyalamaz (GraphQL istekleri proxy'siz çıkardı): sarılır (harden_copy_session).
- max_connection_attempts=1: 429'da RateController.handle_429 hiç çağrılmaz, hata ConnectionException olarak gelir.
Bilinen risk (yalnız not): Instaloader düz python-requests ile HTTP/1.1 konuşur; TLS parmak izi tarayıcınınki değil."""
import ipaddress
import json
import os
import re
import time
import unicodedata
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

from application.social_port import (Cursor, DailyCap, FetchResult, Platform, SocialPost, SocialSource, SocialStop, SourceError,
                                     Unreachable)
from domain.social_brake import Signal

INSTALOADER_VERSION = "4.15.3"  # aşağıdaki iç yapılar (copy_session, context._session, doc_id) bu sürümde doğrulandı
TIMELINE_DOC_ID = "7898261790222653"  # 4.15.3 Profile.get_posts(), giriş yapılmış dal
PROXY_ENV = "SOCIAL_PROXY_INSTAGRAM"  # http://kullanıcı:şifre@sunucu:port (tek sabit konut proxy'si)
TZ_ENV = "SOCIAL_BROWSER_TZ"
BROWSER_TZ = "Europe/Istanbul"  # proxy IP'si Türkiye'de: giriş tarayıcısının saat dilimi buna uysun
SESSION_FILE = "instagram_session"  # JSON: kullanıcı adı + çerezler + tarayıcı kimliği (chmod 600)
BUDGET_FILE = "instagram_queries.json"  # {"day": "YYYY-MM-DD", "count": n} (UTC günü)
BROWSER_DIR = "instagram_browser"  # giriş tarayıcısının profili (cihaz tanınsın, her girişte "yeni cihaz" olmasın)

DAILY_QUERY_CAP = 150  # okuyucu hesabın günlük Instagram sorgu tavanı (tüm kaynaklar toplamı)
MIN_QUERY_GAP_S = 5.0  # iki sorgu arası en az; Instaloader'ın kendi aralığı (super) da ayrıca geçerli
REQUEST_TIMEOUT_S = 30.0
PINNED_MAX = 3  # Instagram en çok 3 gönderi sabitler; hep en üstte gelir
OLD_RUN_STOP = PINNED_MAX + 1  # art arda bu kadar eski gönderi = dur (en üstteki sabitler tolere edilir)
FIRST_RUN_DAYS = 3  # imleçsiz ilk okuma yalnız son 3 gün
FIRST_RUN_MAX_POSTS = 24  # ilk okumada en çok 2 sayfa
MAX_DETAIL = 300

EGRESS_URL = "https://api.ipify.org?format=json"
INSTAGRAM_URL = "https://www.instagram.com/"
LOGIN_URL = "https://www.instagram.com/accounts/login/"
LOGIN_TIMEOUT_S = 15 * 60
LOGIN_POLL_S = 3.0
LOGIN_SETTLE_S = 5.0  # sessionid görüldükten sonra kalan çerezler (csrftoken, ds_user_id) yerleşsin
WEBRTC_POLICY = "disable_non_proxied_udp"  # Chromium: proxy'den geçmeyen UDP yok (STUN ile gerçek IP sızmaz)
USERNAME_RE = re.compile(r"[a-z0-9._]{1,30}")
MEDIA_TYPES = {1: "GraphImage", 2: "GraphVideo", 8: "GraphSidecar"}  # 4.15.3 Post.from_iphone_struct ile aynı

LOADER_OPTIONS = {  # Instaloader(...): indirme/dosya yok, tekrar deneme yok, iPhone ucu (gönderi başına ek istek) kapalı
    "sleep": True, "quiet": True, "download_pictures": False, "download_videos": False, "download_video_thumbnails": False,
    "download_geotags": False, "download_comments": False, "save_metadata": False, "compress_json": False,
    "post_metadata_txt_pattern": "", "storyitem_metadata_txt_pattern": "", "max_connection_attempts": 1,
    "request_timeout": REQUEST_TIMEOUT_S, "resume_prefix": None, "iphone_support": False,
}

# Hata metninde (URL'siz, küçük harf) ya da yönlendirme yolunda aranan parçalar -> sinyal. İlk eşleşen kazanır (ağır olan önce).
STOP_MARKERS: tuple[tuple[str, Signal], ...] = (
    ("checkpoint", Signal.CHECKPOINT),
    ("challenge", Signal.CHECKPOINT),
    ("auth_platform", Signal.CHECKPOINT),  # 2026'da checkpoint_url /auth_platform/?apc=... (GitHub #2728)
    ("feedback_required", Signal.FEEDBACK_REQUIRED),
    ("redirected to login", Signal.LOGIN_REQUIRED),
    ("/accounts/login", Signal.LOGIN_REQUIRED),
    ("login required", Signal.LOGIN_REQUIRED),
    ("logged out", Signal.LOGIN_REQUIRED),
    ("useragent mismatch", Signal.AUTH_ERROR),
    ("try again later", Signal.TEMP_BLOCKED),
    ("restrict certain activity", Signal.TEMP_BLOCKED),
    ("please wait a few minutes", Signal.RATE_LIMITED),
    ("too many requests", Signal.RATE_LIMITED),
)
_SOURCE_ERRORS = {"BadResponseException", "QueryReturnedBadRequestException", "KeyError", "IndexError"}
_LOGIN_FLOW = ("/accounts/login", "/challenge", "/checkpoint", "/two_factor", "/auth_platform")
_USERINFO = re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^\s/@]+@")
_STATUS = re.compile(r"(?:^|: )([1-5]\d\d) [A-Za-z]")

LOGIN_HELP = """
Instagram girişi (okuyucu hesabı):
  1. Açılan tarayıcıda okuyucu hesabıyla (ikinci hesap) KENDİN giriş yap. Kod şifre yazmaz, hiçbir yere tıklamaz.
  2. Doğrulama kodu ya da "bu sen misin" sorulursa tarayıcıda tamamla.
  3. Başka sayfa gezme; kimseyi takip etme, beğenme.
Giriş bitince oturum kendiliğinden alınır ve tarayıcı kapanır (en çok 15 dakika beklenir)."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# --- gizlilik --------------------------------------------------------------------------------------------------------

def mask(text: object, secrets: Iterable[str] = ()) -> str:
    """Hata metninden kimlik bilgilerini siler: adreslerdeki kullanıcı:şifre@ ve bilinen gizli değerler (proxy, çerezler)."""
    out = _USERINFO.sub(r"\1***@", str(text))
    for secret in sorted({s for s in secrets if s and len(s) >= 4}, key=len, reverse=True):
        out = out.replace(secret, "***")
    return out[:MAX_DETAIL]


def proxy_from_env(env: Mapping[str, str]) -> str:
    """Proxy adresi; yoksa ya da biçimi bozuksa SocialStop(IP_CHANGED): proxy'siz tek istek bile atılmaz (fail-closed).
    Yalnız http(s) proxy: hedef adı proxy çözer (CONNECT), yerel DNS sorgusu olmaz (socks5:// yerelde çözerdi)."""
    raw = (env.get(PROXY_ENV) or "").strip()
    if not raw:
        raise SocialStop(Signal.IP_CHANGED, f"proxy ayarı yok ({PROXY_ENV})")
    parts = urlsplit(raw)
    try:
        port = parts.port
    except ValueError:
        port = None
    if parts.scheme not in ("http", "https") or not parts.hostname or not port:
        raise SocialStop(Signal.IP_CHANGED, f"proxy ayarı geçersiz ({PROXY_ENV}: http://kullanıcı:şifre@sunucu:port bekleniyor)")
    return raw


def proxy_secrets(proxy: str) -> tuple[str, ...]:
    p = urlsplit(proxy)
    values = (p.username, p.password, p.hostname, p.netloc.rsplit("@", 1)[-1])
    return tuple(v for raw in values if raw for v in {raw, unquote(raw)})


def playwright_proxy(proxy: str) -> dict:
    """Playwright proxy ayarı: kimlik bilgisi adresten ayrılır (Playwright server'da kullanıcı:şifre kabul etmez)."""
    p = urlsplit(proxy)
    host = f"[{p.hostname}]" if ":" in (p.hostname or "") else p.hostname
    out = {"server": f"{p.scheme}://{host}:{p.port}"}
    if p.username:
        out["username"] = unquote(p.username)
    if p.password:
        out["password"] = unquote(p.password)
    return out


# --- oturum dosyası ----------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SavedSession:
    username: str  # okuyucu hesap (günlüğe yazılmaz)
    cookies: dict[str, str]
    user_agent: str | None = None  # oturumu açan tarayıcının kimliği: Instaloader aynısıyla konuşur ("useragent mismatch" olmasın)

    def secrets(self) -> tuple[str, ...]:
        return (self.username, *(self.cookies.get(k, "") for k in ("sessionid", "csrftoken", "ds_user_id")))


def session_from_browser_cookies(cookies: Iterable[Mapping]) -> dict[str, str]:
    """Tarayıcı çerezleri (Playwright context.cookies()) -> Instaloader oturumu (Instaloader.load_session'ın beklediği
    ad->değer sözlüğü). Yalnız instagram.com çerezleri; sessionid ve csrftoken zorunlu (load_session csrftoken ister)."""
    out: dict[str, str] = {}
    for c in cookies:
        domain = str(c.get("domain") or "").lstrip(".").lower()
        name, value = str(c.get("name") or ""), str(c.get("value") or "")
        if name and value and (domain == "instagram.com" or domain.endswith(".instagram.com")):
            out[name] = value
    missing = [k for k in ("sessionid", "csrftoken") if not out.get(k)]
    if missing:
        raise ValueError(f"tarayıcıda Instagram oturumu yok (eksik çerez: {', '.join(missing)})")
    return out


def _write_private(path: Path, text: str) -> None:
    """Atomik yazım, yalnız sahibi okur (0600)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def write_session(path: Path, saved: SavedSession, now: datetime) -> None:
    _write_private(path, json.dumps({"username": saved.username, "cookies": saved.cookies, "user_agent": saved.user_agent,
                                     "saved_at": now.isoformat(timespec="seconds")}))


def read_session(path: Path) -> SavedSession:
    """Oturum dosyası; yoksa/bozuksa SocialStop(LOGIN_REQUIRED) (sahip VPS'te login yardımcısını çalıştırır)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        username = str(data["username"]).strip()
        cookies = {str(k): str(v) for k, v in data["cookies"].items()}
    except FileNotFoundError:
        raise SocialStop(Signal.LOGIN_REQUIRED, "Instagram oturum dosyası yok: VPS'te giriş yardımcısını çalıştır") from None
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        raise SocialStop(Signal.LOGIN_REQUIRED, f"Instagram oturum dosyası bozuk ({type(e).__name__})") from None
    if not username or not cookies.get("sessionid") or not cookies.get("csrftoken"):
        raise SocialStop(Signal.LOGIN_REQUIRED, "Instagram oturum dosyasında sessionid/csrftoken yok")
    os.chmod(path, 0o600)
    return SavedSession(username, cookies, data.get("user_agent") or None)


# --- günlük tavan ve hız denetimi ---------------------------------------------------------------------------------------

@dataclass
class QueryBudget:
    """Okuyucu hesabın günlük sorgu sayacı (state_dir'de JSON, UTC günü). Her sorgu ÖNCE sayılır; tavan doluysa DailyCap.
    Dosya okunamazsa tavan dolmuş sayılır (fail-closed) ve dosya bugünle yeniden yazılır: ertesi gün kendiliğinden açılır."""
    path: Path
    cap: int = DAILY_QUERY_CAP
    now: Callable[[], datetime] = _utc_now
    used: int = 0  # bu nesnenin saydığı (tur içi)

    def today_count(self) -> int:
        day = self.now().date().isoformat()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return int(data["count"]) if data.get("day") == day else 0
        except FileNotFoundError:
            return 0
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self._save(day, self.cap)
            return self.cap

    def spend(self) -> None:
        count = self.today_count()
        if count >= self.cap:
            raise DailyCap(f"Instagram günlük sorgu tavanı doldu ({count}/{self.cap})")
        self._save(self.now().date().isoformat(), count + 1)
        self.used += 1

    def _save(self, day: str, count: int) -> None:
        _write_private(self.path, json.dumps({"day": day, "count": count}))


def assert_proxied(session, proxy: str) -> None:
    """Oturum hâlâ proxy'li mi (her sorgudan önce). Değilse SocialStop(IP_CHANGED): proxy'siz istek atılmaz."""
    proxies = getattr(session, "proxies", None) or {}
    if proxies.get("https") != proxy or proxies.get("http") != proxy or getattr(session, "trust_env", True):
        raise SocialStop(Signal.IP_CHANGED, "Instaloader oturumunda proxy yok (istek atılmadı)")


def rate_controller_class(base: type, budget: QueryBudget, proxy: str, sleeper: Callable[[float], None] = time.sleep,
                          monotonic: Callable[[], float] = time.monotonic) -> type:
    """instaloader.RateController alt sınıfı. Instaloader'ın kendi aralıkları (super) korunur, ASLA hızlandırılmaz; üstüne:
    proxy denetimi, her sorgunun sayılması + günlük tavan, iki sorgu arası en az MIN_QUERY_GAP_S. 429'da beklenmez,
    tekrar denenmez: SocialStop(RATE_LIMITED)."""

    class CountingRateController(base):
        last_query: float | None = None

        def sleep(self, secs: float) -> None:
            sleeper(secs)

        def wait_before_query(self, query_type: str) -> None:
            assert_proxied(self._context._session, proxy)
            budget.spend()
            if self.last_query is not None:
                gap = MIN_QUERY_GAP_S - (monotonic() - self.last_query)
                if gap > 0:
                    sleeper(gap)
            super().wait_before_query(query_type)
            self.last_query = monotonic()

        def handle_429(self, query_type: str) -> None:
            raise SocialStop(Signal.RATE_LIMITED, "429 Too Many Requests (beklenmedi, tekrar denenmedi)")

    return CountingRateController


# --- proxy ve yanıt bekçisi ---------------------------------------------------------------------------------------------

def signal_from_text(text: str) -> Signal | None:
    low = text.lower()
    return next((signal for marker, signal in STOP_MARKERS if marker in low), None)


def _is_instagram(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host == "instagram.com" or host.endswith(".instagram.com")


def response_guard(masker: Callable[[str], str]) -> Callable:
    """requests yanıt kancası (Instagram yanıtlarında): 429 -> RATE_LIMITED; başka yola yönlendirme -> doğrulama (CHECKPOINT),
    giriş sayfası (LOGIN_REQUIRED) ya da bilinmeyen (AUTH_ERROR). Instaloader yönlendirmeyi GET ile izlemeden tur durur."""

    def hook(resp, *args, **kwargs):
        if not _is_instagram(resp.url):
            return resp
        signal, why = None, ""
        if resp.status_code == 429:
            signal, why = Signal.RATE_LIMITED, "429 Too Many Requests"
        elif resp.is_redirect:
            target = urlsplit(urljoin(resp.url, resp.headers.get("location", ""))).path
            if target.rstrip("/") != urlsplit(resp.url).path.rstrip("/"):  # yalnız "/" eki farkı zararsız
                signal = signal_from_text(target) or Signal.AUTH_ERROR
                why = f"{resp.status_code} yönlendirme: /{target.strip('/').split('/')[0]}/…"
        if signal:
            resp.close()
            raise SocialStop(signal, masker(why))
        return resp

    return hook


def wire_session(session, proxy: str, hook: Callable) -> None:
    """Proxy oturuma AÇIKÇA yazılır (http ve https); ortam değişkenleri (HTTP(S)_PROXY, NO_PROXY) yok sayılır."""
    session.proxies = {"http": proxy, "https": proxy}
    session.trust_env = False
    hooks = session.hooks.setdefault("response", [])
    if hook not in hooks:
        hooks.append(hook)


def harden_copy_session(context_module) -> None:
    """4.15.3 instaloadercontext.copy_session yeni requests.Session açıp yalnız çerez+başlık kopyalar: proxy ve kancalar düşer,
    GraphQL/doc_id istekleri proxy'siz çıkardı. Modül işlevi sarılır (bir kez): kopya kaynağın proxy/trust_env/kancalarını alır."""
    original = getattr(context_module, "copy_session", None)
    if original is None:
        raise RuntimeError("instaloadercontext.copy_session yok: Instaloader sürümü doğrulanan sürüm değil")
    if getattr(original, "proxied", False):
        return

    def copy_session(session, request_timeout=None):
        new = original(session, request_timeout)
        new.proxies = dict(session.proxies)
        new.trust_env = session.trust_env
        new.hooks = {k: list(v) for k, v in session.hooks.items()}
        return new

    copy_session.proxied = True
    context_module.copy_session = copy_session


def proxy_context(context, proxy: str, hook: Callable) -> None:
    """Instaloader bağlamının tüm oturumları proxy'li: asıl oturum + sonradan açılacak anonim oturumlar."""
    wire_session(context._session, proxy, hook)
    make_anonymous = context.get_anonymous_session

    def anonymous_session():
        session = make_anonymous()
        wire_session(session, proxy, hook)
        return session

    context.get_anonymous_session = anonymous_session


def egress_ip(session, timeout: float = REQUEST_TIMEOUT_S) -> str:
    """Çıkış IP'si AYNI requests oturumundan (aynı proxy). İstek elle hazırlanır: Instaloader oturumunun çerezleri alan adsız
    (her siteye gider) ve başlıkları Instagram'a özel; ipify'a ne çerez ne Instagram başlığı gider."""
    import requests  # Instaloader'ın bağımlılığı
    req = requests.Request("GET", EGRESS_URL, headers={"Accept": "application/json"}).prepare()
    resp = session.send(req, timeout=timeout, allow_redirects=False)
    resp.raise_for_status()
    ip = str(resp.json()["ip"])
    ipaddress.ip_address(ip)
    return ip


# --- hata eşleme --------------------------------------------------------------------------------------------------------

def _chain(exc: BaseException) -> list[BaseException]:
    out: list[BaseException] = []
    while exc is not None and exc not in out and len(out) < 6:
        out.append(exc)
        exc = exc.__cause__ or exc.__context__
    return out


def social_error(exc: BaseException, masker: Callable[[str], str] = mask) -> Exception | None:
    """Instaloader/requests hatası -> SocialStop (platform durur) ya da SourceError (yalnız bu kaynak). None: tanınmadı
    (programlama hatası; olduğu gibi yükselsin). Sınıflara adla bakılır: instaloader import edilmeden test edilir."""
    chain = _chain(exc)
    names = {cls.__name__ for e in chain for cls in type(e).__mro__}
    plain = " | ".join(str(e).split(" when accessing ")[0] for e in chain)  # URL'deki hesap adı yanlış eşleşmesin
    detail = masker(f"{type(exc).__name__}: {plain}")
    signal = signal_from_text(plain)
    if signal is None:
        status = next((int(m.group(1)) for m in _STATUS.finditer(plain)), None)
        if "LoginRequiredException" in names:
            signal = Signal.LOGIN_REQUIRED
        elif "TooManyRequestsException" in names:
            signal = Signal.RATE_LIMITED
        elif "JSONDecodeError" in names:
            signal = Signal.AUTH_ERROR  # JSON yerine sayfa geldi (giriş/engel sayfası şüphesi)
        elif "RequestException" in names:  # Instagram'a varılamadı (proxy/zaman aşımı); metindeki kod proxy'nindir
            return SourceError(f"bağlantı hatası (proxy/zaman aşımı): {detail}")
        elif names & {"ProfileNotExistsException", "PrivateProfileNotFollowedException", "QueryReturnedNotFoundException"}:
            return SourceError(f"hesap bulunamadı ya da gizli: {detail}")
        elif status == 429:
            signal = Signal.RATE_LIMITED
        elif status in (401, 403) or names & {"QueryReturnedForbiddenException", "AbortDownloadException"}:
            signal = Signal.AUTH_ERROR
        elif names & _SOURCE_ERRORS or "InstaloaderException" in names or (status and status >= 400):
            return SourceError(f"okunamadı: {detail}")
        else:
            return None
    return SocialStop(signal, detail)


# --- zaman tüneli -------------------------------------------------------------------------------------------------------

def timeline_edges(response: Mapping) -> Mapping:
    """doc_id yanıtından sayfa (edges + page_info). Yapı yoksa SourceError: hesap gizli/silinmiş ya da yanıt yapısı değişmiş."""
    data = response.get("data") if isinstance(response, Mapping) else None
    feed = data.get("xdt_api__v1__feed__user_timeline_graphql_connection") if isinstance(data, Mapping) else None
    if not isinstance(feed, Mapping) or not isinstance(feed.get("edges"), list) or not isinstance(feed.get("page_info"), Mapping):
        raise SourceError("zaman tüneli okunamadı (hesap gizli/silinmiş olabilir ya da yanıt yapısı değişti)")
    return feed


def timeline_nodes(il, context, username: str) -> Iterator[Mapping]:
    """4.15.3 Profile.get_posts()'un giriş yapılmış dalının aynısı, profil sorgusu OLMADAN. Yeni → eski; sayfa 12 gönderi.
    İlk sayfa NodeIterator kurulurken istenir; sonraki sayfa yalnız önceki sayfa bitip devam istenirse."""
    return il.NodeIterator(
        context=context,
        query_hash=None,
        edge_extractor=timeline_edges,
        node_wrapper=lambda node: node,
        query_variables={
            "data": {"count": 12, "include_relationship_info": True, "latest_besties_reel_media": True,
                     "latest_reel_media": True},
            "username": username,
        },
        query_referer=f"https://www.instagram.com/{username}/",
        doc_id=TIMELINE_DOC_ID,
    )


def _first_image(node: Mapping) -> str | None:
    for media in (node, *((node.get("carousel_media") or [])[:1])):
        candidates = ((media or {}).get("image_versions2") or {}).get("candidates") or []
        if candidates and isinstance(candidates[0], Mapping) and candidates[0].get("url"):
            return str(candidates[0]["url"])
    return node.get("display_url") or None


def post_from_node(node: Mapping, source_key: str) -> SocialPost | None:
    """Zaman tüneli düğümü (iPhone yapısı) -> SocialPost; tanınmazsa None. Görsel adresi yalnız yazılır, indirilmez."""
    if not isinstance(node, Mapping):
        return None
    code, ts = node.get("code") or node.get("shortcode"), node.get("taken_at")
    if not isinstance(code, str) or not code or isinstance(ts, bool) or not isinstance(ts, int | float):
        return None
    caption = node.get("caption")
    text = caption.get("text") if isinstance(caption, Mapping) else caption
    user = node.get("user") if isinstance(node.get("user"), Mapping) else {}
    return SocialPost(
        platform="instagram",
        source_key=source_key,
        post_id=code,
        url=f"https://www.instagram.com/p/{code}/",
        posted_at=datetime.fromtimestamp(ts, timezone.utc),
        text=unicodedata.normalize("NFC", text) if isinstance(text, str) else "",
        image_url=_first_image(node),
        owner=str(user.get("username") or source_key).lower(),
        pinned=bool(node.get("timeline_pinned_user_ids") or node.get("pinned_for_users")),
    )


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def scan_timeline(nodes: Iterable[Mapping], source_key: str, cursor: Cursor, max_posts: int, now: datetime) -> FetchResult:
    """Yeni → eski akıştan imleçten yeni gönderiler (saf kural, G/Ç yok; istek sayısı fetch_new'de eklenir).
    Dur: imleç gönderisi (sabit bölgesi olabilecek ilk 3 sıra dışında), art arda OLD_RUN_STOP eski gönderi ya da max_posts.
    İlk okuma (boş imleç): yalnız son FIRST_RUN_DAYS gün. Durunca bir sonraki gönderi İSTENMEZ (yeni sayfa isteği olmasın)."""
    since = _aware(cursor.posted_at)
    floor = now - timedelta(days=FIRST_RUN_DAYS) if cursor.post_id is None and since is None else None
    seen = old_run = 0
    read: list[tuple[int, SocialPost]] = []
    fresh: set[str] = set()
    for i, node in enumerate(nodes):
        seen += 1
        post = post_from_node(node, source_key)
        if post is None:  # tanınmayan düğüm eski sayılır: hepsi bozuksa sonsuz sayfa okunmaz
            old_run += 1
            if old_run >= OLD_RUN_STOP:
                break
            continue
        read.append((i, post))
        hit = cursor.post_id is not None and post.post_id == cursor.post_id
        if hit and i >= PINNED_MAX:
            break
        old = hit or (post.posted_at < floor if floor else since is not None and post.posted_at <= since)
        if old:
            old_run += 1
            if old_run >= OLD_RUN_STOP:
                break
            continue
        old_run = 0
        fresh.add(post.post_id)
        if len(fresh) >= max_posts:
            break
    if seen and not read:
        raise SourceError("gönderi yapısı tanınmadı (zaman tüneli düğümleri değişmiş olabilir)")
    # Sabit: alanı varsa o; yoksa ilk 3 sırada olup altındaki bir gönderiden ESKİ olan (akış başka türlü yeniden eskiye sıralı).
    marked: list[SocialPost] = []
    newest_below: datetime | None = None
    for i, post in reversed(read):
        if not post.pinned and i < PINNED_MAX and newest_below is not None and newest_below > post.posted_at:
            post = replace(post, pinned=True)
        newest_below = post.posted_at if newest_below is None else max(newest_below, post.posted_at)
        marked.append(post)
    posts = sorted((p for p in marked if p.post_id in fresh), key=lambda p: p.posted_at, reverse=True)
    newest = max((p for p in marked if not p.pinned), key=lambda p: p.posted_at, default=None)
    if newest is not None and (since is None or newest.posted_at > since):
        cursor = Cursor(post_id=newest.post_id, posted_at=newest.posted_at)
    return FetchResult(posts=posts, cursor=cursor, seen=seen)


# --- okuyucu ------------------------------------------------------------------------------------------------------------

@dataclass
class InstaloaderFetcher:
    """SocialFetcher (application/social_port). Tur başına build() ile bir kez kurulur."""
    loader: object  # instaloader.Instaloader (oturum yüklü, proxy'li)
    proxy: str
    budget: QueryBudget
    timeline: Callable[[str], Iterable[Mapping]]  # kullanıcı adı -> yeni → eski düğümler
    now: Callable[[], datetime] = _utc_now
    masker: Callable[[str], str] = mask
    egress: Callable[[object], str] = egress_ip
    platform: Platform = field(default="instagram", init=False)

    def check_egress(self) -> str:
        if not self.proxy:
            raise SocialStop(Signal.IP_CHANGED, f"proxy ayarı yok ({PROXY_ENV})")
        session = self.loader.context._session
        assert_proxied(session, self.proxy)
        try:
            return self.egress(session)
        except SocialStop:
            raise
        except Exception as e:  # proxy yanıt vermedi / IP okunamadı: IP doğrulanamadan okuma yapılmaz (tur biter, fren yok)
            raise Unreachable(self.masker(f"çıkış IP'si okunamadı: {type(e).__name__}: {e}")) from None

    def fetch_new(self, source: SocialSource, cursor: Cursor, max_posts: int) -> FetchResult:
        username = source.key.strip().lstrip("@").lower()
        if not USERNAME_RE.fullmatch(username):
            raise SourceError(f"geçersiz Instagram kullanıcı adı ({source.alias})")
        if cursor.post_id is None and cursor.posted_at is None:
            max_posts = min(max_posts, FIRST_RUN_MAX_POSTS)
        if max_posts <= 0:
            return FetchResult(posts=[], cursor=cursor)
        before = self.budget.used
        try:
            result = scan_timeline(self.timeline(username), source.key, cursor, max_posts, self.now())
        except (SocialStop, SourceError, DailyCap):
            raise
        except Exception as e:
            mapped = social_error(e, self.masker)
            if mapped is None:
                raise
            raise mapped from None  # özgün hata zinciri günlüğe kimlik bilgisi taşımasın
        result.requests = self.budget.used - before
        return result

    def close(self) -> None:
        try:
            self.loader.close()
        except Exception:  # kapanış hatası turu bozmasın
            pass


def _open_loader(il, saved: SavedSession, proxy: str, budget: QueryBudget, masker: Callable[[str], str],
                 sleeper: Callable[[float], None], monotonic: Callable[[], float]):
    """Proxy'li, sayan, oturumu yüklü Instaloader. Ağ isteği atmaz."""
    if getattr(il, "__version__", None) != INSTALOADER_VERSION:
        raise RuntimeError(f"instaloader {getattr(il, '__version__', '?')} kurulu; bu okuyucu {INSTALOADER_VERSION} için doğrulandı")
    harden_copy_session(il.instaloadercontext)
    controller = rate_controller_class(il.RateController, budget, proxy, sleeper, monotonic)
    loader = il.Instaloader(**LOADER_OPTIONS, user_agent=saved.user_agent, rate_controller=controller)
    loader.load_session(saved.username, dict(saved.cookies))  # yeni requests.Session açar: proxy bundan SONRA yazılır
    proxy_context(loader.context, proxy, response_guard(masker))
    return loader


def build(env: Mapping[str, str], state_dir: Path, *, now: Callable[[], datetime] = _utc_now,
          sleep: Callable[[float], None] = time.sleep, monotonic: Callable[[], float] = time.monotonic) -> InstaloaderFetcher:
    """Tur başına bir kez. Sıra fail-closed: önce proxy (yoksa IP_CHANGED), sonra oturum (yoksa LOGIN_REQUIRED). Ağ isteği atmaz."""
    proxy = proxy_from_env(env)
    state_dir = Path(state_dir)
    saved = read_session(state_dir / SESSION_FILE)
    import instaloader
    masker = partial(mask, secrets=(*proxy_secrets(proxy), *saved.secrets()))
    budget = QueryBudget(state_dir / BUDGET_FILE, now=now)
    loader = _open_loader(instaloader, saved, proxy, budget, masker, sleep, monotonic)
    return InstaloaderFetcher(loader=loader, proxy=proxy, budget=budget, now=now, masker=masker,
                              timeline=partial(timeline_nodes, instaloader, loader.context))


# --- sahibin girişi (VPS'te, tarayıcıda) --------------------------------------------------------------------------------

def _has_session(cookies: Iterable[Mapping]) -> bool:
    try:
        session_from_browser_cookies(cookies)
        return True
    except ValueError:
        return False


def wait_for_session(read_cookies: Callable[[], list], read_url: Callable[[], str], *, sleep: Callable[[float], None],
                     clock: Callable[[], float], timeout_s: float = LOGIN_TIMEOUT_S) -> list:
    """sessionid görünene ve sayfa giriş/doğrulama akışından çıkana kadar bekler; süre dolarsa SocialStop(LOGIN_REQUIRED)."""
    deadline = clock() + timeout_s
    while True:
        try:
            ready = _has_session(read_cookies()) and not any(p in urlsplit(read_url()).path for p in _LOGIN_FLOW)
        except Exception as e:  # sahip tarayıcıyı kapattı
            raise SocialStop(Signal.LOGIN_REQUIRED, f"tarayıcı kapandı, giriş tamamlanmadı ({type(e).__name__})") from None
        if ready:
            sleep(LOGIN_SETTLE_S)
            return list(read_cookies())
        if clock() >= deadline:
            raise SocialStop(Signal.LOGIN_REQUIRED, f"{int(timeout_s // 60)} dakikada giriş tamamlanmadı")
        sleep(LOGIN_POLL_S)


def seed_browser_profile(profile_dir: Path) -> None:
    """Chromium profil tercihi webrtc.ip_handling_policy: tam Chromium'da --force-webrtc-ip-handling-policy anahtarı yalnız
    content_shell/headless'ta okunur; asıl etkili olan bu tercih. Var olan tercihler korunur."""
    prefs_path = profile_dir / "Default" / "Preferences"
    prefs_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(profile_dir, 0o700)
    try:
        prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
        if not isinstance(prefs, dict):
            prefs = {}
    except (OSError, ValueError):
        prefs = {}
    prefs.setdefault("webrtc", {})["ip_handling_policy"] = WEBRTC_POLICY
    _write_private(prefs_path, json.dumps(prefs))


def _open_owner_browser(p, profile: Path, proxy: str, env: Mapping[str, str]):
    """Sahibin göreceği tarayıcı: kalıcı profil (cihaz tanınsın), aynı proxy, saat dilimi, WebRTC yalnız proxy'den."""
    return p.chromium.launch_persistent_context(
        str(profile), headless=False, proxy=playwright_proxy(proxy),
        timezone_id=(env.get(TZ_ENV) or BROWSER_TZ).strip(),
        args=[f"--webrtc-ip-handling-policy={WEBRTC_POLICY}",  # tam (görünür) Chromium bunu okur
              f"--force-webrtc-ip-handling-policy={WEBRTC_POLICY}"],  # görünmez kip bunu okur; profil tercihi de ayrıca yazıldı
    )


def _egress_page(context, masker: Callable[[str], str]):
    """Önce tarayıcının çıkış IP'si: proxy çalışmıyorsa Instagram hiç açılmaz."""
    page = context.pages[0] if context.pages else context.new_page()
    try:
        page.goto(EGRESS_URL)
        ip = str(json.loads(page.inner_text("body"))["ip"])
        ipaddress.ip_address(ip)
    except Exception as e:
        raise SocialStop(Signal.IP_CHANGED, masker(f"tarayıcının çıkış IP'si okunamadı: {type(e).__name__}: {e}")) from None
    print(f"Tarayıcının çıkış IP'si: {ip} (proxy'nin IP'si olmalı; değilse pencereyi kapat)")
    return page


BROWSE_TIMEOUT_S = 30 * 60
BROWSE_HELP = """
Instagram elle kontrol (okuyucu hesabı, kayıtlı oturumla):
  1. Açılan tarayıcıda Instagram var. Uyarı ("We suspect automated behavior", "Confirm it's you" vb.) çıkarsa KENDİN tamamla.
     Program hiçbir şey yazmaz, tıklamaz. Gezinme, beğenme, takip yok.
  2. Bitince sekmeyi kapat. Çerezler okuyucu oturumuna aktarılır (Instagram'a ek sorgu atılmaz). En çok 30 dakika."""


def browse(env: Mapping[str, str], state_dir: Path, *, now: Callable[[], datetime] = _utc_now,
           sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
           timeout_s: float = BROWSE_TIMEOUT_S) -> None:
    """Sahip için: kayıtlı oturumun kalıcı profiliyle görünür Chromium (aynı proxy); uyarıyı sahip KENDİSİ kapatır. Sekme kapanınca
    ya da süre dolunca tarayıcı çerezleri oturum dosyasına yazılır; tarayıcıda oturum yoksa dosya EZİLMEZ."""
    proxy = proxy_from_env(env)
    state_dir = Path(state_dir)
    saved = read_session(state_dir / SESSION_FILE)  # oturum yoksa önce login
    masker = partial(mask, secrets=(*proxy_secrets(proxy), *saved.secrets()))
    profile = state_dir / BROWSER_DIR
    seed_browser_profile(profile)
    from playwright.sync_api import sync_playwright
    cookies: list = []
    with sync_playwright() as p:
        context = _open_owner_browser(p, profile, proxy, env)
        try:
            page = _egress_page(context, masker)
            page.goto(INSTAGRAM_URL)
            print(BROWSE_HELP)
            deadline = clock() + timeout_s
            while True:
                try:  # kalıcı profilde son sekme kapanınca tarayıcı da kapanabilir: çerezler her turda alınır
                    cookies = list(context.cookies(INSTAGRAM_URL))
                    open_pages = [pg for pg in context.pages if not pg.is_closed()]
                except Exception:
                    break
                if not open_pages or clock() >= deadline:
                    break
                sleep(LOGIN_POLL_S)
        finally:
            try:
                context.close()
            except Exception:
                pass
    try:
        cookie_map = session_from_browser_cookies(cookies)
    except ValueError:
        print("UYARI: tarayıcıda Instagram oturumu yok; kayıtlı oturum değiştirilmedi. Gerekirse: login instagram")
        return
    write_session(state_dir / SESSION_FILE, replace(saved, cookies=cookie_map), now())
    print("Instagram oturum çerezleri güncellendi.")


def login(env: Mapping[str, str], state_dir: Path, *, now: Callable[[], datetime] = _utc_now,
          sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
          timeout_s: float = LOGIN_TIMEOUT_S) -> None:
    """Sahip için: VPS'te AYNI proxy'li görünür Chromium açılır, sahip KENDİSİ girer (kod şifre yazmaz, tıklamaz). sessionid
    görülünce çerezler Instaloader oturumuna çevrilir, tek doğrulama sorgusu (test_login) proxy'den atılır, dosyaya yazılır (0600)."""
    proxy = proxy_from_env(env)
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(state_dir, 0o700)
    masker = partial(mask, secrets=proxy_secrets(proxy))
    profile = state_dir / BROWSER_DIR
    seed_browser_profile(profile)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        context = _open_owner_browser(p, profile, proxy, env)
        try:
            page = _egress_page(context, masker)
            page.goto(LOGIN_URL)
            print(LOGIN_HELP)
            cookies = wait_for_session(lambda: context.cookies(INSTAGRAM_URL), lambda: page.url,
                                       sleep=sleep, clock=clock, timeout_s=timeout_s)
            user_agent = str(page.evaluate("() => navigator.userAgent"))
        except SocialStop:
            raise
        except Exception as e:
            raise SocialStop(Signal.LOGIN_REQUIRED, masker(f"giriş tarayıcısı hatası: {type(e).__name__}: {e}")) from None
        finally:
            context.close()
    try:
        cookie_map = session_from_browser_cookies(cookies)
    except ValueError as e:
        raise SocialStop(Signal.LOGIN_REQUIRED, str(e)) from None
    import instaloader
    saved = SavedSession("oturum", cookie_map, user_agent)  # gerçek kullanıcı adı test_login'den
    masker = partial(mask, secrets=(*proxy_secrets(proxy), *saved.secrets()))
    loader = _open_loader(instaloader, saved, proxy, QueryBudget(state_dir / BUDGET_FILE, now=now), masker,
                          sleep, time.monotonic)
    try:
        username = loader.test_login()
    except SocialStop:
        raise
    except Exception as e:
        raise social_error(e, masker) or SocialStop(Signal.LOGIN_REQUIRED, masker(str(e))) from None
    finally:
        loader.close()
    if not username:
        raise SocialStop(Signal.LOGIN_REQUIRED, "oturum doğrulanamadı (test_login boş döndü): giriş yardımcısını yeniden çalıştır")
    write_session(state_dir / SESSION_FILE, replace(saved, username=str(username)), now())
    print("Instagram oturumu kaydedildi. Tarayıcıda Instagram'a bir daha girme; okuyucu bu oturumu kullanır.")
