import csv
from pathlib import Path

import pandas as pd
import pytest

from services.indicators import BLUE, GREEN, RED, UNAVAILABLE, compute_indicators, elder_state

FIXTURES = Path(__file__).parent / "fixtures"


def load_input() -> pd.DataFrame:
    rows = list(csv.DictReader((FIXTURES / "indicator_input.csv").open(encoding="utf-8")))
    close = [float(r["close"]) for r in rows]
    return pd.DataFrame(
        {"open": close, "high": close, "low": close, "close": close, "volume": 1.0},
        index=[r["session_date"] for r in rows],
    )


def test_matches_independent_reference():
    out = compute_indicators(load_input(), warmup_bars=1)
    ref = list(csv.DictReader((FIXTURES / "indicator_reference.csv").open(encoding="utf-8")))
    assert len(ref) == len(out)
    for i, row in enumerate(ref):
        for col in ("ema13", "ema26", "macd", "signal", "histogram"):
            assert out[col].iloc[i] == pytest.approx(float(row[col]), abs=1e-9), (i, col)
        assert out["elder_state"].iloc[i] == row["elder_state"], i


def test_first_ema_equals_first_close_and_hand_value():
    out = compute_indicators(load_input(), warmup_bars=1)
    assert out["ema13"].iloc[0] == 100.0
    # EMA13[1] = 2/14 * 101 + 12/14 * 100
    assert out["ema13"].iloc[1] == pytest.approx(2 / 14 * 101 + 12 / 14 * 100)


def test_elder_rules_including_ties_and_missing():
    ema13 = pd.Series([1.0, 2.0, 1.0, 1.0, 2.0, 1.0, float("nan"), 1.0])
    hist = pd.Series([0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0])
    states = list(elder_state(ema13, hist))
    assert states == [
        UNAVAILABLE,  # no previous bar
        GREEN,        # both up
        RED,          # both down
        BLUE,         # both flat: ties are blue
        BLUE,         # ema up, histogram flat
        BLUE,         # ema down, histogram up
        UNAVAILABLE,  # missing value
        UNAVAILABLE,  # previous value missing
    ]


def test_state_ignores_zero_line():
    # Histogram rising while still negative counts as rising.
    ema13 = pd.Series([1.0, 2.0])
    hist = pd.Series([-5.0, -4.0])
    assert list(elder_state(ema13, hist)) == [UNAVAILABLE, GREEN]


def test_warmup_rows_are_unavailable_not_blue():
    out = compute_indicators(load_input(), warmup_bars=10)
    assert set(out["elder_state"].iloc[:10]) == {UNAVAILABLE}
    assert UNAVAILABLE not in set(out["elder_state"].iloc[10:])


def test_warmup_longer_than_history():
    out = compute_indicators(load_input(), warmup_bars=500)
    assert set(out["elder_state"]) == {UNAVAILABLE}


def test_deterministic():
    a = compute_indicators(load_input(), 5)
    b = compute_indicators(load_input(), 5)
    pd.testing.assert_frame_equal(a, b)
