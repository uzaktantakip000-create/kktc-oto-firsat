"""Facebook gizli/özel grup okuyucusu (VPS işçisi): ayrı ikinci hesap, gerçek Chromium (Playwright, Xvfb altında görünür kip),
TEK sabit konut proxy'si. Sözleşme: application/social_port.py (build/login, SocialFetcher).

YALNIZ OKUR: beğeni, yorum, tepki, paylaşım, gruba katılma, mesaj YOK; tek gönderi sayfası açılmaz, yorumlar genişletilmez.
Tek izinli tıklama: gönderinin KENDİ gövdesindeki "See more" (yazının tamamı için). Kaydırma tekerlekle, küçük adımlarla,
1,5–4 sn rastgele bekleyişle. Yazar adı/kimliği hiçbir yere geçmez (SocialPost.owner HER ZAMAN None).

Ayrıştırma saf işlevlerdedir (Playwright gerekmez, CI'da çevrimdışı test edilir); tarayıcı kodu yalnız işlev İÇİNDE import edilir.
Forage v2.1.0'dan (MPL-2.0) alınan saf işlevler ayrı dosyada: infrastructure/collectors/forage_parser.py (lisans dosya düzeyinde
orada kalır). Sayfadan veri çeken JS (EXTRACT_JS) bu projenindir; yalnız düz sözlük döndürür."""
import hashlib
import ipaddress
import json
import os
import random
import re
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from application.social_port import Cursor, FetchResult, SocialPost, SocialSource, SocialStop, SourceError, Unreachable
from domain.social_brake import Signal
from infrastructure.collectors import forage_parser

PROXY_ENV = "SOCIAL_PROXY_FACEBOOK"  # http://kullanici:sifre@sunucu:port (yoksa okuma YOK: fail-closed)
HEADLESS_ENV = "SOCIAL_HEADLESS"  # "1" = görünmez kip (yalnız deneme; VPS'te Xvfb altında görünür kip)
TZ_ENV = "SOCIAL_BROWSER_TZ"
BROWSER_TZ = "Europe/Istanbul"  # proxy IP'si Türkiye'de: tarayıcı saat dilimi IP ülkesiyle aynı olmalı. Zaman ayrıştırma da BUNU
# kullanır (Facebook "Yesterday at 3:15 PM"i tarayıcının saat diliminde yazar). Türkiye yıl boyu UTC+3; KKTC 25.10.2026'dan sonra +2.
LOCALE = "en-US"  # arayüz İngilizce olmalı: zaman ifadeleri yalnız İngilizce ayrıştırılır
VIEWPORT = {"width": 1440, "height": 900}  # sabit (her turda değişen ekran boyutu parmak izini bozar)
STATE_FILE = "facebook_storage_state.json"  # oturum çerezleri (chmod 600, repoya GİRMEZ)
FB_ORIGIN = "https://www.facebook.com"
GROUP_URL = FB_ORIGIN + "/groups/{key}/?sorting_setting=CHRONOLOGICAL"
# "Feeds → Groups" en yeni sırada (üye olunan tüm grupların gönderileri tek sayfada). Herkese açık kaynaklardan (2022–2025
# kullanıcı rehberleri, greasyfork betiklerindeki `facebook.com/?filter=groups*` istisnası) bilinen adres; Facebook'a hiç
# girilmeden yazıldı. 2. gün denemesi DOĞRULAR (sıralama gerçekten kronolojik mi, gönderiler [role=feed] içinde mi).
COMBINED_FEED_URL = FB_ORIGIN + "/?filter=groups&sk=h_chr"
LOGIN_URL = FB_ORIGIN + "/login/"
EGRESS_URLS = ("https://api.ipify.org?format=json", "https://checkip.amazonaws.com/")  # ikisi de aynı bağlamdan (proxy) okunur
# WebRTC yalnız proxy üzerinden: Chromium 153'te (Playwright 1.63) yerel denemeyle doğrulandı (05.10.2026): tam Chromium
# `--webrtc-ip-handling-policy`, headless kabuk `--force-webrtc-ip-handling-policy` bayrağını tanıyor; ikisi birlikte verilir.
# Bayrak ileride düşerse turun başındaki WebRTC denetimi (WEBRTC_JS) bunu yakalar ve okuma başlamadan durur.
WEBRTC_ARGS = ("--webrtc-ip-handling-policy=disable_non_proxied_udp", "--force-webrtc-ip-handling-policy=disable_non_proxied_udp")
LAUNCH_ARGS = ("--disable-blink-features=AutomationControlled",) + WEBRTC_ARGS

FIRST_WINDOW = timedelta(hours=24)  # ilk okumada (boş imleç) yalnız son 24 saat
OLD_STREAK = 5  # art arda bu kadar eski gönderi -> dur (en üstteki sabitlenmiş eski gönderiler tek başına durdurmaz)
OLD_MARGIN = timedelta(hours=1)  # "6h" saat hassasiyetinde: imleçten en çok 1 saat geride görünen gönderi "eski" SAYILMAZ (yeni olabilir)
MAX_SCROLLS = 60  # sayfa başına en çok kaydırma (tavan)
IDLE_SCROLLS = 6  # bu kadar kaydırmada yeni gönderi gelmezse akış bitti
CHECK_EVERY = 8  # kaydırma sırasında her 8 adımda bir oturum/engel denetimi
MAX_EXPAND = 15  # sayfa başına en çok "See more" tıklaması
MAX_HOVER = 5  # bağlantısı "#" olan zaman bağlantısına (insan gibi) fareyle gelme: sayfa başına en çok
MAX_TIME_HOVER = 40  # zaman bağlantısına fareyle gelip ipucundaki tarihi okuma: sayfa başına en çok (yalnız alınacak gönderiler)
TIME_TIP_WAIT_S = 3.5  # ipucu (tooltip) bu kadar sürede çıkmazsa tarih yok sayılır
READY_FRACTION = 0.6  # gönderinin üst kenarı ekranın bu oranına gelince okunur (başlık ve yazının başı görünür)
MIN_IMAGE_PX = 100  # bundan küçük görsel (profil resmi, simge) gönderi fotoğrafı sayılmaz
SCROLL_PAUSE_S = (1.5, 4.0)
READ_PAUSE_S = (5.0, 9.0)  # arada bir uzun okuma molası
READ_PAUSE_P = 0.12
LOAD_PAUSE_S = (3.0, 6.0)  # sayfa açıldıktan sonra
PAGE_GAP_S = (6.0, 15.0)  # iki sayfa yüklemesi arası
GOTO_TIMEOUT_MS = 45_000
FEED_TIMEOUT_MS = 15_000
EGRESS_TIMEOUT_MS = 15_000
IMAGE_TIMEOUT_MS = 15_000
MAX_IMAGE_BYTES = 5_000_000  # fotoğraftan fiyat okuma: bundan büyük görsel indirilmez
LOGIN_TIMEOUT_S = 30 * 60  # yeni hesap açma (form + e-posta kodu) 15 dakikaya sığmayabilir
HOME_PATHS = ("", "/", "/home.php")  # giriş ancak ana akışta biter (kayıt/e-posta onayı/checkpoint sayfalarında c_user olsa da beklenir)
LOGIN_POLL_S = 3.0

# Tek tespit listesi: (nerede, aranan, sonuç). Sonuç None = yalnız bu kaynak okunamadı (SourceError). Metin küçük harfe çevrilip
# ’ -> ' yapılarak aranır; ilk eşleşen kazanır (geçici engel, hız uyarısından önce). "Bulunamadı" satırları yalnız akış YOKSA bakılır.
DETECTION: tuple[tuple[str, str, Signal | None], ...] = (
    ("url", "/checkpoint", Signal.CHECKPOINT),
    ("url", "/login", Signal.LOGIN_REQUIRED),
    ("url", "/recover/", Signal.LOGIN_REQUIRED),
    ("text", "confirm your identity", Signal.CHECKPOINT),
    ("text", "your account has been locked", Signal.CHECKPOINT),
    ("text", "we suspended your account", Signal.CHECKPOINT),
    ("text", "your account has been disabled", Signal.CHECKPOINT),
    ("text", "you're temporarily blocked", Signal.TEMP_BLOCKED),
    ("text", "temporarily blocked", Signal.TEMP_BLOCKED),
    ("text", "you can't use this feature right now", Signal.TEMP_BLOCKED),
    ("text", "misusing this feature", Signal.TEMP_BLOCKED),
    ("text", "going too fast", Signal.RATE_LIMITED),
    ("text", "this content isn't available", None),
    ("text", "this group isn't available", None),
    ("text", "this page isn't available", None),
    ("text", "content not found", None),
    ("text", "join this group to see", None),
    ("text", "join group to see", None),
)
EMPTY_MARKERS = ("no posts yet",)
PINNED_MARKERS = ("Featured", "Pinned post", "Admin announcement")  # gönderi başlığında tek başına duran etiket (2. gün doğrulanır)
UI_EXTRA_LINES = {"See translation", "See original", "Rate this translation", "Hide original", "Edited", "Follow", "Join", "· Follow",
            "· Join"}

# Seçiciler (2. gün denemesinde gerçek sayfada doğrulanır): gönderi = akıştaki EN DIŞ makale/kart; yorumlar iç içe makaledir.
POST_SELECTOR = '[role="feed"] [role="article"], [role="feed"] [aria-posinset]'
BODY_SELECTOR = '[data-ad-rendering-role="story_message"], [data-ad-comet-preview="message"], [data-ad-preview="message"]'
# Gövde bulunamazsa yedek metinden atılanlar: yorum, düğme, başlık/yazar, grup adı ve zaman bağlantıları, simgeler.
STRIP_SELECTOR = ('[role="article"], [role="button"], button, h2, h3, h4, strong, svg, img, style, script, [aria-hidden="true"], '
                  '[role="toolbar"], [data-ad-rendering-role="profile_name"], a[href*="/groups/"], a[href*="/user/"], '
                  'a[href*="profile.php"], a[href="#"]')

# Sayfadaki gönderileri DÜZ sözlük olarak döndürür (tarayıcı nesnesi yok). `only` verilirse yalnız o sıradaki gönderi.
EXTRACT_JS = r"""(a) => {
  const R = '[role="article"]';
  const cands = Array.from(document.querySelectorAll(a.posts));
  const posts = cands.filter(x => !cands.some(o => o !== x && o.contains(x)));
  const vh = window.innerHeight || 0;
  const pinRe = new RegExp(a.pinned, 'i');
  const timeRe = /\d|just now|yesterday/i;
  const postRe = /\/posts\/|\/permalink\/|story_fbid=|multi_permalinks=|set=(gm|pcb)\./;
  const visual = (el) => {
    const box = el.getBoundingClientRect();
    const out = [];
    const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = walk.nextNode()) && out.length < 120) {
      const st = n.parentElement ? getComputedStyle(n.parentElement) : null;
      if (st && (st.visibility === 'hidden' || st.display === 'none' || st.opacity === '0')) continue;
      const s = n.textContent || '';
      for (let k = 0; k < s.length && out.length < 120; k++) {
        const r = document.createRange();
        r.setStart(n, k); r.setEnd(n, k + 1);
        const b = r.getBoundingClientRect();
        if (b.width <= 0 || b.height <= 0) continue;
        if (b.bottom <= box.top || b.top >= box.bottom || b.right <= box.left || b.left >= box.right) continue;
        out.push([Math.round(b.top / 4), b.left, s[k]]);
      }
    }
    out.sort((p, q) => (p[0] - q[0]) || (p[1] - q[1]));
    return out.map(c => c[2]).join('');
  };
  const res = [];
  posts.forEach((art, i) => {
    if (a.only !== null && a.only !== undefined && i !== a.only) return;
    const outer = x => { const p = x.parentElement && x.parentElement.closest(R); return !p || !art.contains(p); };
    const root = art.matches(R) ? art : (Array.from(art.querySelectorAll(R)).find(
      x => outer(x) && !/^(Comment|Reply)/i.test(x.getAttribute('aria-label') || '')) || art);
    const own = n => { const c = n.closest(R); return !c || c === root || !art.contains(c); };
    const rect = art.getBoundingClientRect();
    const allBodies = Array.from(art.querySelectorAll(a.body)).filter(own);
    const bodies = allBodies.filter(b => !allBodies.some(o => o !== b && o.contains(b)));
    const inBody = n => bodies.some(b => b.contains(n));
    const links = [], times = [];
    let tlink = -1;
    Array.from(art.querySelectorAll('a[href]')).forEach((el, j) => {
      if (!own(el)) return;
      const href = el.getAttribute('href') || '';
      if (links.length < 60) links.push(href);
      if (tlink < 0 && !inBody(el) && !/\/user\/|profile\.php|\/hashtag\/|set=(gm|pcb)\./.test(href) && !(el.innerText || '').trim()) {
        const tr = el.getBoundingClientRect();  // Facebook zamanı yazı olarak koymuyor (boş span'lar): ipucu fareyle okunur
        if (tr.height >= 8 && tr.height <= 30 && tr.width >= 15 && tr.width <= 220) tlink = j;
      }
      if (inBody(el) || /\/user\/|profile\.php|\/hashtag\//.test(href)) return;
      const label = (el.getAttribute('aria-label') || '').trim();
      const text = (el.innerText || '').trim();
      const lb = (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean).map(id => {
        const t = document.getElementById(id); return t ? (t.innerText || t.textContent || '') : ''; }).join(' ').trim();
      const isPost = postRe.test(href);
      if (!(isPost || href === '#' || timeRe.test(label + ' ' + text + ' ' + lb))) return;
      if (text.length > 120 && !isPost) return;
      if (times.length < 8) times.push({j, href, label: label.slice(0, 120), text: text.slice(0, 120), lb: lb.slice(0, 120),
                                        visual: visual(el).slice(0, 120)});
    });
    let text = bodies.map(b => (b.innerText || '').trim()).filter(Boolean).join('\n');
    const buttons = Array.from(art.querySelectorAll('[role="button"]'));
    let more = -1;
    for (const b of bodies) {
      for (const x of b.querySelectorAll('[role="button"]')) {
        if (/^\s*See more\s*$/i.test(x.innerText || x.textContent || '') && !x.closest('a')) { more = buttons.indexOf(x); break; }
      }
      if (more >= 0) break;
    }
    if (!bodies.length) {
      const c = root.cloneNode(true);
      c.querySelectorAll(a.strip).forEach(x => x.remove());
      c.querySelectorAll('br').forEach(x => x.replaceWith('\n'));
      c.querySelectorAll('div, p, li').forEach(x => x.append('\n'));
      text = (c.textContent || '').trim();
    }
    let pinned = false, seenLeaves = 0;
    for (const e of art.querySelectorAll('span, a, div')) {
      if (++seenLeaves > 400) break;
      if (e.childElementCount === 0 && own(e) && !inBody(e) && pinRe.test((e.textContent || '').trim())) { pinned = true; break; }
    }
    const images = Array.from(art.querySelectorAll('img')).filter(own).slice(0, 12).map(m => ({
      src: m.currentSrc || m.getAttribute('src') || '', w: m.naturalWidth || m.width || 0, h: m.naturalHeight || m.height || 0}));
    const ut = Array.from(art.querySelectorAll('[data-utime]')).find(own);
    res.push({i, top: Math.round(rect.top), vh, links, times, tlink, utime: ut ? ut.getAttribute('data-utime') : null,
              text: text.slice(0, 8000), body: bodies.length > 0, see_more: more, pinned, images});
  });
  return res;
}"""

# Tıklanacak/üzerine gelinecek öğenin ekrandaki yeri (gerçek fare olayı için; JS ile tıklanmaz). Ekranda değilse null.
TARGET_JS = r"""(a) => {
  const cands = Array.from(document.querySelectorAll(a.posts));
  const posts = cands.filter(x => !cands.some(o => o !== x && o.contains(x)));
  const art = posts[a.i];
  if (!art) return null;
  const el = (a.kind === 'link' ? art.querySelectorAll('a[href]') : art.querySelectorAll('[role="button"]'))[a.j];
  if (!el) return null;
  const rr = document.createRange();
  rr.selectNodeContents(el);
  const ok = x => x.width > 0 && x.height > 0 && x.top >= 70 && x.bottom <= window.innerHeight - 10;  // üst şerit altında değil
  const r = Array.from(rr.getClientRects()).find(ok);  // yazının görünen kutusu (blok düğmede tüm satır değil)
  if (!r) return null;
  return {x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width, h: r.height};
}"""

# Fareyle gelinen zaman bağlantısının ipucu (role=tooltip, ör. "Tuesday 6 October 2026 at 18:27"): öğeye dikeyde en yakın görünen ipucu.
TOOLTIP_JS = r"""(a) => {
  let best = null, dist = 1e9;
  for (const t of document.querySelectorAll('[role="tooltip"]')) {
    const r = t.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    const d = Math.abs((r.top + r.bottom) / 2 - a.y);
    if (d < dist) { dist = d; best = (t.innerText || '').trim(); }
  }
  return dist <= a.near ? (best || '').slice(0, 120) : null;
}"""

# Oturum/engel denetimi için sayfa durumu. Gövde metni yalnız akış YOKSA alınır (gönderi metni taranmasın); hiçbir yere yazılmaz.
STATE_JS = r"""() => {
  const feed = !!document.querySelector('[role="feed"]');
  const pass = !!document.querySelector('input[name="pass"]');
  const dialogs = Array.from(document.querySelectorAll('[role="dialog"], [role="alertdialog"]')).slice(0, 5)
    .map(d => (d.innerText || '').slice(0, 800));
  const body = feed ? '' : ((document.body && document.body.innerText) || '').slice(0, 6000);
  return {has_feed: feed, login_form: pass, dialogs, body};
}"""

# about:blank'te (Facebook dışı, ağsız) WebRTC aday toplama: proxy kuralı işliyorsa aday ÇIKMAZ.
WEBRTC_JS = r"""async () => {
  const pc = new RTCPeerConnection({iceServers: []});
  const out = [];
  pc.onicecandidate = e => { if (e.candidate && e.candidate.candidate) out.push(e.candidate.candidate.split(' ')[2] || '?'); };
  pc.createDataChannel('x');
  await pc.setLocalDescription(await pc.createOffer());
  await new Promise(r => setTimeout(r, 2000));
  pc.close();
  return out;
}"""


# ---------------------------------------------------------------- ayar (saf)

def parse_proxy(value: str | None) -> dict:
    """'http://kullanici:sifre@sunucu:port' -> Playwright proxy sözlüğü. Kimlik bilgisi hiçbir hata mesajına girmez."""
    if not value or not value.strip():
        raise SocialStop(Signal.IP_CHANGED, "proxy ayarı yok")
    try:
        u = urlparse(value.strip())
        host, port = u.hostname, u.port
    except ValueError:
        raise SocialStop(Signal.IP_CHANGED, "proxy ayarı okunamadı") from None
    if u.scheme not in ("http", "https", "socks5") or not host or not port:
        raise SocialStop(Signal.IP_CHANGED, "proxy ayarı okunamadı (biçim: http://kullanici:sifre@sunucu:port)")
    if u.scheme == "socks5" and u.username:
        raise SocialStop(Signal.IP_CHANGED, "Chromium şifreli socks5 proxy desteklemiyor: http proxy kullan")
    server = f"{u.scheme}://[{host}]:{port}" if ":" in host else f"{u.scheme}://{host}:{port}"
    out = {"server": server}
    if u.username:
        out |= {"username": unquote(u.username), "password": unquote(u.password or "")}
    return out


def mask_proxy(value: str | None) -> str:
    """Günlük/mesaj için: kullanıcı adı ve şifre gizlenir."""
    if not value:
        return "(yok)"
    try:
        u = urlparse(value.strip())
        host, port = u.hostname or "?", u.port
    except ValueError:
        return "(okunamadı)"
    auth = "***:***@" if u.username or u.password else ""
    return f"{u.scheme}://{auth}{host}" + (f":{port}" if port else "")


def proxy_from_env(env: Mapping[str, str]) -> dict:
    return parse_proxy(env.get(PROXY_ENV))


def browser_tz(env: Mapping[str, str]) -> str:
    """Tarayıcının (ve zaman ayrıştırmanın) saat dilimi. Geçersizse okuma başlamaz (IP ülkesiyle tutarlılık şart)."""
    name = (env.get(TZ_ENV) or BROWSER_TZ).strip()
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise SocialStop(Signal.IP_CHANGED, f"{TZ_ENV} geçersiz saat dilimi") from None
    return name


def launch_args(proxy: dict) -> list[str]:
    """Chromium bayrakları. Yerel DNS çözümü kapalı (proxy sunucusu hariç): hedef adlar proxy'ye ADIYLA gider (CONNECT);
    bir şey proxy dışından çözülmeye kalkarsa başarısız olur (yerel deneme: sayfa ve context.request ikisi de proxy'den geçti)."""
    host = urlparse(proxy["server"]).hostname or ""
    return list(LAUNCH_ARGS) + [f"--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE {host}"]


def context_options(tz_name: str) -> dict:
    return {"locale": LOCALE, "timezone_id": tz_name, "viewport": dict(VIEWPORT)}


def parse_ip(text: str | None) -> str | None:
    """ipify JSON'u ya da düz metin -> geçerli IP (yoksa None)."""
    raw = (text or "").strip()
    try:
        raw = str(json.loads(raw).get("ip", "")) if raw.startswith("{") else raw
        return str(ipaddress.ip_address(raw))
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------- ayrıştırma (saf)

def parse_fb_time(text: str | None, now: datetime, tz: str = BROWSER_TZ) -> datetime | None:
    """Facebook zaman ifadesi -> UTC (saat dilimli). Göreli ifade ("5m", "6h", "Just now") gerçek süre olarak `now`dan çıkarılır
    (yaz saati geçişinde kaymaz); duvar saati ifadesi ("Yesterday at 3:15 PM", "October 2 at 10:00 AM") tarayıcının saat dilimindedir."""
    s = (text or "").strip()
    if not s or len(s) > 80:
        return None
    zone = ZoneInfo(tz)
    local_now = now.astimezone(zone).replace(tzinfo=None)
    parsed = forage_parser.parse_timestamp(s, now=local_now)
    if parsed is None:
        return None
    if forage_parser.is_relative(s):
        out = now - (local_now - parsed)
    else:
        out = parsed.replace(tzinfo=zone).astimezone(timezone.utc)
    if out > now + timedelta(minutes=5) or out < now - timedelta(days=5 * 365):
        return None
    return out


_MONTHS = {m: i for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august", "september",
                                        "october", "november", "december"), 1)}
_TIP_DMY = re.compile(r"(?:[a-z]+,?\s+)?(\d{1,2})\s+([a-z]+),?\s+(\d{4})\s+at\s+(\d{1,2}):(\d{2})\s*([ap]m)?$")
_TIP_MDY = re.compile(r"(?:[a-z]+,?\s+)?([a-z]+)\s+(\d{1,2}),?\s+(\d{4})\s+at\s+(\d{1,2}):(\d{2})\s*([ap]m)?$")


def parse_tooltip_time(text: str | None, now: datetime, tz: str = BROWSER_TZ) -> datetime | None:
    """Zaman bağlantısı ipucundaki tam tarih (tarayıcının saat diliminde) -> UTC. "Tuesday 6 October 2026 at 18:27" (İngiltere) ve
    "Tuesday, October 6, 2026 at 6:27 PM" (ABD). Tanınmayan, gelecekte ya da 5 yıldan eski -> None."""
    s = " ".join((text or "").replace("\u202f", " ").replace("\u200e", "").split()).lower()
    if not s or len(s) > 80:
        return None
    if m := _TIP_DMY.match(s):
        day, month, year, hour, minute, ampm = m.groups()
    elif m := _TIP_MDY.match(s):
        month, day, year, hour, minute, ampm = m.groups()
    else:
        return None
    if month not in _MONTHS:
        return None
    h = int(hour)
    if ampm:
        if not 1 <= h <= 12:
            return None
        h = h % 12 + (12 if ampm == "pm" else 0)
    try:
        out = datetime(int(year), _MONTHS[month], int(day), h, int(minute), tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)
    except ValueError:
        return None
    if out > now + timedelta(minutes=5) or out < now - timedelta(days=5 * 365):
        return None
    return out


@dataclass(frozen=True)
class PostRef:
    group: str | None  # sayısal kimlik ya da okunur ad (slug); bilinmiyorsa None
    post_id: str


_HOSTS = {"facebook.com", "www.facebook.com", "web.facebook.com", "m.facebook.com"}
_RESERVED = {"feed", "discover", "joins", "create", "notifications", "search", "your_groups", "category", "learning_content"}
_GROUP_OK = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_PID_OK = re.compile(r"^(?:\d{5,25}|pfbid[A-Za-z0-9]{10,})$")
_GROUP_POST = re.compile(r"^/groups/([^/]+)/(?:posts|permalink)/([^/?#]+)")
_GROUP_ROOT = re.compile(r"^/groups/([^/?#]+)")


def _valid_group(g: str | None) -> str | None:
    return g if g and _GROUP_OK.match(g) and g.lower() not in _RESERVED else None


def _fb_url(href: str | None):
    if not href or href.startswith("#"):
        return None
    u = urlparse(urljoin(FB_ORIGIN + "/", href))
    return u if (u.hostname or "") in _HOSTS else None


def parse_post_url(href: str | None) -> PostRef | None:
    """Gönderi bağlantısı -> (grup, gönderi kimliği). Yorum bağlantısı ve Facebook dışı adres None."""
    u = _fb_url(href)
    if u is None:
        return None
    q = parse_qs(u.query)
    if "comment_id" in q or "reply_comment_id" in q:
        return None
    if (m := _GROUP_POST.match(u.path)) and _PID_OK.match(m.group(2)):
        return PostRef(_valid_group(m.group(1)), m.group(2))
    if (m := _GROUP_ROOT.match(u.path)) and "multi_permalinks" in q:
        pid = q["multi_permalinks"][0].split(",")[0]
        return PostRef(_valid_group(m.group(1)), pid) if _PID_OK.match(pid) else None
    if (m := re.match(r"^(?:gm|pcb)\.(\d{5,25})$", (q.get("set") or [""])[0])):  # gönderi fotoğrafı: set=gm.<gönderi>
        return PostRef(_valid_group((q.get("idorvanity") or [None])[0]), m.group(1))
    if "story_fbid" in q and (pid := forage_parser.extract_post_id(u.geturl())) and _PID_OK.match(pid):
        return PostRef(None, pid)
    return None


def group_from_url(href: str | None) -> str | None:
    u = _fb_url(href)
    if u is None:
        return None
    if (m := _GROUP_ROOT.match(u.path)) and (g := _valid_group(m.group(1))):
        return g
    return _valid_group((parse_qs(u.query).get("idorvanity") or [None])[0])


def post_identity(item: dict) -> tuple[str | None, str | None]:
    """(grup, gönderi kimliği). Önce zaman bağlantısı (gönderinin kendi bağlantısı), sonra gönderideki diğer bağlantılar."""
    hrefs = [t.get("href") for t in item.get("times") or []] + list(item.get("links") or [])
    group = pid = None
    for h in hrefs:
        ref = parse_post_url(h)
        if ref is None:
            continue
        if pid is None:
            pid, group = ref.post_id, ref.group
        elif ref.post_id == pid and group is None:
            group = ref.group
    if pid and group is None:
        group = next((g for g in map(group_from_url, hrefs) if g), None)
    return group, pid


def permalink(group_key: str, post_id: str) -> str:
    """Kalıcı, izleme parametresiz gönderi adresi (__cft__/__tn__ izleyiciye özeldir, saklanmaz)."""
    return f"{FB_ORIGIN}/groups/{group_key}/posts/{post_id}/"


def _is_time_line(line: str) -> bool:
    return len(line) <= 40 and forage_parser.parse_timestamp(line, now=datetime(2026, 1, 1)) is not None


def clean_text(raw: str | None, fallback: bool = False) -> str:
    """Gövde metni: "See more", çeviri düğmeleri, arayüz kırıntısı atılır. Yedek metinde (gövde bulunamadı) tek başına duran
    zaman satırı da atılır."""
    lines = [re.sub(r"[ \t  ]+", " ", ln).strip() for ln in (raw or "").replace("\r", "\n").split("\n")]
    lines = [ln for ln in lines if ln and ln not in UI_EXTRA_LINES and not (fallback and _is_time_line(ln))]
    text = forage_parser.clean_parts(lines)
    return re.sub(r"\s*(?:…|\.\.\.)$", "", text) if re.search(r"See more\s*$", raw or "") else text


_CDN = re.compile(r"^https://scontent[^/]*\.fbcdn\.net/", re.I)


def pick_image(images: list[dict] | None) -> str | None:
    """Gönderinin ilk fotoğrafı (scontent CDN). Emoji/simge (static.*), küçük görsel (profil resmi) atlanır."""
    for im in images or ():
        src = (im.get("src") or "").strip()
        if not _CDN.match(src) or "emoji" in src:
            continue
        if min(int(im.get("w") or 0), int(im.get("h") or 0)) < MIN_IMAGE_PX:
            continue
        return src
    return None


def item_time(item: dict, now: datetime, tz: str = BROWSER_TZ) -> datetime | None:
    ut = str(item.get("utime") or "")
    if ut.isdigit():
        return datetime.fromtimestamp(int(ut), timezone.utc)

    def rank(t: dict) -> int:
        href = t.get("href") or ""
        return 0 if parse_post_url(href) else (1 if href in ("", "#") else 2)
    for t in sorted(item.get("times") or [], key=rank):
        for k in ("label", "lb", "visual", "text"):
            if dt := parse_fb_time(t.get(k), now, tz):
                return dt
    return None


def source_matches(source: SocialSource, group: str) -> bool:
    return group.lower() in {source.key.lower(), (source.slug or "").lower()} - {""}


def to_post(item: dict, source: SocialSource, post_id: str, now: datetime, tz: str = BROWSER_TZ) -> SocialPost:
    return SocialPost(
        platform="facebook",
        source_key=source.key,
        post_id=post_id,
        url=permalink(source.key, post_id),
        posted_at=item_time(item, now, tz),
        text=clean_text(item.get("text"), fallback=not item.get("body", True)),
        image_url=pick_image(item.get("images")),
        owner=None,  # Facebook'ta yazar ASLA saklanmaz
        pinned=bool(item.get("pinned")),
    )


def _norm(text: str) -> str:
    return (text or "").replace("’", "'").lower()


def classify_page(url: str, state: dict, has_c_user: bool) -> str:
    """Sayfa durumu -> 'ok' | 'empty' ya da istisna (SocialStop: platform durur; SourceError: yalnız bu kaynak)."""
    u = _norm(url)
    for where, needle, signal in DETECTION:
        if where == "url" and needle in u and signal is not None:
            raise SocialStop(signal, f"adres: {needle}")
    if state.get("login_form") or not has_c_user:
        raise SocialStop(Signal.LOGIN_REQUIRED, "giriş formu" if state.get("login_form") else "c_user çerezi yok")
    text = _norm(" ".join(list(state.get("dialogs") or []) + [state.get("body") or ""]))
    for where, needle, signal in DETECTION:
        if where == "text" and signal is not None and needle in text:
            raise SocialStop(signal, f"uyarı: {needle}")
    if state.get("has_feed"):
        return "ok"
    for where, needle, signal in DETECTION:
        if where == "text" and signal is None and needle in text:
            raise SourceError(f"sayfa: {needle}")
    if any(m in text for m in EMPTY_MARKERS):
        return "empty"
    raise SourceError("akış bulunamadı (sayfa yapısı tanınmadı)")


# ---------------------------------------------------------------- durma kuralları (saf)

@dataclass
class FeedScan:
    """Tek kaynağın durma kuralları. Gönderiler akış sırasıyla (yeniden eskiye) offer'a verilir.
    Dur: imleç gönderisi görüldü | art arda OLD_STREAK eski gönderi | max_posts toplandı (kaydırma tavanı tarayıcı tarafında)."""
    cursor: Cursor
    now: datetime
    max_posts: int
    posts: list[SocialPost] = field(default_factory=list)
    seen: int = 0
    old_streak: int = 0
    stop: str | None = None  # 'imlec' | 'eski' | 'tavan'
    _ids: set[str] = field(default_factory=set)
    _newest: SocialPost | None = None  # sonraki imleç: yalnız kesin yeni (imleçten sonraki) gönderilerden
    _newest_seen: SocialPost | None = None  # ilk okumada yeni yoksa: görülen en yeni gönderi

    @property
    def first_read(self) -> bool:
        return self.cursor.post_id is None and self.cursor.posted_at is None

    def limit(self) -> datetime | None:
        """Bu ana kadar (dahil) olan gönderi eski sayılır; None = tarih kuralı yok (yalnız imleç kimliği)."""
        if self.first_read:
            return self.now - FIRST_WINDOW
        return self.cursor.posted_at - OLD_MARGIN if self.cursor.posted_at else None

    def wants(self, post: SocialPost) -> bool:
        """Bu gönderi değerlendirilecek mi (tarih için fareyle gelmeye değer mi)? İmleç gönderisi ve tekrar değil."""
        return not self.stop and post.post_id != self.cursor.post_id and post.post_id not in self._ids

    def offer(self, post: SocialPost) -> bool:
        """Gönderiyi değerlendirir; alındıysa True."""
        if self.stop:
            return False
        if post.post_id == self.cursor.post_id:
            self.seen += 1
            if not post.pinned:  # sonradan sabitlenmiş imleç gönderisi en üstte görünür: durdurmaz (asıl yerinde durdurur)
                self.stop = "imlec"
            return False
        if post.post_id in self._ids:  # sabitlenmiş kopya + asıl yeri: bir kez alınır
            return False
        self._ids.add(post.post_id)
        self.seen += 1
        if self.max_posts <= 0:
            self.stop = "tavan"
            return False
        t, limit = post.posted_at, self.limit()
        if t is None:
            # ilk okumada tarihsiz gönderi alınmaz (eski olabilir). Tarihli gönderi hiç görülmezse en üstteki (sabit olmayan)
            # tarihsiz gönderi imleç olur: sonraki okumalar kimlikle durur (kronolojik akış); yoksa her tur "ilk okuma" kalırdı.
            if self.first_read:
                if not post.pinned and self._newest_seen is None:
                    self._newest_seen = post
                return False
            return self._take(post, newer=True)
        if self.first_read and not post.pinned and (
                self._newest_seen is None or self._newest_seen.posted_at is None or t > self._newest_seen.posted_at):
            self._newest_seen = post
        if limit is not None and (t < limit if self.first_read else t <= limit):
            self.old_streak += 1
            if self.old_streak >= OLD_STREAK:
                self.stop = "eski"
            return False
        newer = self.cursor.posted_at is None or t > self.cursor.posted_at
        if newer:
            self.old_streak = 0
        return self._take(post, newer)  # newer=False: imleçle aynı saat dilimi; tekrar olabilir (yazım upsert ile tekilleşir)

    def _take(self, post: SocialPost, newer: bool) -> bool:
        self.posts.append(post)
        if newer and not post.pinned and (self._newest is None or (
                post.posted_at and (self._newest.posted_at is None or post.posted_at > self._newest.posted_at))):
            self._newest = post
        if len(self.posts) >= self.max_posts:
            self.stop = "tavan"
        return True

    def replace_last(self, post: SocialPost) -> None:
        """"See more" sonrası tam metinle değiştirir (aynı gönderi)."""
        if self.posts and self.posts[-1].post_id == post.post_id:
            self.posts[-1] = post
            if self._newest and self._newest.post_id == post.post_id:
                self._newest = post

    def result(self, seen: int | None = None, requests: int = 0) -> FetchResult:
        posts = sorted(self.posts, key=lambda p: p.posted_at or self.now, reverse=True)
        top = self._newest or self._newest_seen
        cursor = Cursor(top.post_id, top.posted_at or self.cursor.posted_at) if top else self.cursor
        return FetchResult(posts=posts, cursor=cursor, seen=self.seen if seen is None else seen, requests=requests)


@dataclass
class CombinedScan:
    """Birleşik akış (Feeds → Groups): her grubun kendi FeedScan'i; ayrıca akış, izlenen TÜM grupların eşiğinden eskiye
    art arda OLD_STREAK kez düşerse durulur."""
    scans: dict[str, FeedScan]
    old_streak: int = 0

    def offer(self, key: str, post: SocialPost) -> bool:
        taken = self.scans[key].offer(post)
        self.note(post.posted_at)
        return taken

    def note(self, posted_at: datetime | None) -> None:
        if posted_at is None:
            return
        live = [s.limit() for s in self.scans.values() if s.stop is None]
        if live and all(lim is not None and posted_at <= lim for lim in live):
            self.old_streak += 1
        else:
            self.old_streak = 0

    @property
    def stopped(self) -> bool:
        return all(s.stop for s in self.scans.values()) or self.old_streak >= OLD_STREAK


# ---------------------------------------------------------------- tarayıcı

@dataclass
class BrowserSession:
    context: object  # playwright BrowserContext (testte sahte)
    close: Callable[[], None]


def _open_browser(proxy: dict, headless: bool, tz_name: str, state_path: Path | None) -> BrowserSession:
    """Tur başına TEK tarayıcı + TEK bağlam. Proxy tarayıcı düzeyinde verilir: context.request (çıkış IP denetimi) de aynı proxy'yi kullanır."""
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=headless, proxy=proxy, args=launch_args(proxy))
        context = browser.new_context(storage_state=str(state_path) if state_path else None, **context_options(tz_name))
    except Exception:
        pw.stop()
        raise

    def close() -> None:
        for fn in (context.close, browser.close, pw.stop):
            try:
                fn()
            except Exception:
                pass
    return BrowserSession(context, close)


def has_c_user(context) -> bool:
    return any(c.get("name") == "c_user" and c.get("value") for c in context.cookies(FB_ORIGIN))


def _write_state(path: Path, state: dict) -> None:
    """Oturum dosyası: önce 600 izinle geçici dosya, sonra yerine taşı (yarım yazılmış dosya kalmaz)."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _check_webrtc(context) -> None:
    """WebRTC proxy dışına aday üretiyorsa (bayrak etkisiz) Facebook'a HİÇ gidilmeden durulur."""
    page = context.new_page()
    try:
        found = page.evaluate(WEBRTC_JS)
    finally:
        page.close()
    if found:
        raise SocialStop(Signal.IP_CHANGED, f"WebRTC proxy dışına çıkabiliyor ({len(found)} aday): Chromium bayrağı etkisiz")


def _item_key(item: dict) -> str:
    _, pid = post_identity(item)
    if pid:
        return "p:" + pid + (":sabit" if item.get("pinned") else "")  # sabitlenmiş kopya asıl yerinde yeniden değerlendirilir
    raw = (item.get("text") or "")[:300] + "|" + "|".join((item.get("links") or [])[:5])
    return "h:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()


class FacebookBrowserFetcher:
    """SocialFetcher (Facebook). Tarayıcı ilk kullanımda açılır; tur sonunda close() oturumu kaydeder ve kapatır."""
    platform = "facebook"

    def __init__(self, proxy: dict, state_path: Path, headless: bool = False, tz_name: str = BROWSER_TZ, *,
                 opener: Callable[[], BrowserSession] | None = None, sleep: Callable[[float], None] = time.sleep,
                 rng: random.Random | None = None, clock: Callable[[], datetime] | None = None):
        self.proxy, self.state_path, self.headless, self.tz = proxy, Path(state_path), headless, tz_name
        self._opener = opener or (lambda: _open_browser(self.proxy, self.headless, self.tz, self.state_path))
        self._sleep, self._rng = sleep, rng or random.Random()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._session: BrowserSession | None = None
        self._page_obj = None
        self._healthy = True
        self._loads = 0  # sayfa yüklemesi (istek) sayacı
        self._expands = self._hovers = self._time_hovers = 0
        self._expand_off = False
        self.last_stats: dict = {}  # son okumanın sayaçları (yalnız sayı; metin/ad yok): 2. gün denemesinde teşhis için

    # -- oturum

    def _context(self):
        if self._session is None:
            self._session = self._opener()
            try:
                _check_webrtc(self._session.context)
            except SocialStop:
                self._healthy = False
                raise
        return self._session.context

    def _page(self):
        ctx = self._context()
        if self._page_obj is None:
            self._page_obj = ctx.new_page()
        return self._page_obj

    def _pause(self, span: tuple[float, float]) -> None:
        self._sleep(self._rng.uniform(*span))

    def _check(self, page) -> str:
        try:
            return classify_page(page.url, page.evaluate(STATE_JS), has_c_user(self._context()))
        except SocialStop:
            self._healthy = False
            raise

    def check_egress(self) -> str:
        ctx = self._context()
        for url in EGRESS_URLS:
            try:
                resp = ctx.request.get(url, timeout=EGRESS_TIMEOUT_MS)
                ip = parse_ip(resp.text()) if resp.ok else None
            except Exception:
                ip = None
            if ip:
                return ip  # beklenen IP ile karşılaştırma işçinin işi (application/social_run._check_ip); burada karar yok
        raise Unreachable("çıkış IP'si okunamadı (proxy/ağ yanıt vermiyor)")

    def fetch_image(self, url: str) -> tuple[bytes, str] | None:
        """Gönderinin ilk fotoğrafı AYNI bağlamdan (proxy) indirilir (VPS'te proxy dışı çıkış kapalı). Yalnız scontent CDN, image/*,
        en çok 5 MB; başarısızlıkta None (gönderi fotoğrafsız ele alınır)."""
        if not _CDN.match(url or ""):
            return None
        try:
            resp = self._context().request.get(url, timeout=IMAGE_TIMEOUT_MS)
            mime = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
            if not resp.ok or not mime.startswith("image/") or int(resp.headers.get("content-length") or 0) > MAX_IMAGE_BYTES:
                return None
            body = resp.body()
        except (SocialStop, Unreachable):
            raise
        except Exception:
            return None
        return (body, mime) if 0 < len(body) <= MAX_IMAGE_BYTES else None

    def close(self) -> None:
        if self._session is None:
            return
        try:
            ctx = self._session.context
            if self._healthy and has_c_user(ctx):  # Facebook çerezleri yeniler: taze oturum saklanır (düşmüş oturum saklanmaz)
                _write_state(self.state_path, ctx.storage_state())
        finally:
            self._session.close()
            self._session = self._page_obj = None

    # -- sayfa

    def _open_feed(self, page, url: str) -> str:
        if self._loads:
            self._pause(PAGE_GAP_S)
        self._loads += 1
        self._expands = self._hovers = self._time_hovers = 0
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS)
        except Exception as e:  # ağ/proxy hatası (net::ERR_*, zaman aşımı): fren değil, tur biter. Adres (grup) mesaja girmez.
            code = re.search(r"net::(ERR_[A-Z_]+)", str(e))
            if code or "timeout" in type(e).__name__.lower() or "Timeout" in str(e)[:200]:
                raise Unreachable(f"sayfa yüklenemedi ({code.group(1) if code else 'zaman aşımı'})") from None
            raise SourceError(f"sayfa yüklenemedi ({type(e).__name__})") from None
        try:
            page.wait_for_selector('[role="feed"]', timeout=FEED_TIMEOUT_MS)
        except Exception:
            pass  # akış yoksa nedeni aşağıdaki denetim söyler
        self._pause(LOAD_PAUSE_S)
        return self._check(page)

    def _extract(self, page, only: int | None = None) -> list[dict]:
        args = {"posts": POST_SELECTOR, "body": BODY_SELECTOR, "strip": STRIP_SELECTOR,
                "pinned": "^(?:" + "|".join(re.escape(m) for m in PINNED_MARKERS) + ")$", "only": only}
        return list(page.evaluate(EXTRACT_JS, args) or [])

    def _target(self, page, i: int, kind: str, j: int) -> dict | None:
        return page.evaluate(TARGET_JS, {"posts": POST_SELECTOR, "i": i, "kind": kind, "j": j})

    def _mouse_to(self, page, x: float, y: float) -> None:
        page.mouse.move(x + self._rng.uniform(-3, 3), y + self._rng.uniform(-2, 2), steps=self._rng.randint(4, 10))

    def _scroll(self, page) -> None:
        """Tekerlekle küçük adımlar, sonra 1,5–4 sn (arada uzun okuma molası)."""
        if self._rng.random() < 0.3:
            self._mouse_to(page, VIEWPORT["width"] * self._rng.uniform(0.4, 0.6), VIEWPORT["height"] * self._rng.uniform(0.4, 0.7))
        for _ in range(self._rng.randint(2, 5)):
            page.mouse.wheel(0, self._rng.randint(80, 140))
            self._sleep(self._rng.uniform(0.05, 0.2))
        self._pause(READ_PAUSE_S if self._rng.random() < READ_PAUSE_P else SCROLL_PAUSE_S)

    def _identify(self, page, item: dict) -> dict:
        """Kimliği çıkmayan gönderi: zaman bağlantısının üzerine fareyle gelinir (Facebook asıl bağlantıyı o an yazar)."""
        if post_identity(item)[1] or self._hovers >= MAX_HOVER:
            return item
        link = next((t for t in item.get("times") or [] if (t.get("href") or "#") == "#"), None)
        rect = self._target(page, item["i"], "link", link["j"]) if link else None
        if not rect:
            return item
        self._hovers += 1
        self._mouse_to(page, rect["x"], rect["y"])
        self._sleep(self._rng.uniform(0.8, 1.5))
        fresh = self._extract(page, only=item["i"])
        return fresh[0] if fresh else item

    def _hover_time(self, page, item: dict) -> datetime | None:
        """Zaman bağlantısına (gerçek fare) gelinir, çıkan ipucundaki tam tarih okunur. Facebook zamanı yazı olarak koymuyor."""
        if self._time_hovers >= MAX_TIME_HOVER or int(item.get("tlink", -1)) < 0:
            return None
        rect = self._target(page, item["i"], "link", int(item["tlink"]))
        if not rect:
            return None
        self._time_hovers += 1
        self._mouse_to(page, rect["x"], rect["y"])
        waited = 0.0
        while waited < TIME_TIP_WAIT_S:
            step = self._rng.uniform(0.25, 0.4)
            self._sleep(step)
            waited += step
            tip = page.evaluate(TOOLTIP_JS, {"y": rect["y"], "near": 120})
            if tip and (t := parse_tooltip_time(tip, self._clock(), self.tz)):
                return t
        return None

    def _dated(self, page, item: dict, post: SocialPost, stats: Counter) -> SocialPost:
        if post.posted_at is not None:
            return post
        t = self._hover_time(page, item)
        stats["ipucu_tarih" if t else "ipucu_yok"] += int(int(item.get("tlink", -1)) >= 0)
        return replace(post, posted_at=t) if t else post

    def _expand(self, page, item: dict) -> dict | None:
        """Gövdedeki "See more"a gerçek fare tıklaması; sayfa değişirse geri dönülür ve bu tur bir daha basılmaz."""
        if self._expand_off or self._expands >= MAX_EXPAND or int(item.get("see_more", -1)) < 0:
            return None
        rect = self._target(page, item["i"], "button", int(item["see_more"]))
        if not rect or rect["w"] > 300 or rect["h"] > 60:
            return None
        before = page.url
        self._expands += 1
        self._mouse_to(page, rect["x"], rect["y"])
        self._sleep(self._rng.uniform(0.2, 0.6))
        page.mouse.click(rect["x"], rect["y"], delay=self._rng.randint(40, 120))
        self._sleep(self._rng.uniform(0.8, 1.6))
        if page.url != before:
            self._expand_off = True
            self.last_stats["sayfa_degisti"] = self.last_stats.get("sayfa_degisti", 0) + 1
            self._loads += 1
            page.go_back(wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS)
            return None
        fresh = self._extract(page, only=item["i"])
        return fresh[0] if fresh else None

    def _walk(self, page, visit: Callable[[object, dict], bool]) -> str:
        """Akışı kaydırarak hazır gönderileri sırayla visit'e verir; visit True dönerse durur. Döner: durma nedeni."""
        done: set[str] = set()
        idle = 0
        self._mouse_to(page, VIEWPORT["width"] * 0.5, VIEWPORT["height"] * 0.55)  # tekerlek akışın üstünde dönsün
        for n in range(MAX_SCROLLS + 1):
            fresh = 0
            for item in self._extract(page):
                if item.get("top", 0) >= (item.get("vh") or VIEWPORT["height"]) * READY_FRACTION:
                    continue  # henüz ekranın altında: sonraki kaydırmada okunur
                key = _item_key(item)
                if key in done:
                    continue
                done.add(key)
                item = self._identify(page, item)
                if (key2 := _item_key(item)) != key:
                    if key2 in done:
                        continue
                    done.add(key2)
                fresh += 1
                if visit(page, item):
                    return "kural"
            idle = 0 if fresh else idle + 1
            if idle >= IDLE_SCROLLS:
                return "akis_bitti"
            if n == MAX_SCROLLS:
                break
            self._scroll(page)
            if n % CHECK_EVERY == CHECK_EVERY - 1:
                self._check(page)
        return "kaydirma_tavani"

    def _take_expanded(self, page, item: dict, post: SocialPost, scan: FeedScan, stats: Counter) -> None:
        if int(item.get("see_more", -1)) < 0:
            return
        fresh = self._expand(page, item)
        if fresh is None:
            stats["kisa_kaldi"] += 1
            return
        stats["genisletildi"] += 1
        scan.replace_last(replace(post, text=clean_text(fresh.get("text"), fallback=not fresh.get("body", True))))

    # -- okuma

    def fetch_new(self, source: SocialSource, cursor: Cursor, max_posts: int) -> FetchResult:
        now = self._clock()
        scan = FeedScan(cursor, now, max_posts)
        stats: Counter = Counter()
        self.last_stats = stats
        page = self._page()
        start = self._loads
        try:
            status = self._open_feed(page, GROUP_URL.format(key=source.key))
        except SourceError as e:
            raise SourceError(f"{source.alias}: {e}") from None
        if status == "empty":
            return FetchResult(posts=[], cursor=cursor, seen=0, requests=self._loads - start)
        looked = 0

        def visit(page, item: dict) -> bool:
            nonlocal looked
            looked += 1
            group, pid = post_identity(item)
            if not pid:
                stats["kimliksiz"] += 1
                return False
            if group and not source_matches(source, group):
                stats["grup_adi_farkli"] += 1  # grubun kendi akışı: yine de bu gruba yazılır (kaynak listesine slug eklenmeli)
            post = to_post(item, source, pid, now, self.tz)
            if scan.wants(post):
                post = self._dated(page, item, post, stats)
            stats["tarihsiz"] += post.posted_at is None
            stats["yedek_metin"] += not item.get("body", True)
            if scan.offer(post):
                self._take_expanded(page, item, post, scan, stats)
            return scan.stop is not None

        walk = self._walk(page, visit)
        stats["durma:" + (scan.stop or walk)] += 1
        return scan.result(seen=looked, requests=self._loads - start)

    def fetch_combined(self, sources: list[SocialSource], cursors: dict[str, Cursor] | int | None = None,
                       max_posts: int | None = None) -> dict[str, FetchResult]:
        """Tek sayfada (COMBINED_FEED_URL) üye olunan tüm grupların gönderileri; grup gönderi bağlantısından çıkarılır.
        İki çağrı biçimi: fetch_combined(kaynaklar, {anahtar: Cursor}, kaynak_başına_en_çok) ya da işçinin A/B karşılaştırması
        fetch_combined(kaynaklar, en_çok) (imleçsiz = ilk okuma, son 24 saat; sayı kaynak başına tavan olarak kullanılır).
        Sayfa yüklemesi (requests) yalnız ilk kaynağın sonucuna yazılır (toplam doğru kalsın). Akış bulunamazsa SourceError:
        işçi grup grup okumaya (fetch_new) dönebilir."""
        if isinstance(cursors, int):
            cursors, max_posts = None, cursors
        cursors, max_posts = cursors or {}, max_posts if max_posts is not None else 20
        if not sources:
            return {}
        now = self._clock()
        scans = {s.key: FeedScan(cursors.get(s.key) or Cursor(), now, max_posts) for s in sources}
        lookup = {s.key.lower(): s for s in sources} | {s.slug.lower(): s for s in sources if s.slug}
        multi = CombinedScan(scans)
        stats: Counter = Counter()
        self.last_stats = stats
        looked: Counter = Counter()
        page = self._page()
        start = self._loads
        status = self._open_feed(page, COMBINED_FEED_URL)

        def visit(page, item: dict) -> bool:
            group, pid = post_identity(item)
            source = lookup.get((group or "").lower())
            if not pid or source is None:
                stats["izlenmeyen"] += 1
                multi.note(item_time(item, now, self.tz))
                return multi.stopped
            looked[source.key] += 1
            post = to_post(item, source, pid, now, self.tz)
            if scans[source.key].wants(post):
                post = self._dated(page, item, post, stats)
            stats["tarihsiz"] += post.posted_at is None
            if multi.offer(source.key, post):
                self._take_expanded(page, item, post, scans[source.key], stats)
            return multi.stopped

        if status != "empty":
            stats["durma:" + self._walk(page, visit)] += 1
        requests = self._loads - start
        return {s.key: scans[s.key].result(seen=looked[s.key], requests=requests if i == 0 else 0) for i, s in enumerate(sources)}


# ---------------------------------------------------------------- modül yüzü (application/social_port.FetcherModule)

def build(env: Mapping[str, str], state_dir: Path) -> FacebookBrowserFetcher:
    """Tur başına bir kez. Proxy yoksa ya da oturum dosyası yoksa tarayıcı HİÇ açılmaz."""
    proxy = proxy_from_env(env)
    tz_name = browser_tz(env)
    state = Path(state_dir) / STATE_FILE
    if not state.is_file():
        raise SocialStop(Signal.LOGIN_REQUIRED, "oturum dosyası yok: önce login çalıştırılmalı")
    os.chmod(state, 0o600)
    return FacebookBrowserFetcher(proxy, state, headless=(env.get(HEADLESS_ENV) or "").strip() == "1", tz_name=tz_name)


def _on_home(url: str) -> bool:
    """Sayfa Facebook ana akışı mı? (kayıt, e-posta onayı, checkpoint, giriş sayfaları değil)"""
    u = urlparse(url or "")
    return (u.hostname or "").endswith("facebook.com") and u.path in HOME_PATHS


LOGIN_HELP = """
Facebook girişi (ikinci hesap):
  1. Açılan tarayıcı penceresinde (VPS ekranı) Facebook giriş sayfası var.
  2. E-posta ve şifreyi KENDİN yaz (yeni hesap: "Create new account"); doğrulama (e-posta/SMS kodu) isterse tamamla.
     Program hiçbir şey yazmaz, tıklamaz.
  3. "Save your login info?" sorulursa "Not now" de. Bitince sol üstteki Facebook logosuna bas: ANA SAYFA açılınca oturum
     kendiliğinden kaydedilir, pencere kapanır (kayıt, e-posta onayı ve doğrulama sayfalarında beklenir).
Proxy: {proxy} · saat dilimi: {tz} · en çok {minutes} dakika beklenir.
"""


def login(env: Mapping[str, str], state_dir: Path, *, opener: Callable[[], BrowserSession] | None = None,
          sleep: Callable[[float], None] = time.sleep, monotonic: Callable[[], float] = time.monotonic,
          out: Callable[[str], None] = print) -> None:
    """Sahip için etkileşimli giriş: görünür Chromium (aynı proxy), giriş sayfası açılır, sahip kendisi girer.
    c_user çerezi gelince (ve sayfa doğrulama/giriş adresinde değilken) oturum chmod 600 ile kaydedilir."""
    proxy = proxy_from_env(env)
    tz_name = browser_tz(env)
    path = Path(state_dir) / STATE_FILE
    session = (opener or (lambda: _open_browser(proxy, headless=False, tz_name=tz_name, state_path=None)))()
    try:
        _check_webrtc(session.context)
        page = session.context.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS)
        out(LOGIN_HELP.format(proxy=mask_proxy(env.get(PROXY_ENV)), tz=tz_name, minutes=LOGIN_TIMEOUT_S // 60))
        deadline = monotonic() + LOGIN_TIMEOUT_S
        while monotonic() < deadline:
            if has_c_user(session.context) and _on_home(page.url):
                sleep(5)  # Facebook oturum çerezlerini tamamlasın
                _write_state(path, session.context.storage_state())
                out("Oturum kaydedildi. Facebook okuması bu oturumla çalışır.")
                return
            sleep(LOGIN_POLL_S)
        raise SocialStop(Signal.LOGIN_REQUIRED, f"giriş {LOGIN_TIMEOUT_S // 60} dakikada tamamlanmadı; oturum kaydedilmedi")
    finally:
        session.close()


BROWSE_TIMEOUT_S = 30 * 60
BROWSE_HELP = """
Facebook elle kullanım (okuyucu hesabı, kayıtlı oturumla):
  1. VPS ekranındaki tarayıcıda Facebook ana sayfası açık. Gruplara katılma isteğini, soruların cevabını KENDİN gönder.
     Program hiçbir şey yazmaz, tıklamaz. Günde 1-2 gruptan fazlasına istek gönderme; beğeni, yorum, mesaj yok.
  2. Bitince sekmeyi kapat (pencerenin sağ üstündeki X). Oturum güncellenip kaydedilir.
Proxy: {proxy} · saat dilimi: {tz} · en çok {minutes} dakika (sonra kendiliğinden kaydedilip kapanır).
"""


def browse(env: Mapping[str, str], state_dir: Path, *, opener: Callable[[], BrowserSession] | None = None,
           sleep: Callable[[float], None] = time.sleep, monotonic: Callable[[], float] = time.monotonic,
           out: Callable[[str], None] = print) -> None:
    """Sahip için: kayıtlı oturumla görünür Chromium (aynı proxy, aynı parmak izi); sahip gruplara KENDİSİ katılır. Sekmeler kapanınca
    ya da süre dolunca oturum yeniden yazılır; oturum düşmüşse (c_user yok) eski dosya EZİLMEZ."""
    proxy = proxy_from_env(env)
    tz_name = browser_tz(env)
    path = Path(state_dir) / STATE_FILE
    if not path.exists():
        raise SocialStop(Signal.LOGIN_REQUIRED, "oturum dosyası yok: önce login çalıştırılmalı")
    session = (opener or (lambda: _open_browser(proxy, headless=False, tz_name=tz_name, state_path=path)))()
    try:
        _check_webrtc(session.context)
        page = session.context.new_page()
        page.goto(FB_ORIGIN + "/", wait_until="domcontentloaded", timeout=GOTO_TIMEOUT_MS)
        out(BROWSE_HELP.format(proxy=mask_proxy(env.get(PROXY_ENV)), tz=tz_name, minutes=BROWSE_TIMEOUT_S // 60))
        deadline = monotonic() + BROWSE_TIMEOUT_S
        while monotonic() < deadline and any(not p.is_closed() for p in session.context.pages):
            sleep(LOGIN_POLL_S)
        if has_c_user(session.context):
            _write_state(path, session.context.storage_state())
            out("Oturum güncellendi ve kaydedildi.")
        else:
            out("UYARI: oturum düşmüş görünüyor (c_user yok); kayıtlı oturum değiştirilmedi. Gerekirse: login facebook")
    finally:
        session.close()
