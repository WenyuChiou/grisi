"""
GRISI Daily Update Pipeline
============================
Automated daily data refresh → score calculation → dashboard JSON update.
Designed to run via GitHub Actions or local cron.

Data Sources:
  - Yahoo Finance (yfinance): SPY, VIX, ^TNX, ^TWII, 2330.TW, GC=F, USDJPY=X, TWD=X
  - FinMind API (TWSE OpenData): margin balance, institutional investors

Usage:
  python daily_update.py           # Update all
  python daily_update.py --us      # Update US only
  python daily_update.py --tw      # Update TW only
"""

import sys
import os
import json
import argparse
from datetime import datetime, timedelta

# Ensure utf-8
os.environ['PYTHONUTF8'] = '1'

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')


def fetch_us_data():
    """Fetch US market data from Yahoo Finance."""
    import yfinance as yf

    today = datetime.now().strftime('%Y-%m-%d')
    start_90d = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
    end = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')

    print("[US] Fetching from Yahoo Finance...")
    spy = yf.download('SPY', start=start_90d, end=end, progress=False)
    vix = yf.download('^VIX', start=start_90d, end=end, progress=False)
    tnx = yf.download('^TNX', start=(datetime.now()-timedelta(30)).strftime('%Y-%m-%d'), end=end, progress=False)
    gold = yf.download('GC=F', period='1mo', progress=False)
    usdjpy = yf.download('USDJPY=X', period='1mo', progress=False)

    def safe(df):
        c = df['Close']
        return c.iloc[:, 0] if c.ndim > 1 else c

    def rsi(close, p=14):
        d = close.diff()
        g = d.where(d > 0, 0).rolling(p).mean()
        l = (-d.where(d < 0, 0)).rolling(p).mean()
        return 100 - 100 / (1 + g / l)

    spy_c = safe(spy)
    vix_c = safe(vix)
    tnx_c = safe(tnx)

    data = {
        'SPY_close': round(float(spy_c.iloc[-1]), 2),
        'SPY_RSI14': round(float(rsi(spy_c).iloc[-1]), 1),
        'SPY_SMA20': round(float(spy_c.rolling(20).mean().iloc[-1]), 2),
        'SPY_SMA60': round(float(spy_c.rolling(60).mean().iloc[-1]), 2),
        'SPY_vs_52w_high_pct': round(float(spy_c.iloc[-1] / spy_c.rolling(252, min_periods=60).max().iloc[-1] * 100), 1),
        'SPY_5d_return_pct': round(float((spy_c.iloc[-1] / spy_c.iloc[-6] - 1) * 100), 2),
        'SPY_20d_return_pct': round(float((spy_c.iloc[-1] / spy_c.iloc[-21] - 1) * 100), 2),
        'VIX': round(float(vix_c.iloc[-1]), 2),
        'US_10Y_yield': round(float(tnx_c.iloc[-1]), 2),
    }

    global_ctx = {
        'Gold': round(float(safe(gold).iloc[-1]), 0),
        'USDJPY': round(float(safe(usdjpy).iloc[-1]), 2),
    }

    print(f"[US] SPY=${data['SPY_close']}, VIX={data['VIX']}, RSI={data['SPY_RSI14']}")
    return data, global_ctx


def fetch_tw_data():
    """Fetch Taiwan market data from Yahoo Finance + FinMind."""
    import yfinance as yf

    today = datetime.now().strftime('%Y-%m-%d')
    start_90d = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
    end = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')

    print("[TW] Fetching from Yahoo Finance...")
    twii = yf.download('^TWII', start=start_90d, end=end, progress=False)
    tsmc = yf.download('2330.TW', start=start_90d, end=end, progress=False)
    usdtwd = yf.download('TWD=X', period='1mo', progress=False)

    def safe(df):
        c = df['Close']
        return c.iloc[:, 0] if c.ndim > 1 else c

    def rsi(close, p=14):
        d = close.diff()
        g = d.where(d > 0, 0).rolling(p).mean()
        l = (-d.where(d < 0, 0)).rolling(p).mean()
        return 100 - 100 / (1 + g / l)

    twii_c = safe(twii)
    tsmc_c = safe(tsmc)

    market_data = {
        'TAIEX_close': round(float(twii_c.iloc[-1]), 0),
        'TAIEX_RSI14': round(float(rsi(twii_c).iloc[-1]), 1),
        'TAIEX_SMA20': round(float(twii_c.rolling(20).mean().iloc[-1]), 0),
        'TAIEX_vs_52w_high_pct': round(float(twii_c.iloc[-1] / twii_c.rolling(252, min_periods=50).max().iloc[-1] * 100), 1),
        'TSMC_close': round(float(tsmc_c.iloc[-1]), 0),
        'TSMC_vs_52w_high_pct': round(float(tsmc_c.iloc[-1] / tsmc_c.rolling(252, min_periods=50).max().iloc[-1] * 100), 1),
    }

    usdtwd_val = round(float(safe(usdtwd).iloc[-1]), 2)

    # FinMind retail data
    retail_data = {}
    print("[TW] Fetching from FinMind API (TWSE OpenData)...")
    try:
        from FinMind.data import DataLoader
        dl = DataLoader()

        # Margin balance
        margin = dl.taiwan_stock_margin_purchase_short_sale_total(
            start_date='2026-02-01', end_date=today
        )
        mb = margin[margin['name'] == 'MarginPurchase']
        if len(mb) > 0:
            latest_m = int(mb.iloc[-1]['TodayBalance'])
            m5 = int(mb.iloc[-5]['TodayBalance']) if len(mb) >= 5 else latest_m
            retail_data['margin_balance'] = latest_m
            retail_data['margin_5d_change_pct'] = round((latest_m - m5) / m5 * 100, 2)
            retail_data['margin_5d_trend'] = (
                'INCREASING' if retail_data['margin_5d_change_pct'] > 0.3
                else 'DECREASING' if retail_data['margin_5d_change_pct'] < -0.3
                else 'FLAT'
            )

        # Institutional investors
        inst = dl.taiwan_stock_institutional_investors_total(
            start_date='2026-03-01', end_date=today
        )
        if len(inst) > 0:
            ld = inst[inst['date'] == inst['date'].max()]
            tr = ld[ld['name'] == 'total']
            if len(tr) > 0:
                net = float(tr.iloc[0]['buy']) - float(tr.iloc[0]['sell'])
                retail_data['institutional_net_TWD'] = round(net / 1e8, 1)
                retail_data['retail_net_est_TWD'] = round(-net / 1e8, 1)

            fi = ld[ld['name'] == 'Foreign_Investor']
            if len(fi) > 0:
                fnet = float(fi.iloc[0]['buy']) - float(fi.iloc[0]['sell'])
                retail_data['foreign_net_TWD'] = round(fnet / 1e8, 1)

            # Consecutive days
            dfi = inst[inst['name'] == 'Foreign_Investor'].copy()
            dfi['net'] = dfi['buy'].astype(float) - dfi['sell'].astype(float)
            consec = 0
            if len(dfi) > 0:
                direction = 'buy' if dfi.iloc[-1]['net'] > 0 else 'sell'
                for _, r in dfi.iloc[::-1].iterrows():
                    if (direction == 'buy' and r['net'] > 0) or (direction == 'sell' and r['net'] < 0):
                        consec += 1
                    else:
                        break
                retail_data['foreign_consecutive_days'] = consec
                retail_data['foreign_consecutive_direction'] = direction

        # TSMC margin
        tsmc_margin = dl.taiwan_stock_margin_purchase_short_sale(
            stock_id='2330', start_date='2026-02-01', end_date=today
        )
        if len(tsmc_margin) > 0:
            tl = int(tsmc_margin.iloc[-1]['MarginPurchaseTodayBalance'])
            tf = int(tsmc_margin.iloc[0]['MarginPurchaseTodayBalance'])
            retail_data['TSMC_margin_balance'] = tl
            retail_data['TSMC_margin_30d_change_pct'] = round((tl - tf) / tf * 100, 2)

    except Exception as e:
        print(f"[TW] FinMind partial error: {e}")

    print(f"[TW] TAIEX={market_data['TAIEX_close']}, TSMC={market_data['TSMC_close']}")
    return market_data, retail_data, usdtwd_val


def update_snapshot(us_data=None, tw_data=None, tw_retail=None, global_ctx=None, usdtwd=None):
    """Save updated snapshot."""
    today = datetime.now().strftime('%Y-%m-%d')

    snapshot = {
        'date': today,
        'data_sources': {
            'market_prices': 'Yahoo Finance (yfinance)',
            'tw_margin': 'FinMind API (TWSE OpenData)',
            'tw_institutional': 'FinMind API (三大法人)',
            'scoring_method': 'Rolling 252-day Z-score normalization',
            'behavioral_params': 'Kahneman & Tversky (1979), Banerjee (1992)',
        },
    }

    if us_data:
        snapshot['us_market'] = us_data
    if tw_data:
        snapshot['tw_market'] = tw_data
    if tw_retail:
        snapshot['tw_retail_indicators'] = tw_retail
    if global_ctx:
        if usdtwd:
            global_ctx['USDTWD'] = usdtwd
        snapshot['global_context'] = global_ctx

    # Save dated + latest
    dated_path = os.path.join(DATA_DIR, f"snapshot_{today.replace('-', '')}.json")
    latest_path = os.path.join(DATA_DIR, 'snapshot_latest.json')

    for path in [dated_path, latest_path]:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(snapshot, f, indent=2, ensure_ascii=False)

    print(f"[SAVE] Snapshot saved: {dated_path}")
    return snapshot


def update_dashboard_json(snapshot):
    """Update dashboard_data.json with latest snapshot, then sync to docs/data/ for GitHub Pages."""
    dd_path = os.path.join(DATA_DIR, 'dashboard_data.json')

    with open(dd_path, 'r', encoding='utf-8') as f:
        dd = json.load(f)

    dd['snapshot'] = snapshot

    with open(dd_path, 'w', encoding='utf-8') as f:
        json.dump(dd, f, ensure_ascii=False)

    # Sync JSON files to docs/data/ (GitHub Pages serves from /docs)
    import shutil
    docs_data = os.path.join(os.path.dirname(__file__), '..', 'docs', 'data')
    os.makedirs(docs_data, exist_ok=True)
    for fn in ['dashboard_data.json', 'forward_outlook.json', 'phase2_agent_results.json',
               'quintile_data.json', 'overlay_data.json']:
        src = os.path.join(DATA_DIR, fn)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(docs_data, fn))

    print("[SAVE] dashboard_data.json updated + synced to docs/data/")


def main():
    parser = argparse.ArgumentParser(description='GRISI Daily Update')
    parser.add_argument('--us', action='store_true', help='Update US only')
    parser.add_argument('--tw', action='store_true', help='Update TW only')
    args = parser.parse_args()

    # Default: update both
    if not args.us and not args.tw:
        args.us = True
        args.tw = True

    print("=" * 50)
    print(f"GRISI Daily Update — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    us_data = global_ctx = None
    tw_data = tw_retail = None
    usdtwd = None

    if args.us:
        us_data, global_ctx = fetch_us_data()

    if args.tw:
        tw_data, tw_retail, usdtwd = fetch_tw_data()

    snapshot = update_snapshot(us_data, tw_data, tw_retail, global_ctx, usdtwd)
    update_dashboard_json(snapshot)

    print("\n[DONE] Dashboard data updated. Agent narrative needs manual Claude update.")
    print("  Run: /market-analyst to generate full report")


if __name__ == '__main__':
    main()
