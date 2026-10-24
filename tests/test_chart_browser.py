"""Runs the chart page in headless Chromium (the JS is otherwise untested).

Skipped when Playwright or its Chromium is not installed. No network: the
library is loaded from the vendored file.
"""

import datetime as dt

import pytest

from charts import LIBRARY_FILE, chart_html, chart_payload, select_range
from services.calendar import MarketCalendar
from services.indicators import compute_indicators
from tests.fakes import synthetic_bars

sync_api = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # browser binaries missing
            pytest.skip(f"Chromium not available: {exc}")
        yield b
        b.close()


def values_with_gap():
    sessions = MarketCalendar("NYSE").sessions(dt.date(2022, 1, 1), dt.date(2025, 10, 16))
    values = compute_indicators(synthetic_bars(sessions[-800:], seed=11), 250)
    values["quality_flag"] = 0
    rows = select_range(values, "3Y").index
    values.loc[rows[100:200], "quality_flag"] = 1
    return values


@pytest.mark.parametrize("chart_type,ema26,macd", [
    ("candles", False, True), ("candles", True, False), ("line", True, True),
])
def test_chart_page_renders_without_errors(browser, tmp_path, chart_type, ema26, macd):
    payload = chart_payload(values_with_gap(), "6M", chart_type, ema26, macd)
    page_file = tmp_path / "chart.html"
    page_file.write_text(chart_html(payload, LIBRARY_FILE.as_uri()), encoding="utf-8")

    page = browser.new_page(viewport={"width": 900, "height": 560})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.goto(page_file.as_uri())
    page.wait_for_function("document.querySelectorAll('canvas').length > 0")
    page.mouse.move(450, 200)  # crosshair updates the legend
    page.wait_for_timeout(200)
    legend = page.inner_text("#legend")
    panes = page.evaluate("document.querySelectorAll('table tr').length")
    page.close()

    assert errors == []
    assert "Elder" in legend and "EMA13" in legend
    assert ("EMA26" in legend) == ema26 and ("Hist" in legend) == macd
    assert ("O " in legend) == (chart_type == "candles")
    assert panes >= (3 if macd else 1)  # price pane, separator, MACD pane, time axis
