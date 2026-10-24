"""Daily bar provider protocol shared by all data sources."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Protocol

import pandas as pd

BAR_COLUMNS = ["open", "high", "low", "close", "volume"]


class ProviderError(Exception):
    """Base class for provider failures. ``kind`` is recorded in run logs."""

    kind = "provider_error"


class DataNotFound(ProviderError):
    kind = "data_not_found"


class NetworkError(ProviderError):
    kind = "network_error"


class RateLimited(ProviderError):
    """429, captcha or permission errors: stop high-frequency retries."""

    kind = "rate_limited"


@dataclass
class DailyBars:
    """Completed regular-session daily bars.

    ``frame`` is indexed by a sorted ``datetime.date`` index named
    ``session_date`` with the float columns in ``BAR_COLUMNS``.
    """

    symbol: str
    frame: pd.DataFrame
    source: str
    adjustment: str
    params: dict = field(default_factory=dict)


def empty_frame() -> pd.DataFrame:
    frame = pd.DataFrame(columns=BAR_COLUMNS, dtype="float64")
    frame.index = pd.Index([], name="session_date", dtype="object")
    return frame


class DailyBarProvider(Protocol):
    name: str

    def fetch_daily(
        self, symbol: str, start_date: dt.date, end_date_exclusive: dt.date
    ) -> DailyBars:
        """Return bars with ``start_date <= session_date < end_date_exclusive``."""
        ...


def get_provider(name: str, timeout_seconds: float, cache_dir=None) -> DailyBarProvider:
    if name == "yahoo":
        from providers.yahoo import YahooProvider

        return YahooProvider(timeout_seconds=timeout_seconds, cache_dir=cache_dir)
    raise ValueError(f"data provider {name!r} is not implemented")
