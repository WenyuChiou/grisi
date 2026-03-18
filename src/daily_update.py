"""
Moodring Daily Update Pipeline
============================
Automated daily data refresh → score calculation → dashboard JSON update.
Designed to run via GitHub Actions or local cron.

Data Sources:
  - Yahoo Finance (yfinance): SPY, VIX, ^TNX, ^TWII, 2330.TW, GC=F, USDJPY=X, TWD=X,
    ^N225 (Nikkei), ^KS11 (KOSPI), ^STOXX50E (EURO STOXX 50)
  - FinMind API (TWSE OpenData): margin balance, institutional investors

Usage:
  python daily_update.py           # Update all markets
  python daily_update.py --us      # Update US only
  python daily_update.py --tw      # Update TW only
  python daily_update.py --jp      # Update Japan only
  python daily_update.py --kr      # Update Korea only
  python daily_update.py --eu      # Update Europe only
"""

import sys
import os
import json
import argparse
from datetime import datetime, timedelta

# Ensure utf-8
os.environ['PYTHONUTF8'] = '1'

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')

def safe_round(val, decimals=2):
    """Round a value, converting NaN/inf to None for JSON safety."""
    import math
    if val is None or (isinstance(val, float) and (math.isnan(val) or math.isinf(val))):
        return None
    return round(float(val), decimals)

def sanitize_for_json(obj):
    """Recursively replace NaN/Infinity with None for valid JSON output."""
    import math
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    elif isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_for_json(v) for v in obj]
    return obj



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


def fetch_jp_data():
    """Fetch Japan market data from Yahoo Finance."""
    import yfinance as yf

    start_90d = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
    end = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')

    print("[JP] Fetching from Yahoo Finance...")
    nikkei = yf.download('^N225', start=start_90d, end=end, progress=False)

    def safe(df):
        c = df['Close']
        return c.iloc[:, 0] if c.ndim > 1 else c

    def rsi(close, p=14):
        d = close.diff()
        g = d.where(d > 0, 0).rolling(p).mean()
        l = (-d.where(d < 0, 0)).rolling(p).mean()
        return 100 - 100 / (1 + g / l)

    nk_c = safe(nikkei)

    market_data = {
        'NIKKEI_close': round(float(nk_c.iloc[-1]), 2),
        'NIKKEI_RSI14': round(float(rsi(nk_c).iloc[-1]), 1),
        'NIKKEI_SMA20': round(float(nk_c.rolling(20).mean().iloc[-1]), 2),
        'NIKKEI_vs_52w_high_pct': round(float(nk_c.iloc[-1] / nk_c.rolling(252, min_periods=60).max().iloc[-1] * 100), 1),
        'NIKKEI_5d_return_pct': round(float((nk_c.iloc[-1] / nk_c.iloc[-6] - 1) * 100), 2),
        'NIKKEI_20d_return_pct': round(float((nk_c.iloc[-1] / nk_c.iloc[-21] - 1) * 100), 2),
    }

    print(f"[JP] Nikkei={market_data['NIKKEI_close']}, RSI={market_data['NIKKEI_RSI14']}")
    return market_data


def fetch_kr_data():
    """Fetch Korea market data from Yahoo Finance."""
    import yfinance as yf

    start_90d = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
    end = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')

    print("[KR] Fetching from Yahoo Finance...")
    kospi = yf.download('^KS11', start=start_90d, end=end, progress=False)

    def safe(df):
        c = df['Close']
        return c.iloc[:, 0] if c.ndim > 1 else c

    def rsi(close, p=14):
        d = close.diff()
        g = d.where(d > 0, 0).rolling(p).mean()
        l = (-d.where(d < 0, 0)).rolling(p).mean()
        return 100 - 100 / (1 + g / l)

    ks_c = safe(kospi)

    market_data = {
        'KOSPI_close': round(float(ks_c.iloc[-1]), 2),
        'KOSPI_RSI14': round(float(rsi(ks_c).iloc[-1]), 1),
        'KOSPI_SMA20': round(float(ks_c.rolling(20).mean().iloc[-1]), 2),
        'KOSPI_vs_52w_high_pct': round(float(ks_c.iloc[-1] / ks_c.rolling(252, min_periods=60).max().iloc[-1] * 100), 1),
        'KOSPI_5d_return_pct': round(float((ks_c.iloc[-1] / ks_c.iloc[-6] - 1) * 100), 2),
        'KOSPI_20d_return_pct': round(float((ks_c.iloc[-1] / ks_c.iloc[-21] - 1) * 100), 2),
    }

    print(f"[KR] KOSPI={market_data['KOSPI_close']}, RSI={market_data['KOSPI_RSI14']}")
    return market_data


def fetch_eu_data():
    """Fetch Europe market data from Yahoo Finance."""
    import yfinance as yf

    start_90d = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
    end = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')

    print("[EU] Fetching from Yahoo Finance...")
    stoxx = yf.download('^STOXX50E', start=start_90d, end=end, progress=False)

    def safe(df):
        c = df['Close']
        return c.iloc[:, 0] if c.ndim > 1 else c

    def rsi(close, p=14):
        d = close.diff()
        g = d.where(d > 0, 0).rolling(p).mean()
        l = (-d.where(d < 0, 0)).rolling(p).mean()
        return 100 - 100 / (1 + g / l)

    sx_c = safe(stoxx)

    market_data = {
        'STOXX50_close': round(float(sx_c.iloc[-1]), 2),
        'STOXX50_RSI14': round(float(rsi(sx_c).iloc[-1]), 1),
        'STOXX50_SMA20': round(float(sx_c.rolling(20).mean().iloc[-1]), 2),
        'STOXX50_vs_52w_high_pct': round(float(sx_c.iloc[-1] / sx_c.rolling(252, min_periods=60).max().iloc[-1] * 100), 1),
        'STOXX50_5d_return_pct': round(float((sx_c.iloc[-1] / sx_c.iloc[-6] - 1) * 100), 2),
        'STOXX50_20d_return_pct': round(float((sx_c.iloc[-1] / sx_c.iloc[-21] - 1) * 100), 2),
    }

    print(f"[EU] STOXX50={market_data['STOXX50_close']}, RSI={market_data['STOXX50_RSI14']}")
    return market_data


def compute_score(market_data, prefix):
    """Compute simple Moodring score for a market. 0=fear, 100=greed."""
    scores = []

    # 1. RSI position (high RSI = greedy)
    rsi = market_data.get(f'{prefix}_RSI14', 50)
    scores.append(rsi)  # RSI is already 0-100

    # 2. Position vs 52w high (near high = greedy)
    vs_high = market_data.get(f'{prefix}_vs_52w_high_pct', 90)
    # Map 80-100% to 0-100 score
    scores.append(max(0, min(100, (vs_high - 80) * 5)))

    # 3. 20d momentum (positive = greedy)
    mom = market_data.get(f'{prefix}_20d_return_pct', 0)
    # Map -10% to +10% to 0-100
    scores.append(max(0, min(100, (mom + 10) * 5)))

    return round(sum(scores) / len(scores), 1)


def update_snapshot(us_data=None, tw_data=None, tw_retail=None, global_ctx=None, usdtwd=None,
                    jp_data=None, kr_data=None, eu_data=None):
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
    if jp_data:
        snapshot['jp_market'] = jp_data
    if kr_data:
        snapshot['kr_market'] = kr_data
    if eu_data:
        snapshot['eu_market'] = eu_data
    if global_ctx:
        if usdtwd:
            global_ctx['USDTWD'] = usdtwd
        snapshot['global_context'] = global_ctx

    # Save dated + latest
    dated_path = os.path.join(DATA_DIR, f"snapshot_{today.replace('-', '')}.json")
    latest_path = os.path.join(DATA_DIR, 'snapshot_latest.json')

    clean_snapshot = sanitize_for_json(snapshot)
    for path in [dated_path, latest_path]:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(clean_snapshot, f, indent=2, ensure_ascii=False)

    print(f"[SAVE] Snapshot saved: {dated_path}")
    return snapshot


def update_dashboard_json(snapshot, jp_score=None, kr_score=None, eu_score=None):
    """Update dashboard_data.json with latest snapshot."""
    dd_path = os.path.join(DATA_DIR, 'dashboard_data.json')

    with open(dd_path, 'r', encoding='utf-8') as f:
        dd = json.load(f)

    dd['snapshot'] = snapshot

    # Append new market scores
    if jp_score is not None:
        if 'jp_score' not in dd:
            dd['jp_score'] = []
        dd['jp_score'].append(jp_score)
    if kr_score is not None:
        if 'kr_score' not in dd:
            dd['kr_score'] = []
        dd['kr_score'].append(kr_score)
    if eu_score is not None:
        if 'eu_score' not in dd:
            dd['eu_score'] = []
        dd['eu_score'].append(eu_score)

    dd = sanitize_for_json(dd)
    with open(dd_path, 'w', encoding='utf-8') as f:
        json.dump(dd, f, ensure_ascii=False)

    print("[SAVE] dashboard_data.json updated")


def update_agent_results(snapshot, us_data, tw_data, tw_retail, jp_data, kr_data, eu_data, global_ctx):
    """Update phase2_agent_results.json with today's date and scores."""
    path = os.path.join(DATA_DIR, 'phase2_agent_results.json')
    if not os.path.exists(path):
        print("[SKIP] phase2_agent_results.json not found")
        return

    with open(path, 'r', encoding='utf-8') as f:
        agents = json.load(f)

    today = datetime.now().strftime('%Y-%m-%d')
    agents['date'] = today

    # Update base scores from dashboard_data
    dd_path = os.path.join(DATA_DIR, 'dashboard_data.json')
    with open(dd_path, 'r', encoding='utf-8') as f:
        dd = json.load(f)

    us_scores = dd.get('us_score', [])
    tw_scores = dd.get('tw_score', [])
    if us_scores:
        agents['us_base_score'] = us_scores[-1]
    if tw_scores:
        agents['tw_base_score'] = tw_scores[-1]

    agents = sanitize_for_json(agents)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(agents, f, indent=2, ensure_ascii=False)
    print(f"[SAVE] phase2_agent_results.json updated (date={today})")


def update_forward_outlook():
    """Update forward_outlook.json current scores from dashboard_data."""
    fwd_path = os.path.join(DATA_DIR, 'forward_outlook.json')
    dd_path = os.path.join(DATA_DIR, 'dashboard_data.json')
    if not os.path.exists(fwd_path):
        print("[SKIP] forward_outlook.json not found")
        return

    with open(dd_path, 'r', encoding='utf-8') as f:
        dd = json.load(f)
    with open(fwd_path, 'r', encoding='utf-8') as f:
        fwd = json.load(f)

    score_map = {
        'us_current_score': 'us_score',
        'tw_current_score': 'tw_score',
        'jp_current_score': 'jp_score',
        'kr_current_score': 'kr_score',
        'eu_current_score': 'eu_score',
    }
    for fwd_key, dd_key in score_map.items():
        scores = dd.get(dd_key, [])
        if scores:
            fwd[fwd_key] = scores[-1]

    fwd = sanitize_for_json(fwd)
    with open(fwd_path, 'w', encoding='utf-8') as f:
        json.dump(fwd, f, indent=2, ensure_ascii=False)
    print("[SAVE] forward_outlook.json scores updated")


def main():
    parser = argparse.ArgumentParser(description='Moodring Daily Update — US/TW/JP/KR/EU markets')
    parser.add_argument('--us', action='store_true', help='Update US only')
    parser.add_argument('--tw', action='store_true', help='Update TW only')
    parser.add_argument('--jp', action='store_true', help='Update Japan only')
    parser.add_argument('--kr', action='store_true', help='Update Korea only')
    parser.add_argument('--eu', action='store_true', help='Update Europe only')
    args = parser.parse_args()

    # Default: update all markets
    any_selected = args.us or args.tw or args.jp or args.kr or args.eu
    if not any_selected:
        args.us = True
        args.tw = True
        args.jp = True
        args.kr = True
        args.eu = True

    print("=" * 50)
    print(f"Moodring Daily Update — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    us_data = global_ctx = None
    tw_data = tw_retail = None
    usdtwd = None
    jp_data = kr_data = eu_data = None
    jp_score_val = kr_score_val = eu_score_val = None

    if args.us:
        us_data, global_ctx = fetch_us_data()

    if args.tw:
        tw_data, tw_retail, usdtwd = fetch_tw_data()

    if args.jp:
        jp_data = fetch_jp_data()
        jp_score_val = compute_score(jp_data, 'NIKKEI')
        jp_data['jp_moodring_score'] = jp_score_val
        print(f"[JP] Moodring score: {jp_score_val}")

    if args.kr:
        kr_data = fetch_kr_data()
        kr_score_val = compute_score(kr_data, 'KOSPI')
        kr_data['kr_moodring_score'] = kr_score_val
        print(f"[KR] Moodring score: {kr_score_val}")

    if args.eu:
        eu_data = fetch_eu_data()
        eu_score_val = compute_score(eu_data, 'STOXX50')
        eu_data['eu_moodring_score'] = eu_score_val
        print(f"[EU] Moodring score: {eu_score_val}")

    snapshot = update_snapshot(us_data, tw_data, tw_retail, global_ctx, usdtwd,
                              jp_data, kr_data, eu_data)
    update_dashboard_json(snapshot, jp_score_val, kr_score_val, eu_score_val)
    update_agent_results(snapshot, us_data, tw_data, tw_retail, jp_data, kr_data, eu_data, global_ctx)
    update_forward_outlook()

    markets = []
    if args.us: markets.append('US')
    if args.tw: markets.append('TW')
    if args.jp: markets.append('JP')
    if args.kr: markets.append('KR')
    if args.eu: markets.append('EU')
    print(f"\n[DONE] Markets updated: {', '.join(markets)}")
    print("  Run: /market-analyst to generate full report")


if __name__ == '__main__':
    main()
