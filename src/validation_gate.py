"""
Moodring Data Validation Gate
============================
Pre-write invariant checker for daily score data.
Called by daily_update.py before writing to CSV/JSON.
"""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timedelta


MARKETS = ["us", "tw", "jp", "kr", "eu"]
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


class DataValidationError(Exception):
    """Raised when a daily score payload violates write-time invariants."""


def _configure_yfinance_cache() -> None:
    import yfinance as yf

    cache_dir = os.path.join(os.path.dirname(__file__), "..", ".cache", "yfinance")
    os.makedirs(cache_dir, exist_ok=True)
    if hasattr(yf, "set_tz_cache_location"):
        yf.set_tz_cache_location(cache_dir)


def _safe_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_market_open(date_str: str, market: str) -> bool:
    """Check if market was open on date_str via yfinance."""
    import pandas as pd
    import yfinance as yf

    _configure_yfinance_cache()

    tickers = {
        "us": "SPY",
        "tw": "^TWII",
        "jp": "^N225",
        "kr": "^KS11",
        "eu": "^STOXX50E",
    }
    ticker = tickers.get(market.lower())
    if not ticker:
        return True
    check_date = datetime.strptime(date_str, "%Y-%m-%d")
    start = (check_date - timedelta(days=10)).strftime("%Y-%m-%d")
    end = (check_date + timedelta(days=2)).strftime("%Y-%m-%d")
    try:
        raw = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
        if raw is None or raw.empty:
            return False
        close = raw["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        close = close.dropna()
        close = close[close > 0]
        if close.empty:
            return False
        if hasattr(close.index, "tz") and close.index.tz is not None:
            close.index = close.index.tz_localize(None)
        dates_in_data = {d.strftime("%Y-%m-%d") for d in close.index}
        return date_str in dates_in_data
    except Exception:
        return True


def get_market_history(market: str, n: int = 5) -> list[float]:
    """Return the last n non-empty historical scores for a market."""
    market_key = market.lower()
    history: list[float] = []

    if market_key in {"us", "tw"}:
        csv_path = os.path.join(DATA_DIR, "historical_scores.csv")
        column = f"{market_key}_score"
        if not os.path.exists(csv_path):
            return history
        with open(csv_path, newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                value = _safe_float(row.get(column))
                if value is not None:
                    history.append(value)
    elif market_key in {"jp", "kr", "eu"}:
        json_path = os.path.join(DATA_DIR, "overlay_data.json")
        score_key = f"{market_key}_score"
        if not os.path.exists(json_path):
            return history
        with open(json_path, encoding="utf-8") as handle:
            data = json.load(handle)
        for raw_value in data.get(score_key, []):
            value = _safe_float(raw_value)
            if value is not None:
                history.append(value)

    if n <= 0:
        return []
    return history[-n:]


def validate_daily_scores(
    scores: dict[str, float | None], asof_date: str, full_mode: bool = False
) -> None:
    """Validate a daily score payload before writing it to persistent storage."""
    failures: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    open_cache: dict[str, bool] = {}

    for market in MARKETS:
        score = scores.get(market)
        if score is None:
            continue

        is_open = _is_market_open(asof_date, market)
        open_cache[market] = is_open
        if not is_open:
            failures.append(
                {
                    "market": market,
                    "check": "calendar",
                    "severity": "error",
                    "detail": f"{market.upper()} closed on {asof_date} but score={score}",
                }
            )

    for market in MARKETS:
        score = scores.get(market)
        if score is None:
            continue
        if not 0.0 <= score <= 100.0:
            failures.append(
                {
                    "market": market,
                    "check": "range",
                    "severity": "error",
                    "detail": f"{market.upper()} score={score} out of [0, 100]",
                }
            )

    for market in MARKETS:
        score = scores.get(market)
        if score is None:
            continue

        hist = get_market_history(market, n=3)
        rounded_current = round(score, 1)
        if len(hist) >= 1 and round(hist[-1], 1) == rounded_current:
            if len(hist) >= 2 and round(hist[-2], 1) == rounded_current:
                if len(hist) >= 3 and round(hist[-3], 1) == rounded_current:
                    failures.append(
                        {
                            "market": market,
                            "check": "flatline",
                            "severity": "error",
                            "detail": (
                                f"{market.upper()} score={rounded_current} is identical for 3+ consecutive days "
                                f"(last hist: {hist[-3:]}, today: {rounded_current})"
                            ),
                        }
                    )
                else:
                    warnings.append(
                        {
                            "market": market,
                            "check": "stale_2day",
                            "severity": "warning",
                            "detail": f"{market.upper()} score={rounded_current} same as previous 2 days",
                        }
                    )
            else:
                warnings.append(
                    {
                        "market": market,
                        "check": "stale_1day",
                        "severity": "warning",
                        "detail": f"{market.upper()} score={rounded_current} same as previous day",
                    }
                )

    if full_mode:
        for market in MARKETS:
            if scores.get(market) is not None:
                continue
            is_open = open_cache.get(market)
            if is_open is None:
                is_open = _is_market_open(asof_date, market)
                open_cache[market] = is_open
            if is_open:
                failures.append(
                    {
                        "market": market,
                        "check": "missing_open_market",
                        "severity": "error",
                        "detail": f"{market.upper()} was open on {asof_date} but score is None (full_mode run)",
                    }
                )

    if failures or warnings:
        log_dir = os.path.join(os.path.dirname(__file__), "..", "logs", "validation_failures")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, f"{asof_date}.json")
        log_data = {
            "asof_date": asof_date,
            "timestamp": datetime.now().isoformat(),
            "full_mode": full_mode,
            "failures": failures,
            "warnings": warnings,
        }
        with open(log_path, "w", encoding="utf-8") as handle:
            json.dump(log_data, handle, indent=2, ensure_ascii=False)

    if failures:
        raise DataValidationError(
            f"{len(failures)} validation failure(s): " + "; ".join(item["detail"] for item in failures)
        )

    for warning in warnings:
        print(f"[VALIDATION] Warning: {warning['detail']}")
