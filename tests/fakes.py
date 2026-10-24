"""Deterministic synthetic data and a fake provider (no network)."""

from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd

from config import build_config
from providers.base import DailyBars, DataNotFound
from services.calendar import MarketCalendar

UTC = dt.timezone.utc


def synthetic_bars(sessions: list[dt.date], seed: int = 7, base: float = 100.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = len(sessions)
    close = base * np.exp(np.cumsum(rng.normal(0.0003, 0.01, n)))
    open_ = close * (1 + rng.normal(0, 0.003, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.004, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.004, n)))
    volume = rng.integers(1_000_000, 5_000_000, n).astype("float64")
    frame = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=pd.Index(sessions, name="session_date"),
    )
    return frame


class FakeProvider:
    """Serves a fixed synthetic history; tests mutate attributes to simulate faults."""

    name = "fake"

    def __init__(self, first: dt.date = dt.date(2015, 1, 2), last: dt.date = dt.date(2026, 12, 31),
                 seed: int = 7) -> None:
        sessions = MarketCalendar("NYSE").sessions(first, last + dt.timedelta(days=1))
        self.history: dict[str, pd.DataFrame] = {}
        self.seed = seed
        self.sessions = sessions
        self.available_until: dict[str, dt.date] = {}  # simulates data not yet published
        self.drop: dict[str, set[dt.date]] = {}
        self.errors: dict[str, list[Exception]] = {}
        self.calls: list[tuple[str, dt.date, dt.date]] = []
        self.extra_future_rows = 0
        self.adjustment = "fake_adjusted"

    def frame_for(self, symbol: str) -> pd.DataFrame:
        if symbol not in self.history:
            seed = self.seed + sum(map(ord, symbol))
            self.history[symbol] = synthetic_bars(self.sessions, seed=seed)
        return self.history[symbol]

    def fetch_daily(self, symbol: str, start_date: dt.date, end_date_exclusive: dt.date) -> DailyBars:
        self.calls.append((symbol, start_date, end_date_exclusive))
        queue = self.errors.get(symbol)
        if queue:
            raise queue.pop(0)
        frame = self.frame_for(symbol)
        limit = self.available_until.get(symbol)
        end = end_date_exclusive
        if self.extra_future_rows:
            # Misbehaving source: returns rows on/after the exclusive end.
            later = [d for d in frame.index if d >= end][: self.extra_future_rows]
            if later:
                end = later[-1] + dt.timedelta(days=1)
        mask = [(start_date <= d < end) and (limit is None or d <= limit)
                and d not in self.drop.get(symbol, set()) for d in frame.index]
        out = frame[mask].copy()
        if out.empty:
            raise DataNotFound(f"no rows for {symbol}")
        return DailyBars(symbol, out, self.name, self.adjustment, {"start": str(start_date),
                                                                  "end": str(end_date_exclusive)})


def make_config(tmp_path, **overrides):
    raw = {
        "symbols": ["TEST1", "FAKEA"],
        "calendar_exceptions_path": "exceptions.yaml",
        "sqlite_path": "data/market.sqlite3",
        "run_export_dir": "data/exports",
    }
    raw.update(overrides)
    return build_config(raw, tmp_path / "config.json")


def et(year, month, day, hour, minute=0) -> dt.datetime:
    from zoneinfo import ZoneInfo

    return dt.datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("America/New_York")).astimezone(UTC)


def isclose(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
