import datetime as dt

import pandas as pd
import pytest

from providers.base import DataNotFound, NetworkError, ProviderError, RateLimited
from providers.yahoo import classify_error, normalize_frame


def test_normalize_uses_market_date_and_lowercase_columns():
    idx = pd.DatetimeIndex(["2025-10-14", "2025-10-15"]).tz_localize("America/New_York")
    raw = pd.DataFrame({"Open": [1, 2], "High": [2, 3], "Low": [0.5, 1], "Close": [1.5, 2.5],
                        "Volume": [10, 20], "Dividends": [0, 0]}, index=idx)
    out = normalize_frame(raw, "America/New_York")
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]
    assert list(out.index) == [dt.date(2025, 10, 14), dt.date(2025, 10, 15)]


def test_missing_column_rejected():
    raw = pd.DataFrame({"Open": [1.0]}, index=pd.DatetimeIndex(["2025-10-14"]))
    with pytest.raises(ProviderError):
        normalize_frame(raw, "America/New_York")


@pytest.mark.parametrize("exc,kind", [
    (type("YFRateLimitError", (Exception,), {})("x"), RateLimited),
    (Exception("HTTP Error 429: Too Many Requests"), RateLimited),
    (Exception("403 Forbidden"), RateLimited),
    (type("YFPricesMissingError", (Exception,), {})("x"), DataNotFound),
    (TimeoutError("read timed out"), NetworkError),
    (ConnectionError("Failed to perform, curl: (7) CONNECT tunnel failed, response 403"), NetworkError),
    (Exception("Could not resolve host"), NetworkError),
    (Exception("something odd"), ProviderError),
])
def test_error_classification(exc, kind):
    assert type(classify_error(exc)) is kind


def test_provider_cache_dir_is_created_and_used(tmp_path):
    import yfinance.cache as yfc

    from providers.yahoo import YahooProvider

    cache = tmp_path / "provider-cache"
    YahooProvider(timeout_seconds=5, cache_dir=cache)
    assert cache.is_dir()
    yfc.get_tz_cache().store("TEST1", "America/New_York")
    assert yfc.get_tz_cache().lookup("TEST1") == "America/New_York"


def test_adapter_uses_cached_metadata_and_configured_market_date(monkeypatch):
    import sys
    from types import SimpleNamespace
    from providers.yahoo import YahooProvider

    raw = pd.DataFrame({'Open': [10.0], 'High': [11.0], 'Low': [9.0], 'Close': [10.5], 'Volume': [100]},
                       index=pd.DatetimeIndex(['2025-10-15']).tz_localize('Asia/Shanghai'))
    seen = []

    class FakeTicker:
        def history(self, **kwargs):
            seen.append(kwargs)
            return raw

        def get_history_metadata(self):
            return {'currency': 'TST'}

    monkeypatch.setitem(sys.modules, 'yfinance', SimpleNamespace(Ticker=lambda symbol: FakeTicker(), __version__='fake'))
    bars = YahooProvider().fetch_daily('TEST1.SS', dt.date(2025, 10, 15), dt.date(2025, 10, 16), 'Asia/Shanghai')
    assert list(bars.frame.index) == [dt.date(2025, 10, 15)]
    assert bars.params['currency'] == 'TST'
    assert seen[0]['end'] == '2025-10-16'
    assert seen[0]['auto_adjust'] is True
