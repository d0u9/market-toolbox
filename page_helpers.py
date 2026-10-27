"""Pure helpers for the Streamlit page (importable without running it)."""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SYMBOL_LIST_MAX = 20


def display_zone(configured: str, browser_timezone: str | None) -> ZoneInfo:
    """Browser context is per session; never put it in a process-wide cache."""
    if configured != "browser":
        return ZoneInfo(configured)
    try:
        return ZoneInfo(browser_timezone) if browser_timezone else ZoneInfo("UTC")
    except (ValueError, ZoneInfoNotFoundError):
        return ZoneInfo("UTC")


def symbol_picker(count: int) -> str:
    """'list' shows every symbol; 'search' is a searchable combobox for long lists."""
    return "list" if count <= SYMBOL_LIST_MAX else "search"
