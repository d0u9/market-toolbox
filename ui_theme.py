"""Page styling and the HTML header blocks for the Streamlit app.

All dynamic text placed in HTML here is escaped; numbers are formatted here.
"""

from __future__ import annotations

import html

UP = "#1f9d55"
DOWN = "#e03131"

PAGE_CSS = """
<style>
:root {
  --card-bg: #ffffff;
  --card-border: rgba(15, 23, 42, 0.06);
  --shadow: 0 1px 2px rgba(15, 23, 42, 0.04), 0 8px 24px rgba(15, 23, 42, 0.06);
  --shadow-hover: 0 2px 4px rgba(15, 23, 42, 0.06), 0 14px 36px rgba(15, 23, 42, 0.10);
  --ink: #0f172a;
  --muted: #64748b;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
}
.stApp { background: linear-gradient(180deg, #f6f8fb 0%, #eef2f7 100%); }
.block-container { padding-top: 3.4rem; max-width: 1200px; }

@keyframes rise { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
@keyframes pulse { 0%, 100% { box-shadow: 0 0 0 0 var(--pulse); } 50% { box-shadow: 0 0 0 6px transparent; } }

/* Title */
.ei-title { display: flex; align-items: baseline; gap: .8rem; margin-bottom: .2rem; animation: rise .45s ease both; }
.ei-title .t { font-size: 2.1rem; line-height: 1.15; font-weight: 800; letter-spacing: -0.02em; margin: 0; padding: 0;
  background: linear-gradient(90deg, #0f172a, #334155 60%, #1f9d55); -webkit-background-clip: text;
  background-clip: text; color: transparent; }
.ei-title span { color: var(--muted); font-size: .9rem; }

/* Cards: custom HTML cards and Streamlit bordered containers share one look */
/* Streamlit containers created with key="card-..." get the class st-key-card-... */
.ei-card, div[class*="st-key-card"] {
  background: var(--card-bg); border: 1px solid var(--card-border) !important; border-radius: 16px;
  box-shadow: var(--shadow); transition: box-shadow .25s ease, transform .25s ease;
  animation: rise .5s ease both;
}
div[class*="st-key-card"] { padding: 1rem 1.1rem; }
.ei-card:hover, div[class*="st-key-card"]:hover { box-shadow: var(--shadow-hover); }

/* Hero header */
.ei-hero { position: relative; overflow: hidden; padding: 1.3rem 1.5rem 1.2rem 1.7rem; margin-bottom: 1rem; }
.ei-hero::before { content: ""; position: absolute; inset: 0 auto 0 0; width: 6px; background: var(--state); }
.ei-hero::after { content: ""; position: absolute; right: -60px; top: -60px; width: 220px; height: 220px;
  border-radius: 50%; background: radial-gradient(circle, var(--state-soft), transparent 70%); }
.ei-hero .row { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: .8rem; }
.ei-hero .sym { font-size: 2rem; font-weight: 800; color: var(--ink); letter-spacing: -0.02em; line-height: 1; }
.ei-hero .date { color: var(--muted); font-size: .85rem; margin-top: .35rem; }
.ei-hero .price { text-align: right; }
.ei-hero .close { font-family: var(--mono); font-size: 1.9rem; font-weight: 700; color: var(--ink); line-height: 1; }
.ei-hero .chg { font-family: var(--mono); font-weight: 600; font-size: .95rem; margin-top: .3rem; }
.ei-hero .state { display: flex; align-items: center; gap: .55rem; margin-top: 1rem; color: var(--ink); font-weight: 600; }
.ei-hero .state small { color: var(--muted); font-weight: 500; }
.ei-dot { width: .7rem; height: .7rem; border-radius: 50%; background: var(--state); --pulse: var(--state-soft);
  animation: pulse 2.4s ease-in-out infinite; }

/* Stat tiles */
.ei-tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: .8rem; margin-bottom: 1rem; }
.ei-tile { padding: .9rem 1.1rem; }
.ei-tile:hover { transform: translateY(-2px); }
.ei-tile .label { color: var(--muted); font-size: .75rem; text-transform: uppercase; letter-spacing: .06em; }
.ei-tile .value { font-family: var(--mono); font-size: 1.35rem; font-weight: 700; color: var(--ink); margin-top: .25rem; }
.ei-tile .delta { font-family: var(--mono); font-size: .8rem; font-weight: 600; margin-top: .15rem; }
.ei-tiles .ei-tile:nth-child(2) { animation-delay: .05s; }
.ei-tiles .ei-tile:nth-child(3) { animation-delay: .1s; }

/* Sidebar */
section[data-testid="stSidebar"] { background: linear-gradient(180deg, #0f172a 0%, #1e293b 100%); }
section[data-testid="stSidebar"] * { color: #e2e8f0; }
section[data-testid="stSidebar"] label[data-baseweb="radio"] { border-radius: 10px; padding: .25rem .5rem;
  transition: background .2s ease; }
section[data-testid="stSidebar"] label[data-baseweb="radio"]:hover { background: rgba(255, 255, 255, 0.06); }
section[data-testid="stSidebar"] label[data-baseweb="radio"]:has(input:checked) { background: rgba(31, 157, 85, 0.22);
  box-shadow: inset 3px 0 0 #1f9d55; }
section[data-testid="stSidebar"] div[data-baseweb="select"] * { color: #0f172a; }

/* Chart and alerts */
div[data-testid="stPlotlyChart"] { animation: rise .55s ease both; animation-delay: .12s; }
div[data-testid="stAlert"] { border-radius: 12px; }

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; }
}
</style>
"""


def _soft(hex_color: str, alpha: float) -> str:
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r}, {g}, {b}, {alpha})"


def _signed(value: float, fmt: str) -> tuple[str, str]:
    color = UP if value > 0 else DOWN if value < 0 else "#64748b"
    return format(value, "+" + fmt), color


def title_html(title: str, subtitle: str) -> str:
    return (f"<div class='ei-title'><div class='t'>{html.escape(title)}</div>"
            f"<span>{html.escape(subtitle)}</span></div>")


def hero_html(symbol: str, date: str, close: float, change_pct: float,
              state_color: str, state_label: str, state_detail: str) -> str:
    chg, chg_color = _signed(change_pct, ".2f")
    return (
        f"<div class='ei-card ei-hero' style='--state:{state_color};--state-soft:{_soft(state_color, .16)}'>"
        "<div class='row'>"
        f"<div><div class='sym'>{html.escape(symbol)}</div>"
        f"<div class='date'>{html.escape(date)} · adjusted prices</div></div>"
        f"<div class='price'><div class='close'>{close:,.2f}</div>"
        f"<div class='chg' style='color:{chg_color}'>{chg}%</div></div>"
        "</div>"
        f"<div class='state'><span class='ei-dot'></span>Elder state: {html.escape(state_label)}"
        f"<small>{html.escape(state_detail)}</small></div>"
        "</div>"
    )


def tiles_html(tiles: list[tuple[str, float, float, str]]) -> str:
    """Stat tiles from (label, value, change, number format)."""
    parts = []
    for label, value, delta, fmt in tiles:
        d, d_color = _signed(delta, fmt)
        parts.append(
            f"<div class='ei-card ei-tile'><div class='label'>{html.escape(label)}</div>"
            f"<div class='value'>{format(value, fmt)}</div>"
            f"<div class='delta' style='color:{d_color}'>{d} vs prev. day</div></div>"
        )
    return "<div class='ei-tiles'>" + "".join(parts) + "</div>"
