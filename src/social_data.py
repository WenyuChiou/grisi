"""
Moodring Phase 1C: Social/Sentiment Data Collector
================================================
Collect Taiwan retail-specific data for incremental alpha testing.

Sources:
  - FinMind API: margin trading (融資餘額), institutional flows (三大法人)
  - yfinance: additional sentiment proxies (put/call, VIX term structure)
"""

import os
import json
import pandas as pd
import numpy as np
import requests
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')


def fetch_finmind(dataset, data_id=None, start='2010-01-01', end=None):
    """Fetch data from FinMind API (free tier)."""
    if end is None:
        end = datetime.now().strftime('%Y-%m-%d')

    url = 'https://api.finmindtrade.com/api/v4/data'
    params = {
        'dataset': dataset,
        'start_date': start,
        'end_date': end,
    }
    if data_id:
        params['data_id'] = data_id

    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get('status') == 200 and data.get('data'):
            df = pd.DataFrame(data['data'])
            return df
        else:
            print(f"  FinMind {dataset}: status={data.get('status')}, msg={data.get('msg', 'no data')}")
            return None
    except Exception as e:
        print(f"  FinMind {dataset} error: {str(e)[:100]}")
        return None


def collect_tw_margin_data(start='2010-01-01'):
    """
    Collect Taiwan margin trading data (融資融券).
    融資餘額 = retail leverage indicator.
    """
    print("  Fetching TW margin trading (TaiwanStockMarginPurchaseShortSale)...")

    # Total market margin data
    df = fetch_finmind('TaiwanStockTotalMarginPurchaseShortSale', start=start)
    if df is not None and len(df) > 0:
        print(f"    OK: {len(df)} rows")
        return df

    # Fallback: try individual stock margin for TAIEX proxy
    print("  Trying individual margin data for 2330 (TSMC) as proxy...")
    df = fetch_finmind('TaiwanStockMarginPurchaseShortSale', data_id='2330', start=start)
    if df is not None and len(df) > 0:
        print(f"    OK (TSMC proxy): {len(df)} rows")
        return df

    print("    FAILED: no margin data available")
    return None


def collect_tw_institutional_data(start='2010-01-01'):
    """
    Collect institutional investor buy/sell data (三大法人買賣超).
    Foreign investor selling while market rises = retail holding the bag.
    """
    print("  Fetching TW institutional flows (TaiwanStockTotalInstitutionalInvestors)...")

    df = fetch_finmind('TaiwanStockTotalInstitutionalInvestors', start=start)
    if df is not None and len(df) > 0:
        print(f"    OK: {len(df)} rows")
        return df

    print("    FAILED: no institutional data available")
    return None


def collect_tw_daytrade_data(start='2010-01-01'):
    """
    Collect day trading ratio (當沖比率).
    High day trading = retail speculation frenzy.
    """
    print("  Fetching TW day trading ratio...")

    # Try total market day trade stats
    df = fetch_finmind('TaiwanStockDayTrading', start=start)
    if df is not None and len(df) > 0:
        print(f"    OK: {len(df)} rows")
        return df

    print("    FAILED: no day trading data")
    return None


def build_social_features(margin_df, institutional_df, daytrade_df):
    """
    Build social/sentiment features from raw data.
    All features are point-in-time (no lookahead).
    """
    features = pd.DataFrame()

    # ── Margin Trading Features (融資餘額) ──
    if margin_df is not None and len(margin_df) > 0:
        margin = margin_df.copy()
        margin['date'] = pd.to_datetime(margin['date'])
        print(f"    Margin columns: {list(margin.columns)}")
        print(f"    Margin names: {margin['name'].unique() if 'name' in margin.columns else 'N/A'}")

        # Sum across all margin types per date to get total margin balance
        if 'TodayBalance' in margin.columns:
            daily = margin.groupby('date')['TodayBalance'].sum()
            daily = daily.sort_index()
            bal = pd.to_numeric(daily, errors='coerce')

            # Margin balance change rate (20d)
            features['tw_margin_change_20d'] = bal.pct_change(20)
            # Margin balance z-score (high = retail leveraged up = greedy)
            bal_mean = bal.rolling(252, min_periods=60).mean()
            bal_std = bal.rolling(252, min_periods=60).std()
            features['tw_margin_zscore'] = (bal - bal_mean) / bal_std.replace(0, np.nan)
            print(f"    Built margin features: {len(bal)} dates, range {bal.index[0]} to {bal.index[-1]}")

    # ── Institutional Flow Features (三大法人) ──
    if institutional_df is not None and len(institutional_df) > 0:
        inst = institutional_df.copy()
        inst['date'] = pd.to_datetime(inst['date'])
        print(f"    Institutional columns: {list(inst.columns)}")
        print(f"    Institutional names: {inst['name'].unique() if 'name' in inst.columns else 'N/A'}")

        # Compute net buy/sell per institution per date
        inst['buy'] = pd.to_numeric(inst['buy'], errors='coerce')
        inst['sell'] = pd.to_numeric(inst['sell'], errors='coerce')
        inst['net'] = inst['buy'] - inst['sell']

        # Foreign investor net flow (外資)
        foreign_names = [n for n in inst['name'].unique()
                         if '外資' in str(n) or 'Foreign' in str(n) or 'foreign' in str(n)]
        if foreign_names:
            foreign = inst[inst['name'].isin(foreign_names)].groupby('date')['net'].sum().sort_index()
            features['tw_foreign_flow_20d'] = foreign.rolling(20).sum()
            flow_mean = foreign.rolling(252, min_periods=60).mean()
            flow_std = foreign.rolling(252, min_periods=60).std()
            features['tw_foreign_flow_zscore'] = (foreign - flow_mean) / flow_std.replace(0, np.nan)
            print(f"    Built foreign flow features from: {foreign_names}")
        else:
            # Fallback: use total institutional net
            total_net = inst.groupby('date')['net'].sum().sort_index()
            features['tw_inst_net_20d'] = total_net.rolling(20).sum()
            inst_mean = total_net.rolling(252, min_periods=60).mean()
            inst_std = total_net.rolling(252, min_periods=60).std()
            features['tw_inst_net_zscore'] = (total_net - inst_mean) / inst_std.replace(0, np.nan)
            print(f"    Built total institutional features (no foreign label found)")

    # ── Day Trading Features (當沖比率) ──
    if daytrade_df is not None and len(daytrade_df) > 0:
        dt = daytrade_df.copy()
        dt['date'] = pd.to_datetime(dt['date'])
        print(f"    Day trading columns: {list(dt.columns)}")

        for col_name in ['ratio', 'day_trade_ratio', 'DayTradingRatio']:
            if col_name in dt.columns:
                daily = dt.groupby('date')[col_name].mean().sort_index()
                ratio = pd.to_numeric(daily, errors='coerce')
                features['tw_daytrade_ratio'] = ratio
                r_mean = ratio.rolling(252, min_periods=60).mean()
                r_std = ratio.rolling(252, min_periods=60).std()
                features['tw_daytrade_zscore'] = (ratio - r_mean) / r_std.replace(0, np.nan)
                print(f"    Built day trading features from '{col_name}'")
                break

    features.index.name = 'date'
    return features


def main():
    print("=" * 60)
    print("Moodring Phase 1C: Social/Sentiment Data Collection")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    # Step 1: Collect raw data
    print("\n[1/3] Collecting Taiwan retail data from FinMind...")
    margin_df = collect_tw_margin_data()
    institutional_df = collect_tw_institutional_data()
    daytrade_df = collect_tw_daytrade_data()

    # Step 2: Build features
    print("\n[2/3] Building social/sentiment features...")
    social_features = build_social_features(margin_df, institutional_df, daytrade_df)

    if len(social_features.columns) == 0:
        print("\n  WARNING: No social features could be built.")
        print("  Saving raw data for manual inspection...")

        # Save whatever we got for debugging
        raw_data = {}
        for name, df in [('margin', margin_df), ('institutional', institutional_df), ('daytrade', daytrade_df)]:
            if df is not None:
                sample = df.head(5).to_dict('records')
                raw_data[name] = {'columns': list(df.columns), 'rows': len(df), 'sample': sample}
            else:
                raw_data[name] = {'status': 'FAILED'}

        debug_path = os.path.join(DATA_DIR, 'social_data_debug.json')
        with open(debug_path, 'w', encoding='utf-8') as f:
            json.dump(raw_data, f, indent=2, ensure_ascii=False, default=str)
        print(f"  Debug info saved to: {debug_path}")
    else:
        print(f"\n  Social features: {list(social_features.columns)}")
        print(f"  Shape: {social_features.shape}")
        print(f"  Date range: {social_features.index[0]} to {social_features.index[-1]}")

        # Save
        social_path = os.path.join(DATA_DIR, 'social_features.csv')
        social_features.to_csv(social_path, encoding='utf-8')
        print(f"  Saved: {social_path}")

    # Step 3: Summary
    print(f"\n{'=' * 60}")
    print("DATA COLLECTION SUMMARY")
    print(f"{'=' * 60}")
    sources = {
        'margin': margin_df is not None and len(margin_df) > 0 if margin_df is not None else False,
        'institutional': institutional_df is not None and len(institutional_df) > 0 if institutional_df is not None else False,
        'daytrade': daytrade_df is not None and len(daytrade_df) > 0 if daytrade_df is not None else False,
    }
    for name, ok in sources.items():
        status = 'OK' if ok else 'FAILED'
        print(f"  {name}: {status}")

    return social_features


if __name__ == '__main__':
    main()
