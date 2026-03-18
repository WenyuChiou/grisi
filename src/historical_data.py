"""
Moodring Phase 1B: Historical Data Downloader
============================================
Download 10+ years of base indicator data for backtesting.
Uses yfinance for market data + proxy indicators.

Point-in-time: all data is stored with dates, no lookahead possible.
"""

import sys
import io
import os
import json
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime

# Fix Windows encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# Output directory
DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
os.makedirs(DATA_DIR, exist_ok=True)


def download_market_data(start='2010-01-01', end=None):
    """Download all market price data from yfinance."""
    if end is None:
        end = datetime.now().strftime('%Y-%m-%d')

    tickers = {
        # US market
        'SPY': 'SPY',           # S&P 500 ETF
        'QQQ': 'QQQ',           # Nasdaq 100 ETF
        'VIX': '^VIX',          # CBOE Volatility Index
        'TNX': '^TNX',          # 10-Year Treasury Yield

        # Taiwan market
        'TAIEX': '^TWII',       # Taiwan Weighted Index
        'TSMC': '2330.TW',      # TSMC

        # FX
        'USDJPY': 'USDJPY=X',  # USD/JPY

        # Commodity / safe haven
        'GLD': 'GLD',           # Gold ETF (proxy for risk appetite)
    }

    all_data = {}
    for name, ticker in tickers.items():
        print(f"  Downloading {name} ({ticker})...")
        try:
            df = yf.download(ticker, start=start, end=end, progress=False)
            if len(df) > 0:
                # Flatten multi-level columns if needed
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                all_data[name] = df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
                print(f"    OK: {len(df)} rows, {df.index[0].strftime('%Y-%m-%d')} to {df.index[-1].strftime('%Y-%m-%d')}")
            else:
                print(f"    EMPTY: no data returned")
        except Exception as e:
            print(f"    ERROR: {str(e)[:80]}")

    return all_data


def compute_base_features(all_data):
    """
    Compute base indicator features from raw market data.
    All features use rolling windows (point-in-time, no lookahead).
    """
    features = pd.DataFrame()

    # ── US Base Indicators ──

    if 'SPY' in all_data:
        spy = all_data['SPY']['Close']

        # SPY vs 52-week high (rolling)
        spy_52w_high = spy.rolling(252, min_periods=60).max()
        features['us_spy_vs_high'] = spy / spy_52w_high

        # SPY momentum (20-day return)
        features['us_spy_mom_20d'] = spy.pct_change(20)

        # SPY drawdown from rolling max
        features['us_spy_drawdown'] = spy / spy.cummax() - 1

    if 'VIX' in all_data:
        vix = all_data['VIX']['Close']

        # VIX level (inverse = complacency)
        features['us_vix'] = vix
        features['us_vix_inv'] = 1.0 / vix

        # VIX z-score (rolling 252-day)
        vix_mean = vix.rolling(252, min_periods=60).mean()
        vix_std = vix.rolling(252, min_periods=60).std()
        features['us_vix_zscore'] = (vix - vix_mean) / vix_std

    if 'TNX' in all_data:
        tnx = all_data['TNX']['Close']
        features['us_10y_yield'] = tnx
        features['us_10y_change_20d'] = tnx.diff(20)

    # ── Proxy for Put/Call Ratio ──
    # Since we can't get historical CBOE put/call directly from yfinance,
    # use VIX term structure slope as proxy (VIX spike = fear = high put buying)
    if 'VIX' in all_data and 'SPY' in all_data:
        # VIX-SPY correlation regime: high negative corr = normal market
        # Positive corr or decorrelation = stress
        vix = all_data['VIX']['Close']
        spy_ret = all_data['SPY']['Close'].pct_change()
        vix_ret = vix.pct_change()
        features['us_vix_spy_corr_20d'] = spy_ret.rolling(20).corr(vix_ret)

    # ── Taiwan Base Indicators ──

    if 'TAIEX' in all_data:
        taiex = all_data['TAIEX']['Close']

        # TAIEX vs 52-week high
        taiex_52w_high = taiex.rolling(252, min_periods=60).max()
        features['tw_taiex_vs_high'] = taiex / taiex_52w_high

        # TAIEX momentum
        features['tw_taiex_mom_20d'] = taiex.pct_change(20)

        # TAIEX drawdown
        features['tw_taiex_drawdown'] = taiex / taiex.cummax() - 1

        # TAIEX volume surge (proxy for retail activity)
        vol = all_data['TAIEX']['Volume']
        vol_ma20 = vol.rolling(20).mean()
        features['tw_volume_surge'] = vol / vol_ma20

    if 'TSMC' in all_data:
        tsmc = all_data['TSMC']['Close']

        # TSMC vs 52-week high (散戶信心指標)
        tsmc_52w_high = tsmc.rolling(252, min_periods=60).max()
        features['tw_tsmc_vs_high'] = tsmc / tsmc_52w_high

        # TSMC momentum
        features['tw_tsmc_mom_20d'] = tsmc.pct_change(20)

    # ── Taiwan Derived Indicators ──

    if 'TAIEX' in all_data:
        taiex = all_data['TAIEX']['Close']
        taiex_ret = taiex.pct_change()

        # TAIEX realized volatility (20d) — TW VIX proxy
        features['tw_realized_vol_20d'] = taiex_ret.rolling(20).std() * np.sqrt(252)

        # TAIEX drawdown velocity — how fast drawdown deepens (5d change in drawdown)
        dd = taiex / taiex.cummax() - 1
        features['tw_drawdown_velocity'] = dd.diff(5)

    if 'TAIEX' in all_data and 'TSMC' in all_data:
        taiex = all_data['TAIEX']['Close']
        tsmc = all_data['TSMC']['Close']

        # TAIEX-TSMC momentum spread (bellwether divergence)
        # When TSMC outperforms TAIEX = retail chasing leader
        taiex_mom = taiex.pct_change(20)
        tsmc_mom = tsmc.pct_change(20)
        features['tw_tsmc_taiex_spread'] = tsmc_mom - taiex_mom

    if 'USDJPY' in all_data:
        usdjpy = all_data['USDJPY']['Close']

        # USDJPY momentum — rising = risk-on (carry trade), falling = risk-off (yen safe haven)
        features['fx_usdjpy_mom_20d'] = usdjpy.pct_change(20)

    # ── Cross-Market Features ──

    if 'SPY' in all_data and 'TAIEX' in all_data:
        spy_ret = all_data['SPY']['Close'].pct_change()
        taiex_ret = all_data['TAIEX']['Close'].pct_change()

        # US-TW correlation (contagion indicator)
        # Need to align dates first
        aligned = pd.DataFrame({
            'spy': spy_ret,
            'taiex': taiex_ret
        }).dropna()
        if len(aligned) > 60:
            corr = aligned['spy'].rolling(60).corr(aligned['taiex'])
            features['cross_us_tw_corr_60d'] = corr

    if 'GLD' in all_data and 'SPY' in all_data:
        # Gold/SPY ratio — risk appetite indicator
        gld = all_data['GLD']['Close']
        spy = all_data['SPY']['Close']
        aligned = pd.DataFrame({'gld': gld, 'spy': spy}).dropna()
        features['cross_gold_spy_ratio'] = aligned['gld'] / aligned['spy']

    # Clean up
    features.index.name = 'date'
    return features


def compute_target_returns(all_data, horizons=[5, 10, 20, 60]):
    """
    Compute forward returns for backtest targets.
    These are what we're trying to predict.
    """
    targets = pd.DataFrame()

    for name, ticker_name in [('spy', 'SPY'), ('taiex', 'TAIEX')]:
        if ticker_name in all_data:
            close = all_data[ticker_name]['Close']
            for h in horizons:
                # Forward return: % change over next h days
                # IMPORTANT: shift(-h) means we're looking h days into the future
                targets[f'{name}_fwd_{h}d'] = close.pct_change(h).shift(-h)

    targets.index.name = 'date'
    return targets


def main():
    print("=" * 60)
    print("Moodring Phase 1B: Historical Data Download")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    # Step 1: Download raw market data
    print("\n[1/3] Downloading market data (2010-2025)...")
    all_data = download_market_data(start='2010-01-01')

    # Step 2: Compute features
    print("\n[2/3] Computing base features...")
    features = compute_base_features(all_data)
    print(f"  Features: {list(features.columns)}")
    print(f"  Shape: {features.shape}")
    print(f"  Date range: {features.index[0]} to {features.index[-1]}")
    print(f"  Non-null counts:")
    for col in features.columns:
        n = features[col].notna().sum()
        print(f"    {col}: {n} ({n/len(features)*100:.0f}%)")

    # Step 3: Compute target returns
    print("\n[3/3] Computing forward returns (targets)...")
    targets = compute_target_returns(all_data)
    print(f"  Targets: {list(targets.columns)}")
    print(f"  Shape: {targets.shape}")

    # Save to CSV
    features_path = os.path.join(DATA_DIR, 'base_features.csv')
    targets_path = os.path.join(DATA_DIR, 'target_returns.csv')

    features.to_csv(features_path, encoding='utf-8')
    targets.to_csv(targets_path, encoding='utf-8')

    # Also save raw close prices for reference
    closes = pd.DataFrame()
    for name, df in all_data.items():
        closes[name] = df['Close']
    closes_path = os.path.join(DATA_DIR, 'raw_closes.csv')
    closes.to_csv(closes_path, encoding='utf-8')

    print(f"\n  Saved: {features_path}")
    print(f"  Saved: {targets_path}")
    print(f"  Saved: {closes_path}")

    # Summary stats
    print(f"\n{'=' * 60}")
    print("DATA SUMMARY")
    print(f"{'=' * 60}")

    summary = {
        'download_date': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'features_shape': list(features.shape),
        'features_columns': list(features.columns),
        'targets_columns': list(targets.columns),
        'date_range': [str(features.index[0]), str(features.index[-1])],
        'tickers_downloaded': list(all_data.keys()),
        'missing_pct': {col: round(features[col].isna().mean() * 100, 1)
                        for col in features.columns},
    }

    summary_path = os.path.join(DATA_DIR, 'data_summary.json')
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False, default=str)
    print(f"  Saved: {summary_path}")

    print("\n  DONE. Ready for Phase 1B backtest.")
    return features, targets


if __name__ == '__main__':
    main()
