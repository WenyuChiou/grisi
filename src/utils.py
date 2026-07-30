"""
Shared helpers for the MoodRing pipeline.

These were previously copy-pasted as nested closures inside each of
fetch_us_data / fetch_tw_data / fetch_jp_data / fetch_kr_data / fetch_eu_data
in daily_update.py (5x duplication), plus a near-identical RSI implementation
in recalibrate.py. Consolidated here so there is a single source of truth.
"""

import math


def safe(df):
    """Extract the Close price Series from a yfinance download result.

    yfinance sometimes returns a MultiIndex column DataFrame (e.g. when
    multiple tickers/fields are present) — this collapses it to a plain
    1-D Series either way.
    """
    c = df['Close']
    return c.iloc[:, 0] if c.ndim > 1 else c


def rsi(close, period=14):
    """Calculate RSI-14 using a simple (non-Wilder) rolling-mean average.

    `close` is a pandas Series of prices; returns a Series of RSI values.
    """
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    return 100 - 100 / (1 + gain / loss)


def safe_clamp(value, lo=0.0, hi=100.0, label=""):
    """Clamp value to [lo, hi], raising ValueError on NaN.

    Guards against the CPython float quirk where min(100.0, float('nan'))
    silently returns 100.0 — i.e. a missing/corrupt sub-signal would
    otherwise clamp to "extreme greed" instead of surfacing the failure.
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        raise ValueError(f"NaN detected in score component: {label}")
    return max(lo, min(hi, float(value)))
