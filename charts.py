"""Plotly figures for the Elder Impulse dashboard.

The x axis is a category axis of trading-day strings shared by every trace
and subplot, with an explicit ``categoryarray`` so the split per-state traces
cannot reorder it. Non-trading days take no space; no ``rangebreaks``.
"""

from __future__ import annotations

import datetime as dt
import math

import pandas as pd
import plotly.graph_objects as go

from services.indicators import BLUE, GREEN, RED, UNAVAILABLE

# Saturated state colors. Red and green are hard to tell apart for some
# color-vision deficiencies, so the state is always also given as text
# (header badge and hover), never by color alone.
STATE_COLORS = {GREEN: "#1f9d55", RED: "#e03131", BLUE: "#3b7ddd", UNAVAILABLE: "#868e96"}
STATE_DETAIL = {
    GREEN: "EMA13 up, histogram up",
    RED: "EMA13 down, histogram down",
    BLUE: "mixed or flat",
    UNAVAILABLE: "",
}
STATE_SHORT = {GREEN: "Green", RED: "Red", BLUE: "Blue", UNAVAILABLE: "n/a"}
EMA13_COLOR = "#e08a00"
EMA26_COLOR = "#7e4fc9"
GRID_COLOR = "#e9ecef"
AXIS_TEXT = "#495057"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace"

MAX_TICKS = 9
RANGES = {"3M": 3, "6M": 6, "1Y": 12, "3Y": 36}


def select_range(values: pd.DataFrame, range_key: str) -> pd.DataFrame:
    """Rows with a valid Elder state inside the selected range."""
    valid = values[values["elder_state"] != UNAVAILABLE]
    if valid.empty:
        return valid
    end = valid.index[-1]
    start = (pd.Timestamp(end) - pd.DateOffset(months=RANGES[range_key])).date()
    return valid[[d > start for d in valid.index]]


def month_ticks(dates: list[dt.date]) -> tuple[list[str], list[str]]:
    """Tick values and labels at the first trading day of each month.

    A partial first month is skipped so its tick does not collide with the
    next one; long ranges are thinned to at most MAX_TICKS labels. The year
    is shown on January ticks only.
    """
    firsts, seen = [], set()
    for i, d in enumerate(dates):
        key = (d.year, d.month)
        if key not in seen:
            seen.add(key)
            if i == 0 and d.day > 7:
                continue
            firsts.append(d)
    step = max(1, math.ceil(len(firsts) / MAX_TICKS))
    picked = firsts[::step]
    labels = [d.strftime("%Y") if d.month == 1 else d.strftime("%b") for d in picked]
    return [d.isoformat() for d in picked], labels


def line_segments(x: list[str], y: list[float], states: list[str], state: str) -> tuple[list, list]:
    """Points for one state's line trace, broken with ``None`` between runs.

    The segment from the previous day to day t takes day t's color, so each
    run starts at the previous day's point (a shared boundary point).
    """
    xs: list = []
    ys: list = []
    i, n = 0, len(x)
    while i < n:
        if states[i] != state:
            i += 1
            continue
        j = i
        while j + 1 < n and states[j + 1] == state:
            j += 1
        start = i - 1 if i > 0 else i
        xs.extend(x[start : j + 1])
        ys.extend(y[start : j + 1])
        xs.append(x[j])
        ys.append(None)
        i = j + 1
    return xs, ys


def build_figure(
    view: pd.DataFrame,
    symbol: str,
    chart_type: str = "candles",
    show_ema26: bool = False,
    show_macd: bool = True,
) -> go.Figure:
    x = [d.isoformat() for d in view.index]
    states = list(view["elder_state"])
    # Both panels share one x axis (stacked y axes) so a single unified hover
    # box covers price, EMA, state and the MACD histogram.
    fig = go.Figure()
    price = dict(xaxis="x", yaxis="y")

    for state in (GREEN, RED, BLUE):
        color = STATE_COLORS[state]
        if chart_type == "candles":
            sub = view[view["elder_state"] == state]
            fig.add_trace(go.Candlestick(
                x=[d.isoformat() for d in sub.index],
                open=sub["open"], high=sub["high"], low=sub["low"], close=sub["close"],
                increasing=dict(line=dict(color=color, width=1), fillcolor=color),
                decreasing=dict(line=dict(color=color, width=1), fillcolor=color),
                whiskerwidth=0, name=STATE_SHORT[state], hoverinfo="skip", **price,
            ))
        else:
            xs, ys = line_segments(x, list(view["close"]), states, state)
            fig.add_trace(go.Scatter(
                x=xs, y=ys, mode="lines", line=dict(color=color, width=2),
                name=STATE_SHORT[state], hoverinfo="skip", connectgaps=False, **price,
            ))

    fig.add_trace(go.Scatter(
        x=x, y=view["ema13"], mode="lines", line=dict(color=EMA13_COLOR, width=2),
        name="EMA13", hovertemplate="EMA13 %{y:.2f}<extra></extra>", **price,
    ))
    if show_ema26:
        fig.add_trace(go.Scatter(
            x=x, y=view["ema26"], mode="lines", line=dict(color=EMA26_COLOR, width=2),
            name="EMA26", hovertemplate="EMA26 %{y:.2f}<extra></extra>", **price,
        ))

    # One invisible trace carries OHLC and the state label in the hover box.
    custom = list(zip(view["open"], view["high"], view["low"], view["close"],
                      [STATE_SHORT[s] for s in states]))
    fig.add_trace(go.Scatter(
        x=x, y=view["close"], mode="markers", marker=dict(size=8, opacity=0),
        customdata=custom, showlegend=False, name="",
        hovertemplate=("O %{customdata[0]:.2f}  H %{customdata[1]:.2f}  "
                       "L %{customdata[2]:.2f}  C %{customdata[3]:.2f}<br>"
                       "Elder: %{customdata[4]}<extra></extra>"),
        **price,
    ))

    last_state = states[-1]
    tags = [(float(view["close"].iloc[-1]), "y", ".2f")]
    if show_macd:
        fig.add_trace(go.Bar(
            x=x, y=view["histogram"], marker=dict(color=[STATE_COLORS[s] for s in states],
                                                  line=dict(width=0)),
            name="MACD histogram", hovertemplate="Histogram %{y:.3f}<extra></extra>",
            xaxis="x", yaxis="y2",
        ))
        tags.append((float(view["histogram"].iloc[-1]), "y2", ".3f"))

    # Last-value tags on the right-hand price axis, in the latest state's color.
    for value, yref, fmt in tags:
        fig.add_annotation(
            x=1, xref="paper", xanchor="left", y=value, yref=yref, showarrow=False,
            text=format(value, fmt), bgcolor=STATE_COLORS[last_state], borderpad=3,
            font=dict(color="#ffffff", family=MONO, size=12),
        )

    flagged = view["quality_flag"].astype(bool)
    starts = [x[i] for i in range(len(x)) if flagged.iloc[i] and (i == 0 or not flagged.iloc[i - 1])]
    for gx in starts:
        fig.add_shape(type="line", xref="x", yref="paper", x0=gx, x1=gx, y0=0, y1=1,
                      line=dict(color="#b8860b", width=1, dash="dash"))
        fig.add_annotation(x=gx, y=0.99, xref="x", yref="paper", text="data gap", showarrow=False,
                           font=dict(size=10, color="#b8860b"), yanchor="top", xanchor="left")

    tickvals, ticktext = month_ticks(list(view.index))
    axis_font = dict(family=MONO, size=12, color=AXIS_TEXT)
    y_common = dict(side="right", gridcolor=GRID_COLOR, zeroline=False, tickformat=".2f",
                    tickfont=axis_font, ticks="", showline=False)
    fig.update_layout(
        xaxis=dict(type="category", categoryorder="array", categoryarray=x,
                   tickvals=tickvals, ticktext=ticktext, tickangle=0,
                   showgrid=True, gridcolor=GRID_COLOR, tickfont=axis_font,
                   rangeslider=dict(visible=False), anchor="y2" if show_macd else "y",
                   showspikes=True, spikemode="across", spikethickness=1,
                   spikecolor="#adb5bd", spikedash="dot"),
        yaxis=dict(domain=[0.27, 1.0] if show_macd else [0.0, 1.0], **y_common),
        hovermode="x unified",
        hoversubplots="axis",
        hoverlabel=dict(font=dict(family=MONO, size=12)),
        showlegend=False,
        margin=dict(l=8, r=78, t=8, b=8),
        height=560 if show_macd else 460,
        plot_bgcolor="#ffffff",
        paper_bgcolor="#ffffff",
        bargap=0.25,
    )
    if show_macd:
        fig.update_layout(yaxis2=dict(domain=[0.0, 0.22], zerolinecolor="#ced4da",
                                      **{**y_common, "zeroline": True}))
    return fig
