"""Sosyal medya okuma takvimi (VPS işçisi). Saf kural: G/Ç yok; zaman ve rastgelelik dışarıdan verilir.

- Yalnız KKTC gündüzü (08:00–23:00, yerel saat; yaz +3, kış +2: 25.10.2026'da kışa geçilir) okunur.
- Turlar arası aralık sabit değil: ±15 dk sapma. Pencere dışına düşen tur ertesi sabah 08:00 + 0–30 dk'ya kayar.
- Yavaş başlangıç: ilk 7 gün yalnız öncelikli ilk birkaç kaynak, 4 saatte bir; sonra hepsi 2 saatte bir.
- Kaynak sırası her turda karışık; kaynaklar arasında 3–6 dk beklenir (insan gibi, aynı ritim yok)."""
from collections.abc import Sequence
from datetime import datetime, time, timedelta, timezone
from random import Random
from typing import Protocol, TypeVar
from zoneinfo import ZoneInfo

KKTC_TZ = ZoneInfo("Asia/Famagusta")
WINDOW_START = time(8, 0)  # yerel saat; bu saatten önce okunmaz
WINDOW_END = time(23, 0)  # yerel saat; bu saatten sonra okunmaz
JITTER_MIN = 15  # tur aralığına ± bu kadar dakika rastgele sapma
MORNING_JITTER_MIN = 30  # pencere dışına düşen tur: sabah 08:00 + 0–bu kadar dakika
SLOW_START_DAYS = 7  # ilk tur (social:started_at) üzerinden bu kadar gün yavaş başlangıç
SLOW_START_COUNT = {"facebook": 3, "instagram": 4}  # yavaş başlangıçta okunan kaynak sayısı (öncelik sırasıyla ilk N)
SLOW_INTERVAL_H = 4
NORMAL_INTERVAL_H = 2
BETWEEN_SOURCES_S = (180.0, 360.0)  # iki kaynak arasında bekleme (saniye, düzgün dağılım)


class Ranked(Protocol):
    """Takvimin kaynaktan bildiği tek şey (application/social_port.SocialSource bunu karşılar)."""
    platform: str
    priority: int


S = TypeVar("S", bound=Ranked)


def in_window(now: datetime) -> bool:
    return WINDOW_START <= now.astimezone(KKTC_TZ).time() < WINDOW_END


def due(now: datetime, next_after: datetime | None) -> bool:
    """Okuma zamanı geldi mi: gündüz penceresinde ve (hiç tur yoksa ya da) planlanan zaman geçti."""
    return in_window(now) and (next_after is None or now >= next_after)


def _next_morning(at: datetime, rng: Random) -> datetime:
    """`at` anından sonraki ilk yerel 08:00 (+0–30 dk). 08:00 hiçbir yaz/kış geçişine denk gelmez (geçiş gece 03:00/04:00)."""
    local = at.astimezone(KKTC_TZ)
    day = local.date() if local.time() < WINDOW_START else local.date() + timedelta(days=1)
    morning = datetime.combine(day, WINDOW_START, tzinfo=KKTC_TZ)
    return (morning + timedelta(minutes=rng.uniform(0, MORNING_JITTER_MIN))).astimezone(timezone.utc)


def next_after(now: datetime, interval_h: float, rng: Random) -> datetime:
    """Bir sonraki turun en erken zamanı (UTC). Aralık geçen gerçek süredir (yaz/kış geçişinde de interval_h saat)."""
    at = now.astimezone(timezone.utc) + timedelta(hours=interval_h, minutes=rng.uniform(-JITTER_MIN, JITTER_MIN))
    return at if in_window(at) else _next_morning(at, rng)


def plan_cycle(sources: Sequence[S], started_at: datetime | None, now: datetime, rng: Random) -> tuple[list[S], int]:
    """(bu turda okunacak kaynaklar karışık sırayla, tur aralığı saat). started_at yok = ilk tur (yavaş başlangıç)."""
    slow = started_at is None or now - started_at < timedelta(days=SLOW_START_DAYS)
    ranked = sorted(sources, key=lambda s: s.priority)  # eşit öncelikte dosya sırası korunur
    if slow:
        platform = ranked[0].platform if ranked else ""
        ranked = ranked[:SLOW_START_COUNT.get(platform, min(SLOW_START_COUNT.values()))]
    rng.shuffle(ranked)
    return ranked, SLOW_INTERVAL_H if slow else NORMAL_INTERVAL_H


def between_sources_delay(rng: Random) -> float:
    return rng.uniform(*BETWEEN_SOURCES_S)
