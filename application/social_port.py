"""Sosyal medya okuyucu sözleşmesi (VPS işçisi). Facebook (tarayıcı) ve Instagram (Instaloader) okuyucuları bu tiplerle konuşur;
application/social_run.py yalnız bu sözleşmeyi bilir (Playwright/Instaloader bilmez).

Her okuyucu modülü (infrastructure/collectors/facebook_browser.py, infrastructure/collectors/instagram_instaloader.py) iki işlev verir:
  build(env: Mapping[str, str], state_dir: Path) -> SocialFetcher   # tur başına bir kez; ağır kütüphaneyi işlev İÇİNDE import eder
  login(env: Mapping[str, str], state_dir: Path) -> None            # sahip VPS'teki tarayıcıda KENDİSİ girer; kod şifre yazmaz

Gizlilik: yazar adı/kimliği, yorum, profil bağlantısı hiçbir tipte YOK. Gizli grup adı günlüğe yazılmaz (alias kullanılır)."""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from domain.social_brake import Signal

Platform = Literal["facebook", "instagram"]
PLATFORMS: tuple[Platform, ...] = ("facebook", "instagram")


@dataclass(frozen=True)
class SocialSource:
    """Okunacak tek kaynak.
    key: Facebook'ta grubun sayısal kimliği; Instagram'da kullanıcı adı (küçük harf).
    alias: günlükte görünen kısa takma ad (gizli grup adı ASLA günlüğe/çıktıya yazılmaz).
    source_id: veritabanı kimliği (deneme kipinde None)."""
    platform: Platform
    key: str
    url: str
    alias: str
    source_id: str | None = None
    slug: str | None = None  # Facebook grubunun okunur adresi (/groups/<slug>): gönderi bağlantıları bununla da gelebilir
    priority: int = 100  # küçük = önce (yavaş başlangıçta yalnız ilk birkaç kaynak okunur)
    default_steering: str | None = None  # 'LHD' = sol direksiyon grubu


@dataclass(frozen=True)
class Cursor:
    """Kaynakta en son görülen gönderi; okuma bunun gerisine geçince durur. İkisi de None = ilk okuma."""
    post_id: str | None = None
    posted_at: datetime | None = None


@dataclass(frozen=True)
class SocialPost:
    platform: Platform
    source_key: str  # SocialSource.key
    post_id: str  # Facebook gönderi kimliği / Instagram shortcode
    url: str
    posted_at: datetime | None  # UTC, saat dilimli
    text: str
    image_url: str | None = None  # ilk fotoğrafın CDN adresi (fiyat yazıda yoksa okunur)
    owner: str | None = None  # yalnız Instagram: paylaşan açık galeri hesabı. Facebook'ta HER ZAMAN None (yazar saklanmaz)
    pinned: bool = False


@dataclass
class FetchResult:
    posts: list[SocialPost]  # imleçten YENİ gönderiler, en yeniden eskiye
    cursor: Cursor  # bir sonraki okuma için (yeni gönderi yoksa gelen imleç aynen)
    seen: int = 0  # sayfada bakılan toplam gönderi (eski olanlar dahil; 0 = hiç gönderi görünmedi)
    requests: int = 0  # bu okumada yapılan istek / sayfa yüklemesi (günlük tavan için)


class SocialStop(Exception):
    """Platform düzeyinde dur (hesap, oturum, IP ya da hız uyarısı). Tur hemen biter; frenin süresi domain/social_brake'te belirlenir."""

    def __init__(self, signal: Signal, detail: str = ""):
        super().__init__(f"{signal.value}: {detail}" if detail else signal.value)
        self.signal, self.detail = signal, detail


class SourceError(Exception):
    """Yalnız bu kaynak okunamadı (grup bulunamadı, hesap gizli/silinmiş, sayfa yapısı tanınmadı). Tur diğer kaynaklarla sürer."""


class DailyCap(Exception):
    """Hesabın günlük istek tavanı doldu: fren değildir, tur sessizce biter ve ertesi gün sürer."""


class SocialFetcher(Protocol):
    platform: Platform

    def check_egress(self) -> str:
        """Okuyucunun kendi bağlantısıyla (proxy üzerinden) görülen çıkış IP'si. Proxy ayarı yoksa SocialStop(IP_CHANGED)."""
        ...

    def fetch_new(self, source: SocialSource, cursor: Cursor, max_posts: int) -> FetchResult:
        """Kaynağın imleçten yeni gönderileri (en çok max_posts). SocialStop / SourceError / DailyCap fırlatabilir."""
        ...

    def close(self) -> None:
        ...


class StateStore(Protocol):
    """Anahtar-değer durum: Repository.get_state/set_state ile aynı imza (deneme kipinde yerel JSON dosyası)."""

    def get_state(self, key: str, default: str | None = None) -> str | None:
        ...

    def set_state(self, key: str, value: str) -> None:
        ...


class ListingSink(Protocol):
    """İlan yazımı: Repository ile aynı imzalar (deneme kipinde JSONL dosyası). source_id deneme kipinde SocialSource.key'dir."""

    def upsert_listing(self, source_id, item_id: str, data: dict) -> bool:
        ...

    def known_item_ids(self, source_id) -> set[str]:
        ...


class FetcherModule(Protocol):
    """Okuyucu modülünün dış yüzü (yukarıdaki build/login)."""

    def build(self, env: Mapping[str, str], state_dir: Path) -> SocialFetcher:
        ...

    def login(self, env: Mapping[str, str], state_dir: Path) -> None:
        ...
