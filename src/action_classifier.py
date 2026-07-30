from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

STATIC_THRESHOLDS = {"p20": 25.0, "p40": 40.0, "p60": 55.0, "p80": 75.0}
MARKET_COLUMNS = {
    "us": "us_score",
    "tw": "tw_score",
    "jp": "jp_score",
    "kr": "kr_score",
    "eu": "eu_score",
}
# us/tw have per-day history in historical_scores.csv; jp/kr/eu only have
# their score history in overlay_data.json (as parallel score arrays), so
# those markets are sourced from there instead.
CSV_MARKETS = {"us", "tw"}
ACTION_SELL = "積極減碼"
ACTION_REDUCE = "逐步減碼"
ACTION_HOLD = "觀望持有"
ACTION_ACCUMULATE = "逢低布局"
ACTION_BUY = "積極加碼"


def _resolve_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return Path(__file__).resolve().parent.parent / path


def _percentile(values: pd.Series, q: int) -> float:
    try:
        result = np.percentile(values.to_numpy(dtype=float), q, method="linear")
    except TypeError:
        result = np.percentile(values.to_numpy(dtype=float), q, interpolation="linear")
    return round(float(result), 1)


def _scores_from_csv(market_key: str, history_csv: str, lookback_days: int) -> pd.Series:
    history_path = _resolve_path(history_csv)
    df = pd.read_csv(history_path)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.sort_values("date", ascending=True).tail(lookback_days)

    col = MARKET_COLUMNS[market_key]
    if col not in df.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(df[col], errors="coerce").dropna()


def _scores_from_overlay(
    market_key: str,
    lookback_days: int,
    overlay_json: str = "data/overlay_data.json",
) -> pd.Series:
    """JP/KR/EU only have their score history stored as arrays in
    overlay_data.json (no per-day row in historical_scores.csv), so read
    the market's score array from there instead."""
    overlay_path = _resolve_path(overlay_json)
    if not overlay_path.exists():
        return pd.Series(dtype=float)
    with overlay_path.open("r", encoding="utf-8") as f:
        overlay = json.load(f)

    col = MARKET_COLUMNS[market_key]
    values = overlay.get(col, [])
    series = pd.to_numeric(pd.Series(values), errors="coerce").dropna()
    return series.tail(lookback_days)


def compute_action_thresholds(
    market: str,
    history_csv: str = "data/historical_scores.csv",
    lookback_days: int = 252,
    min_samples: int = 60,
) -> dict:
    market_key = market.lower()
    if market_key not in MARKET_COLUMNS:
        raise ValueError(f"Unsupported market: {market}")

    if market_key in CSV_MARKETS:
        scores = _scores_from_csv(market_key, history_csv, lookback_days)
    else:
        scores = _scores_from_overlay(market_key, lookback_days)

    thresholds = {
        "market": market_key,
        "lookback_days": lookback_days,
        "sample_size": int(scores.shape[0]),
        "computed_at": date.today().isoformat(),
    }

    if scores.shape[0] < min_samples:
        return {**thresholds, **STATIC_THRESHOLDS, "fallback": True}

    return {
        **thresholds,
        "p20": _percentile(scores, 20),
        "p40": _percentile(scores, 40),
        "p60": _percentile(scores, 60),
        "p80": _percentile(scores, 80),
        "fallback": False,
    }


def classify_action(score: float | None, thresholds: dict) -> str:
    if score is None:
        return ACTION_HOLD
    if score <= thresholds["p20"]:
        return ACTION_BUY
    if score <= thresholds["p40"]:
        return ACTION_ACCUMULATE
    if score <= thresholds["p60"]:
        return ACTION_HOLD
    if score <= thresholds["p80"]:
        return ACTION_REDUCE
    return ACTION_SELL


def write_thresholds_json(
    thresholds_by_market: dict,
    out_path: str = "data/action_thresholds.json",
) -> None:
    out_file = _resolve_path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **thresholds_by_market,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    tmp_file = out_file.with_name(f"{out_file.name}.tmp")
    tmp_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp_file, out_file)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", required=True, choices=sorted(MARKET_COLUMNS))
    parser.add_argument("--score", type=float, required=True)
    args = parser.parse_args()

    thresholds = compute_action_thresholds(args.market)
    write_thresholds_json({args.market: thresholds})
    print(json.dumps(thresholds, ensure_ascii=False, indent=2))
    print(classify_action(args.score, thresholds))


if __name__ == "__main__":
    main()
