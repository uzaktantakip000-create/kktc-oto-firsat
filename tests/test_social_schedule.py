from datetime import datetime, timedelta, timezone
from random import Random

import pytest

from domain import social_schedule as sch

UTC = timezone.utc


class EdgeRng(Random):
    """uniform her zaman alt (lo=True) ya da üst sınırı verir: sapmanın uçlarını denemek için."""

    def __init__(self, lo: bool):
        super().__init__(0)
        self.lo = lo

    def uniform(self, a, b):
        return a if self.lo else b


def at(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


@pytest.mark.parametrize("now,inside", [
    (at(2026, 10, 5, 4, 59), False),  # yaz saati (+3): 07:59
    (at(2026, 10, 5, 5, 0), True),  # 08:00
    (at(2026, 10, 5, 19, 59), True),  # 22:59
    (at(2026, 10, 5, 20, 0), False),  # 23:00
    (at(2026, 10, 26, 5, 30), False),  # kış saati (+2): 07:30 (yazdaki gibi hesaplansa 08:30 olurdu)
    (at(2026, 10, 26, 6, 0), True),  # 08:00
    (at(2026, 10, 26, 20, 30), True),  # 22:30
    (at(2026, 10, 26, 21, 0), False),  # 23:00
])
def test_daytime_window_follows_kktc_local_time(now, inside):
    assert sch.in_window(now) is inside


def test_due_needs_window_and_planned_time():
    now = at(2026, 10, 5, 9)
    assert sch.due(now, None)  # hiç tur yok
    assert sch.due(now, now - timedelta(minutes=1))
    assert not sch.due(now, now + timedelta(minutes=1))
    assert not sch.due(at(2026, 10, 5, 2), None)  # gece: ilk tur bile beklenir


@pytest.mark.parametrize("lo", [True, False])
def test_next_after_is_interval_with_15_min_jitter(lo):
    now = at(2026, 10, 5, 9)  # 12:00 yerel
    nxt = sch.next_after(now, 2, EdgeRng(lo))
    assert nxt == now + timedelta(hours=2, minutes=-15 if lo else 15)
    for seed in range(50):
        n = sch.next_after(now, 4, Random(seed))
        assert now + timedelta(hours=3, minutes=45) <= n <= now + timedelta(hours=4, minutes=15)


def test_next_after_outside_window_moves_to_next_morning():
    now = at(2026, 10, 5, 19, 30)  # 22:30 yerel; +2 sa = 00:30 -> ertesi sabah
    assert sch.next_after(now, 2, EdgeRng(True)) == at(2026, 10, 6, 5, 0)  # 06.10 08:00 yerel (+3)
    assert sch.next_after(now, 2, EdgeRng(False)) == at(2026, 10, 6, 5, 30)  # 08:00 + 30 dk
    early = at(2026, 10, 6, 1)  # 04:00 yerel (zorla çalıştırılmış gece turu) +2 sa = 06:00: aynı günün sabahı
    assert sch.next_after(early, 2, EdgeRng(True)) == at(2026, 10, 6, 5, 0)


def test_next_morning_across_dst_change_25_10_2026():
    now = at(2026, 10, 24, 19)  # 24.10 22:00 yerel (+3); +4 sa = 25.10 02:00 yerel (geçişten önce)
    nxt = sch.next_after(now, 4, EdgeRng(True))
    assert nxt == at(2026, 10, 25, 6, 0)  # 25.10 08:00 yerel KIŞ saati (+2) = 06:00 UTC (yaz saatiyle 05:00 olurdu)
    assert nxt.astimezone(sch.KKTC_TZ).hour == 8
    late = at(2026, 10, 25, 18, 30)  # 25.10 20:30 yerel (+2): +2 sa = 22:30 yerel, pencere içinde
    assert sch.next_after(late, 2, EdgeRng(True)) == late + timedelta(hours=1, minutes=45)


class Src:
    def __init__(self, platform, name, priority):
        self.platform, self.name, self.priority = platform, name, priority


def test_slow_start_reads_only_first_n_by_priority_every_4h():
    srcs = [Src("facebook", n, p) for n, p in (("e", 50), ("a", 1), ("b", 2), ("d", 9), ("c", 3))]
    now = at(2026, 10, 5, 9)
    picked, interval = sch.plan_cycle(srcs, None, now, Random(1))
    assert interval == 4 and {s.name for s in picked} == {"a", "b", "c"}
    picked, interval = sch.plan_cycle(srcs, now - timedelta(days=6, hours=23), now, Random(1))
    assert interval == 4 and len(picked) == 3
    ig = [Src("instagram", str(i), i) for i in range(6)]
    assert len(sch.plan_cycle(ig, None, now, Random(1))[0]) == 4


def test_after_7_days_all_sources_every_2h_in_shuffled_order():
    srcs = [Src("facebook", str(i), i) for i in range(8)]
    now = at(2026, 10, 5, 9)
    orders = set()
    for seed in range(10):
        picked, interval = sch.plan_cycle(srcs, now - timedelta(days=7), now, Random(seed))
        assert interval == 2 and sorted(s.name for s in picked) == [str(i) for i in range(8)]
        orders.add(tuple(s.name for s in picked))
    assert len(orders) > 1  # sıra her tur aynı değil
    assert sch.plan_cycle([], None, now, Random(0)) == ([], 4)


def test_between_sources_delay_3_to_6_minutes():
    for seed in range(50):
        assert 180 <= sch.between_sources_delay(Random(seed)) <= 360
