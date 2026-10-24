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

import storage
from charts import RANGES, STATE_COLORS, STATE_LABELS, build_figure, select_range
from config import ConfigError, load_config
from services import status
from services.status import QUALITY_TEXT, STATUS_TEXT

REFRESH_SECONDS = 60
WORKER_STALE_AFTER = dt.timedelta(minutes=5)

try:
    CFG = load_config()
except ConfigError as exc:
    st.set_page_config(page_title="Elder Impulse", layout="wide")
    st.error(f"Configuration error: {exc}")
    st.stop()

TITLE = "Elder Impulse"
CHARTS_PER_ROW = 2
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
        return "Data quality: **Complete** (an older gap is kept in the run records)"
    text = f"Data quality: **{QUALITY_TEXT.get(quality, quality)}**"
    if state.get("quality_message"):
        text += f" — {state['quality_message']}"
    return text


def change(cur: float, prev: float) -> str:
    return f"{cur - prev:+.3f}"


def render_symbol(conn, symbol: str, opts: dict) -> tuple[str | None, SnapshotKey | None, bool]:
    """Header and chart. Returns (latest session, snapshot key, gap in display)."""
    _, snapshot = storage.read_symbol(conn, symbol)
    key = snapshot_key(snapshot)
    st.subheader(symbol)
    if snapshot is None:
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
    color = STATE_COLORS.get(elder, "#888888")
    st.markdown(
        f"**{latest.isoformat()}** · adjusted close **{last['close']:.2f}** "
        f"({(last['close'] / prev['close'] - 1) * 100:+.2f}%)  \n"
        f"<span style='display:inline-block;width:0.8em;height:0.8em;border-radius:2px;"
        f"background:{color};margin-right:0.4em'></span>Elder state: **{STATE_LABELS[elder]}**",
        unsafe_allow_html=True,
    )
    c1, c2 = st.columns(2)
    c1.metric("EMA13", f"{last['ema13']:.2f}", change(last["ema13"], prev["ema13"]))
    c2.metric("MACD histogram", f"{last['histogram']:.3f}", change(last["histogram"], prev["histogram"]))

    view = select_range(values, opts["range"])
    gap_in_display = bool(select_range(values, "3Y")["quality_flag"].astype(bool).any())
    if len(view) < 2:
        st.warning("Insufficient history: indicators are not shown.")
    else:
        if view["quality_flag"].astype(bool).any():
            st.warning("Data has a gap; indicators may be affected.")
        fig = cached_figure(*key, symbol, opts["range"], opts["chart"], opts["ema26"], opts["macd"])
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
    lines = [
        f"Update status: **{STATUS_TEXT.get(current, current)}**"
        + (f" — {state['status_detail']}" if state.get("status_detail") else ""),
        quality_line(state, gap_in_display),
        f"Target session: {state['target_session'] or '—'} · latest session: {snapshot['latest_session']}",
        f"Content updated: {fmt_ts(state['content_updated_at'])} · "
        f"last successful check: {fmt_ts(state['last_checked_at'])}",
        f"Source: {snapshot['source']} · adjusted prices ({snapshot['adjustment']}); "
        "values are not historical traded prices",
    ]
    if current != status.UPDATED:
        if current == status.FAILED:
            st.error(lines[0])
        else:
            st.warning(lines[0])
        lines = lines[1:]
    st.caption("  \n".join(lines))


@st.fragment(run_every=REFRESH_SECONDS)
def overview() -> None:
    """One row per configured symbol, so any number of symbols stays readable."""
    rows, dates = [], {}
    conn = open_connection()
    try:
        for symbol in CFG.symbols:
            state, snapshot = storage.read_symbol(conn, symbol)
            row = {"Symbol": symbol, "Date": None, "Elder state": None, "Close": None,
                   "Change %": None, "Update status": STATUS_TEXT.get(state["status"], state["status"])
                   if state else STATUS_TEXT[status.INITIALIZING], "Data quality": None}
            if snapshot is not None:
                values = load_values(*snapshot_key(snapshot))
                if len(values) > 1:
                    last, prev = values.iloc[-1], values.iloc[-2]
                    row.update({
                        "Date": values.index[-1].isoformat(),
                        "Elder state": STATE_LABELS[last["elder_state"]].split(" (")[0],
                        "Close": round(float(last["close"]), 2),
                        "Change %": round(float((last["close"] / prev["close"] - 1) * 100), 2),
                        "Data quality": QUALITY_TEXT.get(state["quality"], state["quality"]),
                    })
                    dates[symbol] = row["Date"]
            rows.append(row)
    finally:
        conn.close()
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if len(set(dates.values())) > 1:
        st.warning("Data dates differ between symbols: "
                   + ", ".join(f"{s} {d}" for s, d in dates.items()))


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


st.title(TITLE)
st.caption(f"Times shown in {CFG.display_timezone}. Trading dates are US/Eastern market dates. "
           "Colors show the Elder Impulse state, not up/down days.")

with st.sidebar:
    opts = {
        "range": st.radio("Range", list(RANGES), index=2, horizontal=True),
        "chart": st.radio("Chart", ["candles", "line"],
                          format_func={"candles": "Candles", "line": "Close line"}.get),
        "ema26": st.checkbox("Show EMA26", value=False),
        "macd": st.checkbox("Show MACD histogram", value=True),
    }
    shown = st.multiselect("Charts", list(CFG.symbols), default=list(CFG.symbols))

if not database_ready():
    wait_for_database()
    st.stop()

overview()

conn = open_connection()
try:
    # A grid of at most CHARTS_PER_ROW charts per row; Streamlit stacks
    # columns on narrow screens.
    for i in range(0, len(shown), CHARTS_PER_ROW):
        row_symbols = shown[i:i + CHARTS_PER_ROW]
        for col, symbol in zip(st.columns(CHARTS_PER_ROW), row_symbols):
            with col:
                _, key, gap_in_display = render_symbol(conn, symbol, opts)
                status_panel(symbol, key, gap_in_display)
except Exception as exc:
    st.error(f"Could not read the local data: {exc}")
finally:
    conn.close()

page_status()
