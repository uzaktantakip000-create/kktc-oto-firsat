"""Sosyal medya okuma turu (VPS işçisi). Yalnız application/social_port sözleşmesini bilir (Playwright/Instaloader bilmez).

Tur: fren ya da duraklama varsa hiç başlamaz -> çıkış IP'si beklenen sabit IP mi -> sıradaki kaynaklar (yavaş başlangıç, karışık sıra,
aralarda 3–6 dk bekleme) -> her kaynakta imleçten yeni gönderiler okunur, ilana çevrilir, imleç ilerler.
Platform uyarısı (SocialStop) turu hemen bitirir ve freni yazar; günlük tavan (DailyCap) ve proxy'nin geçici yanıtsızlığı (Unreachable)
turu fren yazmadan bitirir; tek kaynağın hatası (SourceError) yalnız o kaynağı atlar, ama üst üste 3 kaynak hatası turu erken bitirir
(proxy koptu ya da platform yapısı değişti: boşuna istek atılmaz). Okuyucu her durumda kapatılır; sonraki tur zamanı ve nabız her durumda yazılır.

Durum anahtarları (StateStore, platform başına; yapılı değerler JSON):
  social:brake:<p>               HARD fren {signal, reason, at}; boş = yok. Yalnız `resume` açar
  social:paused_until:<p>        SOFT duraklama bitişi (ISO); süre dolunca kendiliğinden açılır
  social:soft_history:<p>        SOFT sinyal zamanları [ISO] (7 gün içinde ikincisi HARD olur)
  social:next_after:<p>          sonraki turun en erken zamanı (ISO, UTC)
  social:started_at:<p>          ilk turun zamanı (yavaş başlangıç bundan sayılır)
  social:cursor:<p>:<key>        {post_id, posted_at}: kaynakta en son görülen gönderi
  social:heartbeat:<p>           {at, sources, seen, new, errors, status}: son tur özeti
  social:source_errors:<p>:<key> {count, last_at, detail}: üst üste kaynak hatası; boş = yok
Günlüğe ve rapora yalnız kaynağın takma adı (alias) yazılır; grup adı/kimliği, IP, hesap adı yazılmaz."""
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from random import Random

from application.llm_reader import LlmReader
from application.social_ingest import IngestStats, ReadBudget, ingest
from application.social_port import (Cursor, DailyCap, ListingSink, SocialFetcher, SocialSource, SocialStop, SourceError,
                                     StateStore, Unreachable)
from domain.social_brake import SOFT_REPEAT_WINDOW, SOFT_SIGNALS, BrakeDecision, Severity, Signal, classify
from domain.social_schedule import KKTC_TZ, SLOW_INTERVAL_H, between_sources_delay, due, next_after, plan_cycle

MAX_POSTS = 25  # kaynak başına tek okumada en çok gönderi
MAX_ERRORS_IN_ROW = 3  # turda üst üste bu kadar kaynak okunamazsa tur erken biter (fren değil)
SOFT_HISTORY_KEEP = 20

# CycleReport.status
OK, CAPPED, STOPPED, ERROR, UNREACHABLE, ERRORS_IN_ROW = "tamam", "tavan", "fren", "hata", "ulasilamadi", "kaynak_hatalari"
SKIP_BRAKE, SKIP_PAUSE = "atlandi_fren", "atlandi_duraklama"


def key(name: str, platform: str, source_key: str | None = None) -> str:
    return f"social:{name}:{platform}" + (f":{source_key}" if source_key is not None else "")


def _load(store: StateStore, k: str, default=None):
    raw = store.get_state(k)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except ValueError:
        return default


def _dt(raw) -> datetime | None:
    try:
        at = datetime.fromisoformat(raw) if raw else None
    except (TypeError, ValueError):
        return None
    return at.replace(tzinfo=timezone.utc) if at is not None and at.tzinfo is None else at  # elle yazılmış saatsiz değer: UTC


def local(at: datetime | None) -> str:
    return at.astimezone(KKTC_TZ).strftime("%d.%m %H:%M") if at else "-"


@dataclass(frozen=True)
class Block:
    kind: str  # 'fren' (HARD, sahip açar) | 'duraklama' (SOFT, süre dolunca açılır)
    text: str


def current_block(store: StateStore, platform: str, now: datetime) -> Block | None:
    """Okuma yasaksa neden. Okunamayan kayıt da yasak sayılır (şüphede durulur)."""
    if store.get_state(key("brake", platform)):
        brake = _load(store, key("brake", platform), {})
        brake = brake if isinstance(brake, dict) else {}
        return Block("fren", f"fren var ({local(_dt(brake.get('at')))}): {brake.get('reason') or 'kayıt okunamadı'}")
    raw = store.get_state(key("paused_until", platform))
    if raw:
        until = _dt(raw)
        if until is None:
            return Block("duraklama", "duraklama kaydı okunamadı: kontrol edip resume de")
        if now < until:
            return Block("duraklama", f"duraklama {local(until)}'e kadar")
    return None


def is_due(store: StateStore, platform: str, now: datetime) -> bool:
    return due(now, _dt(store.get_state(key("next_after", platform))))


def resume(store: StateStore, platform: str) -> bool:
    """Sahip hesabı kontrol etti: fren ve duraklama kalkar. SOFT geçmişi SİLİNMEZ (7 gün içinde yeni uyarı yine HARD olur)."""
    had = bool(store.get_state(key("brake", platform)) or store.get_state(key("paused_until", platform)))
    store.set_state(key("brake", platform), "")
    store.set_state(key("paused_until", platform), "")
    return had


def load_cursor(store: StateStore, platform: str, source_key: str) -> Cursor:
    c = _load(store, key("cursor", platform, source_key), {})
    if not isinstance(c, dict):
        return Cursor()
    return Cursor(post_id=c.get("post_id") or None, posted_at=_dt(c.get("posted_at")))


def save_cursor(store: StateStore, platform: str, source_key: str, cursor: Cursor) -> None:
    store.set_state(key("cursor", platform, source_key), json.dumps(
        {"post_id": cursor.post_id, "posted_at": cursor.posted_at.isoformat() if cursor.posted_at else None}))


def apply_brake(store: StateStore, platform: str, signal: Signal, now: datetime) -> BrakeDecision:
    """Sinyali frene çevirir ve yazar. SOFT sinyal (HARD'a yükselse de) geçmişe eklenir."""
    raw = _load(store, key("soft_history", platform), [])
    history = [t for t in (_dt(x) for x in (raw if isinstance(raw, list) else [])) if t is not None]
    decision = classify(signal, now, history)
    if signal in SOFT_SIGNALS:
        keep = [t for t in history if now - t <= 4 * SOFT_REPEAT_WINDOW] + [now]
        store.set_state(key("soft_history", platform), json.dumps([t.isoformat() for t in keep[-SOFT_HISTORY_KEEP:]]))
    if decision.severity is Severity.HARD:
        store.set_state(key("brake", platform), json.dumps(
            {"signal": str(signal), "reason": decision.reason, "at": now.isoformat()}, ensure_ascii=False))
    else:
        store.set_state(key("paused_until", platform), decision.pause_until.isoformat())
    return decision


_URL = re.compile(r"https?://\S+")


def scrub(text: str, source: SocialSource) -> str:
    """Okuyucu hata metninden kaynağı ele veren her şeyi (adres, kimlik, kısa ad) takma adla değiştirir; kısaltır."""
    text = _URL.sub("<adres>", text or "")
    for secret in (source.url, source.slug, source.key):
        if secret:
            text = text.replace(secret, source.alias)
    return text[:200]


@dataclass
class SourceReport:
    alias: str
    seen: int = 0
    requests: int = 0
    stats: IngestStats | None = None
    error: str | None = None

    def line(self, platform: str) -> str:
        if self.error:
            return f"{platform} {self.alias}: okunamadı ({self.error})"
        s = self.stats or IngestStats()
        why = ", ".join(f"{k}={v}" for k, v in sorted(s.reasons.items()))
        return (f"{platform} {self.alias}: {self.seen} gönderiye bakıldı, {s.fetched} yeni gönderi, {s.fetched - s.skipped} ilan "
                f"({s.new} yeni yazıldı)" + (f"; ilan değil: {why}" if why else ""))


@dataclass
class CycleReport:
    platform: str
    status: str = OK
    note: str = ""
    slow_start: bool = False
    interval_h: int | None = None
    sources: list[SourceReport] = field(default_factory=list)
    brake: BrakeDecision | None = None
    next_after: datetime | None = None

    def lines(self) -> list[str]:
        if self.status in (SKIP_BRAKE, SKIP_PAUSE):
            return self.summary()
        return [r.line(self.platform) for r in self.sources] + self.summary()

    def summary(self) -> list[str]:
        """Tur sonu satırları (kaynak satırları tur sırasında zaten yazıldı)."""
        p = self.platform
        if self.status in (SKIP_BRAKE, SKIP_PAUSE):
            return [f"{p}: tur atlandı, {self.note}"]
        out = []
        if self.brake is not None:
            sev = "HARD (sahip resume diyene kadar)" if self.brake.severity is Severity.HARD else f"SOFT ({local(self.brake.pause_until)}'e kadar)"
            out.append(f"{p}: FREN {sev}: {self.brake.reason}")
        elif self.status == CAPPED:
            out.append(f"{p}: günlük istek tavanı doldu, tur erken bitti (fren değil)")
        elif self.status == UNREACHABLE:
            out.append(f"{p}: proxy yanıt vermedi, çıkış IP'si okunamadı; okumadan bitti, sonraki turda yeniden denenir (fren değil)")
        elif self.status == ERRORS_IN_ROW:
            out.append(f"{p}: üst üste {MAX_ERRORS_IN_ROW} kaynak okunamadı, tur erken bitti (fren değil)")
        seen = sum(r.seen for r in self.sources)
        new = sum(r.stats.new for r in self.sources if r.stats)
        errors = sum(1 for r in self.sources if r.error)
        out.append(f"{p}: tur bitti [{self.status}] — {len(self.sources)} kaynak, {seen} gönderiye bakıldı, {new} yeni ilan, "
                   f"{errors} kaynak hatası; sonraki tur en erken {local(self.next_after)} (KKTC saati)")
        return out


def _note_source_error(store: StateStore, platform: str, source_key: str, detail: str, now: datetime) -> None:
    prev = _load(store, key("source_errors", platform, source_key), {})
    count = (prev.get("count", 0) if isinstance(prev, dict) else 0) + 1
    store.set_state(key("source_errors", platform, source_key),
                    json.dumps({"count": count, "last_at": now.isoformat(), "detail": detail}, ensure_ascii=False))


def _check_ip(fetcher: SocialFetcher, platform: str, expected_ip: str) -> None:
    if not (expected_ip or "").strip():  # beklenen IP bilinmeden hiç bağlanılmaz
        raise SocialStop(Signal.IP_CHANGED, f"beklenen IP ayarlı değil (SOCIAL_EXPECTED_IP_{platform.upper()})")
    if (fetcher.check_egress() or "").strip() != expected_ip.strip():
        raise SocialStop(Signal.IP_CHANGED, "çıkış IP'si beklenenden farklı")


def _started_at(store: StateStore, platform: str, now: datetime) -> datetime:
    started = _dt(store.get_state(key("started_at", platform)))
    if started is None:
        started = now
        store.set_state(key("started_at", platform), now.isoformat())
    return started


def _finish(store: StateStore, report: CycleReport, now: datetime, interval_h: int, rng: Random) -> None:
    report.next_after = next_after(now, interval_h, rng)
    store.set_state(key("next_after", report.platform), report.next_after.isoformat())
    store.set_state(key("heartbeat", report.platform), json.dumps({
        "at": now.isoformat(), "sources": len(report.sources), "seen": sum(r.seen for r in report.sources),
        "new": sum(r.stats.new for r in report.sources if r.stats), "errors": sum(1 for r in report.sources if r.error),
        "status": report.status}))


def _close(fetcher: SocialFetcher, log) -> None:
    try:
        fetcher.close()
    except Exception as e:  # kapatma hatası turun sonucunu değiştirmez
        log(f"{fetcher.platform}: okuyucu kapatılırken hata ({type(e).__name__})")


class _ErrorsInRow(Exception):
    """Üst üste MAX_ERRORS_IN_ROW kaynak hatası: tur erken biter (fren değil)."""


def _read_sources(platform: str, fetcher: SocialFetcher, planned: list[SocialSource], store: StateStore, sink: ListingSink,
                  report: CycleReport, *, now: datetime, sleep, rng: Random, expected_ip: str, max_posts: int,
                  reader: LlmReader | None, log) -> None:
    """IP kontrolü + kaynaklar sırayla. SocialStop/DailyCap çağırana çıkar; SourceError ve işleme hatası yalnız o kaynağı atlar."""
    budget = ReadBudget()
    errors_in_row = 0
    fetch_image = getattr(fetcher, "fetch_image", None)  # okuyucu görseli kendi bağlantısıyla indirebiliyorsa (yalnız okuyucu bağlıyken)
    _check_ip(fetcher, platform, expected_ip)
    for i, source in enumerate(planned):
        if i:
            sleep(between_sources_delay(rng))
        sr = SourceReport(source.alias)
        report.sources.append(sr)
        cursor = load_cursor(store, platform, source.key)
        try:
            res = fetcher.fetch_new(source, cursor, max_posts)
        except SourceError as e:
            sr.error = scrub(str(e), source) or type(e).__name__
            _note_source_error(store, platform, source.key, sr.error, now)
            log(sr.line(platform))
            errors_in_row += 1
            if errors_in_row >= MAX_ERRORS_IN_ROW:
                raise _ErrorsInRow() from None
            continue
        errors_in_row = 0
        sr.seen, sr.requests = res.seen, res.requests
        try:
            sr.stats = ingest(platform, res.posts, source, sink, reader=reader, fetch_image=fetch_image, budget=budget)
        except (SocialStop, DailyCap):
            raise
        except Exception as e:  # gönderi işlenemedi: imleç ilerlemez (sonraki tur yeniden dener), diğer kaynaklar sürer
            sr.error = f"işleme hatası ({type(e).__name__})"
            _note_source_error(store, platform, source.key, sr.error, now)
            log(sr.line(platform))
            continue
        new_cursor = res.cursor
        if new_cursor.post_id is None and new_cursor.posted_at is None:
            new_cursor = cursor  # okuyucu boş imleç döndü: eldeki imleç kaybolmasın
        save_cursor(store, platform, source.key, new_cursor)
        if store.get_state(key("source_errors", platform, source.key)):
            store.set_state(key("source_errors", platform, source.key), "")
        log(sr.line(platform))
    read = [r for r in report.sources if r.error is None]
    if len(read) >= 2 and all(r.seen == 0 for r in read):  # tüm kaynaklar birden boş: sessiz engel şüphesi
        raise SocialStop(Signal.EMPTY_ANOMALY, f"{len(read)} kaynak")


def run_cycle(platform: str, fetcher: SocialFetcher, sources: list[SocialSource], store: StateStore, sink: ListingSink, *,
              now: datetime, sleep: Callable[[float], None], rng: Random, expected_ip: str, max_posts: int = MAX_POSTS,
              reader: LlmReader | None = None, log: Callable[[str], None] = print,
              min_interval_h: float | None = None) -> CycleReport:
    """Tek okuma turu. min_interval_h: tur aralığı en az bu kadar (yalnız yavaşlatır; ör. uyarı almış yeni hesap). Pencere/sıra kontrolü çağıranındır (--force yalnız onu atlar); fren burada da yeniden denetlenir.
    Okuyucu her durumda kapatılır; tur başladıysa (hata olsa bile) sonraki tur zamanı ve nabız yazılır: hata sık denemeye dönmez."""
    report = CycleReport(platform)
    try:
        block = current_block(store, platform, now)
        if block is not None:
            report.status, report.note = (SKIP_BRAKE if block.kind == "fren" else SKIP_PAUSE), block.text
            return report
        planned, report.interval_h = plan_cycle(sources, _started_at(store, platform, now), now, rng)
        report.slow_start = report.interval_h == SLOW_INTERVAL_H
        if min_interval_h and min_interval_h > report.interval_h:
            report.interval_h = min_interval_h
        log(f"{platform}: tur başladı — {len(planned)}/{len(sources)} kaynak"
            + (" (yavaş başlangıç)" if report.slow_start else "") + f", aralık {report.interval_h} sa")
        _read_sources(platform, fetcher, planned, store, sink, report, now=now, sleep=sleep, rng=rng, expected_ip=expected_ip,
                      max_posts=max_posts, reader=reader, log=log)
    except SocialStop as e:
        report.status, report.brake = STOPPED, apply_brake(store, platform, e.signal, now)
    except DailyCap:
        report.status = CAPPED
    except Unreachable as e:
        report.status, report.note = UNREACHABLE, _URL.sub("<adres>", str(e))[:200]
    except _ErrorsInRow:
        report.status = ERRORS_IN_ROW
    except Exception:
        report.status = ERROR
        raise
    finally:
        _close(fetcher, log)
        if report.interval_h is not None:
            _finish(store, report, now, report.interval_h, rng)
    return report


def _combined_posts(got) -> list:
    """fetch_combined dönüşü: FetchResult, gönderi listesi ya da {kaynak: FetchResult|liste} kabul edilir."""
    if hasattr(got, "posts"):
        return list(got.posts)
    if isinstance(got, dict):
        return [p for v in got.values() for p in (v.posts if hasattr(v, "posts") else v)]
    return list(got)


def compare_feeds(fetcher: SocialFetcher, sources: list[SocialSource], store: StateStore, *, now: datetime,
                  sleep: Callable[[float], None], rng: Random, expected_ip: str, max_posts: int = MAX_POSTS,
                  log: Callable[[str], None] = print) -> dict:
    """Deneme A/B (Facebook): aynı kaynaklar hem tek tek (grup sayfası) hem birleşik akıştan okunur:
    fetcher.fetch_combined(sources, max_posts_toplam). İmleç kullanılmaz ve ilerletilmez, ilan yazılmaz. Fren/IP kuralları normal
    turla aynı; okuma sonrası sonraki tur zamanı yazılır. Dönen sözlükte yalnız takma adlar ve gönderi kimlikleri var:
    {durum, gruplar: {alias: {grup_ici, ortak, kapsama, grup_ici_kimlikler, yalniz_grup_ici} | {hata}}, birlesik_toplam, ...}"""
    platform = fetcher.platform
    out: dict = {"durum": OK, "zaman": now.isoformat(), "gruplar": {}}
    report = CycleReport(platform)
    try:
        block = current_block(store, platform, now)
        if block is not None:
            out["durum"], out["not"] = (SKIP_BRAKE if block.kind == "fren" else SKIP_PAUSE), block.text
            return out
        combined_fn = getattr(fetcher, "fetch_combined", None)
        if combined_fn is None:
            out["durum"], out["not"] = ERROR, "okuyucuda birleşik akış (fetch_combined) yok"
            return out
        planned, report.interval_h = plan_cycle(sources, _started_at(store, platform, now), now, rng)
        _check_ip(fetcher, platform, expected_ip)
        per_group: dict[str, set[str]] = {}
        for i, source in enumerate(planned):
            if i:
                sleep(between_sources_delay(rng))
            try:
                res = fetcher.fetch_new(source, Cursor(), max_posts)
            except SourceError as e:
                out["gruplar"][source.alias] = {"hata": scrub(str(e), source) or type(e).__name__}
                continue
            per_group[source.alias] = {p.post_id for p in res.posts}
            report.sources.append(SourceReport(source.alias, seen=res.seen, requests=res.requests))
        if planned:
            sleep(between_sources_delay(rng))
        combined = {p.post_id for p in _combined_posts(combined_fn(planned, max_posts * max(1, len(planned))))}
        for alias, ids in per_group.items():
            common = ids & combined
            out["gruplar"][alias] = {"grup_ici": len(ids), "ortak": len(common),
                                     "kapsama": round(len(common) / len(ids), 3) if ids else None,
                                     "grup_ici_kimlikler": sorted(ids), "yalniz_grup_ici": sorted(ids - combined)}
        out["birlesik_toplam"] = len(combined)
        out["birlesik_kaynak_disi"] = len(combined - set().union(*per_group.values()))  # birleşik akışta olup grup sayfasında görülmeyen
        out["birlesik_kimlikler"] = sorted(combined)
    except SocialStop as e:
        report.status, report.brake = STOPPED, apply_brake(store, platform, e.signal, now)
        out["durum"], out["not"] = STOPPED, report.brake.reason
    except DailyCap:
        out["durum"] = report.status = CAPPED
    except Unreachable:
        out["durum"] = report.status = UNREACHABLE
    except Exception:
        report.status = ERROR
        raise
    finally:
        _close(fetcher, log)
        if report.interval_h is not None:
            _finish(store, report, now, report.interval_h, rng)
    return out


_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_HEALTHY, _FAILED = {OK, CAPPED}, {ERROR, UNREACHABLE, ERRORS_IN_ROW}


def _utc_iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def status_entry(store: StateStore, platform: str, now: datetime) -> dict | None:
    """Durum dosyası (kktc-bot'un sabah mesajı okur) için platform satırı; hiç tur ve fren yoksa None (platform yazılmaz).
    sonuc: tamam | fren | hata. Ad, grup, hesap, IP yazılmaz (fren nedenindeki IP'ler maskelenir)."""
    block = current_block(store, platform, now)
    hb = _load(store, key("heartbeat", platform), {})
    hb = hb if isinstance(hb, dict) else {}
    if block is None and not hb:
        return None
    status = hb.get("status")
    if block is not None:
        result = "fren"
    elif status in _FAILED:
        result = "hata"
    else:
        result = "tamam"  # tamam/tavan; fren kaydı resume ile kalktıysa sonraki tura kadar "tamam"
    reason = None
    if block is not None:
        brake = _load(store, key("brake", platform), {})
        detail = brake.get("reason") if isinstance(brake, dict) and brake.get("reason") else block.text
        reason = _IPV4.sub("<ip>", str(detail))[:200]
    return {"son_tur_utc": _utc_iso(_dt(hb.get("at"))), "sonuc": result, "fren_nedeni": reason,
            "sonraki_tur_utc": _utc_iso(_dt(store.get_state(key("next_after", platform)))),
            "yeni_ilan": int(hb.get("new") or 0), "kaynak_hatasi": int(hb.get("errors") or 0)}


def status_lines(store: StateStore, platform: str, now: datetime, sources: list[SocialSource]) -> list[str]:
    """`status` komutu: fren/duraklama, son tur, sonraki tur, yavaş başlangıç, kaynak hataları (yalnız takma ad)."""
    block = current_block(store, platform, now)
    out = [f"{platform}: " + (block.text if block else "fren yok")]
    hb = _load(store, key("heartbeat", platform), {})
    if isinstance(hb, dict) and hb:
        out.append(f"  son tur {local(_dt(hb.get('at')))} [{hb.get('status', '-')}]: {hb.get('sources', 0)} kaynak, "
                   f"{hb.get('seen', 0)} gönderiye bakıldı, {hb.get('new', 0)} yeni ilan, {hb.get('errors', 0)} kaynak hatası")
    else:
        out.append("  henüz tur yok")
    nxt = _dt(store.get_state(key("next_after", platform)))
    out.append(f"  sonraki tur en erken {local(nxt)} (KKTC saati; gündüz 08:00–23:00)")
    started = _dt(store.get_state(key("started_at", platform)))
    if started is not None:
        _, interval = plan_cycle([], started, now, Random(0))
        out.append(f"  başlangıç {local(started)}; " + ("yavaş başlangıç sürüyor" if interval == SLOW_INTERVAL_H else "normal tempo"))
    for s in sources:
        err = _load(store, key("source_errors", platform, s.key), {})
        if isinstance(err, dict) and err:
            out.append(f"  {s.alias}: üst üste {err.get('count', 0)} kez okunamadı (son {local(_dt(err.get('last_at')))}): "
                       f"{err.get('detail', '')}")
    return out
