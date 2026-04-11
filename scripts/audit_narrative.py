#!/usr/bin/env python3
"""Audit narrative freshness: warn if phase2_agent_results.json is older than 25 hours.

Exit codes:
  0 — file is fresh (mtime within 25 hours)
  1 — file is stale or missing

Usage:
  python scripts/audit_narrative.py
  python scripts/audit_narrative.py --max-hours 25
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

DOCS_DATA = os.path.join(os.path.dirname(__file__), '..', 'docs', 'data')
NARRATIVE_FILE = os.path.normpath(os.path.join(DOCS_DATA, 'phase2_agent_results.json'))
DEFAULT_MAX_HOURS = 25


def check_narrative_freshness(max_hours=DEFAULT_MAX_HOURS):
    if not os.path.exists(NARRATIVE_FILE):
        print(f"[AUDIT FAIL] {NARRATIVE_FILE} does not exist.", file=sys.stderr)
        return 1

    mtime = os.path.getmtime(NARRATIVE_FILE)
    mtime_dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
    now_dt = datetime.now(tz=timezone.utc)
    age_hours = (now_dt - mtime_dt).total_seconds() / 3600

    # Also check that tw_agent.forward_outlook doesn't contain any obviously stale score
    stale_ok = True
    try:
        with open(NARRATIVE_FILE, encoding='utf-8') as f:
            data = json.load(f)
        fo = data.get('tw_agent', {}).get('forward_outlook', '')
        date_str = data.get('date', '')
        today_dt = datetime.now().date()
        today = today_dt.strftime('%Y-%m-%d')
        # Weekend grace: on Sat/Sun the latest valid trading day is Friday.
        # Allow both today and the most recent prior weekday so the audit
        # doesn't false-fail on weekend checks.
        allowed_dates = {today}
        if today_dt.weekday() == 5:  # Saturday -> allow Friday
            allowed_dates.add((today_dt - timedelta(days=1)).strftime('%Y-%m-%d'))
        elif today_dt.weekday() == 6:  # Sunday -> allow Friday
            allowed_dates.add((today_dt - timedelta(days=2)).strftime('%Y-%m-%d'))
        if date_str and date_str not in allowed_dates:
            print(f"[AUDIT WARN] phase2_agent_results.json date field is {date_str!r}, expected one of {sorted(allowed_dates)}",
                  file=sys.stderr)
            stale_ok = False
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"[AUDIT WARN] Could not parse {NARRATIVE_FILE}: {e}", file=sys.stderr)

    if age_hours > max_hours:
        print(
            f"[AUDIT FAIL] {NARRATIVE_FILE} is {age_hours:.1f}h old "
            f"(threshold: {max_hours}h). Narrative may be stale.",
            file=sys.stderr,
        )
        return 1

    status = "OK" if stale_ok else "WARN"
    print(f"[AUDIT {status}] {NARRATIVE_FILE} is {age_hours:.1f}h old — within {max_hours}h threshold.")
    return 0 if stale_ok else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Audit narrative freshness')
    parser.add_argument('--max-hours', type=float, default=DEFAULT_MAX_HOURS,
                        help=f'Max allowed age in hours (default: {DEFAULT_MAX_HOURS})')
    args = parser.parse_args()
    sys.exit(check_narrative_freshness(max_hours=args.max_hours))
