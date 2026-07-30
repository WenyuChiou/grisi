"""
Tests for core daily_update.py / utils.py functions:
  - safe_clamp() — NaN-safe score-component clamping (P0 fix)
  - compute_score() — Moodring composite score calculation
  - rsi() — RSI-14 indicator
  - fetch_*_data() — graceful handling of empty yfinance results

Run with: pytest tests/test_daily_update.py -v
"""

from __future__ import annotations

import math
import os
import sys

import pandas as pd
import pytest

# Ensure src/ is importable both as a package (src.daily_update) and as
# top-level modules (daily_update.py does `from utils import ...`,
# `from validation_gate import ...` etc. — non-package-qualified imports
# that require src/ itself to be on sys.path).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from src import daily_update
from src.utils import rsi, safe_clamp


# ── safe_clamp() ─────────────────────────────────────────────────────────────

def test_safe_clamp_normal_value_passthrough() -> None:
    assert safe_clamp(50.0) == 50.0
    assert safe_clamp(0.0) == 0.0
    assert safe_clamp(100.0) == 100.0


def test_safe_clamp_clamps_above_hi() -> None:
    assert safe_clamp(150.0) == 100.0


def test_safe_clamp_clamps_below_lo() -> None:
    assert safe_clamp(-25.0) == 0.0


def test_safe_clamp_boundary_values_unchanged() -> None:
    # Exactly at the boundary should not be altered.
    assert safe_clamp(0.0, lo=0.0, hi=100.0) == 0.0
    assert safe_clamp(100.0, lo=0.0, hi=100.0) == 100.0

def test_safe_clamp_custom_bounds() -> None:
    assert safe_clamp(5.0, lo=-10.0, hi=10.0) == 5.0
    assert safe_clamp(20.0, lo=-10.0, hi=10.0) == 10.0


def test_safe_clamp_none_raises_value_error() -> None:
    with pytest.raises(ValueError):
        safe_clamp(None, label="test_signal")


def test_safe_clamp_nan_raises_value_error() -> None:
    # This is the core P0 fix: min(100.0, float('nan')) silently returns
    # 100.0 in vanilla Python, which would clamp a broken signal to
    # "extreme greed" instead of surfacing the failure. safe_clamp must
    # raise instead of silently returning a bogus clamped value.
    with pytest.raises(ValueError):
        safe_clamp(float("nan"), label="test_signal")


def test_safe_clamp_error_message_includes_label() -> None:
    with pytest.raises(ValueError, match="my_signal"):
        safe_clamp(float("nan"), label="my_signal")

# ── compute_score() ──────────────────────────────────────────────────────────

def test_compute_score_neutral_inputs_yield_50() -> None:
    # RSI=50 (mid), vs_high=90 (mid of an 80-100 floor/range), mom=0 (mid of
    # a +/-10 momentum range) should all clamp to 50 -> equal-weight avg = 50.
    market_data = {
        "TEST_RSI14": 50.0,
        "TEST_vs_52w_high_pct": 90.0,
        "TEST_20d_return_pct": 0.0,
    }
    assert daily_update.compute_score(market_data, "TEST") == 50.0


def test_compute_score_extreme_greed_inputs_yield_100() -> None:
    market_data = {
        "TEST_RSI14": 100.0,
        "TEST_vs_52w_high_pct": 100.0,
        "TEST_20d_return_pct": 10.0,
    }
    assert daily_update.compute_score(market_data, "TEST") == 100.0


def test_compute_score_extreme_fear_inputs_yield_0() -> None:
    market_data = {
        "TEST_RSI14": 0.0,
        "TEST_vs_52w_high_pct": 80.0,
        "TEST_20d_return_pct": -10.0,
    }
    assert daily_update.compute_score(market_data, "TEST") == 0.0

def test_compute_score_missing_keys_use_defaults() -> None:
    # No keys present at all -> falls back to the built-in defaults
    # (rsi=50, vs_high=90, mom=0), which also average out to 50.0.
    assert daily_update.compute_score({}, "TEST") == 50.0


def test_compute_score_returns_none_on_nan_rsi() -> None:
    market_data = {
        "TEST_RSI14": float("nan"),
        "TEST_vs_52w_high_pct": 90.0,
        "TEST_20d_return_pct": 0.0,
    }
    assert daily_update.compute_score(market_data, "TEST") is None


def test_compute_score_returns_none_on_nan_vs_high() -> None:
    market_data = {
        "TEST_RSI14": 50.0,
        "TEST_vs_52w_high_pct": float("nan"),
        "TEST_20d_return_pct": 0.0,
    }
    assert daily_update.compute_score(market_data, "TEST") is None


def test_compute_score_returns_none_on_nan_momentum() -> None:
    market_data = {
        "TEST_RSI14": 50.0,
        "TEST_vs_52w_high_pct": 90.0,
        "TEST_20d_return_pct": float("nan"),
    }
    assert daily_update.compute_score(market_data, "TEST") is None

def test_compute_score_never_returns_bogus_100_on_nan() -> None:
    # Regression guard for the exact bug safe_clamp fixes: a NaN sub-signal
    # must never silently produce a 100.0 "extreme greed" score.
    market_data = {
        "TEST_RSI14": float("nan"),
        "TEST_vs_52w_high_pct": float("nan"),
        "TEST_20d_return_pct": float("nan"),
    }
    result = daily_update.compute_score(market_data, "TEST")
    assert result is None
    assert result != 100.0


# ── rsi() ─────────────────────────────────────────────────────────────────────

def test_rsi_monotonic_increasing_series_is_100() -> None:
    # All gains, zero losses -> RS = inf -> RSI = 100.
    series = pd.Series(range(1, 21), dtype=float)
    result = rsi(series)
    assert result.iloc[-1] == 100.0


def test_rsi_monotonic_decreasing_series_is_0() -> None:
    # All losses, zero gains -> RS = 0 -> RSI = 0.
    series = pd.Series(range(20, 0, -1), dtype=float)
    result = rsi(series)
    assert result.iloc[-1] == 0.0

def test_rsi_flat_series_is_nan() -> None:
    # No gains, no losses -> 0/0 -> NaN. Downstream code (safe_clamp) is
    # responsible for treating this as a missing signal, not rsi() itself.
    series = pd.Series([100.0] * 20)
    result = rsi(series)
    assert math.isnan(result.iloc[-1])


def test_rsi_known_price_series() -> None:
    # Classic textbook RSI-14 example series (Wilder-style prices), computed
    # here with the simple (non-Wilder) rolling-mean variant this codebase
    # uses. Expected value pinned from the current implementation.
    prices = [
        44, 44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84,
        46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22,
    ]
    series = pd.Series(prices, dtype=float)
    result = rsi(series)
    assert round(result.iloc[-1], 2) == 73.33


def test_rsi_returns_series_same_length_as_input() -> None:
    series = pd.Series(range(1, 31), dtype=float)
    result = rsi(series)
    assert len(result) == len(series)

# ── fetch_*_data() graceful handling of empty DataFrames ─────────────────────

def _empty_ohlc_df() -> pd.DataFrame:
    """An empty-but-well-formed yfinance-style download result."""
    return pd.DataFrame({"Close": pd.Series([], dtype=float)})


def test_fetch_us_data_handles_empty_dataframe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daily_update, "yf_download_with_retry", lambda *a, **kw: _empty_ohlc_df())
    data, global_ctx, market_open = daily_update.fetch_us_data()
    assert data is None
    assert global_ctx is None
    assert market_open is False


def test_fetch_tw_data_handles_empty_dataframe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daily_update, "yf_download_with_retry", lambda *a, **kw: _empty_ohlc_df())
    market_data, retail_data, usdtwd_val, market_open = daily_update.fetch_tw_data()
    assert market_data is None
    assert retail_data is None
    assert usdtwd_val is None
    assert market_open is False

def test_fetch_jp_data_handles_empty_dataframe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daily_update, "yf_download_with_retry", lambda *a, **kw: _empty_ohlc_df())
    market_data, market_open = daily_update.fetch_jp_data()
    assert market_data is None
    assert market_open is False


def test_fetch_eu_data_handles_empty_dataframe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daily_update, "yf_download_with_retry", lambda *a, **kw: _empty_ohlc_df())
    market_data, market_open = daily_update.fetch_eu_data()
    assert market_data is None
    assert market_open is False


def test_fetch_kr_data_raises_clear_error_on_empty_dataframe(monkeypatch: pytest.MonkeyPatch) -> None:
    # fetch_kr_data has no "skip gracefully" path — both ^KS11 and the
    # 069500.KS fallback are tried, and if both are empty it raises a
    # clear RuntimeError instead of crashing later with a cryptic
    # IndexError from .iloc[-1] on an empty Series.
    monkeypatch.setattr(daily_update, "yf_download_with_retry", lambda *a, **kw: _empty_ohlc_df())
    with pytest.raises(RuntimeError):
        daily_update.fetch_kr_data()


def test_fetch_us_data_handles_none_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daily_update, "yf_download_with_retry", lambda *a, **kw: None)
    data, global_ctx, market_open = daily_update.fetch_us_data()
    assert data is None
    assert global_ctx is None
    assert market_open is False