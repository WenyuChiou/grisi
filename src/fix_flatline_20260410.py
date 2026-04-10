#!/usr/bin/env python3
"""
Retroactive cleanup for TW/JP flatline bug on 2026-04-03/06/07.

Root cause: daily_update.py wrote carry-forward tw_score/jp_score for closed market days
into overlay_data.json and historical_scores.csv.

What this script fixes:
- overlay_data.json: tw_score -> None for 04-03, 04-06, 04-07
- overlay_data.json: jp_score/jp_dates entry for 04-07 -> removed
- historical_scores.csv: tw_score -> '' for 04-03, 04-06, 04-07
- Syncs fixed files to docs/data/
"""
import json
import csv
import os
import shutil

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, 'docs', 'data')

TW_BAD_DATES = {'2026-04-03', '2026-04-06', '2026-04-07'}
JP_BAD_DATES = {'2026-04-07'}


def fix_overlay():
    path = os.path.join(DATA_DIR, 'overlay_data.json')
    with open(path, 'r', encoding='utf-8') as f:
        ov = json.load(f)

    # Fix tw_score in shared dates/tw_score arrays
    dates = ov.get('dates', [])
    tw_score = ov.get('tw_score', [])
    tw_fixed = 0
    if len(dates) == len(tw_score):
        for i, d in enumerate(dates):
            if d in TW_BAD_DATES and tw_score[i] is not None:
                print(f"  overlay tw_score[{d}]: {tw_score[i]} -> None")
                tw_score[i] = None
                tw_fixed += 1
        ov['tw_score'] = tw_score
    else:
        print(f"  WARNING: dates({len(dates)}) != tw_score({len(tw_score)}) length mismatch, skipping tw fix")

    # Fix jp_score/jp_dates - remove entries for bad dates
    jp_dates = ov.get('jp_dates', [])
    jp_score = ov.get('jp_score', [])
    jp_fixed = 0
    new_jp_dates = []
    new_jp_score = []
    for d, s in zip(jp_dates, jp_score):
        if d in JP_BAD_DATES:
            print(f"  overlay jp_score[{d}]: {s} -> removed")
            jp_fixed += 1
        else:
            new_jp_dates.append(d)
            new_jp_score.append(s)
    ov['jp_dates'] = new_jp_dates
    ov['jp_score'] = new_jp_score

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(ov, f, ensure_ascii=False)
    print(f"  overlay_data.json: {tw_fixed} tw fixes, {jp_fixed} jp fixes -> saved")


def fix_csv():
    path = os.path.join(DATA_DIR, 'historical_scores.csv')
    rows = []
    with open(path, 'r', encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or ['date', 'us_score', 'tw_score', 'divergence']
        rows = list(reader)

    fixed = 0
    for row in rows:
        d = row.get('date', '')
        if d in TW_BAD_DATES:
            old = row.get('tw_score', '')
            row['tw_score'] = ''
            # Recalculate divergence
            us_v = row.get('us_score', '')
            try:
                row['divergence'] = str(round(abs(float(us_v)), 1)) if us_v else ''
            except (TypeError, ValueError):
                row['divergence'] = ''
            print(f"  csv tw_score[{d}]: {old} -> '' (divergence recalculated)")
            fixed += 1

    with open(path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['date', 'us_score', 'tw_score', 'divergence'])
        writer.writeheader()
        writer.writerows(rows)
    print(f"  historical_scores.csv: {fixed} rows fixed -> saved")


def verify():
    import pandas as pd
    df = pd.read_csv(os.path.join(DATA_DIR, 'historical_scores.csv'))
    print("\n=== historical_scores.csv (last 12 rows) ===")
    print(df[['date', 'us_score', 'tw_score', 'divergence']].tail(12).to_string(index=False))

    with open(os.path.join(DATA_DIR, 'overlay_data.json')) as f:
        ov = json.load(f)
    dates = ov.get('dates', [])
    tw_score = ov.get('tw_score', [])
    jp_dates = ov.get('jp_dates', [])
    jp_score = ov.get('jp_score', [])
    print("\n=== overlay_data.json tw_score (last 12) ===")
    for d, s in zip(dates[-12:], tw_score[-12:]):
        print(f"  {d}: {s}")
    print("\n=== overlay_data.json jp_score (last 10) ===")
    for d, s in zip(jp_dates[-10:], jp_score[-10:]):
        print(f"  {d}: {s}")


if __name__ == '__main__':
    print("=== Fixing TW/JP flatline in overlay_data.json ===")
    fix_overlay()
    print("\n=== Fixing TW flatline in historical_scores.csv ===")
    fix_csv()
    print("\n=== Verification ===")
    verify()
    print("\nDone. Run git diff docs/data/ to confirm changes.")
