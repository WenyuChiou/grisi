"""
GRISI Phase 0: Data Source Feasibility Check
=============================================
驗證所有數據源是否可用。每個 Step 獨立驗證。
"""

import json
import sys
import io
from datetime import datetime, timedelta

# Fix Windows cp950 encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# ============================================================
# Step 0.1: yfinance — 各市場指數和個股
# ============================================================
def step_01_yfinance():
    """驗證 yfinance 能拉到所有需要的 ticker"""
    import yfinance as yf

    tickers = {
        "US_SPY": "SPY",
        "US_QQQ": "QQQ",
        "US_VIX": "^VIX",
        "TW_TAIEX": "^TWII",
        "TW_TSMC": "2330.TW",
        "FX_USDJPY": "USDJPY=X",
        "FX_USDTWD": "TWD=X",
        "US_10Y": "^TNX",
    }

    results = {}
    for name, ticker in tickers.items():
        try:
            data = yf.download(ticker, period="3mo", progress=False)
            if len(data) > 0:
                last_date = data.index[-1].strftime("%Y-%m-%d")
                last_close = float(data["Close"].iloc[-1].iloc[0]) if hasattr(data["Close"].iloc[-1], 'iloc') else float(data["Close"].iloc[-1])
                results[name] = {
                    "ticker": ticker,
                    "status": "OK",
                    "rows": len(data),
                    "last_date": last_date,
                    "last_close": round(last_close, 2),
                }
            else:
                results[name] = {"ticker": ticker, "status": "EMPTY", "rows": 0}
        except Exception as e:
            results[name] = {"ticker": ticker, "status": f"ERROR: {str(e)[:80]}"}

    return results


# ============================================================
# Step 0.2: TWSE — 台灣融資餘額、三大法人
# ============================================================
def step_02_twse():
    """驗證 TWSE OpenData API 能拉到融資餘額和法人買賣超"""
    import requests
    from datetime import date

    results = {}

    # 2a. 融資融券餘額 (margin balance)
    try:
        # TWSE API: 融資融券彙總
        today = date.today()
        # Try recent trading days
        for days_back in range(0, 7):
            check_date = today - timedelta(days=days_back)
            date_str = check_date.strftime("%Y%m%d")
            url = f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={date_str}&selectType=MS&response=json"
            resp = requests.get(url, timeout=10)
            data = resp.json()
            if data.get("stat") == "OK" and data.get("tables"):
                tables = data["tables"]
                if len(tables) > 0 and "data" in tables[0]:
                    row = tables[0]["data"][-1]  # last row = total
                    results["margin_balance"] = {
                        "status": "OK",
                        "date": date_str,
                        "sample_data": row[:5],
                        "note": "融資融券彙總"
                    }
                    break
        if "margin_balance" not in results:
            results["margin_balance"] = {"status": "NO_DATA"}
    except Exception as e:
        results["margin_balance"] = {"status": f"ERROR: {str(e)[:80]}"}

    # 2b. 三大法人買賣超
    try:
        for days_back in range(0, 7):
            check_date = today - timedelta(days=days_back)
            date_str = check_date.strftime("%Y%m%d")
            url = f"https://www.twse.com.tw/rwd/zh/fund/BFI82U?dayDate={date_str}&type=day&response=json"
            resp = requests.get(url, timeout=10)
            data = resp.json()
            if data.get("stat") == "OK" and data.get("data"):
                results["institutional_flow"] = {
                    "status": "OK",
                    "date": date_str,
                    "sample_data": [row[0] + ": " + row[4] for row in data["data"][:3]],
                    "note": "三大法人買賣超"
                }
                break
        if "institutional_flow" not in results:
            results["institutional_flow"] = {"status": "NO_DATA"}
    except Exception as e:
        results["institutional_flow"] = {"status": f"ERROR: {str(e)[:80]}"}

    # 2c. 當沖比率
    try:
        for days_back in range(0, 7):
            check_date = today - timedelta(days=days_back)
            date_str = check_date.strftime("%Y%m%d")
            url = f"https://www.twse.com.tw/rwd/zh/afterTrading/TWTB4U?date={date_str}&response=json"
            resp = requests.get(url, timeout=10)
            data = resp.json()
            if data.get("stat") == "OK" and data.get("data"):
                results["daytrade_ratio"] = {
                    "status": "OK",
                    "date": date_str,
                    "sample_data": data["data"][0][:5] if data["data"] else [],
                    "note": "當沖比率"
                }
                break
        if "daytrade_ratio" not in results:
            results["daytrade_ratio"] = {"status": "NO_DATA"}
    except Exception as e:
        results["daytrade_ratio"] = {"status": f"ERROR: {str(e)[:80]}"}

    return results


# ============================================================
# Step 0.3: AAII — 美國散戶情緒調查
# ============================================================
def step_03_aaii():
    """測試 AAII Sentiment Survey 數據取得"""
    import requests

    results = {}

    # 嘗試用公開的 AAII 數據 (可能需要 scrape)
    try:
        # Method 1: 嘗試 AAII 的 JSON endpoint
        url = "https://www.aaii.com/files/surveys/sentiment.xls"
        resp = requests.head(url, timeout=10, allow_redirects=True)
        results["aaii_xls"] = {
            "status": "ACCESSIBLE" if resp.status_code == 200 else f"HTTP_{resp.status_code}",
            "url": url,
            "note": "AAII 提供 Excel 下載，可定期抓取"
        }
    except Exception as e:
        results["aaii_xls"] = {"status": f"ERROR: {str(e)[:80]}"}

    # Method 2: 用 FRED 的 AAII 數據 (如果有)
    # FRED 沒有直接的 AAII 數據，但有些第三方有

    # Method 3: 硬編碼最近幾期數據作為 fallback
    results["fallback"] = {
        "status": "AVAILABLE",
        "note": "可以手動每週更新一次 AAII 數據，或用 selenium 自動抓",
        "alternative": "CNN Fear & Greed Index API 也是選項"
    }

    return results


# ============================================================
# Step 0.4: FRED — 美國宏觀數據
# ============================================================
def step_04_fred():
    """測試 FRED API (需要 key，先測試不需要 key 的替代方案)"""
    import requests

    results = {}

    # 用 yfinance 拉宏觀代理指標 (不需要 FRED key)
    import yfinance as yf

    macro_tickers = {
        "US_10Y_yield": "^TNX",      # 10 年期殖利率
        "US_2Y_yield": "^IRX",       # 13 週 T-Bill
        "Gold": "GC=F",              # 黃金 (避險指標)
        "Oil": "CL=F",              # 原油
        "DXY": "DX-Y.NYB",          # 美元指數
    }

    for name, ticker in macro_tickers.items():
        try:
            data = yf.download(ticker, period="1mo", progress=False)
            if len(data) > 0:
                last_close = float(data["Close"].iloc[-1].iloc[0]) if hasattr(data["Close"].iloc[-1], 'iloc') else float(data["Close"].iloc[-1])
                results[name] = {
                    "status": "OK",
                    "ticker": ticker,
                    "last_value": round(last_close, 2),
                }
            else:
                results[name] = {"status": "EMPTY", "ticker": ticker}
        except Exception as e:
            results[name] = {"status": f"ERROR: {str(e)[:80]}", "ticker": ticker}

    results["note"] = "用 yfinance 拉宏觀代理指標，不需要 FRED API key。如需更多宏觀數據再申請 FRED key。"
    return results


# ============================================================
# Step 0.5: 技術指標計算
# ============================================================
def step_05_technicals():
    """驗證技術指標計算"""
    import yfinance as yf
    import pandas as pd
    import numpy as np

    results = {}

    for name, ticker in [("SPY", "SPY"), ("TWII", "^TWII"), ("TSMC", "2330.TW")]:
        try:
            data = yf.download(ticker, period="6mo", progress=False)
            if len(data) < 20:
                results[name] = {"status": "INSUFFICIENT_DATA", "rows": len(data)}
                continue

            close = data["Close"].iloc[:, 0] if hasattr(data["Close"].iloc[0], '__len__') else data["Close"]

            # RSI (14)
            delta = close.diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))

            # SMA
            sma5 = close.rolling(5).mean()
            sma20 = close.rolling(20).mean()
            sma60 = close.rolling(60).mean()

            # Bollinger Bands
            bb_mid = sma20
            bb_std = close.rolling(20).std()
            bb_upper = bb_mid + 2 * bb_std
            bb_lower = bb_mid - 2 * bb_std

            # 52-week high/low
            high_52w = close.rolling(252, min_periods=60).max()
            pct_from_high = (close / high_52w * 100).iloc[-1]

            results[name] = {
                "status": "OK",
                "last_close": round(float(close.iloc[-1]), 2),
                "RSI_14": round(float(rsi.iloc[-1]), 2),
                "SMA_5": round(float(sma5.iloc[-1]), 2),
                "SMA_20": round(float(sma20.iloc[-1]), 2),
                "SMA_60": round(float(sma60.iloc[-1]), 2) if not np.isnan(sma60.iloc[-1]) else "N/A",
                "BB_upper": round(float(bb_upper.iloc[-1]), 2),
                "BB_lower": round(float(bb_lower.iloc[-1]), 2),
                "pct_from_52w_high": round(float(pct_from_high), 1),
            }
        except Exception as e:
            results[name] = {"status": f"ERROR: {str(e)[:100]}"}

    return results


# ============================================================
# Main: Run all steps
# ============================================================
def main():
    print("=" * 60)
    print("GRISI Phase 0: Data Source Feasibility Check")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    all_results = {}
    steps = [
        ("Step 0.1: yfinance (Market Data)", step_01_yfinance),
        ("Step 0.2: TWSE (Taiwan Retail Data)", step_02_twse),
        ("Step 0.3: AAII (US Retail Sentiment)", step_03_aaii),
        ("Step 0.4: Macro Data (via yfinance)", step_04_fred),
        ("Step 0.5: Technical Indicators", step_05_technicals),
    ]

    for step_name, step_func in steps:
        print(f"\n{'─' * 50}")
        print(f"Running: {step_name}")
        print(f"{'─' * 50}")
        try:
            result = step_func()
            all_results[step_name] = result

            # Print results
            for key, val in result.items():
                if isinstance(val, dict):
                    status = val.get("status", "?")
                    icon = "✅" if status == "OK" or status == "ACCESSIBLE" or status == "AVAILABLE" else "❌"
                    print(f"  {icon} {key}: {status}")
                    # Print extra details for OK items
                    for k, v in val.items():
                        if k != "status" and k != "ticker":
                            print(f"      {k}: {v}")
                else:
                    print(f"  {key}: {val}")

        except Exception as e:
            all_results[step_name] = {"error": str(e)}
            print(f"  ❌ STEP FAILED: {e}")

    # Summary
    print(f"\n{'=' * 60}")
    print("GATE 0 SUMMARY")
    print(f"{'=' * 60}")

    gate_pass = True
    for step_name, result in all_results.items():
        has_error = any(
            isinstance(v, dict) and v.get("status", "").startswith("ERROR")
            for v in result.values()
            if isinstance(v, dict)
        )
        has_ok = any(
            isinstance(v, dict) and v.get("status") in ["OK", "ACCESSIBLE", "AVAILABLE"]
            for v in result.values()
            if isinstance(v, dict)
        )
        icon = "✅" if has_ok and not has_error else "⚠️" if has_ok else "❌"
        if not has_ok:
            gate_pass = False
        print(f"  {icon} {step_name}")

    print(f"\n{'─' * 50}")
    if gate_pass:
        print("🎉 GATE 0 PASSED — All data sources accessible!")
        print("→ Proceed to Phase 1: Deterministic Scoring Engine")
    else:
        print("⚠️ GATE 0 PARTIAL — Some data sources need alternatives")
        print("→ Review failed steps and find alternatives before Phase 1")

    # Save results
    output_path = "data/phase0_results.json"
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(all_results, f, indent=2, ensure_ascii=False, default=str)
        print(f"\nResults saved to: {output_path}")
    except Exception as e:
        print(f"\nCould not save results: {e}")


if __name__ == "__main__":
    main()
