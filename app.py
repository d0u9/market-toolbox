"""Streamlit dashboard. Reads the local cache only; never calls a data provider.

Charts render in the main script run and are rebuilt only when a published
snapshot changes. Small fragments poll the database every 60 seconds for
status and trigger a full rerun when a new snapshot appears, so an unchanged
chart is not re-sent to the browser every minute.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

import streamlit_shadcn_ui as ui

import storage
from charts import (
    LIBRARY_PATH, RANGES, STATE_DETAIL, STATE_SHORT, chart_html, chart_payload, select_range,
)
from config import ConfigError, load_config
from page_helpers import symbol_picker
from services import status
from services.status import QUALITY_TEXT, STATUS_TEXT

REFRESH_SECONDS = 60
CHART_HEIGHT = 560
CHART_HEIGHT_NO_MACD = 440
# Waiting for the source after the close is normal; only a failure is red.
BADGE_VARIANT = {status.UPDATED: "default", status.FAILED: "destructive"}
# shadcn badges have no green/blue, so the label carries the state as well.
ELDER_BADGE = {"Green": "default", "Red": "destructive", "Blue": "secondary"}
WORKER_STALE_AFTER = dt.timedelta(minutes=5)

try:
    CFG = load_config()
except ConfigError as exc:
    st.set_page_config(page_title="Elder Impulse", layout="wide")
    st.error(f"Configuration error: {exc}")
    st.stop()

TITLE = "Elder Impulse"
# Absolute path, honoring server.baseUrlPath: the chart iframe has no URL of its own.
_BASE = st.get_option("server.baseUrlPath").strip("/")
LIBRARY_URL = f"{'/' + _BASE if _BASE else ''}/app/static/{LIBRARY_PATH}"
st.set_page_config(page_title=TITLE, layout="wide")

# (snapshot id, content hash): ids restart at 1 when the database is recreated,
# so the hash is part of every cache key.
SnapshotKey = tuple[int, str]


def fmt_ts(value: str | None) -> str:
    ts = storage.parse_ts(value)
    if ts is None:
        return "—"
    return ts.astimezone(CFG.display_tz).strftime("%Y-%m-%d %H:%M %Z")


def open_connection():
    # A fresh read-only connection per use: Streamlit runs sessions on several
    # threads, and a shared connection cannot hold independent transactions.
    return storage.connect_ro(CFG.sqlite_path)


def database_ready() -> bool:
    try:
        conn = open_connection()
    except Exception:
        return False
    try:
        conn.execute("SELECT 1 FROM symbol_state LIMIT 1")
        return True
    except Exception:
        return False
    finally:
        conn.close()


def snapshot_key(snapshot: dict | None) -> SnapshotKey | None:
    return None if snapshot is None else (snapshot["id"], snapshot["content_hash"])


@st.cache_data(max_entries=8, show_spinner=False)
def load_values(snapshot_id: int, content_hash: str) -> pd.DataFrame:
    # Snapshot rows are immutable, so (id, hash) is a complete cache key.
    conn = open_connection()
    try:
        return storage.read_values(conn, snapshot_id)
    finally:
        conn.close()


@st.cache_data(max_entries=32, show_spinner=False)
def cached_chart(snapshot_id: int, content_hash: str, range_key: str,
                 chart_type: str, show_ema26: bool, show_macd: bool) -> str:
    values = load_values(snapshot_id, content_hash)
    return chart_html(chart_payload(values, range_key, chart_type, show_ema26, show_macd), LIBRARY_URL)


def quality_line(state: dict, gap_in_display: bool) -> str:
    quality = state["quality"]
    if quality == status.QUALITY_GAP and not gap_in_display:
        # The affected bars have scrolled out of every display range.
        return "Data complete in view"
    return f"Data: {QUALITY_TEXT.get(quality, quality)}"


def render_symbol(conn, symbol: str) -> tuple[str | None, SnapshotKey | None, bool]:
    """Header and chart. Returns (latest session, snapshot key, gap in display)."""
    _, snapshot = storage.read_symbol(conn, symbol)
    key = snapshot_key(snapshot)
    if snapshot is None:
        st.subheader(symbol)
        st.info("Initializing: no data published yet. The page refreshes automatically.")
        return None, key, False

    values = load_values(*key)
    if values.empty:  # snapshot replaced between reads; the status panel reruns the page
        st.info("Refreshing data…")
        return None, key, False
    last = values.iloc[-1]
    prev = values.iloc[-2] if len(values) > 1 else last
    latest = values.index[-1]

    elder = last["elder_state"]
    label, detail = STATE_SHORT[elder], STATE_DETAIL[elder]
    st.subheader(symbol, anchor=False)
    st.caption(f"{latest.isoformat()} · adjusted prices · Elder state: {label}"
               + (f" ({detail})" if detail else ""))

    stats = [
        ("Close", f"{last['close']:,.2f}", f"{(last['close'] / prev['close'] - 1) * 100:+.2f}%",
         "from previous day"),
        ("EMA13", f"{last['ema13']:.2f}", f"{last['ema13'] - prev['ema13']:+.2f}", "from previous day"),
        ("MACD histogram", f"{last['histogram']:.3f}", f"{last['histogram'] - prev['histogram']:+.3f}",
         "from previous day"),
        ("Elder state", label, None, detail or None),
    ]
    # Keep each metric wide enough for its label and value. A shared grid
    # stretches cards to the same height and wraps on narrower screens.
    with ui.elements(key=f"stats-{symbol}") as metrics:
        with metrics.grid(columns=4, min_column_width=180):
            for name, value, delta, description in stats:
                with metrics.card(size="sm"):
                    with metrics.card_header():
                        metrics.text(name, variant="muted")
                        metrics.heading(value, level=2)
                    with metrics.card_content():
                        with metrics.stack(gap="sm"):
                            if delta is not None:
                                metrics.badge(delta, variant="outline")
                            metrics.text(description or "—", variant="muted")

    gap_in_display = bool(select_range(values, "3Y")["quality_flag"].astype(bool).any())
    with st.container(border=True):
        with st.container(horizontal=True, horizontal_alignment="distribute",
                          vertical_alignment="center"):
            range_key = ui.tabs(list(RANGES), value="1Y", key="range", label="Range",
                                width="content")
            chart = ui.tabs(["Candles", "Line"], value="Candles", key="chart", label="Chart type",
                            width="content")
        # Size each switch to its label and keep related overlays together.
        with st.container(horizontal=True, vertical_alignment="center", gap="medium"):
            show_ema26 = ui.switch("EMA26", key="ema26", width="content")
            show_macd = ui.switch("MACD histogram", value=True, key="macd", width="content")
        view = select_range(values, range_key or "1Y")
        if len(view) < 2:
            ui.alert("Insufficient history", "Indicators are not shown.", key="insufficient")
        else:
            if view["quality_flag"].astype(bool).any():
                ui.alert("Data has a gap", "Indicators may be affected.", variant="destructive",
                         key="gap")
            page = cached_chart(*key, range_key or "1Y", "line" if chart == "Line" else "candles",
                                bool(show_ema26), bool(show_macd))
            st.iframe(page, height=CHART_HEIGHT if show_macd else CHART_HEIGHT_NO_MACD,
                      alt=f"{symbol} Elder Impulse chart")
    return latest.isoformat(), key, gap_in_display


@st.fragment(run_every=REFRESH_SECONDS)
def status_panel(symbol: str, shown_key: SnapshotKey | None, gap_in_display: bool) -> None:
    try:
        conn = open_connection()
    except Exception as exc:
        st.warning(f"Database not readable: {exc}")
        return
    try:
        state, snapshot = storage.read_symbol(conn, symbol)
    finally:
        conn.close()
    if snapshot_key(snapshot) != shown_key:
        st.rerun(scope="app")  # a new snapshot was published: redraw the chart
    if state is None or snapshot is None:
        if state and state.get("status_detail"):
            st.caption(state["status_detail"])
        return

    current = state["status"]
    quality = quality_line(state, gap_in_display)
    ui.badges([(STATUS_TEXT.get(current, current),
                BADGE_VARIANT.get(current, "secondary")),
               (quality, "secondary")], key=f"badges-{symbol}")
    if current != status.UPDATED and state.get("status_detail"):
        ui.alert(STATUS_TEXT.get(current, current), state["status_detail"],
                 variant="destructive" if current == status.FAILED else "default",
                 key=f"status-alert-{symbol}")
    lines = [
        *([state["quality_message"]] if state.get("quality_message") else []),
        f"Target session: {state['target_session'] or '—'} · latest session: {snapshot['latest_session']}",
        f"Content updated: {fmt_ts(state['content_updated_at'])} · "
        f"last successful check: {fmt_ts(state['last_checked_at'])}",
        f"Source: {snapshot['source']} · adjusted prices ({snapshot['adjustment']}); "
        "values are not historical traded prices",
    ]
    st.caption("  \n".join(lines))


@st.fragment(run_every=REFRESH_SECONDS)
def page_status() -> None:
    try:
        conn = open_connection()
        try:
            heartbeat = storage.get_meta(conn, "worker_heartbeat")
        finally:
            conn.close()
    except Exception:
        heartbeat = None
    seen = storage.parse_ts(heartbeat)
    now = dt.datetime.now(dt.timezone.utc)
    if seen is None or now - seen > WORKER_STALE_AFTER:
        st.warning(f"The update worker has not checked in since {fmt_ts(heartbeat)}. "
                   "Data will not refresh until it is running again.")
    st.caption(f"Page checked {now.astimezone(CFG.display_tz).strftime('%Y-%m-%d %H:%M:%S %Z')}")


def latest_quotes(symbols) -> dict[str, tuple[str, float, float | None] | None]:
    """Latest (Elder label, close, day change %) per symbol; None when there is no data."""
    quotes: dict[str, tuple[str, float, float | None] | None] = dict.fromkeys(symbols)
    if not database_ready():
        return quotes
    conn = open_connection()
    try:
        for sym in symbols:
            _, snapshot = storage.read_symbol(conn, sym)
            if snapshot is None:
                continue
            values = load_values(*snapshot_key(snapshot))
            if values.empty:
                continue
            last = values.iloc[-1]
            pct = (last["close"] / values.iloc[-2]["close"] - 1) * 100 if len(values) > 1 else None
            quotes[sym] = (STATE_SHORT[last["elder_state"]], float(last["close"]), pct)
    except Exception:
        pass  # the main panel reports read errors
    finally:
        conn.close()
    return quotes


def watchlist_sidebar(symbols, current: str) -> str:
    """Brand card plus one shadcn card per symbol; returns the selected symbol."""
    quotes = latest_quotes(symbols)
    views = {}
    with ui.elements(key="sidebar") as side:
        with side.stack(gap="md"):
            with side.card(size="sm"):
                with side.card_header():
                    side.heading(TITLE, level=3)
                    side.text(f"Daily trend dashboard · refreshes every {REFRESH_SECONDS} s",
                              variant="muted")
            side.text("Watchlist", variant="label")
            for sym in symbols:
                quote = quotes[sym]
                with side.card(size="sm", key=f"watch-{sym}"):
                    with side.card_content():
                        with side.stack(gap="sm"):
                            with side.stack(direction="horizontal", justify="between", align="center"):
                                side.heading(sym, level=4)
                                if quote:
                                    side.badge(quote[0], variant=ELDER_BADGE.get(quote[0], "outline"))
                            if quote is None:
                                side.text("No data yet", variant="muted")
                            else:
                                change = "" if quote[2] is None else f" · {quote[2]:+.2f}%"
                                side.text(f"{quote[1]:,.2f}{change}", variant="muted")
                            views[sym] = side.button(
                                "Viewing" if sym == current else "View", key=f"view-{sym}",
                                variant="default" if sym == current else "outline", size="sm",
                                stretch=True)
    for sym, handle in views.items():
        if handle.clicked:
            return sym
    return current


@st.fragment(run_every=REFRESH_SECONDS)
def wait_for_database() -> None:
    if database_ready():
        st.rerun(scope="app")
    st.info("Initializing: waiting for the worker to create the database.")


with ui.elements(key="page-header") as header:
    with header.card():
        with header.card_header():
            header.heading(TITLE, level=2)
            header.text(f"Times shown in {CFG.display_timezone}. Trading dates are US/Eastern "
                        "market dates. Colors show the Elder Impulse state, not up/down days.",
                        variant="muted")

with st.sidebar:
    # One symbol per page: a plain list for a few symbols, a searchable
    # combobox once the list would get too long for the sidebar.
    # The selected symbol lives in the URL (?symbol=QQQ) so a page can be
    # bookmarked or shared.
    wanted = st.query_params.get("symbol")
    initial = wanted if wanted in CFG.symbols else CFG.symbols[0]
    if symbol_picker(len(CFG.symbols)) == "list":
        selected = watchlist_sidebar(CFG.symbols, initial)
        if selected != initial:  # redraw so the sidebar highlights the new symbol
            st.query_params["symbol"] = selected
            st.rerun()
    else:
        selected = ui.combobox("Symbol", list(CFG.symbols), value=initial,
                               clearable=False, key="symbol")
    selected = selected if selected in CFG.symbols else initial
    st.query_params["symbol"] = selected

if not database_ready():
    wait_for_database()
    st.stop()

conn = open_connection()
try:
    _, key, gap_in_display = render_symbol(conn, selected)
    with st.container(border=True):
        status_panel(selected, key, gap_in_display)
except Exception as exc:
    st.error(f"Could not read the local data: {exc}")
finally:
    conn.close()

page_status()
st.caption("Charts: [TradingView Lightweight Charts™](https://www.tradingview.com/) · "
           "Copyright (c) 2026 TradingView, Inc. · Apache License 2.0")
