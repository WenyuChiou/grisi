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
import time
import argparse
from datetime import datetime, timedelta

# Ensure utf-8
os.environ['PYTHONUTF8'] = '1'

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')


def finmind_with_retry(fn, *args, max_retries=3, backoff=10, **kwargs):
    """Call a FinMind DataLoader method with exponential backoff on rate-limit errors."""
    for attempt in range(max_retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            msg = str(e).lower()
            if attempt < max_retries - 1 and ('rate' in msg or 'limit' in msg or '429' in msg or 'too many' in msg):
                wait = backoff * (2 ** attempt)
                print(f"[FinMind] Rate limit hit, retrying in {wait}s (attempt {attempt+1}/{max_retries})...")
                time.sleep(wait)
            else:
                raise

def yf_download_with_retry(ticker, max_retries=3, **kwargs):
    """Wrap yf.download() with exponential backoff (5s, 10s, 20s) on transient errors."""
    import yfinance as yf
    delays = [5, 10, 20]
    for attempt in range(max_retries):
        try:
            result = getattr(yf, 'download')(ticker, **kwargs)
            if result is not None and not result.empty:
                return result
            if attempt < max_retries - 1:
                wait = delays[attempt]
                print(f"[yfinance] Empty result for {ticker}, retrying in {wait}s (attempt {attempt+1}/{max_retries})...")
                time.sleep(wait)
            else:
                return result
        except Exception as e:
            if attempt < max_retries - 1:
                wait = delays[attempt]
                print(f"[yfinance] Error fetching {ticker}: {e}, retrying in {wait}s (attempt {attempt+1}/{max_retries})...")
                time.sleep(wait)
            else:
                raise

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



MARKET_TICKERS = {
    'us': 'SPY',
    'tw': '^TWII',
    'jp': '^N225',
    'kr': '^KS11',
    'eu': '^STOXX50E',
}


def validate_market_open(date, market):
    """Check if a market was open on the given date.

    Uses yfinance to verify whether a valid (non-zero) price exists for
    that date. Returns True if the market was open, False if closed.

    Args:
        date: Date string 'YYYY-MM-DD' or datetime object.
        market: One of 'us', 'tw', 'jp', 'kr', 'eu'.

    Returns:
        bool: True if market was open on that date.
    """
    import pandas as pd

    if isinstance(date, datetime):
        date_str = date.strftime('%Y-%m-%d')
    else:
        date_str = str(date)

    ticker = MARKET_TICKERS.get(market.lower())
    if not ticker:
        print(f"[VALIDATE] Unknown market: {market}")
        return True  # Assume open if unknown

    check_date = datetime.strptime(date_str, '%Y-%m-%d')
    start = (check_date - timedelta(days=10)).strftime('%Y-%m-%d')
    end = (check_date + timedelta(days=2)).strftime('%Y-%m-%d')

    try:
        raw = yf_download_with_retry(ticker, start=start, end=end, progress=False, auto_adjust=True)
        if raw is None or raw.empty:
            print(f"[VALIDATE] {market.upper()}/{ticker}: no data returned for {date_str}, assuming closed")
            return False

        close = raw['Close']
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        close = close.dropna()
        close = close[close > 0]  # Remove zero/invalid prices

        if close.empty:
            return False

        if hasattr(close.index, 'tz') and close.index.tz is not None:
            close.index = close.index.tz_localize(None)

        dates_in_data = {d.strftime('%Y-%m-%d') for d in close.index}
        is_open = date_str in dates_in_data

        if not is_open:
            valid_before = sorted(d for d in dates_in_data if d <= date_str)
            last_td = valid_before[-1] if valid_before else 'unknown'
            print(f"[VALIDATE] {market.upper()}: CLOSED on {date_str} (last trading day: {last_td})")

        return is_open
    except Exception as e:
        print(f"[VALIDATE] Error checking {market.upper()} on {date_str}: {e}")
        return True  # Fail-safe: assume open to avoid skipping valid data


def get_last_valid_score(market):
    """Get the most recent valid score for a market (used for carry-forward on holidays).

    Args:
        market: One of 'us', 'tw', 'jp', 'kr', 'eu'.

    Returns:
        float or None: Last valid score, or None if not found.
    """
    import csv as csv_module

    market = market.lower()

    if market in ('us', 'tw'):
        csv_path = os.path.join(DATA_DIR, 'historical_scores.csv')
        if os.path.exists(csv_path):
            with open(csv_path, 'r', encoding='utf-8') as f:
                rows = list(csv_module.DictReader(f))
            col = f'{market}_score'
            for row in reversed(rows):
                val = row.get(col, '').strip()
                if val:
                    try:
                        return float(val)
                    except ValueError:
                        continue
    else:
        ov_path = os.path.join(DATA_DIR, 'overlay_data.json')
        if os.path.exists(ov_path):
            with open(ov_path, 'r', encoding='utf-8') as f:
                ov = json.load(f)
            scores = ov.get(f'{market}_score', [])
            for s in reversed(scores):
                if s is not None:
                    return float(s)

    return None


def clean_holiday_anomalies(sync_docs=True):
    """Retroactively fix holiday/market-closed artifacts in overlay_data.json
    and historical_scores.csv.

    For each (date, score) entry:
      - If the date has no corresponding price data (market was closed), replace
        the score with the previous valid day's carry-forward value.
      - Also detects sudden >50% score drops/spikes that recover the next day
        (classic holiday artifact pattern).

    Args:
        sync_docs: If True, sync fixed files to docs/data/ after cleanup.
    """
    import csv as csv_module
    import shutil

    ov_path = os.path.join(DATA_DIR, 'overlay_data.json')
    csv_path = os.path.join(DATA_DIR, 'historical_scores.csv')

    if not os.path.exists(ov_path):
        print("[CLEAN] overlay_data.json not found, skipping")
        return

    with open(ov_path, 'r', encoding='utf-8') as f:
        ov = json.load(f)

    fixed_count = 0

    # (score_dates_key, score_key, price_dates_key) for each market
    market_configs = [
        ('dates', 'us_score', 'spy_dates'),
        ('dates', 'tw_score', 'twii_dates'),
        ('jp_dates', 'jp_score', 'nikkei_dates'),
        ('kr_dates', 'kr_score', 'kospi_dates'),
        ('eu_dates', 'eu_score', 'stoxx50_dates'),
    ]

    for dates_key, score_key, price_dates_key in market_configs:
        score_dates = ov.get(dates_key, [])
        scores = ov.get(score_key, [])
        price_dates_set = set(ov.get(price_dates_key, []))

        if not score_dates or not scores:
            continue
        if len(score_dates) != len(scores):
            print(f"[CLEAN] {score_key}: dates/scores length mismatch, skipping")
            continue

        new_scores = list(scores)
        last_valid_score = None

        # Pass 1: carry-forward for dates with no price data
        for i, (date, score) in enumerate(zip(score_dates, scores)):
            if score is None:
                continue
            if date in price_dates_set:
                # Valid trading day — update last known good score
                if float(score) > 0:
                    last_valid_score = score
            else:
                # No price for this date → market was closed
                if last_valid_score is not None:
                    print(f"[CLEAN] {score_key} {date}: {score} → {last_valid_score} (no price, carry-forward)")
                    new_scores[i] = last_valid_score
                    fixed_count += 1

        # Pass 2: spike/dip detection (>50% deviation from both neighbors)
        for i in range(1, len(new_scores) - 1):
            s_prev = new_scores[i - 1]
            s_curr = new_scores[i]
            s_next = new_scores[i + 1]
            if s_prev is None or s_curr is None or s_next is None:
                continue
            if s_prev <= 0 or s_next <= 0:
                continue
            pct_from_prev = abs(s_curr - s_prev) / s_prev * 100
            pct_from_next = abs(s_curr - s_next) / s_next * 100
            if pct_from_prev > 50 and pct_from_next > 50:
                fixed_val = round((s_prev + s_next) / 2, 1)
                print(f"[CLEAN] {score_key} {score_dates[i]}: spike/dip {s_curr} → {fixed_val} "
                      f"(neighbors: {s_prev}, {s_next})")
                new_scores[i] = fixed_val
                fixed_count += 1

        ov[score_key] = new_scores

    print(f"[CLEAN] Fixed {fixed_count} anomalies in overlay_data.json")

    ov = sanitize_for_json(ov)
    with open(ov_path, 'w', encoding='utf-8') as f:
        json.dump(ov, f, ensure_ascii=False)
    print("[CLEAN] Saved overlay_data.json")

    # ── Fix historical_scores.csv (US/TW) ──
    if os.path.exists(csv_path):
        spy_dates_set = set(ov.get('spy_dates', []))
        twii_dates_set = set(ov.get('twii_dates', []))

        with open(csv_path, 'r', encoding='utf-8', newline='') as f:
            rows = list(csv_module.DictReader(f))

        csv_fixed = 0
        last_us = None
        last_tw = None

        for row in rows:
            date = row.get('date', '')
            us_str = row.get('us_score', '').strip()
            tw_str = row.get('tw_score', '').strip()

            us_val = float(us_str) if us_str else None
            tw_val = float(tw_str) if tw_str else None

            us_changed = tw_changed = False

            if us_val is not None:
                if date not in spy_dates_set and last_us is not None:
                    print(f"[CLEAN-CSV] us_score {date}: {us_val} → {last_us} (no SPY price)")
                    row['us_score'] = str(last_us)
                    us_changed = True
                    csv_fixed += 1
                else:
                    last_us = us_val

            if tw_val is not None:
                if date not in twii_dates_set and last_tw is not None:
                    print(f"[CLEAN-CSV] tw_score {date}: {tw_val} → {last_tw} (no TWII price)")
                    row['tw_score'] = str(last_tw)
                    tw_changed = True
                    csv_fixed += 1
                else:
                    last_tw = tw_val

            if us_changed or tw_changed:
                us_v = float(row.get('us_score', 0) or 0)
                tw_v = float(row.get('tw_score', 0) or 0)
                row['divergence'] = str(round(abs(us_v - tw_v), 1))

        print(f"[CLEAN-CSV] Fixed {csv_fixed} rows in historical_scores.csv")

        with open(csv_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv_module.DictWriter(f, fieldnames=['date', 'us_score', 'tw_score', 'divergence'])
            writer.writeheader()
            writer.writerows(rows)
        print("[CLEAN-CSV] Saved historical_scores.csv")

    # ── Sync to docs/data/ ──
    if sync_docs:
        docs_data_dir = os.path.normpath(os.path.join(DATA_DIR, '..', 'docs', 'data'))
        if os.path.isdir(docs_data_dir):
            for fname in ['overlay_data.json', 'historical_scores.csv']:
                src = os.path.join(DATA_DIR, fname)
                if os.path.exists(src):
                    shutil.copy2(src, os.path.join(docs_data_dir, fname))
            print("[CLEAN] Synced cleaned files → docs/data/")


def fetch_us_data():
    """Fetch US market data from Yahoo Finance."""
    import yfinance as yf

    today = datetime.now().strftime('%Y-%m-%d')
    start_90d = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
    end = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')

    print("[US] Fetching from Yahoo Finance...")
    spy = yf_download_with_retry('SPY', start=start_90d, end=end, progress=False, auto_adjust=True)
    vix = yf_download_with_retry('^VIX', start=start_90d, end=end, progress=False, auto_adjust=True)
    tnx = yf_download_with_retry('^TNX', start=(datetime.now()-timedelta(30)).strftime('%Y-%m-%d'), end=end, progress=False, auto_adjust=True)
    gold = yf_download_with_retry('GC=F', period='1mo', progress=False, auto_adjust=True)
    usdjpy = yf_download_with_retry('USDJPY=X', period='1mo', progress=False, auto_adjust=True)

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

    today = datetime.now().strftime('%Y-%m-%d')
    spy_idx = spy_c.index.tz_localize(None) if (hasattr(spy_c.index, 'tz') and spy_c.index.tz is not None) else spy_c.index
    spy_dates = [d.strftime('%Y-%m-%d') for d in spy_idx]
    market_open = today in spy_dates and float(spy_c.iloc[-1]) > 0
    if not market_open:
        last_date = spy_dates[-1] if spy_dates else 'none'
        print(f"[US] Market CLOSED on {today} (last trading date in data: {last_date})")

    print(f"[US] SPY=${data['SPY_close']}, VIX={data['VIX']}, RSI={data['SPY_RSI14']}")
    return data, global_ctx, market_open


def fetch_tw_data():
    """Fetch Taiwan market data from Yahoo Finance + FinMind."""
    import yfinance as yf

    today = datetime.now().strftime('%Y-%m-%d')
    start_90d = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
    end = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')

    print("[TW] Fetching from Yahoo Finance...")
    twii = yf_download_with_retry('^TWII', start=start_90d, end=end, progress=False, auto_adjust=True)
    tsmc = yf_download_with_retry('2330.TW', start=start_90d, end=end, progress=False, auto_adjust=True)
    usdtwd = yf_download_with_retry('TWD=X', period='1mo', progress=False, auto_adjust=True)

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
        'TAIEX_5d_return_pct': round(float((twii_c.iloc[-1] / twii_c.iloc[-6] - 1) * 100), 2),
        'TAIEX_20d_return_pct': round(float((twii_c.iloc[-1] / twii_c.iloc[-21] - 1) * 100), 2),
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

        # Dynamic start dates: margin needs ~30d history, institutional needs ~20d
        margin_start = (datetime.now() - timedelta(days=45)).strftime('%Y-%m-%d')
        inst_start = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')

        # Margin balance
        margin = finmind_with_retry(
            dl.taiwan_stock_margin_purchase_short_sale_total,
            start_date=margin_start, end_date=today
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
        inst = finmind_with_retry(
            dl.taiwan_stock_institutional_investors_total,
            start_date=inst_start, end_date=today
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
        tsmc_margin = finmind_with_retry(
            dl.taiwan_stock_margin_purchase_short_sale,
            stock_id='2330', start_date=margin_start, end_date=today
        )
        if len(tsmc_margin) > 0:
            tl = int(tsmc_margin.iloc[-1]['MarginPurchaseTodayBalance'])
            tf = int(tsmc_margin.iloc[0]['MarginPurchaseTodayBalance'])
            retail_data['TSMC_margin_balance'] = tl
            retail_data['TSMC_margin_30d_change_pct'] = round((tl - tf) / tf * 100, 2)

    except Exception as e:
        print(f"[TW] FinMind partial error: {e}")

    today = datetime.now().strftime('%Y-%m-%d')
    twii_idx = twii_c.index.tz_localize(None) if (hasattr(twii_c.index, 'tz') and twii_c.index.tz is not None) else twii_c.index
    twii_dates = [d.strftime('%Y-%m-%d') for d in twii_idx]
    market_open = today in twii_dates and float(twii_c.iloc[-1]) > 0
    if not market_open:
        last_date = twii_dates[-1] if twii_dates else 'none'
        print(f"[TW] Market CLOSED on {today} (last trading date in data: {last_date})")

    print(f"[TW] TAIEX={market_data['TAIEX_close']}, TSMC={market_data['TSMC_close']}")
    return market_data, retail_data, usdtwd_val, market_open


def fetch_jp_data():
    """Fetch Japan market data from Yahoo Finance."""
    import yfinance as yf

    # 400 days ensures 252+ trading days for rolling 52w high calculation
    start_400d = (datetime.now() - timedelta(days=400)).strftime('%Y-%m-%d')
    start_90d = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
    end = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')

    print("[JP] Fetching from Yahoo Finance...")
    nikkei = yf_download_with_retry('^N225', start=start_400d, end=end, progress=False, auto_adjust=True)

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

    today = datetime.now().strftime('%Y-%m-%d')
    nk_idx = nk_c.index.tz_localize(None) if (hasattr(nk_c.index, 'tz') and nk_c.index.tz is not None) else nk_c.index
    nk_dates = [d.strftime('%Y-%m-%d') for d in nk_idx]
    market_open = today in nk_dates and float(nk_c.iloc[-1]) > 0
    if not market_open:
        last_date = nk_dates[-1] if nk_dates else 'none'
        print(f"[JP] Market CLOSED on {today} (last trading date in data: {last_date})")

    print(f"[JP] Nikkei={market_data['NIKKEI_close']}, RSI={market_data['NIKKEI_RSI14']}")
    return market_data, market_open


def fetch_kr_data():
    """Fetch Korea market data from Yahoo Finance."""
    import yfinance as yf

    # 400 days ensures 252+ trading days for rolling 52w high calculation
    start_400d = (datetime.now() - timedelta(days=400)).strftime('%Y-%m-%d')
    end = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')

    print("[KR] Fetching from Yahoo Finance...")
    kospi = yf_download_with_retry('^KS11', start=start_400d, end=end, progress=False, auto_adjust=True)

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

    today = datetime.now().strftime('%Y-%m-%d')
    ks_idx = ks_c.index.tz_localize(None) if (hasattr(ks_c.index, 'tz') and ks_c.index.tz is not None) else ks_c.index
    ks_dates = [d.strftime('%Y-%m-%d') for d in ks_idx]
    market_open = today in ks_dates and float(ks_c.iloc[-1]) > 0
    if not market_open:
        last_date = ks_dates[-1] if ks_dates else 'none'
        print(f"[KR] Market CLOSED on {today} (last trading date in data: {last_date})")

    print(f"[KR] KOSPI={market_data['KOSPI_close']}, RSI={market_data['KOSPI_RSI14']}")
    return market_data, market_open


def fetch_eu_data():
    """Fetch Europe market data from Yahoo Finance."""
    import yfinance as yf

    # 400 days ensures 252+ trading days for rolling 52w high calculation
    start_400d = (datetime.now() - timedelta(days=400)).strftime('%Y-%m-%d')
    end = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')

    print("[EU] Fetching from Yahoo Finance...")
    stoxx = yf_download_with_retry('^STOXX50E', start=start_400d, end=end, progress=False, auto_adjust=True)

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

    today = datetime.now().strftime('%Y-%m-%d')
    sx_idx = sx_c.index.tz_localize(None) if (hasattr(sx_c.index, 'tz') and sx_c.index.tz is not None) else sx_c.index
    sx_dates = [d.strftime('%Y-%m-%d') for d in sx_idx]
    market_open = today in sx_dates and float(sx_c.iloc[-1]) > 0
    if not market_open:
        last_date = sx_dates[-1] if sx_dates else 'none'
        print(f"[EU] Market CLOSED on {today} (last trading date in data: {last_date})")

    print(f"[EU] STOXX50={market_data['STOXX50_close']}, RSI={market_data['STOXX50_RSI14']}")
    return market_data, market_open


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


def append_scores_to_csv(us_score=None, tw_score=None):
    """Append today's US/TW scores to historical_scores.csv.

    This is required so that rebuild_dashboard_daily.py picks up the latest
    scores when it rebuilds dashboard_data.json from the CSV.
    """
    import csv

    csv_path = os.path.join(DATA_DIR, 'historical_scores.csv')
    today = datetime.now().strftime('%Y-%m-%d')

    # Read existing rows to check for duplicate and get current data
    rows = []
    if os.path.exists(csv_path):
        with open(csv_path, 'r', encoding='utf-8', newline='') as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or ['date', 'us_score', 'tw_score', 'divergence']
            rows = list(reader)

    # Check if today already exists
    if rows and rows[-1].get('date') == today:
        print(f"[CSV] historical_scores.csv already has entry for {today}, updating in place")
        last = rows[-1]
        if us_score is not None:
            last['us_score'] = round(us_score, 1)
        if tw_score is not None:
            last['tw_score'] = round(tw_score, 1)
        us_val = float(last.get('us_score', 0) or 0)
        tw_val = float(last.get('tw_score', 0) or 0)
        last['divergence'] = round(abs(us_val - tw_val), 1)
    else:
        # Fill missing score from last row if not provided
        last_us = float(rows[-1].get('us_score', 50) or 50) if rows else 50
        last_tw = float(rows[-1].get('tw_score', 50) or 50) if rows else 50
        us_val = us_score if us_score is not None else last_us
        tw_val = tw_score if tw_score is not None else last_tw
        new_row = {
            'date': today,
            'us_score': round(us_val, 1),
            'tw_score': round(tw_val, 1),
            'divergence': round(abs(us_val - tw_val), 1),
        }
        rows.append(new_row)
        print(f"[CSV] Appended to historical_scores.csv: {today} us={round(us_val,1)} tw={round(tw_val,1)}")

    with open(csv_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['date', 'us_score', 'tw_score', 'divergence'])
        writer.writeheader()
        writer.writerows(rows)


def update_dashboard_json(snapshot, jp_score=None, kr_score=None, eu_score=None):
    """Update dashboard_data.json with latest snapshot."""
    dd_path = os.path.join(DATA_DIR, 'dashboard_data.json')

    with open(dd_path, 'r', encoding='utf-8') as f:
        dd = json.load(f)

    dd['snapshot'] = snapshot

    today = datetime.now().strftime('%Y-%m-%d')

    # Append new market scores AND corresponding dates (keep arrays in sync)
    for score_val, score_key, dates_key in [
        (jp_score, 'jp_score', 'jp_dates'),
        (kr_score, 'kr_score', 'kr_dates'),
        (eu_score, 'eu_score', 'eu_dates'),
    ]:
        if score_val is not None:
            scores = dd.setdefault(score_key, [])
            dates = dd.setdefault(dates_key, [])
            # Avoid duplicate entry for today
            if dates and dates[-1] == today:
                scores[-1] = score_val  # overwrite today's score if re-run
            else:
                scores.append(score_val)
                dates.append(today)

    dd = sanitize_for_json(dd)
    with open(dd_path, 'w', encoding='utf-8') as f:
        json.dump(dd, f, ensure_ascii=False)

    print("[SAVE] dashboard_data.json updated")


def update_overlay_json(snapshot, jp_score=None, kr_score=None, eu_score=None,
                        us_open=True, tw_open=True, jp_open=True, kr_open=True, eu_open=True):
    """Append today's scores and prices to overlay_data.json (used by overlay chart).

    Only writes price entries for markets that were actually open today (us_open, tw_open, etc.).
    Carry-forward scores are still written regardless of open status, but prices from stale
    (closed-market) fetches are skipped to prevent holiday artifacts.
    """
    ov_path = os.path.join(DATA_DIR, 'overlay_data.json')
    if not os.path.exists(ov_path):
        print("[SKIP] overlay_data.json not found")
        return

    with open(ov_path, 'r', encoding='utf-8') as f:
        ov = json.load(f)

    today = datetime.now().strftime('%Y-%m-%d')

    # Avoid duplicate entries
    existing_dates = ov.get('dates', [])
    if existing_dates and existing_dates[-1] == today:
        print("[SKIP] overlay_data.json already has today's data")
        return

    # US/TW scores from dashboard_data (latest appended value)
    dd_path = os.path.join(DATA_DIR, 'dashboard_data.json')
    with open(dd_path, 'r', encoding='utf-8') as f:
        dd = json.load(f)

    us_scores = dd.get('us_score', [])
    tw_scores = dd.get('tw_score', [])
    if us_scores:
        ov.setdefault('dates', []).append(today)
        ov.setdefault('us_score', []).append(us_scores[-1])
    if tw_scores:
        ov.setdefault('tw_score', []).append(tw_scores[-1])

    # Prices from snapshot
    us_mkt = snapshot.get('us_market', {})
    tw_mkt = snapshot.get('tw_market', {})
    jp_mkt = snapshot.get('jp_market', {})
    kr_mkt = snapshot.get('kr_market', {})
    eu_mkt = snapshot.get('eu_market', {})

    def append_price(dates_key, price_key, value):
        if value is not None:
            existing = ov.get(dates_key, [])
            if not existing or existing[-1] != today:
                ov.setdefault(dates_key, []).append(today)
                ov.setdefault(price_key, []).append(round(float(value), 2))

    if us_open:
        append_price('spy_dates', 'spy', us_mkt.get('SPY_close'))
    if tw_open:
        append_price('twii_dates', 'twii', tw_mkt.get('TAIEX_close'))
    if jp_open:
        append_price('nikkei_dates', 'nikkei', jp_mkt.get('NIKKEI_close'))
    if kr_open:
        append_price('kospi_dates', 'kospi', kr_mkt.get('KOSPI_close'))
    if eu_open:
        append_price('stoxx50_dates', 'stoxx50', eu_mkt.get('STOXX50_close'))

    # JP/KR/EU scores
    for mkt, score_val, d_key, s_key in [
        ('jp', jp_score, 'jp_dates', 'jp_score'),
        ('kr', kr_score, 'kr_dates', 'kr_score'),
        ('eu', eu_score, 'eu_dates', 'eu_score'),
    ]:
        if score_val is not None:
            existing = ov.get(d_key, [])
            if not existing or existing[-1] != today:
                ov.setdefault(d_key, []).append(today)
                ov.setdefault(s_key, []).append(round(float(score_val), 1))

    ov = sanitize_for_json(ov)
    with open(ov_path, 'w', encoding='utf-8') as f:
        json.dump(ov, f, ensure_ascii=False)

    print("[SAVE] overlay_data.json updated")


def generate_narrative(mkt_data, mkt_name, retail=None, global_ctx=None, score=None):
    """Generate a short narrative for a market based on current data."""
    if not mkt_data:
        return None

    prefix_map = {
        'US': ('SPY', 'SPY_close', 'SPY_RSI14', 'SPY_5d_return_pct', 'SPY_20d_return_pct'),
        'TW': ('TAIEX', 'TAIEX_close', None, None, None),
        'JP': ('Nikkei', 'NIKKEI_close', 'NIKKEI_RSI14', 'NIKKEI_5d_return_pct', 'NIKKEI_20d_return_pct'),
        'KR': ('KOSPI', 'KOSPI_close', 'KOSPI_RSI14', 'KOSPI_5d_return_pct', 'KOSPI_20d_return_pct'),
        'EU': ('STOXX50', 'STOXX50_close', 'STOXX50_RSI14', 'STOXX50_5d_return_pct', 'STOXX50_20d_return_pct'),
    }

    idx_name, close_key, rsi_key, r5d_key, r20d_key = prefix_map.get(mkt_name, (None,)*5)
    if not idx_name:
        return None

    close = mkt_data.get(close_key, '?')
    rsi = mkt_data.get(rsi_key, '?') if rsi_key else '?'
    r5d = mkt_data.get(r5d_key) if r5d_key else None
    r20d = mkt_data.get(r20d_key) if r20d_key else None

    parts = [f"{idx_name} at {close:,.0f}" if isinstance(close, (int, float)) else f"{idx_name} at {close}"]

    if rsi != '?':
        zone = 'oversold' if rsi < 30 else 'near oversold' if rsi < 35 else 'overbought' if rsi > 70 else 'neutral'
        parts.append(f"RSI {rsi} ({zone})")

    if r5d is not None:
        parts.append(f"5d return {r5d:+.1f}%")
    if r20d is not None:
        parts.append(f"20d return {r20d:+.1f}%")

    if score is not None:
        if score < 25:
            mood = "extreme fear — historically strong buy signal"
        elif score < 40:
            mood = "fearful — contrarian opportunity building"
        elif score < 60:
            mood = "neutral — no strong directional signal"
        elif score < 75:
            mood = "greedy — caution warranted"
        else:
            mood = "extreme greed — historically poor entry point"
        parts.append(f"Moodring score {score:.1f} ({mood})")

    narrative = ". ".join(parts) + "."

    # US-specific additions
    if mkt_name == 'US':
        vix = mkt_data.get('VIX')
        if vix:
            vix_desc = 'elevated' if vix > 20 else 'calm' if vix < 15 else 'moderate'
            narrative += f" VIX at {vix} ({vix_desc})."
        if global_ctx:
            gold = global_ctx.get('Gold')
            if gold:
                narrative += f" Gold ${gold:,.0f}."

    # TW-specific additions
    if mkt_name == 'TW' and retail:
        fw = retail.get('foreign_net_TWD')
        consec = retail.get('foreign_consecutive_days', 0)
        direction = retail.get('foreign_consecutive_direction', '')
        margin_chg = retail.get('margin_5d_change_pct')
        if fw is not None:
            narrative += f" Foreign investors net {'buy' if fw > 0 else 'sell'} TWD {abs(fw):.1f}B, {consec}d consecutive {direction}."
        if margin_chg is not None:
            narrative += f" Margin balance 5d change {margin_chg:+.1f}%."
        tsmc_margin = retail.get('TSMC_margin_30d_change_pct')
        if tsmc_margin is not None:
            narrative += f" TSMC margin 30d change {tsmc_margin:+.1f}%."

    return narrative


def score_to_sentiment_level(score):
    """Map a Moodring score to a sentiment level string."""
    if score is None:
        return None
    if score < 25:
        return 'EXTREME_FEAR'
    if score < 40:
        return 'FEARFUL'
    if score < 55:
        return 'NEUTRAL'
    if score < 75:
        return 'GREEDY'
    return 'EXTREME_GREED'


def score_to_action(score):
    """Map a Moodring score to a Chinese action recommendation."""
    if score is None:
        return '觀望持有'
    if score < 25:
        return '積極加碼'
    if score < 40:
        return '逢低布局'
    if score < 55:
        return '觀望持有'
    if score < 75:
        return '逐步減碼'
    return '積極減碼'


def generate_narrative_tw(mkt_data, mkt_name, retail=None, score=None):
    """Generate emotional first-person retail investor monologue in Traditional Chinese.
    Simulates retail psychology — irrational, fearful or greedy — based on current sentiment.
    """
    if not mkt_data or score is None:
        return None

    if score < 25:
        level = 'extreme_fear'
    elif score < 40:
        level = 'fearful'
    elif score < 55:
        level = 'neutral'
    elif score < 75:
        level = 'bullish'
    else:
        level = 'extreme_greed'

    if mkt_name == 'US':
        r5d = mkt_data.get('SPY_5d_return_pct')
        vix = mkt_data.get('VIX')
        close = mkt_data.get('SPY_close')
        if level == 'extreme_fear':
            vix_str = f"，VIX 衝到 {vix:.0f}" if vix else ""
            return (f"完了完了！SPY 一路跌{vix_str}，這是要崩盤嗎？"
                    f"朋友都在喊要出場，我的帳面已經虧了一大塊，再不跑就來不及了。"
                    f"但萬一跑了就反彈怎麼辦？腦子一團亂，根本睡不著。")
        if level == 'fearful':
            r5d_str = f"又跌了 {r5d:+.1f}%" if r5d is not None else "持續走弱"
            vix_str = f"VIX {vix:.1f}，" if vix else ""
            return (f"這週 SPY {r5d_str}，{vix_str}帳面損失越來越大，心裡很煎熬。"
                    f"身邊的人說繼續 hold，但我越來越懷疑，這次是不是真的跌很深？"
                    f"要不要先出一些保命？等反彈再說？好難決定，一直盯盤也沒用。")
        if level == 'neutral':
            spy_str = f"美股 SPY {close:.0f}" if close else "美股"
            vix_str = f"VIX {vix:.1f}，" if vix else ""
            r5d_str = f"近 5 日 {r5d:+.1f}%，" if r5d is not None else ""
            return (f"最近{spy_str}漲漲跌跌，{vix_str}{r5d_str}看不出明確方向..."
                    "每天開盤看一下，也沒什麼特別感覺。要加碼嗎？感覺時機還沒到；"
                    "要減倉嗎？也沒那麼糟。就繼續觀望吧，等明確訊號再說。")
        if level == 'bullish':
            return ("感覺最近盤面有點強勢耶，SPY 站上來了！"
                    "朋友說 AI 概念股要爆發，我手上有一點部位...要加碼嗎？又怕追高。"
                    "但再不上車會不會錯過這波？再等等看好了。")
        # extreme_greed
        return ("這波漲太猛了！我已經全倉，朋友說還要繼續漲。"
                "每天看帳戶數字越來越好看，好爽！要不要再借點錢加碼？"
                "現在不上車以後肯定後悔！")

    if mkt_name == 'TW':
        consec = (retail or {}).get('foreign_consecutive_days', 0)
        direction = (retail or {}).get('foreign_consecutive_direction', '')
        taiex = mkt_data.get('TAIEX_close')
        tw_r5d = mkt_data.get('TAIEX_5d_return_pct')
        tsmc = mkt_data.get('TSMC_close')
        if level == 'extreme_fear':
            fw_str = f"外資連賣 {consec} 天" if direction == 'sell' else "法人大賣"
            taiex_str = f"台股 {taiex:.0f}" if taiex else "台股"
            return (f"慘了！{taiex_str} 一直跌，{fw_str}，感覺主力都在跑..."
                    f"台積電也撐不住了嗎？我的持股每天都在縮水，"
                    f"朋友說要停損，但我還是在等解套。再這樣下去真的撐不住了。")
        if level == 'fearful':
            fw_str = f"外資已經連賣 {consec} 天" if direction == 'sell' else "法人偏賣"
            r5d_str = f"，這週 {tw_r5d:+.1f}%" if tw_r5d is not None else ""
            return (f"台股最近軟軟的{r5d_str}，{fw_str}...我在想要不要先減一點倉。"
                    f"台積電融資還在增加，感覺散戶還在接，但法人不捧場讓我很擔心。"
                    f"再等等看，但心裡越來越不安。")
        if level == 'neutral':
            taiex_str = f"台股 {taiex:.0f}" if taiex else "台股"
            r5d_str = f"，這週 {tw_r5d:+.1f}%" if tw_r5d is not None else ""
            tsmc_str = f"台積電 {tsmc:.0f} 也沒什麼大動作" if tsmc else "台積電也沒什麼大動作"
            fw_str = f"外資連{'買' if direction=='buy' else '賣'} {consec} 天" if consec > 0 else "外資進出不穩定"
            return (f"{taiex_str}{r5d_str}，不上不下，有點難受。{fw_str}，{tsmc_str}..."
                    "這種盤最讓人糾結，不知道是要加碼還是保守一點。"
                    "先維持現在的部位好了，等方向明確再說。")
        if level == 'bullish':
            fw_str = f"外資連買 {consec} 天" if direction == 'buy' else "法人偏買"
            return (f"台股最近有點起色！{fw_str}，感覺有資金要進來..."
                    f"台積電如果繼續往上，我的帳面會好看很多。"
                    f"要加碼嗎？心裡很癢，但還是怕追高。再等一天確認方向。")
        # extreme_greed
        return ("台股大多頭！台積電帶頭衝，融資跟著爆量..."
                "這種盤一定要滿倉！我已經加碼了，朋友說這波才剛開始。"
                "現在不買等什麼？！錢放著只會貶值！")

    if mkt_name == 'JP':
        r5d = mkt_data.get('NIKKEI_5d_return_pct')
        r20d = mkt_data.get('NIKKEI_20d_return_pct')
        nikkei = mkt_data.get('NIKKEI_close')
        usdjpy = mkt_data.get('USDJPY')
        if level == 'extreme_fear':
            nikkei_str = f"日經 {nikkei:.0f}" if nikkei else "日股"
            return (f"{nikkei_str} 慘不忍睹，日圓一直升值，出口股壓力超大..."
                    "BOJ 到底要怎麼搞？日經已經跌了好多，我還要繼續 hold 嗎？"
                    "先出場觀望好了，日圓風險太難搞了。")
        if level == 'fearful':
            r5d_str = f"這週跌了 {r5d:+.1f}%" if r5d is not None else "持續下跌"
            usdjpy_str = f"，美元兌日圓 {usdjpy:.1f}" if usdjpy else ""
            return (f"日股 {r5d_str}{usdjpy_str}，日圓的走向真的讓人頭大..."
                    f"每次以為要反彈，又被 BOJ 的話壓回去。"
                    f"要不要減倉？不確定性太高，心裡不踏實。")
        if level == 'neutral':
            nikkei_str = f"日經 {nikkei:.0f}" if nikkei else "日股"
            r5d_str = f"，近 5 日 {r5d:+.1f}%" if r5d is not None else ""
            return (f"{nikkei_str}{r5d_str}，上上下下，日圓也沒個定論..."
                    "不是很敢加碼，但也沒到要跑的地步。"
                    "先維持目前部位，等 BOJ 下次開會再說。")
        if level == 'bullish':
            return ("日股感覺最近有回穩的跡象，日圓如果不繼續升，出口股應該有機會反彈..."
                    "我有點想加碼，但還在等確認訊號。希望這次是真的反彈，不是假突破。")
        # extreme_greed
        return ("日股飆起來了！日圓穩住，外資大買！"
                "這時候不加碼太可惜了，我已經加碼一些，感覺這波有搞頭！")

    if mkt_name == 'KR':
        r5d = mkt_data.get('KOSPI_5d_return_pct')
        r20d = mkt_data.get('KOSPI_20d_return_pct')
        kospi = mkt_data.get('KOSPI_close')
        if level == 'extreme_fear':
            kospi_str = f"KOSPI {kospi:.0f}" if kospi else "韓股"
            return (f"{kospi_str} 崩！三星、海力士一起跌，DRAM 需求不知道哪裡去了..."
                    "這種跌法我真的不懂，先出場保命再說。")
        if level == 'fearful':
            r5d_str = f"{r5d:+.1f}%" if r5d is not None else "偏弱"
            kospi_str = f"KOSPI {kospi:.0f}，" if kospi else ""
            return (f"{kospi_str}韓股最近 {r5d_str}，AI 記憶體的題材還管用嗎？"
                    f"三星最近感覺被市場嫌棄...不確定要不要繼續 hold，感覺風險有點高。")
        if level == 'neutral':
            kospi_str = f"KOSPI {kospi:.0f}" if kospi else "韓股"
            r5d_str = f"，近 5 日 {r5d:+.1f}%" if r5d is not None else ""
            r20d_str = f"（月跌 {r20d:+.1f}%）" if r20d is not None else ""
            return (f"{kospi_str}{r5d_str}{r20d_str}，三星和海力士一個強一個弱..."
                    "等 DRAM 報價有更清楚方向再說，現在追高追低都不划算。")
        if level == 'bullish':
            return ("韓股有點動靜！AI 記憶體需求題材還在，海力士最近表現不錯..."
                    "要加碼嗎？感覺有機會，但要等三星也跟上才放心。")
        # extreme_greed
        return ("韓股 AI 記憶體行情大爆發！海力士、三星都在飆..."
                "這種時候不能缺席，趕快加碼！等到回頭確認一定已經來不及了。")

    if mkt_name == 'EU':
        r20d = mkt_data.get('STOXX50_20d_return_pct')
        if level == 'extreme_fear':
            return ("歐股一片慘烈，俄烏問題、ECB 態度曖昧..."
                    "能源股、汽車股全部跌，感覺歐洲的問題比美股嚴重多了。"
                    "先清倉歐股，等局勢清楚再說。")
        if level == 'fearful':
            r20d_str = f"近月跌了 {r20d:+.1f}%" if r20d is not None else "持續走弱"
            return (f"歐股 {r20d_str}，能源、銀行輪流出事..."
                    f"ECB 的態度也讓人看不懂，到底要降還是不降利率？"
                    f"這種不確定性讓我不敢輕易加碼。先觀望。")
        if level == 'neutral':
            return ("歐股不上不下，沒什麼特別感覺。"
                    "德法政局、俄烏...太多地緣風險讓人難以判斷。"
                    "現在不是加碼歐股的時機，繼續等待就好。")
        if level == 'bullish':
            return ("歐股最近有點起色，德國財政刺激的消息讓市場有點興奮..."
                    "要不要進場歐股做個分散？感覺估值比美股便宜，有點吸引力。")
        # extreme_greed
        return ("歐股大反彈！德法政策刺激、俄烏局勢緩和..."
                "現在不進場太可惜了！趕快買進，這種機會不常有！")

    return None


def generate_key_factors_tw(mkt_data, mkt_name, retail=None, score=None, global_ctx=None):
    """Generate current key_factors list in Traditional Chinese from live market data."""
    factors = []

    if mkt_name == 'US':
        rsi = mkt_data.get('SPY_RSI14')
        close = mkt_data.get('SPY_close')
        sma20 = mkt_data.get('SPY_SMA20')
        sma60 = mkt_data.get('SPY_SMA60')
        r20d = mkt_data.get('SPY_20d_return_pct')
        vix = mkt_data.get('VIX')
        yield10y = mkt_data.get('US_10Y_yield')
        if rsi is not None:
            if rsi < 30:
                factors.append(f"RSI {rsi:.1f} 進入超賣 — 技術性反彈機率升高")
            elif rsi < 35:
                factors.append(f"RSI {rsi:.1f} 接近超賣 — 技術支撐區間")
            elif rsi > 70:
                factors.append(f"RSI {rsi:.1f} 超買 — 短線追高風險上升")
            else:
                factors.append(f"RSI {rsi:.1f} 中性偏弱 — 無強力技術訊號")
        if close is not None and sma20 is not None and sma60 is not None:
            if close < sma20 and close < sma60:
                factors.append(f"SPY 低於 SMA20/SMA60 雙線 — 下行趨勢仍在")
            elif close < sma20:
                factors.append(f"SPY 低於 SMA20 ({sma20:.0f}) — 短線趨勢偏空")
            elif close > sma20 and close > sma60:
                factors.append(f"SPY 站上 SMA20/SMA60 — 多頭格局")
        if vix is not None:
            if vix > 30:
                factors.append(f"VIX {vix:.1f} 恐慌高位 — 系統性風險升溫")
            elif vix > 20:
                factors.append(f"VIX {vix:.1f} 偏高未破 30 — 恐慌升溫但非極端")
            else:
                factors.append(f"VIX {vix:.1f} 溫和 — 市場波動可控")
        if r20d is not None:
            desc = '顯著修正' if r20d < -5 else '溫和回調' if r20d < 0 else '穩健上漲'
            factors.append(f"20 日報酬 {r20d:+.1f}% — {desc}")
        if yield10y is not None:
            desc = '聯準會降息預期分歧' if yield10y > 4.0 else '降息預期升溫'
            factors.append(f"10Y 殖利率 {yield10y:.2f}% — {desc}")

    elif mkt_name == 'TW':
        rsi = mkt_data.get('TAIEX_RSI14')
        if retail:
            tsmc_margin = retail.get('TSMC_margin_30d_change_pct')
            margin_5d = retail.get('margin_5d_change_pct')
            fw = retail.get('foreign_net_TWD')
            consec = retail.get('foreign_consecutive_days', 0)
            direction = retail.get('foreign_consecutive_direction', '')
            retail_net = retail.get('retail_net_est_TWD')
            if tsmc_margin is not None:
                risk = "融資槓桿集中風險" if tsmc_margin > 20 else "融資增速正常"
                factors.append(f"台積電融資月增 {tsmc_margin:+.1f}% — {risk}")
            if margin_5d is not None:
                trend = "散戶槓桿擴張" if margin_5d > 0.5 else "散戶降槓桿" if margin_5d < -0.5 else "融資餘額穩定"
                factors.append(f"融資餘額 5 日 {margin_5d:+.2f}% — {trend}")
            if fw is not None and consec > 0:
                dir_zh = "買超" if direction == 'buy' else "賣超"
                risk_zh = "外資賣壓持續" if direction == 'sell' else "外資回流訊號"
                dir_action = "買" if direction == 'buy' else "賣"
                factors.append(f"外資{dir_zh} {abs(fw):.1f}億，連{dir_action} {consec}日 — {risk_zh}")
            if retail_net is not None:
                action = "散戶逆勢接刀" if retail_net > 0 else "散戶跟空"
                dir_zh = "買超" if retail_net > 0 else "賣超"
                factors.append(f"散戶估計{dir_zh} {abs(retail_net):.0f}億 — {action}")
        if rsi is not None:
            if rsi < 30:
                factors.append(f"TAIEX RSI {rsi:.1f} — 技術超賣，反彈機率升高")
            else:
                desc = '中性偏弱' if rsi < 50 else '中性偏強'
                factors.append(f"TAIEX RSI {rsi:.1f} — {desc}，無自動反彈護盾")

    elif mkt_name == 'JP':
        rsi = mkt_data.get('NIKKEI_RSI14')
        r5d = mkt_data.get('NIKKEI_5d_return_pct')
        r20d = mkt_data.get('NIKKEI_20d_return_pct')
        if rsi is not None:
            if rsi < 30:
                factors.append(f"日經 RSI {rsi:.1f} 超賣 — 技術反彈條件成立")
            elif rsi < 35:
                factors.append(f"日經 RSI {rsi:.1f} 接近超賣 — 短線反彈機率升高")
            else:
                factors.append(f"日經 RSI {rsi:.1f} 中性")
        if r5d is not None:
            desc = '近期急跌' if r5d < -3 else '溫和下跌' if r5d < 0 else '小幅反彈'
            factors.append(f"5 日報酬 {r5d:+.1f}% — {desc}")
        if r20d is not None:
            desc = '累積跌幅顯著，逆勢訊號升溫' if r20d < -8 else '中等回調'
            factors.append(f"20 日報酬 {r20d:+.1f}% — {desc}")
        if global_ctx:
            usdjpy = global_ctx.get('USDJPY')
            if usdjpy:
                desc = '日圓走強，出口股承壓' if usdjpy < 150 else '日圓偏弱，出口股獲利' if usdjpy > 155 else '日圓中性'
                factors.append(f"USDJPY {usdjpy:.1f} — {desc}")

    elif mkt_name == 'KR':
        rsi = mkt_data.get('KOSPI_RSI14')
        r5d = mkt_data.get('KOSPI_5d_return_pct')
        r20d = mkt_data.get('KOSPI_20d_return_pct')
        if rsi is not None:
            if rsi < 30:
                factors.append(f"KOSPI RSI {rsi:.1f} 超賣 — 技術反彈條件成立")
            elif rsi > 60:
                factors.append(f"KOSPI RSI {rsi:.1f} 偏強 — 短線追高需謹慎")
            else:
                factors.append(f"KOSPI RSI {rsi:.1f} 中性")
        if r5d is not None:
            desc = '近期走強' if r5d > 2 else '小幅下跌' if r5d < 0 else '持平'
            factors.append(f"5 日報酬 {r5d:+.1f}% — {desc}")
        if r20d is not None:
            desc = '中期回調' if r20d < -5 else '中期持穩'
            factors.append(f"20 日報酬 {r20d:+.1f}% — {desc}")
        if score is not None:
            if score > 65:
                factors.append(f"Moodring {score:.1f} — 貪婪區，追高報酬遞減")
            elif score < 35:
                factors.append(f"Moodring {score:.1f} — 逆勢買入區間")

    elif mkt_name == 'EU':
        rsi = mkt_data.get('STOXX50_RSI14')
        r5d = mkt_data.get('STOXX50_5d_return_pct')
        r20d = mkt_data.get('STOXX50_20d_return_pct')
        if rsi is not None:
            if rsi < 30:
                factors.append(f"STOXX50 RSI {rsi:.1f} — 深度超賣")
            elif rsi < 35:
                factors.append(f"STOXX50 RSI {rsi:.1f} 接近超賣 — 技術支撐區")
            else:
                factors.append(f"STOXX50 RSI {rsi:.1f} 中性")
        if r5d is not None:
            desc = '近期持續下跌' if r5d < -2 else '小幅震盪'
            factors.append(f"5 日報酬 {r5d:+.1f}% — {desc}")
        if r20d is not None:
            desc = '累積跌幅較重，地緣風險溢價持續' if r20d < -6 else '中期回調'
            factors.append(f"20 日報酬 {r20d:+.1f}% — {desc}")
        if score is not None and score < 40:
            factors.append(f"Moodring {score:.1f} — 恐懼區，需更多降息確信訊號")

    return factors if factors else None


def generate_watch_for_tw(mkt_data, mkt_name, score=None, retail=None, global_ctx=None):
    """Generate Chinese 觀察重點 text from current market data."""
    parts = []

    if mkt_name == 'US':
        rsi = mkt_data.get('SPY_RSI14')
        sma20 = mkt_data.get('SPY_SMA20')
        vix = mkt_data.get('VIX')
        yield10y = mkt_data.get('US_10Y_yield')
        if rsi is not None and rsi < 40:
            parts.append(f"等待 RSI 從超賣區回升（目標重回 40+）")
        if sma20 is not None:
            parts.append(f"SPY 能否收復 SMA20 ({sma20:.0f}) 是短線趨勢轉折關鍵")
        if vix is not None and vix > 20:
            parts.append(f"VIX {vix:.1f} 需回落 20 以下才代表恐慌解除")
        if yield10y is not None:
            parts.append(f"10Y 殖利率 {yield10y:.2f}% — 注意聯準會措辭及就業數據")

    elif mkt_name == 'TW':
        if retail:
            direction = retail.get('foreign_consecutive_direction', '')
            consec = retail.get('foreign_consecutive_days', 0)
            tsmc_margin = retail.get('TSMC_margin_30d_change_pct')
            if direction == 'sell':
                parts.append(f"外資已連賣 {consec} 日，翻買（連買 2-3 天）才是反彈確認訊號")
            if tsmc_margin is not None and tsmc_margin > 15:
                parts.append(f"台積電融資月增 {tsmc_margin:+.1f}%，融資若繼續升速則追繳壓力升高")
        parts.append("觀察 TAIEX 能否守住 SMA20 及外資方向性轉變")

    elif mkt_name == 'JP':
        usdjpy = (global_ctx or {}).get('USDJPY')
        if usdjpy:
            parts.append(f"USDJPY {usdjpy:.1f} — 日圓升破 150 將觸發套利平倉，是最大尾部風險")
        parts.append("BOJ 下次利率決策時間點及措辭是最重要催化劑")
        parts.append("RSI 技術超賣提供反彈可能，但需美股穩定配合")

    elif mkt_name == 'KR':
        parts.append("三星/SK 海力士 DRAM 報價走勢及韓元匯率是韓股主要驅動力")
        parts.append("AI 記憶體題材降溫時需注意回調風險")

    elif mkt_name == 'EU':
        parts.append("ECB 降息路徑及德國財政刺激規模是歐股非對稱風險主要來源")
        parts.append("RSI 超賣提供技術反彈條件，但地緣風險溢價難以量化")

    return "；".join(parts) + "。" if parts else None


def build_cross_market_view(us_final, tw_final, divergence, snapshot, jp_score, kr_score, eu_score, kr_scores_hist=None):
    """Rebuild cross_market_view from live data each run — keeps numbers fresh."""
    us_mkt = snapshot.get('us_market', {})
    tw_mkt = snapshot.get('tw_market', {})
    eu_mkt = snapshot.get('eu_market', {})
    retail = snapshot.get('tw_retail_indicators', {})
    parts = []

    # US/TW divergence
    if us_final is not None and tw_final is not None and divergence is not None:
        tw_zone = "中性區" if 40 <= tw_final < 55 else ("恐懼區" if tw_final < 40 else "貪婪區")
        us_trend = "仍偏弱" if 40 <= us_final < 50 else ("恐懼" if us_final < 40 else "偏強")
        parts.append(f"US ({us_final}) 和 TW ({tw_final}) 分歧 {divergence} — TW 回升至{tw_zone}，US {us_trend}")

    # RSI
    us_rsi = us_mkt.get('SPY_RSI14')
    tw_rsi = tw_mkt.get('TAIEX_RSI14')
    rsi_parts = []
    if us_rsi is not None:
        rsi_parts.append(f"US RSI {us_rsi:.1f} {'超賣' if us_rsi < 30 else ('接近超賣' if us_rsi < 35 else '中性')}")
    if tw_rsi is not None:
        rsi_parts.append(f"台股 RSI {tw_rsi:.1f} {'超賣' if tw_rsi < 30 else ('中性偏弱' if tw_rsi < 45 else '中性')}")
    if rsi_parts:
        parts.append("，".join(rsi_parts))

    # TW institutional/retail flow
    foreign_net = retail.get('foreign_net_TWD')
    foreign_days = retail.get('foreign_consecutive_days')
    foreign_dir = retail.get('foreign_consecutive_direction', '')
    retail_net = retail.get('retail_net_est_TWD')
    tsmc_margin = retail.get('TSMC_margin_30d_change_pct')
    tw_parts = []
    if foreign_net is not None and foreign_days is not None:
        dir_word = "賣" if foreign_dir == 'sell' else "買"
        tw_parts.append(f"TW 外資連{dir_word} {foreign_days} 天（{abs(foreign_net):.1f}億）")
    if retail_net is not None:
        tw_parts.append(f"散戶逆勢接刀 {retail_net:.0f}億" if (retail_net > 0 and foreign_dir == 'sell') else f"散戶同步買超 {retail_net:.0f}億" if retail_net > 0 else f"散戶同步減碼 {abs(retail_net):.0f}億")
    if tsmc_margin is not None:
        sign = "增" if tsmc_margin >= 0 else "減"
        tw_parts.append(f"台積電融資月{sign} {abs(tsmc_margin):.1f}%")
    if tw_parts:
        parts.append("；".join(tw_parts))

    # KR with last-week zone comparison
    other_parts = []
    if kr_score is not None:
        kr_level = "中性" if 40 <= kr_score < 55 else ("恐懼區" if kr_score < 40 else "貪婪區")
        if kr_scores_hist and len(kr_scores_hist) >= 6:
            prev_kr = kr_scores_hist[-6]
            prev_level = "貪婪區" if prev_kr >= 55 else ("中性" if prev_kr >= 40 else "恐懼區")
            if prev_level != kr_level:
                other_parts.append(f"韓股 ({kr_score}) 由上週{prev_level}回落至{kr_level}")
            else:
                other_parts.append(f"韓股 ({kr_score}) {kr_level}")
        else:
            other_parts.append(f"韓股 ({kr_score}) {kr_level}")

    if eu_score is not None:
        eu_rsi = eu_mkt.get('STOXX50_RSI14')
        eu_level = "恐懼區" if eu_score < 40 else ("中性" if eu_score < 55 else "貪婪區")
        eu_str = f"歐股 Moodring {eu_score} 仍在{eu_level}"
        if eu_rsi is not None:
            eu_str += f"，RSI {eu_rsi:.1f} {'接近超賣' if eu_rsi < 35 else ('超賣' if eu_rsi < 30 else '偏弱')}"
        other_parts.append(eu_str)
    if other_parts:
        parts.append("，".join(other_parts))

    return "。".join(parts) + "。" if parts else ""


def build_global_narrative(today, us_final, tw_final, snapshot, jp_score, kr_score, eu_score, kr_scores_hist=None):
    """Rebuild global_narrative from live data each run — keeps numbers fresh."""
    us_mkt = snapshot.get('us_market', {})
    tw_mkt = snapshot.get('tw_market', {})
    kr_mkt = snapshot.get('kr_market', {})
    eu_mkt = snapshot.get('eu_market', {})
    retail = snapshot.get('tw_retail_indicators', {})
    gl = snapshot.get('global_context', {})
    parts = []

    # US
    us_rsi = us_mkt.get('SPY_RSI14')
    vix = us_mkt.get('VIX')
    us_pieces = []
    if us_rsi is not None:
        us_pieces.append(f"SPY RSI {us_rsi:.1f} {'進入超賣' if us_rsi < 30 else ('接近超賣' if us_rsi < 35 else '中性')}")
    if vix is not None:
        us_pieces.append(f"VIX {vix:.2f}")
    if us_final is not None:
        us_pieces.append(f"Moodring {us_final} {'偏弱中性' if 40 <= us_final < 50 else ('中性' if 50 <= us_final < 55 else ('恐懼' if us_final < 40 else '偏強'))}")
    if us_pieces:
        parts.append("美股 " + "，".join(us_pieces))

    # TW
    tw_rsi = tw_mkt.get('TAIEX_RSI14')
    foreign_net = retail.get('foreign_net_TWD')
    foreign_days = retail.get('foreign_consecutive_days')
    foreign_dir = retail.get('foreign_consecutive_direction', '')
    retail_net = retail.get('retail_net_est_TWD')
    tw_pieces = []
    if tw_final is not None:
        tw_pieces.append(f"回升至 Moodring {tw_final}")
    if tw_rsi is not None:
        tw_pieces.append(f"TAIEX RSI {tw_rsi:.1f}")
    if foreign_net is not None and foreign_days is not None:
        dir_word = "賣" if foreign_dir == 'sell' else "買"
        tw_pieces.append(f"外資連{dir_word} {foreign_days} 天（{abs(foreign_net):.1f}億）")
    if retail_net is not None and retail_net > 0:
        tw_pieces.append(f"散戶逆勢接刀 {retail_net:.0f}億")
    if tw_pieces:
        parts.append("台股" + "，".join(tw_pieces))

    # JP
    jp_pieces = []
    if jp_score is not None:
        jp_level = "中性" if 40 <= jp_score < 55 else ("恐懼" if jp_score < 40 else "貪婪")
        jp_pieces.append(f"Moodring {jp_score} {jp_level}")
    usdjpy = gl.get('USDJPY')
    if usdjpy is not None:
        jp_pieces.append(f"USDJPY {usdjpy:.1f} {'日圓偏弱' if usdjpy >= 150 else '日圓偏強'}")
    if jp_pieces:
        parts.append("日經 " + "，".join(jp_pieces))

    # KR
    kr_pieces = []
    if kr_score is not None:
        kr_level = "中性" if 40 <= kr_score < 55 else ("恐懼" if kr_score < 40 else "貪婪")
        if kr_scores_hist and len(kr_scores_hist) >= 6:
            prev_kr = kr_scores_hist[-6]
            prev_level = "貪婪區" if prev_kr >= 55 else ("中性" if prev_kr >= 40 else "恐懼區")
            if prev_level != kr_level:
                kr_pieces.append(f"Moodring {kr_score} 由上週{prev_level}回落{kr_level}")
            else:
                kr_pieces.append(f"Moodring {kr_score} {kr_level}")
        else:
            kr_pieces.append(f"Moodring {kr_score} {kr_level}")
    kr_r5d = kr_mkt.get('KOSPI_5d_return_pct')
    if kr_r5d is not None:
        kr_pieces.append(f"KOSPI 近 5 日 {kr_r5d:+.1f}%")
    if kr_pieces:
        parts.append("韓股 " + "，".join(kr_pieces))

    # EU
    eu_pieces = []
    if eu_score is not None:
        eu_level = "恐懼區" if eu_score < 40 else ("中性" if eu_score < 55 else "貪婪區")
        eu_pieces.append(f"Moodring {eu_score} {eu_level}")
    eu_rsi = eu_mkt.get('STOXX50_RSI14')
    if eu_rsi is not None:
        eu_pieces.append(f"STOXX50 RSI {eu_rsi:.1f} {'接近超賣' if eu_rsi < 35 else ('超賣' if eu_rsi < 30 else '偏弱')}")
    eu_r20d = eu_mkt.get('STOXX50_20d_return_pct')
    if eu_r20d is not None:
        eu_pieces.append(f"20 日 {eu_r20d:+.1f}%")
    if eu_pieces:
        parts.append("歐股 " + "，".join(eu_pieces))

    # Overall summary
    overall_parts = []
    if us_final is not None and eu_score is not None and us_final < 50 and eu_score < 50:
        overall_parts.append("US/EU 技術偏空")
    if tw_final is not None and 40 <= tw_final < 55:
        overall_parts.append("TW 籌碼中性")
    if foreign_dir == 'sell' and foreign_days is not None:
        overall_parts.append("等外資翻買確認")
    elif foreign_dir == 'buy' and foreign_days is not None and foreign_days >= 2:
        overall_parts.append("外資買超確認中")
    if overall_parts:
        parts.append("整體：" + "，".join(overall_parts))

    return today + "：" + "。".join(parts) + "。" if parts else ""


def build_agent_cross_market_summary(agent_key, snapshot, us_final, tw_final, jp_score, kr_score, eu_score):
    """Build a per-market summary unique to each market — avoids every market showing the same template text."""
    us_mkt = snapshot.get('us_market', {})
    tw_mkt = snapshot.get('tw_market', {})
    jp_mkt = snapshot.get('jp_market', {})
    kr_mkt = snapshot.get('kr_market', {})
    eu_mkt = snapshot.get('eu_market', {})
    retail = snapshot.get('tw_retail_indicators', {})
    gl = snapshot.get('global_context', {})

    if agent_key == 'us_agent':
        rsi = us_mkt.get('SPY_RSI14')
        vix = us_mkt.get('VIX')
        r20d = us_mkt.get('SPY_20d_return_pct')
        spy = us_mkt.get('SPY_close')
        sma20 = us_mkt.get('SPY_SMA20')
        parts = []
        if us_final is not None:
            parts.append(f"US Moodring {us_final}")
        if rsi is not None:
            parts.append(f"SPY RSI {rsi:.1f} {'超賣' if rsi < 30 else ('接近超賣' if rsi < 35 else '中性')}")
        if vix is not None:
            parts.append(f"VIX {vix:.2f} {'恐慌偏高' if vix >= 25 else ('偏高' if vix >= 20 else '正常')}")
        if r20d is not None:
            parts.append(f"20 日 {r20d:+.1f}%")
        if spy is not None and sma20 is not None:
            parts.append(f"SPY {'低於' if spy < sma20 else '高於'} SMA20 ({sma20:.0f})")
        return "；".join(parts) + "。" if parts else ""

    elif agent_key == 'tw_agent':
        tw_rsi = tw_mkt.get('TAIEX_RSI14')
        foreign_net = retail.get('foreign_net_TWD')
        foreign_days = retail.get('foreign_consecutive_days')
        foreign_dir = retail.get('foreign_consecutive_direction', '')
        retail_net = retail.get('retail_net_est_TWD')
        tsmc_margin = retail.get('TSMC_margin_30d_change_pct')
        parts = []
        if tw_final is not None:
            parts.append(f"TW Moodring {tw_final}")
        if tw_rsi is not None:
            parts.append(f"TAIEX RSI {tw_rsi:.1f} {'超賣' if tw_rsi < 30 else ('中性偏弱' if tw_rsi < 45 else '中性')}")
        if foreign_net is not None and foreign_days is not None:
            dir_word = "賣" if foreign_dir == 'sell' else "買"
            parts.append(f"外資連{dir_word} {foreign_days} 天（{abs(foreign_net):.1f}億）")
        if retail_net is not None:
            ret_desc = "逆勢接刀" if (retail_net > 0 and foreign_dir == 'sell') else ("同步買超" if retail_net > 0 else "同步減碼")
            parts.append(f"散戶{ret_desc} {abs(retail_net):.0f}億")
        if tsmc_margin is not None:
            sign = "增" if tsmc_margin >= 0 else "減"
            parts.append(f"台積電融資月{sign} {abs(tsmc_margin):.1f}%")
        return "；".join(parts) + "。" if parts else ""

    elif agent_key == 'jp_agent':
        rsi = jp_mkt.get('NIKKEI_RSI14')
        r20d = jp_mkt.get('NIKKEI_20d_return_pct')
        nikkei = jp_mkt.get('NIKKEI_close')
        usdjpy = gl.get('USDJPY')
        parts = []
        if jp_score is not None:
            jp_level = "中性" if 40 <= jp_score < 55 else ("恐懼" if jp_score < 40 else "貪婪")
            parts.append(f"日經 Moodring {jp_score} {jp_level}")
        if rsi is not None:
            parts.append(f"RSI {rsi:.1f} {'超賣' if rsi < 30 else ('偏弱' if rsi < 45 else '中性')}")
        if r20d is not None:
            parts.append(f"20 日 {r20d:+.1f}%")
        if usdjpy is not None:
            parts.append(f"USDJPY {usdjpy:.1f} {'日圓偏弱（利出口）' if usdjpy >= 150 else ('日圓偏強（壓出口）' if usdjpy < 140 else '日圓中性')}")
        return "；".join(parts) + "。" if parts else ""

    elif agent_key == 'kr_agent':
        rsi = kr_mkt.get('KOSPI_RSI14')
        r5d = kr_mkt.get('KOSPI_5d_return_pct')
        r20d = kr_mkt.get('KOSPI_20d_return_pct')
        parts = []
        if kr_score is not None:
            kr_level = "中性" if 40 <= kr_score < 55 else ("恐懼" if kr_score < 40 else "貪婪")
            parts.append(f"韓股 Moodring {kr_score} {kr_level}")
        if rsi is not None:
            parts.append(f"KOSPI RSI {rsi:.1f} {'超賣' if rsi < 30 else ('偏弱' if rsi < 45 else '中性')}")
        if r5d is not None:
            parts.append(f"近 5 日 {r5d:+.1f}%")
        if r20d is not None:
            parts.append(f"月報酬 {r20d:+.1f}%")
        return "；".join(parts) + "。" if parts else ""

    elif agent_key == 'eu_agent':
        rsi = eu_mkt.get('STOXX50_RSI14')
        r5d = eu_mkt.get('STOXX50_5d_return_pct')
        r20d = eu_mkt.get('STOXX50_20d_return_pct')
        parts = []
        if eu_score is not None:
            eu_level = "恐懼區" if eu_score < 40 else ("中性" if eu_score < 55 else "貪婪區")
            parts.append(f"歐股 Moodring {eu_score} {eu_level}")
        if rsi is not None:
            parts.append(f"STOXX50 RSI {rsi:.1f} {'超賣' if rsi < 30 else ('接近超賣' if rsi < 35 else '偏弱')}")
        if r5d is not None:
            parts.append(f"近 5 日 {r5d:+.1f}%")
        if r20d is not None:
            parts.append(f"20 日 {r20d:+.1f}%")
        return "；".join(parts) + "。" if parts else ""

    return ""


def update_agent_results(snapshot, us_data, tw_data, tw_retail, jp_data, kr_data, eu_data, global_ctx):
    """Update phase2_agent_results.json with today's date, scores, and narratives."""
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
    jp_scores = dd.get('jp_score', [])
    kr_scores = dd.get('kr_score', [])
    eu_scores = dd.get('eu_score', [])
    us_base = us_scores[-1] if us_scores else None
    tw_base = tw_scores[-1] if tw_scores else None
    jp_score = jp_scores[-1] if jp_scores else None
    kr_score = kr_scores[-1] if kr_scores else None
    eu_score = eu_scores[-1] if eu_scores else None
    if us_base is not None:
        agents['us_base_score'] = us_base
    if tw_base is not None:
        agents['tw_base_score'] = tw_base

    # --- Collect market data from snapshot ---
    us_mkt = snapshot.get('us_market', {})
    tw_mkt = snapshot.get('tw_market', {})
    jp_mkt = snapshot.get('jp_market', {})
    kr_mkt = snapshot.get('kr_market', {})
    eu_mkt = snapshot.get('eu_market', {})
    retail = snapshot.get('tw_retail_indicators', {})
    gl = snapshot.get('global_context', {})

    # Resolve per-market scores (snapshot inline score takes precedence for JP/KR/EU)
    jp_score = jp_mkt.get('jp_moodring_score') or jp_score
    kr_score = kr_mkt.get('kr_moodring_score') or kr_score
    eu_score = eu_mkt.get('eu_moodring_score') or eu_score

    score_map = {
        'us_agent': us_base,
        'tw_agent': tw_base,
        'jp_agent': jp_score,
        'kr_agent': kr_score,
        'eu_agent': eu_score,
    }

    # --- Issue 2: Generate emotional Chinese retail narratives ---
    narr_tw_map = {
        'us_agent': generate_narrative_tw(us_mkt, 'US', score=us_base),
        'tw_agent': generate_narrative_tw(tw_mkt, 'TW', retail=retail, score=tw_base),
        'jp_agent': generate_narrative_tw(jp_mkt, 'JP', score=jp_score),
        'kr_agent': generate_narrative_tw(kr_mkt, 'KR', score=kr_score),
        'eu_agent': generate_narrative_tw(eu_mkt, 'EU', score=eu_score),
    }

    # --- English narratives (narrative_en) still use the quant generate_narrative ---
    narr_en_map = {
        'us_agent': generate_narrative(us_mkt, 'US', global_ctx=gl, score=us_base),
        'tw_agent': generate_narrative(tw_mkt, 'TW', retail=retail, score=tw_base),
        'jp_agent': generate_narrative(jp_mkt, 'JP', score=jp_score),
        'kr_agent': generate_narrative(kr_mkt, 'KR', score=kr_score),
        'eu_agent': generate_narrative(eu_mkt, 'EU', score=eu_score),
    }

    # --- Issue 1 & 4: Generate fresh key_factors and sentiment_level for all agents ---
    key_factors_map = {
        'us_agent': generate_key_factors_tw(us_mkt, 'US', score=us_base, global_ctx=gl),
        'tw_agent': generate_key_factors_tw(tw_mkt, 'TW', retail=retail, score=tw_base),
        'jp_agent': generate_key_factors_tw(jp_mkt, 'JP', score=jp_score, global_ctx=gl),
        'kr_agent': generate_key_factors_tw(kr_mkt, 'KR', score=kr_score),
        'eu_agent': generate_key_factors_tw(eu_mkt, 'EU', score=eu_score),
    }

    # --- Issue 3 & 4: Generate watch_for_tw for all agents ---
    watch_for_map = {
        'us_agent': generate_watch_for_tw(us_mkt, 'US', score=us_base, global_ctx=gl),
        'tw_agent': generate_watch_for_tw(tw_mkt, 'TW', score=tw_base, retail=retail),
        'jp_agent': generate_watch_for_tw(jp_mkt, 'JP', score=jp_score, global_ctx=gl),
        'kr_agent': generate_watch_for_tw(kr_mkt, 'KR', score=kr_score),
        'eu_agent': generate_watch_for_tw(eu_mkt, 'EU', score=eu_score),
    }

    for agent_key in ['us_agent', 'tw_agent', 'jp_agent', 'kr_agent', 'eu_agent']:
        if agent_key not in agents:
            continue
        score = score_map[agent_key]

        # Issue 1: update sentiment_level from current score
        sl = score_to_sentiment_level(score)
        if sl:
            agents[agent_key]['sentiment_level'] = sl

        # Action field: derived from individual market score, never shared
        agents[agent_key]['action'] = score_to_action(score)

        # Issue 2: narrative_tw = emotional Chinese monologue; narrative_en = quant summary
        narr_tw = narr_tw_map[agent_key]
        if narr_tw:
            agents[agent_key]['narrative_tw'] = narr_tw
            agents[agent_key]['narrative'] = narr_tw  # default display = Chinese emotional

        narr_en = narr_en_map[agent_key]
        if narr_en:
            agents[agent_key]['narrative_en'] = narr_en

        # Issue 1 & 4: update key_factors and key_factors_tw
        kf = key_factors_map[agent_key]
        if kf:
            agents[agent_key]['key_factors'] = kf
            agents[agent_key]['key_factors_tw'] = kf

        # Issue 3 & 4: add watch_for_tw
        wf = watch_for_map[agent_key]
        if wf:
            agents[agent_key]['watch_for_tw'] = wf

    # --- Issue 5: Recalculate summary final scores ---
    if 'summary' not in agents:
        agents['summary'] = {}
    us_delta = agents.get('us_agent', {}).get('adjusted_score_delta', 0) or 0
    tw_delta = agents.get('tw_agent', {}).get('adjusted_score_delta', 0) or 0
    if us_base is not None:
        agents['summary']['us_final_score'] = round(us_base + us_delta, 1)
    if tw_base is not None:
        agents['summary']['tw_final_score'] = round(tw_base + tw_delta, 1)
    # Recalculate divergence if both final scores available
    us_final = agents['summary'].get('us_final_score')
    tw_final = agents['summary'].get('tw_final_score')
    if us_final is not None and tw_final is not None:
        agents['summary']['divergence'] = round(abs(us_final - tw_final), 1)

    divergence_val = agents['summary'].get('divergence')
    kr_scores_hist = dd.get('kr_score', [])

    # --- Regenerate cross_market_view and global_narrative from live data each run ---
    new_cross = build_cross_market_view(
        us_final, tw_final, divergence_val, snapshot,
        jp_score, kr_score, eu_score, kr_scores_hist=kr_scores_hist
    )
    if new_cross:
        agents['summary']['cross_market_view'] = new_cross

    new_global = build_global_narrative(
        today, us_final, tw_final, snapshot,
        jp_score, kr_score, eu_score, kr_scores_hist=kr_scores_hist
    )
    if new_global:
        agents['global_narrative'] = new_global

    # --- Per-market cross_market_summary: unique text per market, not shared template ---
    for agent_key in ['us_agent', 'tw_agent', 'jp_agent', 'kr_agent', 'eu_agent']:
        if agent_key not in agents:
            continue
        mkt_summary = build_agent_cross_market_summary(
            agent_key, snapshot, us_final, tw_final, jp_score, kr_score, eu_score
        )
        if mkt_summary:
            agents[agent_key]['cross_market_summary'] = mkt_summary

    agents = sanitize_for_json(agents)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(agents, f, indent=2, ensure_ascii=False)
    print(f"[SAVE] phase2_agent_results.json updated (date={today})")


def update_forward_outlook(compute_scores=None):
    """Update forward_outlook.json current scores from dashboard_data.

    Args:
        compute_scores: dict mapping fwd_key → compute_score value
                        用來執行 sanity check（差距 > 5 分時輸出警告）。
                        例如: {'us_current_score': 39.6, 'tw_current_score': 55.0}
    """
    SANITY_THRESHOLD = 5.0  # 分數差距超過此值即發出警告

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
            new_val = scores[-1]
            # Sanity check：比對 compute_score 與 dashboard 最新值
            if compute_scores and fwd_key in compute_scores:
                computed = compute_scores[fwd_key]
                gap = abs(computed - new_val) if (computed is not None and new_val is not None) else None
                if gap is not None and gap > SANITY_THRESHOLD:
                    print(
                        f"[警告][SANITY] {fwd_key}: compute_score={computed} "
                        f"vs dashboard={new_val}, 差距={gap:.1f} > {SANITY_THRESHOLD}，"
                        f"請確認 historical_scores.csv 是否已正確更新"
                    )
            fwd[fwd_key] = new_val

    fwd = sanitize_for_json(fwd)
    with open(fwd_path, 'w', encoding='utf-8') as f:
        json.dump(fwd, f, indent=2, ensure_ascii=False)
    print("[SAVE] forward_outlook.json scores updated")


def generate_memory_scene():
    """Find historical dates with similar sentiment scores and show forward returns.
    Reads overlay_data.json, computes analogues, writes memory_scene.json."""
    import math

    ov_path = os.path.join(DATA_DIR, 'overlay_data.json')
    if not os.path.exists(ov_path):
        print("[MEMORY] overlay_data.json not found, skipping")
        return

    print("[MEMORY] Generating memory scene...")
    with open(ov_path, 'r', encoding='utf-8') as f:
        ov = json.load(f)

    today = datetime.now().strftime('%Y-%m-%d')
    threshold = 3.0  # ±3 points

    # Market configs: (score_dates_key, score_key, price_dates_key, price_key)
    market_configs = {
        'us': ('dates', 'us_score', 'spy_dates', 'spy'),
        'tw': ('dates', 'tw_score', 'twii_dates', 'twii'),
        'jp': ('jp_dates', 'jp_score', 'nikkei_dates', 'nikkei'),
        'kr': ('kr_dates', 'kr_score', 'kospi_dates', 'kospi'),
        'eu': ('eu_dates', 'eu_score', 'stoxx50_dates', 'stoxx50'),
    }

    def _build_price_map(dates_key, price_key):
        """Build date->price lookup dict."""
        dates = ov.get(dates_key, [])
        prices = ov.get(price_key, [])
        return {d: p for d, p in zip(dates, prices) if p is not None}

    def _forward_return(price_map, sorted_price_dates, date, days):
        """Calculate forward return from date, looking ahead `days` trading days."""
        if date not in price_map:
            return None
        try:
            idx = sorted_price_dates.index(date)
        except ValueError:
            return None
        target_idx = idx + days
        if target_idx >= len(sorted_price_dates):
            return None
        target_date = sorted_price_dates[target_idx]
        start_price = price_map[date]
        end_price = price_map[target_date]
        if start_price is None or end_price is None or start_price == 0:
            return None
        return round((end_price / start_price - 1) * 100, 2)

    def _generate_context(price_map, sorted_price_dates, score_dates, scores, date, idx):
        """Generate a data-derived context string for a historical analogue date."""
        parts = []

        # Check score vs 252d range
        start_idx = max(0, idx - 252)
        window_scores = [s for s in scores[start_idx:idx+1] if s is not None]
        if len(window_scores) >= 20:
            s_min = min(window_scores)
            s_max = max(window_scores)
            current = scores[idx]
            if s_max > s_min:
                pct = (current - s_min) / (s_max - s_min) * 100
                if pct < 10:
                    parts.append("Score near 52w low")
                elif pct > 90:
                    parts.append("Score near 52w high")

        # 5d price change leading into this date
        if date in price_map:
            try:
                pidx = sorted_price_dates.index(date)
                if pidx >= 5:
                    prev_date = sorted_price_dates[pidx - 5]
                    p_now = price_map[date]
                    p_prev = price_map.get(prev_date)
                    if p_prev and p_prev > 0:
                        chg = (p_now / p_prev - 1) * 100
                        if abs(chg) > 3:
                            parts.append(f"{'Sharp' if abs(chg) > 5 else ''} 5d {'rally' if chg > 0 else 'decline'} of {chg:+.1f}%".strip())
            except ValueError:
                pass

        # 20d price change
        if date in price_map:
            try:
                pidx = sorted_price_dates.index(date)
                if pidx >= 20:
                    prev_date = sorted_price_dates[pidx - 20]
                    p_now = price_map[date]
                    p_prev = price_map.get(prev_date)
                    if p_prev and p_prev > 0:
                        chg = (p_now / p_prev - 1) * 100
                        if abs(chg) > 5:
                            parts.append(f"20d move {chg:+.1f}%")
            except ValueError:
                pass

        # Always include the zone as fallback context
        if not parts:
            zone = _score_zone(scores[idx])
            parts.append(f"{zone} zone")

        return ", ".join(parts)

    def _score_zone(score):
        """Classify score into sentiment zone."""
        if score is None:
            return "unknown"
        if score < 25:
            return "Extreme Fear"
        elif score < 40:
            return "Fear"
        elif score < 60:
            return "Neutral"
        elif score < 75:
            return "Greed"
        else:
            return "Extreme Greed"

    result = {"date": today}

    for mkt, (sdates_key, score_key, pdates_key, price_key) in market_configs.items():
        score_dates = ov.get(sdates_key, [])
        scores = ov.get(score_key, [])
        if not score_dates or not scores or len(score_dates) != len(scores):
            print(f"[MEMORY] {mkt.upper()}: insufficient data, skipping")
            continue

        # Current score is the last entry
        current_score = scores[-1]
        if current_score is None:
            print(f"[MEMORY] {mkt.upper()}: current score is None, skipping")
            continue

        price_map = _build_price_map(pdates_key, price_key)
        sorted_price_dates = sorted(price_map.keys())

        # Find all similar historical dates (exclude last 5 days to allow fwd calc)
        similar = []
        for i in range(len(score_dates) - 5):
            s = scores[i]
            if s is None:
                continue
            dist = abs(s - current_score)
            if dist <= threshold:
                d = score_dates[i]
                fwd_5d = _forward_return(price_map, sorted_price_dates, d, 5)
                fwd_10d = _forward_return(price_map, sorted_price_dates, d, 10)
                fwd_20d = _forward_return(price_map, sorted_price_dates, d, 20)
                context = _generate_context(price_map, sorted_price_dates, score_dates, scores, d, i)
                similar.append({
                    "date": d,
                    "score": safe_round(s, 1),
                    "distance": safe_round(dist, 1),
                    "fwd_5d": safe_round(fwd_5d, 2),
                    "fwd_10d": safe_round(fwd_10d, 2),
                    "fwd_20d": safe_round(fwd_20d, 2),
                    "context": context,
                })

        if not similar:
            print(f"[MEMORY] {mkt.upper()}: no similar dates found")
            continue

        # Top 5 closest by distance, then by recency
        top5 = sorted(similar, key=lambda x: (x['distance'], -(score_dates.index(x['date']) if x['date'] in score_dates else 0)))[:5]

        # Summary stats
        fwd_20d_vals = [x['fwd_20d'] for x in similar if x['fwd_20d'] is not None]
        avg_fwd_20d = safe_round(sum(fwd_20d_vals) / len(fwd_20d_vals), 2) if fwd_20d_vals else None
        win_rate = safe_round(sum(1 for v in fwd_20d_vals if v > 0) / len(fwd_20d_vals) * 100, 0) if fwd_20d_vals else None

        best_analogue = max(similar, key=lambda x: x['fwd_20d'] if x['fwd_20d'] is not None else -9999)['date'] if fwd_20d_vals else None
        worst_analogue = min(similar, key=lambda x: x['fwd_20d'] if x['fwd_20d'] is not None else 9999)['date'] if fwd_20d_vals else None

        result[mkt] = {
            "current_score": safe_round(current_score, 1),
            "analogues": top5,
            "summary": {
                "n_similar": len(similar),
                "avg_fwd_20d": avg_fwd_20d,
                "win_rate_20d": win_rate,
                "best_analogue": best_analogue,
                "worst_analogue": worst_analogue,
            }
        }
        print(f"[MEMORY] {mkt.upper()}: score={current_score}, {len(similar)} analogues found, avg 20d fwd={avg_fwd_20d}%")

    # Cross-market pattern analysis
    us_score = scores[-1] if (scores := ov.get('us_score', [])) else None
    tw_score = scores[-1] if (scores := ov.get('tw_score', [])) else None
    if us_score is not None and tw_score is not None:
        us_zone = _score_zone(us_score)
        tw_zone = _score_zone(tw_score)
        pattern = f"US {us_zone} + TW {tw_zone}"

        # Find historical occurrences of same cross-market pattern
        us_dates = ov.get('dates', [])
        us_scores = ov.get('us_score', [])
        tw_scores_all = ov.get('tw_score', [])
        n = min(len(us_dates), len(us_scores), len(tw_scores_all))

        us_price_map = _build_price_map('spy_dates', 'spy')
        tw_price_map = _build_price_map('twii_dates', 'twii')
        us_sorted = sorted(us_price_map.keys())
        tw_sorted = sorted(tw_price_map.keys())

        cross_matches = []
        for i in range(n - 20):  # need 20d forward
            us_s = us_scores[i]
            tw_s = tw_scores_all[i]
            if us_s is None or tw_s is None:
                continue
            if _score_zone(us_s) == us_zone and _score_zone(tw_s) == tw_zone:
                d = us_dates[i]
                us_fwd = _forward_return(us_price_map, us_sorted, d, 20)
                tw_fwd = _forward_return(tw_price_map, tw_sorted, d, 20)
                cross_matches.append({"date": d, "us_fwd_20d": us_fwd, "tw_fwd_20d": tw_fwd})

        if cross_matches:
            us_fwd_vals = [x['us_fwd_20d'] for x in cross_matches if x['us_fwd_20d'] is not None]
            tw_fwd_vals = [x['tw_fwd_20d'] for x in cross_matches if x['tw_fwd_20d'] is not None]
            result['cross_market'] = {
                "pattern": pattern,
                "n_occurrences": len(cross_matches),
                "avg_fwd_20d_us": safe_round(sum(us_fwd_vals) / len(us_fwd_vals), 2) if us_fwd_vals else None,
                "avg_fwd_20d_tw": safe_round(sum(tw_fwd_vals) / len(tw_fwd_vals), 2) if tw_fwd_vals else None,
                "last_occurred": cross_matches[-1]['date'],
            }
            print(f"[MEMORY] Cross-market: {pattern}, {len(cross_matches)} occurrences")

    result = sanitize_for_json(result)
    out_path = os.path.join(DATA_DIR, 'memory_scene.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"[MEMORY] Saved: {out_path}")


def generate_self_improve():
    """Track component-level performance and signal health.
    Reads overlay_data.json, computes IC and health metrics, writes self_improve.json."""
    import math
    from scipy.stats import spearmanr

    ov_path = os.path.join(DATA_DIR, 'overlay_data.json')
    if not os.path.exists(ov_path):
        print("[SELF-IMPROVE] overlay_data.json not found, skipping")
        return

    print("[SELF-IMPROVE] Generating self-improve diagnostics...")
    with open(ov_path, 'r', encoding='utf-8') as f:
        ov = json.load(f)

    today = datetime.now().strftime('%Y-%m-%d')

    # Market configs: (score_dates_key, score_key, price_dates_key, price_key)
    market_configs = {
        'us': ('dates', 'us_score', 'spy_dates', 'spy'),
        'tw': ('dates', 'tw_score', 'twii_dates', 'twii'),
        'jp': ('jp_dates', 'jp_score', 'nikkei_dates', 'nikkei'),
        'kr': ('kr_dates', 'kr_score', 'kospi_dates', 'kospi'),
        'eu': ('eu_dates', 'eu_score', 'stoxx50_dates', 'stoxx50'),
    }

    def _compute_ic(score_dates, scores, price_dates, prices, window=None):
        """Compute Spearman IC between scores and forward 20d returns.
        If window is set, use only the last `window` score observations."""
        # Build price lookup
        price_map = {d: p for d, p in zip(price_dates, prices) if p is not None}
        sorted_pdates = sorted(price_map.keys())

        # Build aligned (score, fwd_20d_return) pairs
        pairs = []
        start_idx = max(0, len(score_dates) - window) if window else 0
        for i in range(start_idx, len(score_dates) - 20):
            s = scores[i]
            d = score_dates[i]
            if s is None:
                continue
            if d not in price_map:
                continue
            try:
                pidx = sorted_pdates.index(d)
            except ValueError:
                continue
            if pidx + 20 >= len(sorted_pdates):
                continue
            fwd_date = sorted_pdates[pidx + 20]
            p_start = price_map[d]
            p_end = price_map[fwd_date]
            if p_start is None or p_end is None or p_start == 0:
                continue
            fwd_ret = (p_end / p_start - 1) * 100
            pairs.append((s, fwd_ret))

        if len(pairs) < 30:
            return None, len(pairs)

        s_vals, r_vals = zip(*pairs)
        ic, _ = spearmanr(s_vals, r_vals)
        if math.isnan(ic):
            return None, len(pairs)
        return round(ic, 4), len(pairs)

    def _score_zone_label(score):
        if score < 25:
            return "extreme_fear"
        elif score < 40:
            return "fear"
        elif score < 60:
            return "neutral"
        elif score < 75:
            return "greed"
        else:
            return "extreme_greed"

    markets_result = {}
    active_flags = []
    overall_health = "good"

    for mkt, (sdates_key, score_key, pdates_key, price_key) in market_configs.items():
        score_dates = ov.get(sdates_key, [])
        scores = ov.get(score_key, [])
        price_dates = ov.get(pdates_key, [])
        prices = ov.get(price_key, [])

        if not score_dates or not scores or len(score_dates) != len(scores):
            print(f"[SELF-IMPROVE] {mkt.upper()}: insufficient score data, skipping")
            continue
        if not price_dates or not prices:
            print(f"[SELF-IMPROVE] {mkt.upper()}: insufficient price data, skipping")
            continue

        # Full history IC
        full_ic, full_n = _compute_ic(score_dates, scores, price_dates, prices)

        # Recent 252d IC
        recent_ic, recent_n = _compute_ic(score_dates, scores, price_dates, prices, window=252)

        # IC trend and health
        # Compare absolute IC values -- higher |IC| means stronger signal
        flags = []
        if full_ic is not None and recent_ic is not None and abs(full_ic) > 0.001:
            ic_change_pct = round((abs(recent_ic) - abs(full_ic)) / abs(full_ic) * 100, 1)
            # Also check if sign has flipped (signal inversion = decaying)
            sign_flipped = (full_ic < 0 and recent_ic > 0) or (full_ic > 0 and recent_ic < 0)
            if sign_flipped or ic_change_pct < -20:
                ic_trend = "decaying"
                flags.append(f"IC decaying: recent {recent_ic} vs historical {full_ic}")
            elif ic_change_pct > 20:
                ic_trend = "improving"
            else:
                ic_trend = "stable"
        else:
            ic_change_pct = None
            ic_trend = "insufficient_data"

        # Health based on absolute recent IC
        if recent_ic is not None:
            abs_ic = abs(recent_ic)
            if abs_ic > 0.08:
                health = "good"
            elif abs_ic >= 0.05:
                health = "warning"
            else:
                health = "poor"
                flags.append(f"Weak IC: {recent_ic}")
        else:
            health = "insufficient_data"

        # Score distribution over last 60 entries
        recent_scores = [s for s in scores[-60:] if s is not None]
        score_mean_60d = safe_round(sum(recent_scores) / len(recent_scores), 1) if recent_scores else None
        if len(recent_scores) >= 2:
            mean = sum(recent_scores) / len(recent_scores)
            variance = sum((x - mean) ** 2 for x in recent_scores) / (len(recent_scores) - 1)
            score_std_60d = safe_round(variance ** 0.5, 1)
        else:
            score_std_60d = None

        zone_dist = {"extreme_fear": 0, "fear": 0, "neutral": 0, "greed": 0, "extreme_greed": 0}
        for s in recent_scores:
            zone_dist[_score_zone_label(s)] += 1

        # Check if score is stuck in one zone
        total_recent = len(recent_scores)
        if total_recent > 0:
            max_zone_pct = max(zone_dist.values()) / total_recent * 100
            max_zone_name = max(zone_dist, key=zone_dist.get)
            if max_zone_pct >= 80:
                flags.append(f"Score stuck in {max_zone_name} zone {max_zone_pct:.0f}% of last 60d")

        if flags:
            active_flags.extend([f"{mkt.upper()}: {f}" for f in flags])

        if health in ("poor", "warning") and overall_health == "good":
            overall_health = "warning" if health == "warning" else "poor"
        if health == "poor":
            overall_health = "poor"

        markets_result[mkt] = {
            "full_ic_20d": safe_round(full_ic, 4) if full_ic is not None else None,
            "recent_ic_20d": safe_round(recent_ic, 4) if recent_ic is not None else None,
            "ic_trend": ic_trend,
            "ic_change_pct": safe_round(ic_change_pct, 1) if ic_change_pct is not None else None,
            "score_mean_60d": score_mean_60d,
            "score_std_60d": score_std_60d,
            "zone_distribution_60d": zone_dist,
            "health": health,
            "flags": flags,
        }
        print(f"[SELF-IMPROVE] {mkt.upper()}: full_ic={full_ic}, recent_ic={recent_ic}, trend={ic_trend}, health={health}")

    # Recommendation
    if overall_health == "good":
        recommendation = "All signals performing within expected range."
    elif overall_health == "warning":
        recommendation = "Some signals showing weakness. Monitor closely."
    else:
        recommendation = "Signal degradation detected. Consider recalibration."

    result = {
        "date": today,
        "markets": markets_result,
        "system_health": {
            "overall": overall_health,
            "active_flags": active_flags,
            "last_calibration": today,
            "recommendation": recommendation,
        }
    }

    result = sanitize_for_json(result)
    out_path = os.path.join(DATA_DIR, 'self_improve.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"[SELF-IMPROVE] Saved: {out_path}")


def main():
    parser = argparse.ArgumentParser(description='Moodring Daily Update — US/TW/JP/KR/EU markets')
    parser.add_argument('--us', action='store_true', help='Update US only')
    parser.add_argument('--tw', action='store_true', help='Update TW only')
    parser.add_argument('--jp', action='store_true', help='Update Japan only')
    parser.add_argument('--kr', action='store_true', help='Update Korea only')
    parser.add_argument('--eu', action='store_true', help='Update Europe only')
    parser.add_argument('--clean', action='store_true',
                        help='Retroactively clean holiday anomalies in overlay_data.json and historical_scores.csv')
    args = parser.parse_args()

    # Default: update all markets
    any_selected = args.us or args.tw or args.jp or args.kr or args.eu or args.clean
    if not any_selected:
        args.us = True
        args.tw = True
        args.jp = True
        args.kr = True
        args.eu = True

    print("=" * 50)
    print(f"Moodring Daily Update — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    # Retroactive cleanup mode
    if args.clean:
        print("\n[CLEAN] Running retroactive holiday anomaly cleanup...")
        clean_holiday_anomalies(sync_docs=True)
        if not any(v for k, v in vars(args).items() if k != 'clean' and v):
            print("\n[DONE] Cleanup complete.")
            return

    today = datetime.now().strftime('%Y-%m-%d')

    us_data = global_ctx = None
    tw_data = tw_retail = None
    usdtwd = None
    jp_data = kr_data = eu_data = None
    jp_score_val = kr_score_val = eu_score_val = None
    us_open = tw_open = jp_open = kr_open = eu_open = True  # assume open unless detected closed

    if args.us:
        us_data, global_ctx, us_open = fetch_us_data()

    if args.tw:
        tw_data, tw_retail, usdtwd, tw_open = fetch_tw_data()

    if args.jp:
        jp_data, jp_open = fetch_jp_data()
        if jp_open:
            jp_score_val = compute_score(jp_data, 'NIKKEI')
        else:
            jp_score_val = get_last_valid_score('jp')
            print(f"[JP] Market CLOSED — carry-forward score: {jp_score_val}")
        jp_data['jp_moodring_score'] = jp_score_val
        print(f"[JP] Moodring score: {jp_score_val}")

    if args.kr:
        kr_data, kr_open = fetch_kr_data()
        if kr_open:
            kr_score_val = compute_score(kr_data, 'KOSPI')
        else:
            kr_score_val = get_last_valid_score('kr')
            print(f"[KR] Market CLOSED — carry-forward score: {kr_score_val}")
        kr_data['kr_moodring_score'] = kr_score_val
        print(f"[KR] Moodring score: {kr_score_val}")

    if args.eu:
        eu_data, eu_open = fetch_eu_data()
        if eu_open:
            eu_score_val = compute_score(eu_data, 'STOXX50')
        else:
            eu_score_val = get_last_valid_score('eu')
            print(f"[EU] Market CLOSED — carry-forward score: {eu_score_val}")
        eu_data['eu_moodring_score'] = eu_score_val
        print(f"[EU] Moodring score: {eu_score_val}")

    # Compute US/TW scores (or carry-forward if market closed)
    if args.us:
        if us_open and us_data:
            us_score_val = compute_score(us_data, 'SPY')
        else:
            us_score_val = get_last_valid_score('us')
            print(f"[US] Market CLOSED — carry-forward score: {us_score_val}")
        if us_score_val is not None:
            print(f"[US] Moodring score: {us_score_val}")
    else:
        us_score_val = None

    if args.tw:
        if tw_open and tw_data:
            tw_score_val = compute_score(tw_data, 'TAIEX')
        else:
            tw_score_val = get_last_valid_score('tw')
            print(f"[TW] Market CLOSED — carry-forward score: {tw_score_val}")
        if tw_score_val is not None:
            print(f"[TW] Moodring score: {tw_score_val}")
    else:
        tw_score_val = None
    if us_score_val is not None or tw_score_val is not None:
        append_scores_to_csv(us_score=us_score_val, tw_score=tw_score_val)

    snapshot = update_snapshot(us_data, tw_data, tw_retail, global_ctx, usdtwd,
                              jp_data, kr_data, eu_data)
    update_dashboard_json(snapshot, jp_score_val, kr_score_val, eu_score_val)
    update_overlay_json(snapshot, jp_score_val, kr_score_val, eu_score_val,
                        us_open=us_open, tw_open=tw_open, jp_open=jp_open,
                        kr_open=kr_open, eu_open=eu_open)
    update_agent_results(snapshot, us_data, tw_data, tw_retail, jp_data, kr_data, eu_data, global_ctx)

    # 建立 compute_score 參考值供 sanity check 使用
    live_compute_scores = {}
    if us_score_val is not None:
        live_compute_scores['us_current_score'] = us_score_val
    if tw_score_val is not None:
        live_compute_scores['tw_current_score'] = tw_score_val
    if jp_data:
        live_compute_scores['jp_current_score'] = jp_score_val
    if kr_data:
        live_compute_scores['kr_current_score'] = kr_score_val
    if eu_data:
        live_compute_scores['eu_current_score'] = eu_score_val
    update_forward_outlook(compute_scores=live_compute_scores)
    generate_memory_scene()
    generate_self_improve()

    markets = []
    if args.us: markets.append('US')
    if args.tw: markets.append('TW')
    if args.jp: markets.append('JP')
    if args.kr: markets.append('KR')
    if args.eu: markets.append('EU')
    print(f"\n[DONE] Markets updated: {', '.join(markets)}")
    print("  Run: /market-analyst to generate full report")

    # ── Sync data files to docs/data/ for GitHub Pages ──
    import shutil
    docs_data_dir = os.path.normpath(os.path.join(DATA_DIR, '..', 'docs', 'data'))
    if os.path.isdir(docs_data_dir):
        sync_files = [
            'dashboard_data.json', 'historical_scores.csv', 'forward_outlook.json',
            'overlay_data.json', 'phase2_agent_results.json', 'memory_scene.json',
            'self_improve.json',
        ]
        for fname in sync_files:
            src = os.path.join(DATA_DIR, fname)
            dst = os.path.join(docs_data_dir, fname)
            if os.path.exists(src):
                shutil.copy2(src, dst)
        # Sync today's snapshot
        today_snap = os.path.join(DATA_DIR, f"snapshot_{today.replace('-', '')}.json")
        if os.path.exists(today_snap):
            shutil.copy2(today_snap, os.path.join(docs_data_dir, f"snapshot_{today.replace('-', '')}.json"))
            shutil.copy2(today_snap, os.path.join(docs_data_dir, 'snapshot_latest.json'))
        print("[SYNC] docs/data/ updated")


if __name__ == '__main__':
    main()
