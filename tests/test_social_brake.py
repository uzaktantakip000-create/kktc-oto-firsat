from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from domain.social_brake import SOFT_PAUSE, BrakeDecision, Severity, Signal, classify

NOW = datetime(2026, 10, 5, 9, tzinfo=timezone.utc)
HARD = [Signal.CHECKPOINT, Signal.LOGIN_REQUIRED, Signal.AUTH_ERROR, Signal.FEEDBACK_REQUIRED, Signal.TEMP_BLOCKED, Signal.IP_CHANGED]


@pytest.mark.parametrize("signal", HARD)
def test_account_session_ip_signals_are_hard_until_owner_resumes(signal):
    d = classify(signal, NOW, [])
    assert (d.severity, d.signal, d.pause_until) == (Severity.HARD, signal, None)
    assert "resume" in d.reason


@pytest.mark.parametrize("signal", [Signal.RATE_LIMITED, Signal.EMPTY_ANOMALY])
def test_rate_limit_and_empty_anomaly_pause_48_hours(signal):
    d = classify(signal, NOW, [NOW - timedelta(days=8)])  # 7 günden eski uyarı sayılmaz
    assert (d.severity, d.pause_until) == (Severity.SOFT, NOW + SOFT_PAUSE)
    assert d.pause_until == NOW + timedelta(hours=48)


def test_second_soft_within_7_days_escalates_to_hard():
    d = classify(Signal.RATE_LIMITED, NOW, [NOW - timedelta(days=6, hours=23)])
    assert (d.severity, d.pause_until) == (Severity.HARD, None)
    assert "ikinci" in d.reason
    assert classify(Signal.EMPTY_ANOMALY, NOW, [NOW - timedelta(days=7)]).severity is Severity.HARD  # sınır dahil


def test_soft_time_in_the_future_counts_as_recent():
    assert classify(Signal.RATE_LIMITED, NOW, [NOW + timedelta(hours=1)]).severity is Severity.HARD  # saat kayması: şüphede HARD


def test_decision_is_frozen():
    d = classify(Signal.CHECKPOINT, NOW, [])
    assert isinstance(d, BrakeDecision)
    with pytest.raises(FrozenInstanceError):
        d.reason = "x"
