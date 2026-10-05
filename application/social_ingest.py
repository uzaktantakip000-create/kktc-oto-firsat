"""Sosyal medya gönderisi (SocialPost) -> ilan satırı. VPS işçisinin okuduğu gönderiler, Apify toplayıcılarının KENDİ çeviricilerinden
geçer (application/collect_facebook, application/collect_instagram); burada yalnız tip uyarlaması ve aynı döngü var, kural kopyası yok.

Deneme kipi (1. aşama): okuyucu (yapay zekâ) bağlı değil, yalnız kural okur; her gönderi (ilan olsun olmasın) deneme dosyasına
tek satır yazılır (sink.record_post). Veritabanı kipinde (2. aşama) sink Repository olur: yalnız ilanlar upsert_listing ile yazılır.
Facebook: yazar/yorum/profil hiç yoktur; gizli grup adı ilan satırına ya da günlüğe girmez (direksiyon ipucu dışında)."""
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Protocol

from application.collect_facebook import (LLM_BUDGET_S, LLM_RETRY, MAX_KM_READS, MAX_PHOTO_READS, listing_data,
                                          llm_listing_data, photo_listing_data)
from application.collect_instagram import listing_data as instagram_listing_data
from application.llm_reader import LlmReader, listing_fields
from application.social_port import ListingSink, SocialPost, SocialSource
from domain.caption_parser import is_sold_post, sold_ilan_no
from domain.freetext_parser import diagnose
from infrastructure.collectors.facebook_groups import RawGroupPost
from infrastructure.collectors.instagram_apify import RawPost


@dataclass
class IngestStats:
    fetched: int = 0
    new: int = 0  # yeni yazılan ilan (deneme dosyasında daha önce olmayan)
    skipped: int = 0  # ilan sayılmadı (neden: reasons)
    parsed: int = 0  # kural okudu
    llm_read: int = 0  # kural okuyamadı, yapay zekâ okudu (en fazla 🟡)
    km_read: int = 0  # fiyat yazıda var ama km yok: yapay zekâ km'yi okudu
    photo_read: int = 0  # fiyat yazıda yok: ilk fotoğraftan okundu
    photo_candidates: int = 0  # fiyat yazıda yok ama fotoğraf var (okuyucu bağlıysa fotoğraftan okunabilir)
    sold: int = 0  # "SATILDI" paylaşımı (Instagram)
    reasons: dict[str, int] = field(default_factory=dict)  # ilan sayılmayan gönderilerin nedeni + fotoğraf okuma sayaçları


@dataclass
class ReadBudget:
    """Tur başına yapay zekâ okuma sınırları (collect_facebook ile aynı tavanlar); turdaki kaynaklar arasında paylaşılır.
    Süre yalnız gönderi işlerken geçen süredir: kaynaklar arasındaki bekleme bütçeyi yemez."""
    km_left: int = MAX_KM_READS
    photos_left: int = MAX_PHOTO_READS
    seconds_left: float = LLM_BUDGET_S
    clock: Callable[[], float] = time.monotonic


class PostJournal(Protocol):
    """Deneme kipi: her gönderi tek satır. reason None = ilan. Dönüş: yeni ilan yazıldı mı."""

    def record_post(self, alias: str, post: SocialPost, data: dict | None, reason: str | None) -> bool:
        ...


def sink_source_id(source: SocialSource):
    return source.source_id or source.key


def steering_hint(source: SocialSource) -> dict:
    """collect_facebook.listing_data direksiyonu kaynak adından okur ("sol direksiyon"). Gerçek (gizli) grup adı YERİNE yalnız ipucu."""
    return {"name": "sol direksiyon" if source.default_steering == "LHD" else ""}


def raw_group_post(post: SocialPost, source: SocialSource) -> RawGroupPost:
    return RawGroupPost(post_id=post.post_id, url=post.url, posted_at=post.posted_at, text=post.text,
                        group_url=source.url, image_url=post.image_url)


def raw_instagram_post(post: SocialPost, source: SocialSource) -> RawPost:
    return RawPost(shortcode=post.post_id, url=post.url, posted_at=post.posted_at, caption=post.text,
                   photo_url=post.image_url, owner=(post.owner or source.key).lower())


def _count(reasons: dict[str, int], why: str) -> None:
    reasons[why] = reasons.get(why, 0) + 1


def _save(sink: ListingSink, source: SocialSource, post: SocialPost, data: dict | None, reason: str | None) -> bool:
    """Deneme dosyası (record_post) her gönderiyi nedeniyle yazar; Repository yalnız satırı olanı (upsert_listing)."""
    record = getattr(sink, "record_post", None)
    if record is not None:
        return record(source.alias, post, data, reason)
    return data is not None and sink.upsert_listing(sink_source_id(source), post.post_id, data)


def ingest_facebook(posts: Iterable[SocialPost], source: SocialSource, sink: ListingSink, *, reader: LlmReader | None = None,
                    fetch_image=None, budget: ReadBudget | None = None) -> IngestStats:
    """collect_facebook_groups döngüsünün aynısı (km tamamlama, yapay zekâ yeniden deneme, fotoğraftan fiyat; tavanlar ve süre bütçesi).
    reader=None (deneme kipi): yalnız kural okur. fetch_image: okuyucunun kendi bağlantısıyla görsel indirme (yoksa fotoğraf okunmaz).
    Aynı gönderiye ikinci kez para harcanmasın diye bilinen gönderiler (sink.known_item_ids) yapay zekâya gitmez."""
    st, budget = IngestStats(), budget or ReadBudget()
    hint = steering_hint(source)
    known = sink.known_item_ids(sink_source_id(source)) if reader else set()
    t0 = budget.clock()

    def llm_open() -> bool:
        return budget.clock() - t0 < budget.seconds_left

    try:
        for post in posts:
            raw = raw_group_post(post, source)
            st.fetched += 1
            fresh = raw.post_id not in known
            data = listing_data(raw, hint)
            if data is not None:
                st.parsed += 1
            if data is not None and data["km"] is None and reader and budget.km_left > 0 and llm_open() and fresh:
                budget.km_left -= 1  # fiyat yazıda var ama km yok: tek ucuz okuma km'yi tamamlar (alıntı doğrulamalı)
                read = reader.read(raw.text)
                if read is not None and read.is_car and read.km:
                    data["km"] = read.km
                    st.km_read += 1
            why = None
            if data is None:
                why = diagnose(raw.text)
                if reader and why in LLM_RETRY and llm_open() and fresh:  # araç gönderisi ama kural okuyamadı: yapay zekâ bir kez dener
                    data = llm_listing_data(raw, hint, reader)
                    if data:
                        st.llm_read += 1
                if data is None and why == "fiyat_yok" and raw.image_url:
                    st.photo_candidates += 1
                    if reader and fetch_image and budget.photos_left > 0 and llm_open() and fresh:  # fiyat yazıda yok: ilk fotoğrafa bak
                        budget.photos_left -= 1
                        data = photo_listing_data(raw, hint, reader, fetch_image)
                        if data:
                            st.photo_read += 1
                        _count(st.reasons, "foto_fiyat" if data else "foto_okunamadi")
                elif data is None and why == "fiyat_yok":
                    _count(st.reasons, "foto_url_yok")  # okuyucu görsel adresi vermedi (şablon kontrolü için sayaç)
                if data is not None:
                    why = None
                else:
                    why = "okunamadi" if why == "ok" else why  # tanı "okunur" dedi ama kural okuyamadı
                    st.skipped += 1
                    _count(st.reasons, why)
            if _save(sink, source, post, data, why):
                st.new += 1
    finally:
        budget.seconds_left -= budget.clock() - t0
    return st


def ingest_instagram(posts: Iterable[SocialPost], source: SocialSource, sink: ListingSink, *, reader: LlmReader | None = None,
                     on_sold: Callable[[object, str], int] | None = None) -> IngestStats:
    """collect_instagram.collect_sources döngüsünün aynısı. Instagram'da her gönderi satır olur (okunamayan da; extraction_by boş).
    "SATILDI" paylaşımı: deneme kipinde yalnız kaydedilir; on_sold (2. aşama: repo.deactivate_by_ilan_no) eski ilanı kapatır."""
    st = IngestStats()
    sid = sink_source_id(source)
    known = sink.known_item_ids(sid) if reader else set()
    for post in posts:
        raw = raw_instagram_post(post, source)
        st.fetched += 1
        data, parsed = instagram_listing_data(raw)
        sold = is_sold_post(raw.caption)
        if not parsed and reader and raw.shortcode not in known and raw.caption.strip() and not sold:
            extra = listing_fields(reader.read(raw.caption))  # yalnız yeni ve okunamayan gönderi: bir kez ödenir
            if extra:
                data = {**data, **extra}
                st.llm_read += 1
        if no := sold_ilan_no(raw.caption):  # yeni ilan değil; eski ilan kapatılır, bu gönderi aktif ilan sayılmaz
            data = {**data, "is_active": False, "urgency_signals": ["satildi"]}
            if on_sold is not None:
                on_sold(sid, no)
        st.parsed += parsed
        if sold:
            why = "satildi"
            st.sold += 1
        elif data.get("extraction_by"):
            why = None
        else:
            why = "sablon_yok" if raw.caption.strip() else "metin_yok"
        if why is not None:
            st.skipped += 1
            _count(st.reasons, why)
        if _save(sink, source, post, data, why):
            st.new += 1
    return st


def ingest(platform: str, posts: Iterable[SocialPost], source: SocialSource, sink: ListingSink, *,
           reader: LlmReader | None = None, fetch_image=None, budget: ReadBudget | None = None, on_sold=None) -> IngestStats:
    if platform == "facebook":
        return ingest_facebook(posts, source, sink, reader=reader, fetch_image=fetch_image, budget=budget)
    if platform == "instagram":
        return ingest_instagram(posts, source, sink, reader=reader, on_sold=on_sold)
    raise ValueError(f"bilinmeyen platform: {platform}")
