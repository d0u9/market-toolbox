import datetime as dt

from charts import build_figure, line_segments, month_ticks, select_range
from services.calendar import MarketCalendar
from services.indicators import compute_indicators
from tests.fakes import synthetic_bars


def make_values(n_sessions=800, warmup=250):
    sessions = MarketCalendar("NYSE").sessions(dt.date(2022, 1, 1), dt.date(2025, 10, 16))
    values = compute_indicators(synthetic_bars(sessions[-n_sessions:], seed=11), warmup)
    values["quality_flag"] = 0
    return values


def test_category_axis_is_explicit_and_ordered():
    view = select_range(make_values(), "1Y")
    fig = build_figure(view, "TEST1", "candles", show_ema26=True, show_macd=True)
    expected = [d.isoformat() for d in view.index]
    assert fig.layout.hovermode == "x unified"
    assert fig.layout.hoversubplots == "axis"
    axis = fig.layout.xaxis
    assert axis.type == "category" and axis.categoryorder == "array"
    assert list(axis.categoryarray) == expected
    assert set(axis.tickvals) <= set(expected)
    # Every trace, including the MACD histogram, uses the single shared x axis.
    assert {t.xaxis for t in fig.data} == {"x"}
    assert any(t.type == "bar" and t.yaxis == "y2" for t in fig.data)
    # Three state candle traces; together they hold every day exactly once.
    candles = [t for t in fig.data if t.type == "candlestick"]
    assert len(candles) == 3
    xs = sorted(x for t in candles for x in t.x)
    assert xs == sorted(expected)


def test_candle_colors_follow_state_not_direction():
    view = select_range(make_values(), "3M")
    fig = build_figure(view, "TEST1", "candles")
    for t in (t for t in fig.data if t.type == "candlestick"):
        assert t.increasing.fillcolor == t.decreasing.fillcolor
        assert t.increasing.line.color == t.decreasing.line.color


def test_line_segments_share_boundaries():
    x = ["d1", "d2", "d3", "d4", "d5"]
    y = [1.0, 2.0, 3.0, 4.0, 5.0]
    states = ["green", "green", "red", "red", "green"]
    gx, gy = line_segments(x, y, states, "green")
    rx, ry = line_segments(x, y, states, "red")
    # Green: d1-d2, break, then the d4->d5 segment (starts at red's last point).
    assert gx == ["d1", "d2", "d2", "d4", "d5", "d5"]
    assert gy == [1.0, 2.0, None, 4.0, 5.0, None]
    # Red starts at the previous green point d2.
    assert rx == ["d2", "d3", "d4", "d4"] and ry == [2.0, 3.0, 4.0, None]


def test_line_view_builds_and_has_no_rangebreaks():
    view = select_range(make_values(), "6M")
    fig = build_figure(view, "TEST1", "line", show_macd=False)
    assert not fig.layout.xaxis.rangebreaks
    assert all(t.type != "bar" for t in fig.data)


def test_ranges_exclude_warmup_and_cap_at_three_years():
    values = make_values(n_sessions=600, warmup=250)
    view = select_range(values, "3Y")
    assert len(view) == 350 and (view["elder_state"] != "unavailable").all()
    assert len(select_range(values, "3M")) < 70


def test_month_ticks_thinning():
    days = [dt.date(2023, 1, 1) + dt.timedelta(days=i) for i in range(0, 3 * 365)]
    values, labels = month_ticks(days)
    assert len(values) <= 13 and len(values) == len(labels)
    assert month_ticks(days[:60])[0][0] == "2023-01-01"
    assert month_ticks(days[:60])[1][:2] == ["2023", "Feb"]
    # A range starting mid-month skips that partial month.
    assert month_ticks(days[20:80])[0][0] == "2023-02-01"


def test_histogram_colored_by_state_and_last_value_tags():
    from charts import STATE_COLORS

    view = select_range(make_values(), "6M")
    fig = build_figure(view, "TEST1", "candles", show_macd=True)
    bar = next(t for t in fig.data if t.type == "bar")
    assert list(bar.marker.color) == [STATE_COLORS[s] for s in view["elder_state"]]
    tags = [a for a in fig.layout.annotations if a.xref == "paper"]
    assert [a.text for a in tags] == [f"{view['close'].iloc[-1]:.2f}", f"{view['histogram'].iloc[-1]:.2f}"]
    assert fig.layout.yaxis.side == "right"


def test_gap_marker_drawn_when_flagged():
    values = make_values()
    view = select_range(values, "1Y").copy()
    view.iloc[50:150, view.columns.get_loc("quality_flag")] = 1
    fig = build_figure(view, "TEST1", "candles")
    assert any(a.text == "data gap" for a in fig.layout.annotations)
