"""Daily bar validation and history-gap grading (design section 5.2 / 8)."""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

QUALITY_COMPLETE = "complete"
QUALITY_GAP = "history_gap"
QUALITY_INSUFFICIENT = "insufficient_history"

# Bars after a single missing session that carry a quality flag (~4x EMA26).
GAP_AFFECTED_BARS = 100
JUMP_WARN_RATIO = 0.20


class TargetMissing(Exception):
    """The provider has not delivered the target session yet."""


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    gaps: list[dt.date] = field(default_factory=list)
    quality: str = QUALITY_COMPLETE

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_bars(
    bars: pd.DataFrame,
    expected: list[dt.date],
    target: dt.date,
    required_bars: int,
) -> ValidationResult:
    """Validate bars already truncated to ``<= target``.

    ``expected`` holds the calendar sessions in the requested window and
    ``required_bars`` is the warmup length plus the display length.
    Raises ``TargetMissing`` if the target row is absent.
    """
    result = ValidationResult()
    index = list(bars.index)

    if bars.index.has_duplicates:
        result.errors.append("duplicate session dates")
    if index != sorted(index):
        result.errors.append("session dates are not increasing")

    prices = bars[["open", "high", "low", "close"]].to_numpy(dtype="float64")
    volume = bars["volume"].to_numpy(dtype="float64")
    if not np.isfinite(prices).all() or (prices <= 0).any():
        result.errors.append("prices must be finite and greater than zero")
    else:
        o, h, l, c = prices.T
        bad = (h < np.maximum(o, c)) | (l > np.minimum(o, c)) | (h < l)
        if bad.any():
            first = index[int(np.argmax(bad))]
            result.errors.append(f"invalid OHLC relationship on {int(bad.sum())} row(s), first {first}")
    if not np.isfinite(volume).all() or (volume < 0).any():
        result.errors.append("volume must be finite and non-negative")

    expected_set = set(expected)
    unexpected = [d for d in index if d not in expected_set]
    if unexpected:
        result.errors.append(
            f"{len(unexpected)} row(s) on non-session dates, first {unexpected[0]}"
        )

    if result.errors:
        return result
    # Checked after the structural checks so bad data is reported as a
    # validation failure rather than as "still waiting for the target day".
    if target not in set(index):
        raise TargetMissing(f"target session {target} not in provider data")

    head_missing = bool(expected) and index[0] > expected[0]
    if len(index) < required_bars:
        if head_missing:
            result.errors.append(
                f"window head missing from {expected[0]} to {index[0]}; "
                f"{len(index)} bars available, {required_bars} required"
            )
            return result
        result.quality = QUALITY_INSUFFICIENT
        result.warnings.append(f"only {len(index)} bars, {required_bars} required")
    elif head_missing:
        result.warnings.append(f"window head starts at {index[0]}, expected {expected[0]}")

    present = set(index)
    gaps = [d for d in expected if index[0] < d < target and d not in present]
    if len(gaps) > 1:
        result.errors.append(
            f"{len(gaps)} missing sessions in history: {', '.join(map(str, gaps[:5]))}"
        )
        return result
    if gaps:
        result.gaps = gaps
        if result.quality == QUALITY_COMPLETE:
            result.quality = QUALITY_GAP
        result.warnings.append(f"missing session {gaps[0]}")

    returns = bars["close"].pct_change().abs()
    for day, ret in returns[returns > JUMP_WARN_RATIO].items():
        log.warning("large close-to-close move of %.1f%% on %s", ret * 100, day)
    return result


def gap_flags(index: pd.Index, gaps: list[dt.date]) -> pd.Series:
    """1 for the GAP_AFFECTED_BARS rows following each gap, else 0."""
    flags = pd.Series(0, index=index, dtype="int64")
    dates = list(index)
    for gap in gaps:
        pos = next((i for i, d in enumerate(dates) if d > gap), None)
        if pos is not None:
            flags.iloc[pos : pos + GAP_AFFECTED_BARS] = 1
    return flags
