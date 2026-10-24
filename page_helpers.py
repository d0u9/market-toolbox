"""Pure helpers for the Streamlit page (importable without running it)."""

from __future__ import annotations

SYMBOL_LIST_MAX = 20


def symbol_picker(count: int) -> str:
    """'list' shows every symbol; 'search' is a searchable combobox for long lists."""
    return "list" if count <= SYMBOL_LIST_MAX else "search"
