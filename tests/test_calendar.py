import datetime as dt

import pytest

from services.calendar import (
    CalendarExceptionsError,
    MarketCalendar,
    load_exceptions,
    years_before,
)
from tests.fakes import et

D = dt.date


@pytest.fixture(scope="module")
def cal():
    return MarketCalendar("NYSE")


def test_before_and_after_delay_on_regular_day(cal):
    # Wed 2025-10-15: close 16:00 ET, ready 16:30 ET.
    assert cal.target_session(et(2025, 10, 15, 16, 29), 30) == D(2025, 10, 14)
    assert cal.target_session(et(2025, 10, 15, 16, 30), 30) == D(2025, 10, 15)


def test_weekend_targets_friday(cal):
    assert cal.target_session(et(2025, 10, 18, 12), 30) == D(2025, 10, 17)
    assert cal.target_session(et(2025, 10, 20, 9), 30) == D(2025, 10, 17)


@pytest.mark.parametrize("day", [D(2025, 3, 7), D(2025, 3, 10), D(2025, 10, 31), D(2025, 11, 3)])
def test_dst_switch_uses_local_market_close(cal, day):
    # Close is 16:00 New York both sides of each DST change (UTC offset differs).
    before = et(day.year, day.month, day.day, 16, 29)
    after = et(day.year, day.month, day.day, 16, 30)
    assert cal.target_session(before, 30) < day
    assert cal.target_session(after, 30) == day


def test_holiday_is_skipped(cal):
    # 2025-07-04 is a holiday; 2025-07-03 closes early at 13:00 ET.
    assert cal.target_session(et(2025, 7, 4, 20), 30) == D(2025, 7, 3)
    assert D(2025, 7, 4) not in cal.sessions(D(2025, 7, 1), D(2025, 7, 8))


def test_early_close(cal):
    # Day after Thanksgiving 2025 closes at 13:00 ET.
    assert cal.target_session(et(2025, 11, 28, 13, 29), 30) == D(2025, 11, 26)
    assert cal.target_session(et(2025, 11, 28, 13, 30), 30) == D(2025, 11, 28)


def test_display_timezone_does_not_matter(cal):
    # The same instant expressed in other zones yields the same target.
    now = et(2025, 10, 15, 16, 45)
    from zoneinfo import ZoneInfo

    for zone in ("Europe/London", "Asia/Tokyo", "Pacific/Kiritimati"):
        assert cal.target_session(now.astimezone(ZoneInfo(zone)), 30) == D(2025, 10, 15)


def test_sessions_end_exclusive(cal):
    s = cal.sessions(D(2025, 10, 13), D(2025, 10, 17))
    assert s == [D(2025, 10, 13), D(2025, 10, 14), D(2025, 10, 15), D(2025, 10, 16)]
    assert cal.sessions(D(2025, 10, 13), D(2025, 10, 13)) == []


def test_exceptions_closure_and_early_close(tmp_path):
    path = tmp_path / "ex.yaml"
    path.write_text(
        "closures:\n  - date: 2025-10-15\n    reason: test closure\n"
        "early_closes:\n  - date: '2025-10-16'\n    close_time: '12:00'\n    reason: test early\n",
        encoding="utf-8",
    )
    ex = load_exceptions(path)
    cal = MarketCalendar("NYSE", ex)
    assert D(2025, 10, 15) not in cal.sessions(D(2025, 10, 13), D(2025, 10, 18))
    assert cal.target_session(et(2025, 10, 15, 20), 30) == D(2025, 10, 14)
    assert cal.target_session(et(2025, 10, 16, 12, 30), 30) == D(2025, 10, 16)
    assert cal.target_session(et(2025, 10, 16, 12, 29), 30) == D(2025, 10, 14)


def test_missing_exceptions_file_is_empty(tmp_path):
    ex = load_exceptions(tmp_path / "none.yaml")
    assert ex.closures == {} and ex.early_closes == {}


@pytest.mark.parametrize("text", [
    "closures: [\n",                                  # invalid YAML
    "closures:\n  - date: 2025-13-40\n    reason: x\n",  # invalid date
    "closures:\n  - date: 2025-10-15\n",               # missing reason
    "holidays: []\n",                                 # unknown key
    "early_closes:\n  - date: 2025-10-15\n    close_time: noon\n    reason: x\n",
    "closures:\n  - {date: 2025-10-15, reason: a}\n  - {date: 2025-10-15, reason: b}\n",
])
def test_invalid_exceptions_rejected(tmp_path, text):
    path = tmp_path / "ex.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(CalendarExceptionsError):
        load_exceptions(path)


def test_years_before_leap_day():
    assert years_before(D(2024, 2, 29), 5) == D(2019, 2, 28)
    assert years_before(D(2025, 10, 24), 5) == D(2020, 10, 24)
