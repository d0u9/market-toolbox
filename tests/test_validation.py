import datetime as dt

import numpy as np
import pytest

from services.calendar import MarketCalendar
from services.validation import (
    GAP_AFFECTED_BARS,
    QUALITY_COMPLETE,
    QUALITY_GAP,
    QUALITY_INSUFFICIENT,
    TargetMissing,
    gap_flags,
    validate_bars,
)
from tests.fakes import synthetic_bars

CAL = MarketCalendar("NYSE")
START = dt.date(2024, 1, 1)  # a holiday: the first expected session is Jan 2
TARGET = dt.date(2025, 10, 17)
EXPECTED = CAL.sessions(START, TARGET + dt.timedelta(days=1))


def bars(sessions=None):
    return synthetic_bars(list(EXPECTED if sessions is None else sessions), seed=3)


def test_clean_data_passes_and_holiday_start_is_not_head_gap():
    assert EXPECTED[0] == dt.date(2024, 1, 2)
    r = validate_bars(bars(), EXPECTED, TARGET, required_bars=100)
    assert r.ok and r.quality == QUALITY_COMPLETE and r.gaps == []


def test_missing_target_raises():
    with pytest.raises(TargetMissing):
        validate_bars(bars(EXPECTED[:-1]), EXPECTED, TARGET, 100)


def test_single_middle_gap_allowed_with_warning():
    missing = EXPECTED[200]
    r = validate_bars(bars([d for d in EXPECTED if d != missing]), EXPECTED, TARGET, 100)
    assert r.ok and r.quality == QUALITY_GAP and r.gaps == [missing]


def test_two_middle_gaps_block():
    drop = {EXPECTED[200], EXPECTED[300]}
    r = validate_bars(bars([d for d in EXPECTED if d not in drop]), EXPECTED, TARGET, 100)
    assert not r.ok


def test_two_adjacent_missing_block():
    drop = {EXPECTED[200], EXPECTED[201]}
    r = validate_bars(bars([d for d in EXPECTED if d not in drop]), EXPECTED, TARGET, 100)
    assert not r.ok


def test_head_missing_with_too_little_history_blocks():
    r = validate_bars(bars(EXPECTED[50:]), EXPECTED, TARGET, required_bars=len(EXPECTED))
    assert not r.ok and "head" in r.errors[0]


def test_head_missing_is_not_treated_as_middle_gap():
    # Enough bars remain: allowed, but the head is not reported as a gap.
    r = validate_bars(bars(EXPECTED[50:]), EXPECTED, TARGET, required_bars=100)
    assert r.ok and r.gaps == [] and any("head" in w for w in r.warnings)


def test_short_history_without_head_gap_is_insufficient():
    r = validate_bars(bars(), EXPECTED, TARGET, required_bars=len(EXPECTED) + 1)
    assert r.ok and r.quality == QUALITY_INSUFFICIENT


def test_duplicate_dates_block():
    b = bars()
    b = b.iloc[list(range(len(b))) + [10]].sort_index()
    assert not validate_bars(b, EXPECTED, TARGET, 100).ok


def test_unsorted_dates_block():
    b = bars().iloc[::-1]
    assert not validate_bars(b, EXPECTED, TARGET, 100).ok


@pytest.mark.parametrize("col,value", [
    ("close", 0.0), ("close", -1.0), ("open", np.nan), ("high", np.inf), ("volume", -5.0),
    ("volume", np.nan),
])
def test_invalid_values_block(col, value):
    b = bars()
    b.iloc[100, b.columns.get_loc(col)] = value
    assert not validate_bars(b, EXPECTED, TARGET, 100).ok


def test_invalid_ohlc_relationship_blocks():
    b = bars()
    b.iloc[100, b.columns.get_loc("high")] = b["low"].iloc[100] * 0.9
    r = validate_bars(b, EXPECTED, TARGET, 100)
    assert not r.ok and "OHLC" in r.errors[0]


def test_row_on_non_session_date_blocks():
    b = bars()
    b.loc[dt.date(2025, 7, 4)] = b.iloc[0]
    b = b.sort_index()
    assert not validate_bars(b, EXPECTED, TARGET, 100).ok


def test_large_jump_is_logged_not_dropped(caplog):
    b = bars()
    b.iloc[100:, :4] *= 1.5
    r = validate_bars(b, EXPECTED, TARGET, 100)
    assert r.ok and len(b) == len(EXPECTED)
    assert "large close-to-close move" in caplog.text


def test_gap_flags_cover_next_100_bars():
    index = bars().index
    gap = EXPECTED[200]
    kept = index[index != gap]
    flags = gap_flags(kept, [gap])
    assert flags.sum() == GAP_AFFECTED_BARS
    first = list(kept).index(EXPECTED[201])
    assert flags.iloc[first] == 1 and flags.iloc[first - 1] == 0
    assert flags.iloc[first + GAP_AFFECTED_BARS] == 0
