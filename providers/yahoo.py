"""Yahoo Finance adapter built on yfinance (personal research use)."""

from __future__ import annotations

import datetime as dt

import pandas as pd

from providers.base import (
    BAR_COLUMNS,
    DailyBars,
    DataNotFound,
    NetworkError,
    ProviderError,
    RateLimited,
)

ADJUSTMENT = "auto_adjust"


class YahooProvider:
    name = "yahoo"

    def __init__(self, timeout_seconds: float = 30) -> None:
        self.timeout_seconds = timeout_seconds

    def fetch_daily(
        self, symbol: str, start_date: dt.date, end_date_exclusive: dt.date
    ) -> DailyBars:
        import yfinance as yf

        params = {
            "start": start_date.isoformat(),
            "end": end_date_exclusive.isoformat(),  # yfinance end is exclusive
            "interval": "1d",
            "auto_adjust": True,
            "actions": False,
            "prepost": False,
            "yfinance_version": getattr(yf, "__version__", "unknown"),
        }
        try:
            raw = yf.Ticker(symbol).history(
                start=params["start"],
                end=params["end"],
                interval="1d",
                auto_adjust=True,
                actions=False,
                prepost=False,
                timeout=self.timeout_seconds,
                raise_errors=True,
            )
        except Exception as exc:  # yfinance raises many unrelated types
            raise classify_error(exc) from exc

        if raw is None or raw.empty:
            raise DataNotFound(f"no rows returned for {symbol}")
        return DailyBars(
            symbol=symbol,
            frame=normalize_frame(raw),
            source=self.name,
            adjustment=ADJUSTMENT,
            params=params,
        )


def normalize_frame(raw: pd.DataFrame) -> pd.DataFrame:
    rename = {c: c.lower() for c in raw.columns}
    frame = raw.rename(columns=rename)
    missing = [c for c in BAR_COLUMNS if c not in frame.columns]
    if missing:
        raise ProviderError(f"provider response is missing columns: {missing}")
    frame = frame[BAR_COLUMNS].astype("float64")
    index = pd.DatetimeIndex(frame.index)
    if index.tz is not None:
        # Yahoo stamps daily bars in the exchange time zone; keep the market date.
        index = index.tz_convert("America/New_York")
    frame.index = pd.Index([ts.date() for ts in index], name="session_date")
    return frame.sort_index()


def classify_error(exc: Exception) -> ProviderError:
    name = type(exc).__name__
    text = str(exc).lower()
    # Transport failures first: a proxy refusing the tunnel ("CONNECT tunnel
    # failed, response 403") is a network problem, not a Yahoo permission error.
    if "tunnel" in text or "curl: (" in text or "proxy" in text:
        return NetworkError(f"{name}: {exc}")
    if name == "YFRateLimitError" or any(
        k in text for k in ("429", "too many requests", "rate limit", "captcha", "401", "403", "forbidden", "unauthorized")
    ):
        return RateLimited(f"{name}: {exc}")
    if name in {"YFTickerMissingError", "YFPricesMissingError"} or "no data found" in text or "delisted" in text:
        return DataNotFound(f"{name}: {exc}")
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)) or any(
        k in text for k in ("timeout", "timed out", "connection", "resolve", "network", "curl")
    ):
        return NetworkError(f"{name}: {exc}")
    return ProviderError(f"{name}: {exc}")
