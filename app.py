"""Streamlit dashboard. Reads the local cache only; never calls a data provider."""

from __future__ import annotations

import datetime as dt
import html

import pandas as pd
import streamlit as st

import storage
from charts import RANGES, STATE_COLORS, STATE_SHORT, build_figure, select_range
from config import ConfigError, load_config
from services.validation import QUALITY_COMPLETE, QUALITY_GAP, QUALITY_INSUFFICIENT

STATUS_TEXT = {
    "updated": "Updated",
    "waiting": "Waiting for data",
    "failed": "Update failed",
    "initializing": "Initializing",
}
QUALITY_TEXT = {
    QUALITY_COMPLETE: "Complete",
    QUALITY_GAP: "History gap",
    QUALITY_INSUFFICIENT: "Insufficient history",
}


try:
    CFG = load_config()
except ConfigError as exc:
    st.set_page_config(page_title="Elder Impulse", layout="wide")
    st.error(f"Configuration error: {exc}")
    st.stop()

TITLE = f"{' / '.join(CFG.symbols)} Elder Impulse"
st.set_page_config(page_title=TITLE, layout="wide")


def fmt_ts(value: str | None) -> str:
    ts = storage.parse_ts(value)
    if ts is None:
        return "—"
    return ts.astimezone(CFG.display_tz).strftime("%Y-%m-%d %H:%M %Z")


def open_connection():
    # A fresh read-only connection per use: Streamlit runs sessions on several
    # threads, and a shared connection cannot hold independent transactions.
    return storage.connect_ro(CFG.sqlite_path)


@st.cache_data(max_entries=8, show_spinner=False)
def load_values(snapshot_id: int) -> pd.DataFrame:
    # Snapshot rows are immutable, so the snapshot id is a complete cache key.
    conn = open_connection()
    try:
        return storage.read_values(conn, snapshot_id)
    finally:
        conn.close()


@st.cache_data(max_entries=32, show_spinner=False)
def cached_figure(snapshot_id: int, symbol: str, range_key: str, chart_type: str,
                  show_ema26: bool, show_macd: bool):
    view = select_range(load_values(snapshot_id), range_key)
    return build_figure(view, symbol, chart_type, show_ema26, show_macd)


def quality_line(state: dict, gap_in_display: bool) -> str:
    quality = state["quality"]
    if quality == QUALITY_GAP and not gap_in_display:
        # The affected bars have scrolled out of every display range.
        return "Data quality: **Complete** (an older gap is kept in the run records)"
    text = f"Data quality: **{QUALITY_TEXT.get(quality, quality)}**"
    if state.get("quality_message"):
        text += f" — {state['quality_message']}"
    return text


CARD_CSS = """
<style>
.elder-head {display:flex; flex-wrap:wrap; align-items:center; gap:0.5rem 0.9rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; color:#495057;}
.elder-head .sym {font-size:1.6rem; font-weight:700; color:#212529;}
.elder-head .pill {color:#fff; font-weight:600; border-radius:999px; padding:0.15rem 0.75rem;
  font-family: inherit;}
.elder-head .kv b {color:#212529; font-weight:600;}
.elder-head .dot {display:inline-block; width:0.6rem; height:0.6rem; border-radius:2px;
  margin-right:0.25rem; vertical-align:baseline;}
.elder-sub {font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size:0.85rem; color:#6c757d; margin-top:0.2rem;}
</style>
"""
PILL_TEXT = {"green": "Green · EMA13 ↑ hist ↑", "red": "Red · EMA13 ↓ hist ↓",
             "blue": "Blue · mixed", "unavailable": "n/a"}


def header_html(symbol: str, values: pd.DataFrame, show_ema26: bool) -> str:
    last = values.iloc[-1]
    prev = values.iloc[-2] if len(values) > 1 else last
    state, prev_state = last["elder_state"], prev["elder_state"]
    day = (last["close"] / prev["close"] - 1) * 100
    ema26 = f"<span class='kv'>EMA26 <b>{last['ema26']:.2f}</b></span>" if show_ema26 else ""
    return (
        "<div class='elder-head'>"
        f"<span class='sym'>{html.escape(symbol)}</span>"
        f"<span class='pill' style='background:{STATE_COLORS[state]}'>{PILL_TEXT[state]}</span>"
        f"<span class='kv'>Close <b>{last['close']:.2f}</b> ({day:+.2f}%)</span>"
        f"<span class='kv'>EMA13 <b>{last['ema13']:.2f}</b> ({last['ema13'] - prev['ema13']:+.2f})</span>"
        f"{ema26}"
        f"<span class='kv'>Hist <b>{last['histogram']:.3f}</b> "
        f"({last['histogram'] - prev['histogram']:+.3f})</span>"
        f"<span class='kv'>Prev day <span class='dot' style='background:{STATE_COLORS[prev_state]}'>"
        f"</span>{STATE_SHORT[prev_state]}</span>"
        "</div>"
        f"<div class='elder-sub'>{values.index[-1].isoformat()} · adjusted prices · "
        "candle and bar colors show the Elder state, not up/down days</div>"
    )


def render_symbol(conn, symbol: str, opts: dict) -> str | None:
    state, snapshot = storage.read_symbol(conn, symbol)
    if state is None or snapshot is None:
        st.subheader(symbol)
        st.info("Initializing: no data published yet. The page refreshes automatically.")
        if state and state.get("status_detail"):
            st.caption(state["status_detail"])
        return None

    values = load_values(snapshot["id"])
    if values.empty:  # snapshot replaced between reads; next refresh catches up
        load_values.clear()
        st.info("Refreshing data…")
        return None
    st.markdown(header_html(symbol, values, opts["ema26"]), unsafe_allow_html=True)

    view = select_range(values, opts["range"])
    gap_in_display = bool(select_range(values, "3Y")["quality_flag"].astype(bool).any())
    if len(view) < 2:
        st.warning("Insufficient history: indicators are not shown.")
    else:
        if view["quality_flag"].astype(bool).any():
            st.warning("Data has a gap; indicators may be affected.")
        fig = cached_figure(snapshot["id"], symbol, opts["range"], opts["chart"],
                            opts["ema26"], opts["macd"])
        st.plotly_chart(fig, use_container_width=True, key=f"chart-{symbol}")

    status = state["status"]
    lines = [
        f"Update status: **{STATUS_TEXT.get(status, status)}**"
        + (f" — {state['status_detail']}" if state.get("status_detail") else ""),
        quality_line(state, gap_in_display),
        f"Target session: {state['target_session'] or '—'} · latest session: {snapshot['latest_session']}",
        f"Content updated: {fmt_ts(state['content_updated_at'])} · "
        f"last successful check: {fmt_ts(state['last_checked_at'])}",
        f"Source: {snapshot['source']} · adjusted prices ({snapshot['adjustment']}); "
        "values are not historical traded prices",
    ]
    if status != "updated":
        if status == "failed":
            st.error(lines[0])
        else:
            st.warning(lines[0])
        lines = lines[1:]
    st.caption("  \n".join(lines))
    return snapshot["latest_session"]


st.title(TITLE)
st.caption(f"Times shown in {CFG.display_timezone}. Trading dates are US/Eastern market dates.")

with st.sidebar:
    opts = {
        "range": st.radio("Range", list(RANGES), index=2, horizontal=True),
        "chart": st.radio("Chart", ["candles", "line"],
                          format_func={"candles": "Candles", "line": "Close line"}.get),
        "ema26": st.checkbox("Show EMA26", value=False),
        "macd": st.checkbox("Show MACD histogram", value=True),
    }


@st.fragment(run_every=60)
def dashboard() -> None:
    try:
        conn = open_connection()
    except FileNotFoundError:
        st.info("Initializing: waiting for the worker to create the database.")
        return
    except Exception as exc:  # e.g. locked during first initialization
        st.warning(f"Database not ready yet: {exc}")
        return
    try:
        render_all(conn)
    except Exception as exc:
        if "no such table" in str(exc):
            st.info("Initializing: waiting for the worker to create the database schema.")
        else:
            st.error(f"Could not read the local data: {exc}")
    finally:
        conn.close()


def render_all(conn) -> None:
    st.markdown(CARD_CSS, unsafe_allow_html=True)
    cols = st.columns(len(CFG.symbols))
    dates = []
    for col, symbol in zip(cols, CFG.symbols):
        with col, st.container(border=True):
            dates.append(render_symbol(conn, symbol, opts))
    known = {d for d in dates if d}
    if len(known) > 1:
        st.warning("Data dates differ between symbols: "
                   + ", ".join(f"{s} {d}" for s, d in zip(CFG.symbols, dates) if d))
    st.caption(f"Page checked {dt.datetime.now(CFG.display_tz).strftime('%Y-%m-%d %H:%M:%S %Z')}")


dashboard()
