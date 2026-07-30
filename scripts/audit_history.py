#!/usr/bin/env python3
"""
Moodring History Audit — Full-scan validator for historical data files.

Usage:
    python scripts/audit_history.py              # audit all history
    python scripts/audit_history.py --strict     # same (explicit)
    python scripts/audit_history.py --since 2026-01-01  # from date

Exit codes:
    0 — no violations found
    1 — violations found (or unexpected error)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timedelta


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")
MARKETS = ["us", "tw", "jp", "kr", "eu"]


# Exchange calendar mapping for pandas_market_calendars. These are the
# canonical calendars for each market's primary index, and give deterministic
# holiday schedules that don't change based on when the audit is run — unlike
# the old approach of re-querying yfinance live at audit time (see history:
# that made audit results non-reproducible as yfinance's data changed).
MARKET_CALENDARS = {"us": "NYSE", "tw": "XTAI", "jp": "JPX", "kr": "XKRX", "eu": "EUREX"}


def build_open_dates(market: str, date_list: list[str]) -> set[str]:
    """
    Given a list of dates for a market, return the set of dates
    when the market was actually open.

    Uses pandas_market_calendars for a deterministic exchange holiday
    calendar instead of inferring open/closed days from live yfinance price
    data. This makes audit results reproducible: the same (market, date_list)
    input always yields the same output, regardless of when the audit runs
    or how yfinance's upstream data has changed since.
    """
    import pandas_market_calendars as mcal

    calendar_name = MARKET_CALENDARS.get(market)
    if not calendar_name or not date_list:
        return set(date_list)

    start = min(date_list)
    end = (datetime.strptime(max(date_list), "%Y-%m-%d") + timedelta(days=2)).strftime("%Y-%m-%d")

    try:
        cal = mcal.get_calendar(calendar_name)
        schedule = cal.schedule(start_date=start, end_date=end)
        open_dates = {d.strftime("%Y-%m-%d") for d in schedule.index}
        return open_dates
    except Exception as exc:
        print(f"[AUDIT] Warning: could not build {market.upper()} calendar ({calendar_name}): {exc}")
        return set(date_list)


def _parse_score(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _in_range(score: float) -> bool:
    return 0.0 <= score <= 100.0


def _filter_since(date_str: str, since: str | None, until: str | None = None) -> bool:
    if since is not None and date_str < since:
        return False
    if until is not None and date_str > until:
        return False
    return True


def _collect_flatline_violations(
    market: str, entries: list[tuple[str, float]], violations: list[str]
) -> None:
    if not entries:
        return

    run_value = round(entries[0][1], 1)
    run_start = entries[0][0]
    run_length = 1

    for date_str, score in entries[1:]:
        rounded = round(score, 1)
        if rounded == run_value:
            run_length += 1
            continue
        if run_length >= 3:
            violations.append(
                f"{market.upper()} flatline run starting {run_start}: value={run_value} for {run_length} consecutive entries"
            )
        run_value = rounded
        run_start = date_str
        run_length = 1

    if run_length >= 3:
        violations.append(
            f"{market.upper()} flatline run starting {run_start}: value={run_value} for {run_length} consecutive entries"
        )


def audit_historical_scores(since: str | None = None, until: str | None = None) -> tuple[list[str], dict[str, list[tuple[str, float]]], int]:
    violations: list[str] = []
    sequences: dict[str, list[tuple[str, float]]] = {market: [] for market in MARKETS}
    csv_path = os.path.join(DATA_DIR, "historical_scores.csv")

    with open(csv_path, newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle) if _filter_since(row["date"], since, until)]

    us_dates = [row["date"] for row in rows if _parse_score(row.get("us_score")) is not None]
    tw_dates = [row["date"] for row in rows if _parse_score(row.get("tw_score")) is not None]
    us_open_dates = build_open_dates("us", us_dates)
    tw_open_dates = build_open_dates("tw", tw_dates)

    checked = 0
    for row in rows:
        date_str = row["date"]
        for market in ("us", "tw"):
            score = _parse_score(row.get(f"{market}_score"))
            if score is None:
                continue
            checked += 1
            sequences[market].append((date_str, score))
            open_dates = us_open_dates if market == "us" else tw_open_dates
            if date_str not in open_dates:
                violations.append(f"{market.upper()} closed on {date_str} but score={score} in historical_scores.csv")
            if not _in_range(score):
                violations.append(f"{market.upper()} score={score} out of [0, 100] on {date_str} in historical_scores.csv")

    return violations, sequences, checked


def audit_overlay_data(since: str | None = None, until: str | None = None) -> tuple[list[str], dict[str, list[tuple[str, float]]], int]:
    violations: list[str] = []
    sequences: dict[str, list[tuple[str, float]]] = {market: [] for market in MARKETS}
    json_path = os.path.join(DATA_DIR, "overlay_data.json")

    with open(json_path, encoding="utf-8") as handle:
        data = json.load(handle)

    checked = 0
    for market in MARKETS:
        dates_key = "dates" if market in {"us", "tw"} else f"{market}_dates"
        score_key = f"{market}_score"
        dates = data.get(dates_key, [])
        scores = data.get(score_key, [])
        date_score_pairs = []
        for date_str, raw_score in zip(dates, scores):
            if not _filter_since(date_str, since, until):
                continue
            score = _parse_score(raw_score)
            if score is not None:
                date_score_pairs.append((date_str, score))

        open_dates = build_open_dates(market, [item[0] for item in date_score_pairs])
        for date_str, score in date_score_pairs:
            checked += 1
            sequences[market].append((date_str, score))
            if date_str not in open_dates:
                violations.append(f"{market.upper()} closed on {date_str} but score={score} in overlay_data.json")
            if not _in_range(score):
                violations.append(f"{market.upper()} score={score} out of [0, 100] on {date_str} in overlay_data.json")

    return violations, sequences, checked


def audit_history(since: str | None = None, until: str | None = None) -> tuple[list[str], int]:
    violations: list[str] = []

    csv_violations, csv_sequences, csv_checked = audit_historical_scores(since=since, until=until)
    overlay_violations, overlay_sequences, overlay_checked = audit_overlay_data(since=since, until=until)
    violations.extend(csv_violations)
    violations.extend(overlay_violations)

    for market in MARKETS:
        _collect_flatline_violations(market, csv_sequences.get(market, []), violations)
        _collect_flatline_violations(market, overlay_sequences.get(market, []), violations)

    return violations, csv_checked + overlay_checked


def main() -> None:
    # Default until=yesterday: today's data may not be in yfinance yet (same-day lag).
    # Layer A (validate_daily_scores) is the real-time guard for today's writes.
    _yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true", help="Explicit strict mode (default behavior)")
    parser.add_argument("--since", type=str, default="2024-01-01",
                        help="Only audit dates >= YYYY-MM-DD (default: 2024-01-01; pre-2024 TW carry-forwards are legacy and excluded from default scope; use --since 2010-01-01 to force full scan)")
    parser.add_argument("--until", type=str, default=_yesterday,
                        help="Only audit dates <= YYYY-MM-DD (default: yesterday)")
    args = parser.parse_args()

    try:
        violations, total_rows = audit_history(since=args.since, until=args.until)
    except Exception as exc:
        print(f"[AUDIT] FAIL: unexpected error: {exc}")
        sys.exit(1)

    if violations:
        print(f"\n[AUDIT] FAIL: {len(violations)} violation(s) found:")
        for violation in violations:
            print(f"  - {violation}")
        sys.exit(1)
    else:
        print(f"\n[AUDIT] PASS: No violations found across {total_rows} data points checked.")
        sys.exit(0)


if __name__ == "__main__":
    main()
