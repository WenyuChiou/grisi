"""
GRISI Data Snapshot — Pull all available data for US + Taiwan
"""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import yfinance as yf
import numpy as np
from FinMind.data import DataLoader

dl = DataLoader()
today = '2026-03-16'
start_90d = '2025-12-16'
start_30d = '2026-02-14'

print('=' * 60)
print('GRISI Data Snapshot')
print(f'Date: {today}')
print('=' * 60)

def safe_close(df):
    """Extract close price series from yfinance DataFrame"""
    if df['Close'].ndim > 1:
        return df['Close'].iloc[:, 0]
    return df['Close']

def calc_rsi(close, period=14):
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

# =============================================
# 1. US Market Data
# =============================================
print('\n--- US MARKET DATA ---')

spy = yf.download('SPY', start=start_90d, end='2026-03-17', progress=False)
vix = yf.download('^VIX', start=start_90d, end='2026-03-17', progress=False)
tnx = yf.download('^TNX', start=start_30d, end='2026-03-17', progress=False)

spy_c = safe_close(spy)
vix_c = safe_close(vix)
tnx_c = safe_close(tnx)

spy_rsi = calc_rsi(spy_c)
spy_sma20 = spy_c.rolling(20).mean()
spy_sma60 = spy_c.rolling(60).mean()
spy_52w_high = spy_c.rolling(252, min_periods=60).max()

spy_5d_ret = (spy_c.iloc[-1] / spy_c.iloc[-6] - 1) * 100
spy_20d_ret = (spy_c.iloc[-1] / spy_c.iloc[-21] - 1) * 100

us_data = {
    "SPY_close": round(float(spy_c.iloc[-1]), 2),
    "SPY_RSI14": round(float(spy_rsi.iloc[-1]), 1),
    "SPY_SMA20": round(float(spy_sma20.iloc[-1]), 2),
    "SPY_SMA60": round(float(spy_sma60.iloc[-1]), 2),
    "SPY_vs_52w_high_pct": round(float(spy_c.iloc[-1] / spy_52w_high.iloc[-1] * 100), 1),
    "SPY_5d_return_pct": round(float(spy_5d_ret), 2),
    "SPY_20d_return_pct": round(float(spy_20d_ret), 2),
    "VIX": round(float(vix_c.iloc[-1]), 2),
    "US_10Y_yield": round(float(tnx_c.iloc[-1]), 2),
}

for k, v in us_data.items():
    print(f'  {k}: {v}')

# =============================================
# 2. Taiwan Market Data
# =============================================
print('\n--- TAIWAN MARKET DATA ---')

twii = yf.download('^TWII', start=start_90d, end='2026-03-17', progress=False)
tsmc = yf.download('2330.TW', start=start_90d, end='2026-03-17', progress=False)

twii_c = safe_close(twii)
tsmc_c = safe_close(tsmc)

twii_rsi = calc_rsi(twii_c)
twii_sma20 = twii_c.rolling(20).mean()
twii_52w_high = twii_c.rolling(252, min_periods=50).max()
tsmc_52w_high = tsmc_c.rolling(252, min_periods=50).max()

tw_data = {
    "TAIEX_close": round(float(twii_c.iloc[-1]), 0),
    "TAIEX_RSI14": round(float(twii_rsi.iloc[-1]), 1),
    "TAIEX_SMA20": round(float(twii_sma20.iloc[-1]), 0),
    "TAIEX_vs_52w_high_pct": round(float(twii_c.iloc[-1] / twii_52w_high.iloc[-1] * 100), 1),
    "TSMC_close": round(float(tsmc_c.iloc[-1]), 0),
    "TSMC_vs_52w_high_pct": round(float(tsmc_c.iloc[-1] / tsmc_52w_high.iloc[-1] * 100), 1),
}

for k, v in tw_data.items():
    print(f'  {k}: {v}')

# =============================================
# 3. Taiwan Retail Indicators (FinMind)
# =============================================
print('\n--- TAIWAN RETAIL INDICATORS ---')

# 3a. Margin Balance (融資餘額)
margin = dl.taiwan_stock_margin_purchase_short_sale_total(
    start_date='2026-02-01', end_date=today
)
margin_buy = margin[margin['name'] == 'MarginPurchase'].copy()

tw_retail = {}
if len(margin_buy) > 0:
    latest_margin = int(margin_buy.iloc[-1]['TodayBalance'])
    first_margin = int(margin_buy.iloc[0]['TodayBalance'])
    margin_30d_chg = (latest_margin - first_margin) / first_margin * 100

    # 5-day trend
    if len(margin_buy) >= 5:
        m5_start = int(margin_buy.iloc[-5]['TodayBalance'])
        m5_end = int(margin_buy.iloc[-1]['TodayBalance'])
        margin_5d_chg = (m5_end - m5_start) / m5_start * 100
    else:
        margin_5d_chg = 0

    tw_retail["margin_balance"] = latest_margin
    tw_retail["margin_30d_change_pct"] = round(margin_30d_chg, 2)
    tw_retail["margin_5d_change_pct"] = round(margin_5d_chg, 2)
    tw_retail["margin_5d_trend"] = "INCREASING" if margin_5d_chg > 0.3 else "DECREASING" if margin_5d_chg < -0.3 else "FLAT"

# 3b. Institutional Investors (三大法人)
inst = dl.taiwan_stock_institutional_investors_total(
    start_date='2026-03-01', end_date=today
)
if len(inst) > 0:
    latest_date = inst['date'].max()
    latest_inst = inst[inst['date'] == latest_date]

    total_row = latest_inst[latest_inst['name'] == 'total']
    if len(total_row) > 0:
        inst_buy = float(total_row.iloc[0]['buy'])
        inst_sell = float(total_row.iloc[0]['sell'])
        inst_net = inst_buy - inst_sell
        retail_net_est = -inst_net  # retail = opposite of institutional

        tw_retail["institutional_net_TWD"] = round(inst_net / 1e8, 1)  # in 億
        tw_retail["retail_net_est_TWD"] = round(retail_net_est / 1e8, 1)

    # Foreign investor
    fi = latest_inst[latest_inst['name'] == 'Foreign_Investor']
    if len(fi) > 0:
        fi_net = float(fi.iloc[0]['buy']) - float(fi.iloc[0]['sell'])
        tw_retail["foreign_net_TWD"] = round(fi_net / 1e8, 1)

    # Foreign consecutive days
    daily_fi = inst[inst['name'] == 'Foreign_Investor'].copy()
    daily_fi['net'] = daily_fi['buy'].astype(float) - daily_fi['sell'].astype(float)
    consecutive = 0
    if len(daily_fi) > 0:
        direction = 'buy' if daily_fi.iloc[-1]['net'] > 0 else 'sell'
        for _, row in daily_fi.iloc[::-1].iterrows():
            if (direction == 'buy' and row['net'] > 0) or (direction == 'sell' and row['net'] < 0):
                consecutive += 1
            else:
                break
        tw_retail["foreign_consecutive_days"] = consecutive
        tw_retail["foreign_consecutive_direction"] = direction

# 3c. TSMC Margin
tsmc_margin = dl.taiwan_stock_margin_purchase_short_sale(
    stock_id='2330', start_date='2026-02-01', end_date=today
)
if len(tsmc_margin) > 0:
    tsmc_m_latest = int(tsmc_margin.iloc[-1]['MarginPurchaseTodayBalance'])
    tsmc_m_first = int(tsmc_margin.iloc[0]['MarginPurchaseTodayBalance'])
    tsmc_m_chg = (tsmc_m_latest - tsmc_m_first) / tsmc_m_first * 100
    tw_retail["TSMC_margin_balance"] = tsmc_m_latest
    tw_retail["TSMC_margin_30d_change_pct"] = round(tsmc_m_chg, 2)

for k, v in tw_retail.items():
    print(f'  {k}: {v}')

# =============================================
# 4. Global Context
# =============================================
print('\n--- GLOBAL CONTEXT ---')

usdjpy = yf.download('USDJPY=X', period='1mo', progress=False)
usdtwd = yf.download('TWD=X', period='1mo', progress=False)
gold = yf.download('GC=F', period='1mo', progress=False)

global_data = {
    "USDJPY": round(float(safe_close(usdjpy).iloc[-1]), 2),
    "USDTWD": round(float(safe_close(usdtwd).iloc[-1]), 2),
    "Gold": round(float(safe_close(gold).iloc[-1]), 0),
}

for k, v in global_data.items():
    print(f'  {k}: {v}')

# =============================================
# 5. Compile Full Snapshot
# =============================================
snapshot = {
    "date": today,
    "us_market": us_data,
    "tw_market": tw_data,
    "tw_retail_indicators": tw_retail,
    "global_context": global_data,
}

# Save
output_path = "data/snapshot_20260316.json"
with open(output_path, "w", encoding="utf-8") as f:
    json.dump(snapshot, f, indent=2, ensure_ascii=False)

print(f'\nSnapshot saved to: {output_path}')

# =============================================
# 6. Quick Interpretation
# =============================================
print('\n' + '=' * 60)
print('QUICK INTERPRETATION (raw data, no LLM)')
print('=' * 60)

# US signals
print('\nUS Retail Signals:')
if us_data["VIX"] > 25:
    print('  [!] VIX elevated (>25) -> market anxiety')
elif us_data["VIX"] < 15:
    print('  [!] VIX very low (<15) -> complacency')
else:
    print(f'  [~] VIX at {us_data["VIX"]} -> moderate')

if us_data["SPY_RSI14"] < 30:
    print('  [!] SPY RSI oversold (<30)')
elif us_data["SPY_RSI14"] > 70:
    print('  [!] SPY RSI overbought (>70)')
else:
    print(f'  [~] SPY RSI at {us_data["SPY_RSI14"]} -> neutral')

print(f'  SPY is {us_data["SPY_vs_52w_high_pct"]}% of 52W high')

# TW signals
print('\nTW Retail Signals:')
margin_trend = tw_retail.get("margin_5d_trend", "?")
print(f'  Margin trend (5d): {margin_trend}')
print(f'  Margin 30d change: {tw_retail.get("margin_30d_change_pct", "?")}%')

retail_net = tw_retail.get("retail_net_est_TWD", 0)
if retail_net > 0:
    print(f'  [!] Retail NET BUY {retail_net} bn TWD -> retail is BUYING')
elif retail_net < 0:
    print(f'  [!] Retail NET SELL {abs(retail_net)} bn TWD -> retail is SELLING')

fi_dir = tw_retail.get("foreign_consecutive_direction", "?")
fi_days = tw_retail.get("foreign_consecutive_days", 0)
print(f'  Foreign investors: consecutive {fi_days} days {fi_dir}')

print(f'  TAIEX is {tw_data["TAIEX_vs_52w_high_pct"]}% of 52W high')
print(f'  TSMC is {tw_data["TSMC_vs_52w_high_pct"]}% of 52W high')

print('\n' + '=' * 60)
print('DONE')
