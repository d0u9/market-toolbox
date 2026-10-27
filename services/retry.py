"""When to try a failed update again (design section 7)."""

from __future__ import annotations

import datetime as dt
import random
from zoneinfo import ZoneInfo

DELAYS_MINUTES = [5, 15, 30, 60, 60, 60, 60]
MAX_ATTEMPTS = 8
RECOVERY_HOUR = 8
JITTER_RATIO = 0.1


def next_recovery_time(now: dt.datetime, market_tz: ZoneInfo) -> dt.datetime:
    """The next 08:00 in the symbol's market timezone, returned in UTC."""
    local = now.astimezone(market_tz)
    candidate = local.replace(hour=RECOVERY_HOUR, minute=0, second=0, microsecond=0)
    if candidate <= local:
        candidate = dt.datetime.combine(
            local.date() + dt.timedelta(days=1), dt.time(RECOVERY_HOUR), market_tz
        )
    return candidate.astimezone(dt.timezone.utc)


def next_attempt(attempts: int, now: dt.datetime, rng: random.Random,
                 market_tz: ZoneInfo,
                 stop_until_recovery: bool = False) -> tuple[dt.datetime, bool]:
    """Return (next attempt time, retries exhausted) after ``attempts`` failures.

    ``stop_until_recovery`` skips the short delays, e.g. after repeated rate limits.
    """
    if stop_until_recovery or attempts >= MAX_ATTEMPTS:
        return next_recovery_time(now, market_tz), True
    base = DELAYS_MINUTES[min(attempts, len(DELAYS_MINUTES)) - 1]
    jitter = 1 + rng.uniform(-JITTER_RATIO, JITTER_RATIO)
    return now + dt.timedelta(minutes=base * jitter), False
