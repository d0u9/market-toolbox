import datetime as dt
import json

from charts import STATE_COLORS, STATE_SHORT, chart_html, chart_payload, select_range
from services.calendar import MarketCalendar
from services.indicators import compute_indicators
from tests.fakes import synthetic_bars


def make_values(n_sessions=800, warmup=250):
    sessions = MarketCalendar("NYSE").sessions(dt.date(2022, 1, 1), dt.date(2025, 10, 16))
    values = compute_indicators(synthetic_bars(sessions[-n_sessions:], seed=11), warmup)
    values["quality_flag"] = 0
    return values


def test_candles_carry_state_colors_in_trading_day_order():
    values = make_values()
    payload = chart_payload(values, "1Y")
    rows = select_range(values, "3Y")
    times = [p["time"] for p in payload["price"]]
    assert times == [d.isoformat() for d in rows.index] == sorted(times)
    for p, state in zip(payload["price"], rows["elder_state"]):
        assert p["color"] == p["borderColor"] == p["wickColor"] == STATE_COLORS[state]
    assert all(payload["states"][t] == STATE_SHORT[s] for t, s in zip(times, rows["elder_state"]))


def test_visible_range_opens_on_selected_period():
    values = make_values()
    for key in ("3M", "6M", "1Y", "3Y"):
        assert chart_payload(values, key)["visible_from"] == select_range(values, key).index[0].isoformat()


def test_warmup_bars_are_never_drawn():
    values = make_values(n_sessions=600, warmup=250)
    payload = chart_payload(values, "3Y")
    assert len(payload["price"]) == 350


def test_line_segment_into_a_day_has_that_days_color():
    # Lightweight Charts colors a segment by its start point, so point i
    # carries day i+1's color; the last point keeps its own.
    values = make_values()
    payload = chart_payload(values, "1Y", chart_type="line")
    states = list(select_range(values, "3Y")["elder_state"])
    colors = [p["color"] for p in payload["price"]]
    assert colors[:-1] == [STATE_COLORS[s] for s in states[1:]]
    assert colors[-1] == STATE_COLORS[states[-1]]
    assert set(payload["price"][0]) == {"time", "value", "color"}


def test_optional_series():
    values = make_values()
    off = chart_payload(values, "1Y", show_ema26=False, show_macd=False)
    assert off["ema26"] == [] and off["histogram"] == []
    on = chart_payload(values, "1Y", show_ema26=True, show_macd=True)
    rows = select_range(values, "3Y")
    assert len(on["ema26"]) == len(on["histogram"]) == len(rows)
    assert [h["color"] for h in on["histogram"]] == [STATE_COLORS[s] for s in rows["elder_state"]]


def test_gap_markers_at_start_of_flagged_runs():
    values = make_values()
    rows = select_range(values, "3Y").index
    values.loc[rows[50:150], "quality_flag"] = 1
    assert chart_payload(values, "1Y")["gaps"] == [rows[50].isoformat()]


def test_html_is_self_contained_and_escapes_payload():
    payload = chart_payload(make_values(), "1Y")
    payload["states"]["x"] = "</script><script>alert(1)</script>"
    page = chart_html(payload)
    assert "TradingView Lightweight Charts" in page  # vendored library inlined
    assert "attributionLogo: true" in page
    assert "http://" not in page.split("<script>", 1)[0]  # no external stylesheet or script
    assert "</script><script>alert" not in page
    data = page.split("const P = ", 1)[1].split(";\nconst LC", 1)[0]
    assert json.loads(data.replace("<\\/", "</"))["states"]["x"].startswith("</script>")
