import datetime as dt
import random

import pytest

from services import retry
from tests.fakes import et


def test_delays_follow_schedule_within_jitter():
    now = et(2025, 10, 15, 17)
    rng = random.Random(3)
    for attempts, base in enumerate(retry.DELAYS_MINUTES, start=1):
        at, exhausted = retry.next_attempt(attempts, now, rng)
        minutes = (at - now).total_seconds() / 60
        assert not exhausted
        assert base * (1 - retry.JITTER_RATIO) <= minutes <= base * (1 + retry.JITTER_RATIO)


def test_exhausted_or_stopped_waits_for_recovery():
    now = et(2025, 10, 15, 17)
    rng = random.Random(3)
    assert retry.next_attempt(retry.MAX_ATTEMPTS, now, rng) == (et(2025, 10, 16, 8), True)
    assert retry.next_attempt(1, now, rng, stop_until_recovery=True) == (et(2025, 10, 16, 8), True)


@pytest.mark.parametrize("now,expected", [
    (et(2025, 10, 15, 7, 59), et(2025, 10, 15, 8)),   # before 08:00: same day
    (et(2025, 10, 15, 8), et(2025, 10, 16, 8)),       # exactly 08:00: next day
    (et(2025, 3, 8, 12), et(2025, 3, 9, 8)),          # across the spring DST change
    (et(2025, 11, 1, 12), et(2025, 11, 2, 8)),        # across the autumn DST change
])
def test_recovery_time_is_eight_new_york(now, expected):
    got = retry.next_recovery_time(now)
    assert got == expected and got.tzinfo == dt.timezone.utc
