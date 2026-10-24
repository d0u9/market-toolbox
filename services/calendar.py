"""Market sessions, confirmed exceptions and target-session selection."""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

MARKET_TZ = ZoneInfo("America/New_York")
UTC = dt.timezone.utc


class CalendarExceptionsError(ValueError):
    pass


@dataclass(frozen=True)
class CalendarExceptions:
    closures: dict[dt.date, str] = field(default_factory=dict)
    early_closes: dict[dt.date, tuple[dt.time, str]] = field(default_factory=dict)
    version: str = "none"


def file_hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return "missing"


def load_exceptions(path: Path) -> CalendarExceptions:
    """Parse the exceptions YAML. A missing file means no exceptions."""
    if not path.exists():
        return CalendarExceptions(version="missing")
    return parse_exceptions(path.read_text(encoding="utf-8"), str(path))


def parse_exceptions(text: str, path: str = "calendar exceptions") -> CalendarExceptions:
    try:
        data = yaml.safe_load(text) or {}
    except (yaml.YAMLError, ValueError) as exc:  # PyYAML raises ValueError for bad dates
        raise CalendarExceptionsError(f"{path}: invalid YAML: {exc}") from None
    if not isinstance(data, dict):
        raise CalendarExceptionsError(f"{path}: top level must be a mapping")
    unknown = set(data) - {"closures", "early_closes"}
    if unknown:
        raise CalendarExceptionsError(f"{path}: unknown key(s): {sorted(unknown)}")

    def entries(key: str) -> list[dict]:
        items = data.get(key) or []
        if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
            raise CalendarExceptionsError(f"{path}: '{key}' must be a list of mappings")
        return items

    def parse_date(item: dict, key: str) -> dt.date:
        value = item.get("date")
        if isinstance(value, dt.datetime):
            return value.date()
        if isinstance(value, dt.date):
            return value
        try:
            return dt.date.fromisoformat(str(value))
        except ValueError:
            raise CalendarExceptionsError(f"{path}: '{key}' has invalid date {value!r}") from None

    def reason(item: dict, key: str) -> str:
        value = item.get("reason")
        if not isinstance(value, str) or not value.strip():
            raise CalendarExceptionsError(f"{path}: every '{key}' entry needs a reason")
        return value.strip()

    closures: dict[dt.date, str] = {}
    for item in entries("closures"):
        day = parse_date(item, "closures")
        if day in closures:
            raise CalendarExceptionsError(f"{path}: duplicate closure date {day}")
        closures[day] = reason(item, "closures")
    early: dict[dt.date, tuple[dt.time, str]] = {}
    for item in entries("early_closes"):
        try:
            close_time = dt.time.fromisoformat(str(item.get("close_time")))
        except ValueError:
            raise CalendarExceptionsError(
                f"{path}: early close has invalid close_time {item.get('close_time')!r}"
            ) from None
        day = parse_date(item, "early_closes")
        if day in early or day in closures:
            raise CalendarExceptionsError(f"{path}: duplicate exception date {day}")
        early[day] = (close_time, reason(item, "early_closes"))
    return CalendarExceptions(closures, early, hashlib.sha256(text.encode("utf-8")).hexdigest()[:16])


class MarketCalendar:
    def __init__(self, name: str = "NYSE", exceptions: CalendarExceptions | None = None) -> None:
        import pandas_market_calendars as mcal

        self._calendar = mcal.get_calendar(name)
        self.name = name
        self.exceptions = exceptions or CalendarExceptions()
        # The worker asks for the target every minute; the recent schedule only
        # changes when the market date does.
        self._recent: tuple[dt.date, pd.Series] | None = None

    def schedule(self, start: dt.date, end_inclusive: dt.date) -> pd.Series:
        """Map session date -> UTC close timestamp, exceptions applied."""
        sched = self._calendar.schedule(start_date=start, end_date=end_inclusive)
        closes: dict[dt.date, dt.datetime] = {
            ts.date(): close.to_pydatetime().astimezone(UTC)
            for ts, close in zip(sched.index, sched["market_close"])
        }
        for day in self.exceptions.closures:
            closes.pop(day, None)
        for day, (close_time, _) in self.exceptions.early_closes.items():
            if day in closes:
                closes[day] = dt.datetime.combine(day, close_time, MARKET_TZ).astimezone(UTC)
        return pd.Series(closes, dtype="object").sort_index()

    def sessions(self, start: dt.date, end_exclusive: dt.date) -> list[dt.date]:
        if end_exclusive <= start:
            return []
        return list(self.schedule(start, end_exclusive - dt.timedelta(days=1)).index)

    def target_session(self, now_utc: dt.datetime, delay_minutes: int) -> dt.date:
        """Latest session whose close plus the delay is not after ``now_utc``."""
        today = now_utc.astimezone(MARKET_TZ).date()
        if self._recent is None or self._recent[0] != today:
            self._recent = (today, self.schedule(today - dt.timedelta(days=21), today))
        sched = self._recent[1]
        ready = [d for d, close in sched.items() if close + dt.timedelta(minutes=delay_minutes) <= now_utc]
        if not ready:
            raise RuntimeError("no completed session found in the last 21 days")
        return ready[-1]


def years_before(day: dt.date, years: int) -> dt.date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # Feb 29
        return day.replace(year=day.year - years, day=28)
