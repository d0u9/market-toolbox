"""Elder Impulse chart, drawn with TradingView Lightweight Charts™ (vendored).

``chart_payload`` turns indicator rows into plain JSON-ready data (pure, tested
without a browser); ``chart_html`` wraps it into a page for ``st.iframe`` that
loads the vendored library from ``LIBRARY_PATH`` (served from ./static, so the
browser caches it and each redraw only sends the data). Bars are placed by
trading-day order, so non-trading days take no space.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

from services.indicators import BLUE, GREEN, RED, UNAVAILABLE

# Saturated state colors. Red and green are hard to tell apart for some
# color-vision deficiencies, so the state is always also given as text
# (card, chart legend), never by color alone.
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
GAP_COLOR = "#b8860b"

RANGES = {"3M": 3, "6M": 6, "1Y": 12, "3Y": 36}
# Location under the Streamlit app's static root (./static).
LIBRARY_PATH = "lightweight-charts/lightweight-charts.standalone.production.js"
LIBRARY_FILE = Path(__file__).resolve().parent / "static" / LIBRARY_PATH


def number_precision(values, minimum: int = 2) -> int:
    """Keep small nonzero quote values visible without assuming cents."""
    nonzero = [abs(float(value)) for value in values if float(value) != 0 and math.isfinite(float(value))]
    if not nonzero:
        return minimum
    return max(minimum, min(10, 2 - math.floor(math.log10(min(nonzero)))))


def select_range(values: pd.DataFrame, range_key: str) -> pd.DataFrame:
    """Rows with a valid Elder state inside the selected range."""
    valid = values[values["elder_state"] != UNAVAILABLE]
    if valid.empty:
        return valid
    end = valid.index[-1]
    start = (pd.Timestamp(end) - pd.DateOffset(months=RANGES[range_key])).date()
    return valid[[d > start for d in valid.index]]


def chart_payload(values: pd.DataFrame, range_key: str, chart_type: str = "candles",
                  show_ema26: bool = False, show_macd: bool = True) -> dict:
    """Data for the chart page.

    All valid bars of the longest range are included so the user can scroll
    back; ``visible_from`` opens the chart on the selected range.
    """
    rows = select_range(values, "3Y")
    shown = select_range(values, range_key)
    times = [d.isoformat() for d in rows.index]
    colors = [STATE_COLORS[s] for s in rows["elder_state"]]
    flags = rows["quality_flag"].astype(bool).tolist()

    def series(column: str) -> list[dict]:
        return [{"time": t, "value": float(v)} for t, v in zip(times, rows[column])]

    payload = {
        "price_precision": number_precision(rows["low"]),
        "histogram_precision": number_precision(rows["histogram"], 3),
        "chart_type": chart_type,
        "show_ema26": show_ema26,
        "show_macd": show_macd,
        "visible_from": shown.index[0].isoformat() if len(shown) else None,
        "ema13": series("ema13"),
        "ema26": series("ema26") if show_ema26 else [],
        "histogram": [dict(p, color=c) for p, c in zip(series("histogram"), colors)] if show_macd else [],
        # Start of each run of gap-affected bars, marked on the chart.
        "gaps": [t for i, t in enumerate(times) if flags[i] and (i == 0 or not flags[i - 1])],
        "states": {t: STATE_SHORT[s] for t, s in zip(times, rows["elder_state"])},
        "state_colors": {STATE_SHORT[s]: c for s, c in STATE_COLORS.items() if s != UNAVAILABLE},
    }
    if chart_type == "line":
        # Lightweight Charts paints a line segment with the color of its start
        # point. The design colors the move from day t-1 to day t with day t's
        # state, so each point carries the next day's color (the last keeps its own).
        segment_colors = colors[1:] + colors[-1:]
        payload["price"] = [dict(p, color=c) for p, c in zip(series("close"), segment_colors)]
    else:
        payload["price"] = [
            {"time": t, "open": float(o), "high": float(h),
             "low": float(lo), "close": float(c),
             "color": col, "borderColor": col, "wickColor": col}
            for t, o, h, lo, c, col in zip(times, rows["open"], rows["high"], rows["low"],
                                           rows["close"], colors)
        ]
    return payload


_PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<style>
  html, body { margin: 0; height: 100%; background: #fff;
    font-family: Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }
  /* The header sits above the chart in normal flow and wraps on narrow widths,
     so the legend and the color key never overlap each other or the bars. */
  body { display: flex; flex-direction: column; }
  #top { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: baseline;
    gap: 2px 16px; padding: 6px 12px 4px; }
  #chart { position: relative; flex: 1; min-height: 0; }
  #legend { font-size: 12px; color: #52525b; line-height: 1.5; font-variant-numeric: tabular-nums; }
  #legend b { color: #09090b; font-weight: 600; }
  #key { font-size: 11px; color: #71717a; line-height: 1.5; }
  .hint { margin-left: 12px; color: #a1a1aa; }
  @media (hover: none) { .hint { display: none; } }
  .sw { display: inline-block; width: 8px; height: 8px; border-radius: 2px; margin: 0 3px 0 8px; }
</style></head>
<body><div id="top"><div id="legend"></div><div id="key"></div></div><div id="chart"></div>
<script src="__LIBRARY_URL__"></script>
<script>
const P = __PAYLOAD__;
const LC = LightweightCharts;
const chart = LC.createChart(document.getElementById("chart"), {
  autoSize: true,
  layout: { background: { type: "solid", color: "#ffffff" }, textColor: "#52525b", fontSize: 11,
            fontFamily: getComputedStyle(document.body).fontFamily, attributionLogo: true,
            panes: { separatorColor: "#e4e4e7", separatorHoverColor: "#f4f4f5" } },
  grid: { vertLines: { color: "#f4f4f5" }, horzLines: { color: "#f4f4f5" } },
  rightPriceScale: { borderColor: "#e4e4e7" },
  timeScale: { borderColor: "#e4e4e7", rightOffset: 4, minBarSpacing: 1 },
  crosshair: { mode: LC.CrosshairMode.Normal },
});
const price = P.chart_type === "line"
  ? chart.addSeries(LC.LineSeries, { lineWidth: 2, priceLineVisible: true })
  : chart.addSeries(LC.CandlestickSeries, {});
const priceFormat = { type: "price", precision: P.price_precision, minMove: 10 ** -P.price_precision };
price.applyOptions({ priceFormat });
price.setData(P.price);
const ema13 = chart.addSeries(LC.LineSeries, { color: "__EMA13__", lineWidth: 2,
  priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, priceFormat });
ema13.setData(P.ema13);
let ema26 = null;
if (P.show_ema26) {
  ema26 = chart.addSeries(LC.LineSeries, { color: "__EMA26__", lineWidth: 2,
    priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, priceFormat });
  ema26.setData(P.ema26);
}
let hist = null;
if (P.show_macd) {
  hist = chart.addSeries(LC.HistogramSeries, { priceLineVisible: false,
    priceFormat: { type: "price", precision: P.histogram_precision, minMove: 10 ** -P.histogram_precision } }, 1);
  hist.setData(P.histogram);
  chart.panes()[0].setStretchFactor(3);
  chart.panes()[1].setStretchFactor(1);
}
if (P.gaps.length) {
  LC.createSeriesMarkers(price, P.gaps.map(t => ({ time: t, position: "aboveBar",
    color: "__GAP__", shape: "arrowDown", text: "data gap" })));
}
if (P.visible_from) {
  chart.timeScale().setVisibleRange({ from: P.visible_from, to: P.price[P.price.length - 1].time });
}

// A plain mouse wheel scrolls the page; zoom and pan by wheel need Ctrl or ⌘
// (a trackpad pinch arrives as Ctrl + wheel). Stopping the event in the capture
// phase keeps it from the chart without preventDefault, so the scroll reaches
// the parent page. Drag to pan and touch gestures are unchanged.
window.addEventListener("wheel", e => {
  if (!e.ctrlKey && !e.metaKey) e.stopPropagation();
}, { capture: true });

// Static color key and a legend that follows the crosshair (last bar by default).
const key = document.getElementById("key");
key.append("Color = Elder state");
for (const [name, color] of Object.entries(P.state_colors)) {
  const sw = document.createElement("span"); sw.className = "sw"; sw.style.background = color;
  key.append(sw, name);
}
const hint = document.createElement("span"); hint.className = "hint";
hint.textContent = "Ctrl/⌘ + scroll to zoom";
key.append(hint);
const byTime = {};
P.price.forEach((p, i) => { byTime[p.time] = i; });
const fmt = (v, d) => v === undefined ? "–" : v.toFixed(d);
const legend = document.getElementById("legend");
function timeKey(t) {
  if (typeof t === "string") return t;
  if (t && t.year) return `${t.year}-${String(t.month).padStart(2, "0")}-${String(t.day).padStart(2, "0")}`;
  return null;
}
function showLegend(t) {
  const i = byTime[t] ?? P.price.length - 1;
  const p = P.price[i], time = p.time;
  const parts = [["", time]];
  if (P.chart_type === "line") parts.push(["C", fmt(p.value, P.price_precision)]);
  else parts.push(["O", fmt(p.open, P.price_precision)], ["H", fmt(p.high, P.price_precision)], ["L", fmt(p.low, P.price_precision)], ["C", fmt(p.close, P.price_precision)]);
  parts.push(["EMA13", fmt(P.ema13[i]?.value, P.price_precision)]);
  if (P.show_ema26) parts.push(["EMA26", fmt(P.ema26[i]?.value, P.price_precision)]);
  if (P.show_macd) parts.push(["Hist", fmt(P.histogram[i]?.value, P.histogram_precision)]);
  parts.push(["Elder", P.states[time]]);
  legend.replaceChildren();
  parts.forEach(([label, value], n) => {
    if (n) legend.append("  ");
    if (label) legend.append(label + " ");
    const b = document.createElement("b"); b.textContent = value; legend.append(b);
  });
}
chart.subscribeCrosshairMove(param => showLegend(timeKey(param.time)));
showLegend(null);
</script></body></html>
"""


def chart_html(payload: dict, library_url: str) -> str:
    """The chart page; the JSON is escaped so it cannot close the script tag."""
    data = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    return (_PAGE.replace("__EMA13__", EMA13_COLOR).replace("__EMA26__", EMA26_COLOR)
            .replace("__GAP__", GAP_COLOR).replace("__LIBRARY_URL__", library_url)
            .replace("__PAYLOAD__", data))
