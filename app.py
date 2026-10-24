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
from charts import RANGES, STATE_DETAIL, STATE_SHORT, build_figure, select_range
from config import ConfigError, load_config
from page_helpers import symbol_picker
from services import status
from services.status import QUALITY_TEXT, STATUS_TEXT

REFRESH_SECONDS = 60
# Waiting for the source after the close is normal; only a failure is red.
BADGE_VARIANT = {status.UPDATED: "default", status.FAILED: "destructive"}
WORKER_STALE_AFTER = dt.timedelta(minutes=5)

try:
    CFG = load_config()
except ConfigError as exc:
    st.set_page_config(page_title="Elder Impulse", layout="wide")
    st.error(f"Configuration error: {exc}")
    st.stop()

TITLE = "Elder Impulse"
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
def cached_figure(snapshot_id: int, content_hash: str, symbol: str, range_key: str,
                  chart_type: str, show_ema26: bool, show_macd: bool):
    view = select_range(load_values(snapshot_id, content_hash), range_key)
    return build_figure(view, symbol, chart_type, show_ema26, show_macd)


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

    cols = st.columns(4)
    # The shadcn metric card has no up/down coloring, so the sign carries direction.
    stats = [
        ("Close", f"{last['close']:,.2f}",
         f"{(last['close'] / prev['close'] - 1) * 100:+.2f}% from previous day"),
        ("EMA13", f"{last['ema13']:.2f}", f"{last['ema13'] - prev['ema13']:+.2f} from previous day"),
        ("MACD histogram", f"{last['histogram']:.3f}",
         f"{last['histogram'] - prev['histogram']:+.3f} from previous day"),
        ("Elder state", label, detail or None),
    ]
    for col, (name, value, description) in zip(cols, stats):
        with col:
            ui.metric_card(name, value, description=description, key=f"stat-{symbol}-{name}")

    gap_in_display = bool(select_range(values, "3Y")["quality_flag"].astype(bool).any())
    with st.container(border=True):
        left, right = st.columns([3, 2])
        with left:
            range_key = ui.tabs(list(RANGES), value="1Y", key="range", label="Range")
        with right:
            chart = ui.tabs(["Candles", "Line"], value="Candles", key="chart", label="Chart type")
        s1, s2, _ = st.columns([1, 1, 3])
        with s1:
            show_ema26 = ui.switch("EMA26", key="ema26")
        with s2:
            show_macd = ui.switch("MACD histogram", value=True, key="macd")
        view = select_range(values, range_key or "1Y")
        if len(view) < 2:
            ui.alert("Insufficient history", "Indicators are not shown.", key="insufficient")
        else:
            if view["quality_flag"].astype(bool).any():
                ui.alert("Data has a gap", "Indicators may be affected.", variant="destructive",
                         key="gap")
            fig = cached_figure(*key, symbol, range_key or "1Y",
                                "line" if chart == "Line" else "candles",
                                bool(show_ema26), bool(show_macd))
            st.plotly_chart(fig, width="stretch", key=f"chart-{symbol}")
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


@st.fragment(run_every=REFRESH_SECONDS)
def wait_for_database() -> None:
    if database_ready():
        st.rerun(scope="app")
    st.info("Initializing: waiting for the worker to create the database.")


st.title(TITLE, anchor=False)
st.caption(f"Times shown in {CFG.display_timezone}. Trading dates are US/Eastern market dates. "
           "Colors show the Elder Impulse state, not up/down days.")

with st.sidebar:
    # One symbol per page: a plain list for a few symbols, a searchable
    # combobox once the list would get too long for the sidebar.
    # The selected symbol lives in the URL (?symbol=QQQ) so a page can be
    # bookmarked or shared.
    wanted = st.query_params.get("symbol")
    initial = wanted if wanted in CFG.symbols else CFG.symbols[0]
    if symbol_picker(len(CFG.symbols)) == "list":
        selected = ui.radio_group("Symbol", list(CFG.symbols), value=initial, key="symbol")
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
