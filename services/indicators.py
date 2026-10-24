"""Pure indicator functions: EMA, MACD histogram and Elder Impulse state.

EMA(n)[0] = C[0]; EMA(n)[t] = a*C[t] + (1-a)*EMA(n)[t-1], a = 2/(n+1).
Equivalent to pandas ``ewm(span=n, adjust=False)``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

CALCULATION_VERSION = "elder-v1"
GREEN, RED, BLUE, UNAVAILABLE = "green", "red", "blue", "unavailable"


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def elder_state(ema13: pd.Series, histogram: pd.Series) -> pd.Series:
    d_ema = ema13.diff()
    d_hist = histogram.diff()
    state = np.where(
        (d_ema > 0) & (d_hist > 0),
        GREEN,
        np.where((d_ema < 0) & (d_hist < 0), RED, BLUE),
    )
    out = pd.Series(state, index=ema13.index, dtype="object")
    out[d_ema.isna() | d_hist.isna()] = UNAVAILABLE
    return out


def compute_indicators(bars: pd.DataFrame, warmup_bars: int) -> pd.DataFrame:
    """Return a copy of ``bars`` with indicator columns over the full window.

    The first ``warmup_bars`` rows keep their indicator values for continuity
    but their Elder state is ``unavailable``.
    """
    out = bars.copy()
    close = out["close"].astype("float64")
    out["ema13"] = ema(close, 13)
    out["ema26"] = ema(close, 26)
    macd = ema(close, 12) - out["ema26"]
    signal = ema(macd, 9)
    out["macd"] = macd
    out["signal"] = signal
    out["histogram"] = macd - signal
    state = elder_state(out["ema13"], out["histogram"])
    state.iloc[: min(warmup_bars, len(state))] = UNAVAILABLE
    out["elder_state"] = state
    return out
