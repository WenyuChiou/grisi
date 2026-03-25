"""
Rebuild dashboard_data.json with DAILY frequency.

Reads historical_scores.csv for US/TW daily scores,
downloads daily prices from yfinance, and regenerates
the dashboard_data.json with daily-frequency arrays.

Preserves `snapshot` key unchanged.
Syncs `agents` key from phase2_agent_results.json (source of truth).
"""

import sys
import io
import os
import json
import numpy as np
import pandas as pd
from datetime import datetime

os.environ['PYTHONUTF8'] = '1'
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')


def sanitize_for_json(obj):
    """Recursively replace NaN/Inf with None for JSON serialization."""
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_for_json(v) for v in obj]
    elif isinstance(obj, float):
        if np.isnan(obj) or np.isinf(obj):
            return None
        return obj
    return obj


def main():
    import yfinance as yf

    print("=" * 60)
    print("Rebuild dashboard_data.json → DAILY frequency")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    # ── Load existing dashboard to preserve snapshot/agents ──
    db_path = os.path.join(DATA_DIR, 'dashboard_data.json')
    with open(db_path, 'r', encoding='utf-8') as f:
        db_data = json.load(f)

    snapshot = db_data.get('snapshot', {})

    # Sync agents from phase2_agent_results.json (source of truth) instead of
    # preserving the stale copy in dashboard_data.json.
    phase2_path = os.path.join(DATA_DIR, 'phase2_agent_results.json')
    if os.path.exists(phase2_path):
        with open(phase2_path, 'r', encoding='utf-8') as f:
            agents = json.load(f)
        print(f"[AGENTS] Loaded fresh agents from phase2_agent_results.json (date={agents.get('date', '?')})")
    else:
        agents = db_data.get('agents', {})
        print("[AGENTS] phase2_agent_results.json not found — preserving existing agents")

    # ── Load historical scores CSV ──
    csv_path = os.path.join(DATA_DIR, 'historical_scores.csv')
    df = pd.read_csv(csv_path, parse_dates=['date'], encoding='utf-8')
    df = df.set_index('date').sort_index()
    df = df.dropna(subset=['us_score', 'tw_score'], how='all')
    # Drop rows where both scores are NaN
    df = df.dropna(subset=['us_score'], how='any')

    print(f"\nHistorical scores: {len(df)} daily rows")
    print(f"  Range: {df.index[0].strftime('%Y-%m-%d')} to {df.index[-1].strftime('%Y-%m-%d')}")

    # ── Download daily prices ──
    print("\nDownloading daily prices from yfinance...")
    tickers = {
        'spy': 'SPY',
        'twii': '^TWII',
        'tsmc': '2330.TW',
        'nikkei': '^N225',
        'kospi': '^KS11',
        'stoxx50': '^STOXX50E',
    }

    prices = {}
    for name, ticker in tickers.items():
        print(f"  {ticker}...", end=' ')
        raw = yf.download(ticker, start='2009-01-01',
                          end=datetime.now().strftime('%Y-%m-%d'),
                          progress=False, auto_adjust=True)
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        series = raw['Close'].dropna()
        series = series[series > 0]  # Remove zero prices (holiday/data artifacts)
        if hasattr(series.index, 'tz') and series.index.tz is not None:
            series.index = series.index.tz_localize(None)
        prices[name] = series
        print(f"{len(series)} rows")

    # ── Build new dashboard_data ──
    print("\nBuilding daily dashboard_data.json...")

    new_data = {}

    # US/TW scores (daily)
    new_data['dates'] = [d.strftime('%Y-%m-%d') for d in df.index]
    new_data['us_score'] = [round(float(v), 1) if not np.isnan(v) else None for v in df['us_score'].values]
    new_data['tw_score'] = [round(float(v), 1) if not np.isnan(v) else None for v in df['tw_score'].values]
    new_data['divergence'] = [round(float(v), 1) if not np.isnan(v) else None for v in df['divergence'].values]

    print(f"  dates: {len(new_data['dates'])} points")

    # Price arrays (daily)
    for name in ['spy', 'twii', 'tsmc', 'nikkei', 'kospi', 'stoxx50']:
        series = prices[name]
        new_data[f'{name}_dates'] = [d.strftime('%Y-%m-%d') for d in series.index]
        new_data[name] = [round(float(v), 2) for v in series.values]
        print(f"  {name}: {len(series)} points")

    # Preserve snapshot and agents
    new_data['snapshot'] = snapshot
    new_data['agents'] = agents

    # Preserve JP/KR/EU score arrays (will be updated by backtest_expansion)
    for key in ['jp_score', 'jp_dates', 'kr_score', 'kr_dates', 'eu_score', 'eu_dates']:
        if key in db_data:
            new_data[key] = db_data[key]

    # Sanitize NaN
    new_data = sanitize_for_json(new_data)

    # ── Write output ──
    with open(db_path, 'w', encoding='utf-8') as f:
        json.dump(new_data, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nSaved: {db_path}")

    # ── Verify ──
    with open(db_path, 'r', encoding='utf-8') as f:
        verify = json.load(f)

    n_dates = len(verify.get('dates', []))
    print(f"\nVerification:")
    print(f"  dates: {n_dates} points")
    if n_dates >= 2:
        d0 = verify['dates'][0]
        d1 = verify['dates'][1]
        dlast = verify['dates'][-1]
        print(f"  first: {d0}, second: {d1}, last: {dlast}")
        # Check gap
        from datetime import datetime as dt
        gap = (dt.strptime(d1, '%Y-%m-%d') - dt.strptime(d0, '%Y-%m-%d')).days
        print(f"  gap between first two: {gap} day(s)")
    print(f"  snapshot preserved: {'snapshot' in verify}")
    print(f"  agents synced: {'agents' in verify} (date={verify.get('agents',{}).get('date','?')})")
    print(f"  spy points: {len(verify.get('spy', []))}")

    # ── Sync to docs/data/ for GitHub Pages live dashboard ──
    docs_data_dir = os.path.join(DATA_DIR, '..', 'docs', 'data')
    docs_data_dir = os.path.normpath(docs_data_dir)
    if os.path.isdir(docs_data_dir):
        import shutil
        sync_files = [
            'dashboard_data.json',
            'historical_scores.csv',
            'forward_outlook.json',
            'overlay_data.json',
            'phase2_agent_results.json',
            'memory_scene.json',
            'self_improve.json',
        ]
        for fname in sync_files:
            src = os.path.join(DATA_DIR, fname)
            dst = os.path.join(docs_data_dir, fname)
            if os.path.exists(src):
                shutil.copy2(src, dst)
        print(f"Synced {len(sync_files)} files → docs/data/")

    print("\nDone!")


if __name__ == '__main__':
    main()
