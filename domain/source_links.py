"""Sahibin bota attığı bir linkin hangi KAYNAK türü olduğu (saf; ağ yok): Instagram hesabı, Facebook grubu ya da bir site.
Kaynak anahtarı kalıcıdır ve büyük/küçük harf duyarsızdır: "ig:<kullanıcı>", "fb:<grup>", "web:<alan adı>" (www. atılır).
Instagram gönderisi/reel, Facebook gönderisi ve paylaşım kısaltmaları kaynak DEĞİLDİR: `kind` bunları ayrı söyler (bot uygun cevabı verir)."""
import re
from dataclasses import dataclass

IG_RESERVED = {"p", "reel", "reels", "explore", "accounts", "stories", "tv", "direct", "about", "developer", "legal", "web", "share"}
IG_USER = re.compile(r"[a-z0-9._]{1,30}")
FB_GROUP = re.compile(r"[a-z0-9._-]{1,64}")  # sayısal kimlik ya da grubun kısa adı (sosyal okuyucunun kabul ettiği kalıp)
FB_HOSTS = ("facebook.com", "m.facebook.com", "web.facebook.com", "mbasic.facebook.com", "fb.com")
# Facebook SAYFASI (22.10.2026, sosyal oturum 0be07c6): kayıtlı sayfa satırı (sources.url "https://www.facebook.com/<ad>/") okuyucuya
# "fbp:<ad>" anahtarıyla gider; <ad> küçük harf kısa ad ya da sayısal sayfa kimliği. Sahibin bota attığı sayfa linki kaynak sayılmaz
# (parse_source_link onu "fb_post" der); sayfa satırını bot oturumu ekler. Bu yollar sayfa değildir.
FB_PAGE = re.compile(r"[a-z0-9.]{1,64}")
FB_NOT_PAGE = {"groups", "share", "profile.php", "permalink.php", "story.php", "watch", "marketplace", "reel", "reels", "events", "pages",
               "photo", "photo.php", "photos", "login", "help", "gaming", "hashtag", "people", "search", "p", "home.php", "friends"}
IG_HOSTS = ("instagram.com", "m.instagram.com")
DOMAIN = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}")
URL = re.compile(r"https?://([^/?#\s]+)([^?#\s]*)(?:[?#]\S*)?", re.I)  # adres, yol, (sorgu/çapa atılır); domain/ G-Ç modülü (urllib) kullanmaz


@dataclass(frozen=True)
class SourceLink:
    kind: str  # "instagram" | "facebook" | "web" | "ig_post" | "fb_post" | "fb_share"
    handle: str = ""  # kullanıcı adı / grup kimliği ya da kısa adı / alan adı (küçük harf)

    @property
    def platform(self) -> str | None:
        return self.kind if self.kind in ("instagram", "facebook", "web") else None

    @property
    def key(self) -> str | None:
        return {"instagram": f"ig:{self.handle}", "facebook": f"fb:{self.handle}", "web": f"web:{self.handle}"}.get(self.kind)

    @property
    def url(self) -> str | None:
        return {"instagram": f"https://www.instagram.com/{self.handle}/", "facebook": f"https://www.facebook.com/groups/{self.handle}/",
                "web": f"https://{self.handle}/"}.get(self.kind)


def parse_source_link(text: str) -> SourceLink | None:
    """Tek bir http(s) linki: kaynak türü. Link değilse ya da alan adı geçersizse None."""
    raw = (text or "").strip()
    m = URL.fullmatch(raw)
    if m is None:
        return None
    host = m.group(1).rsplit("@", 1)[-1].split(":", 1)[0].lower().rstrip(".")  # kullanıcı:şifre@ ve :port atılır
    if host.startswith("www."):
        host = host[4:]
    if not DOMAIN.fullmatch(host):
        return None
    path = [p for p in m.group(2).lower().split("/") if p]
    if host in IG_HOSTS:
        if not path or path[0] in IG_RESERVED or not IG_USER.fullmatch(path[0]):
            return SourceLink("ig_post")  # gönderi, reel, hikâye ya da ana sayfa: hesap değil
        return SourceLink("instagram", path[0])
    if host in FB_HOSTS:
        if path[:1] == ["share"]:
            return SourceLink("fb_share")  # paylaşım kısaltması: hangi gruba gittiği ağsız bilinemez
        if len(path) >= 2 and path[0] == "groups" and FB_GROUP.fullmatch(path[1]) and len(path) == 2:
            return SourceLink("facebook", path[1])
        return SourceLink("fb_post")  # grup gönderisi, sayfa, profil, Marketplace...
    return SourceLink("web", host)


def key_of_url(platform: str, url: str | None) -> str | None:
    """Kayıtlı bir kaynağın (sources satırı) anahtarı: eşleme ve çift kayıt kontrolü için. Çözülemezse None."""
    link = parse_source_link(url or "")
    if link is None:
        return None
    if platform == "web":
        return f"web:{link.handle}" if link.kind == "web" else None
    if platform == "facebook" and link.kind == "fb_post":
        return fb_page_key(url)
    return link.key if link.kind == platform else None


def fb_page_key(url: str | None) -> str | None:
    """Kayıtlı Facebook sayfası satırının anahtarı "fbp:<ad>": yalnız tek parçalı yol ("https://www.facebook.com/kibrisaraba/"), küçük harfe
    çevrilir. Grup, gönderi, profil ve diğer yollar None."""
    m = URL.fullmatch((url or "").strip())
    if m is None:
        return None
    host = m.group(1).rsplit("@", 1)[-1].split(":", 1)[0].lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    path = [p for p in m.group(2).lower().split("/") if p]
    if host not in FB_HOSTS or len(path) != 1 or path[0] in FB_NOT_PAGE or not FB_PAGE.fullmatch(path[0]):
        return None
    return f"fbp:{path[0]}"
