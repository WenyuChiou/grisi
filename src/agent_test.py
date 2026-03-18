"""
GRISI Agent Test — Bilingual Cultural Agents
=============================================
US Agent speaks English, TW Agent speaks Traditional Chinese.
Uses psychometric behavioral parameters + real market data.
"""
import sys, io, json, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

try:
    import anthropic
except ImportError:
    print("Installing anthropic...")
    os.system("pip install anthropic --quiet")
    import anthropic

# Load real market snapshot
with open("data/snapshot_20260316.json", "r", encoding="utf-8") as f:
    snapshot = json.load(f)

client = anthropic.Anthropic()

# ============================================================
# Shared market data context (same for all agents)
# ============================================================
MARKET_DATA = f"""
## Market Data Snapshot — {snapshot['date']}

### US Market
- SPY: ${snapshot['us_market']['SPY_close']} (RSI: {snapshot['us_market']['SPY_RSI14']}, {snapshot['us_market']['SPY_vs_52w_high_pct']}% of 52W high)
- SPY 5-day return: {snapshot['us_market']['SPY_5d_return_pct']}%
- SPY 20-day return: {snapshot['us_market']['SPY_20d_return_pct']}%
- SPY below SMA20 ({snapshot['us_market']['SPY_SMA20']}) and SMA60 ({snapshot['us_market']['SPY_SMA60']})
- VIX: {snapshot['us_market']['VIX']}
- US 10Y Yield: {snapshot['us_market']['US_10Y_yield']}%

### Taiwan Market
- TAIEX: {snapshot['tw_market']['TAIEX_close']:.0f} (RSI: {snapshot['tw_market']['TAIEX_RSI14']}, {snapshot['tw_market']['TAIEX_vs_52w_high_pct']}% of 52W high)
- TSMC: {snapshot['tw_market']['TSMC_close']:.0f} ({snapshot['tw_market']['TSMC_vs_52w_high_pct']}% of 52W high)
- Margin balance (全市場融資餘額): {snapshot['tw_retail_indicators']['margin_balance']:,} shares
- Margin 5-day trend: {snapshot['tw_retail_indicators']['margin_5d_trend']} ({snapshot['tw_retail_indicators']['margin_5d_change_pct']:+.2f}%)
- TSMC margin balance: {snapshot['tw_retail_indicators']['TSMC_margin_balance']:,} shares (+{snapshot['tw_retail_indicators']['TSMC_margin_30d_change_pct']:.1f}% in 30 days)
- Institutional net: {snapshot['tw_retail_indicators']['institutional_net_TWD']:+.1f} billion TWD (negative = selling)
- Foreign investors: consecutive {snapshot['tw_retail_indicators']['foreign_consecutive_days']} days {snapshot['tw_retail_indicators']['foreign_consecutive_direction']}
- Retail estimated net: {snapshot['tw_retail_indicators']['retail_net_est_TWD']:+.1f} billion TWD (positive = buying)

### Global
- USD/JPY: {snapshot['global_context']['USDJPY']}
- USD/TWD: {snapshot['global_context']['USDTWD']}
- Gold: ${snapshot['global_context']['Gold']:.0f}
"""

# ============================================================
# Agent tool schema (structured output)
# ============================================================
SENTIMENT_TOOL = {
    "name": "submit_sentiment",
    "description": "Submit your sentiment analysis for the current market conditions.",
    "input_schema": {
        "type": "object",
        "properties": {
            "sentiment_level": {
                "type": "string",
                "enum": ["EXTREME_FEAR", "FEAR", "CAUTIOUS", "NEUTRAL",
                         "CAUTIOUS_OPTIMISM", "OPTIMISM", "EUPHORIA"],
                "description": "Your overall sentiment level"
            },
            "conviction": {
                "type": "string",
                "enum": ["LOW", "MEDIUM", "HIGH"],
                "description": "How confident you are in this assessment"
            },
            "action": {
                "type": "string",
                "enum": ["STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL"],
                "description": "What action you would take right now"
            },
            "reasoning": {
                "type": "string",
                "description": "Your reasoning in your native language and speaking style (US=English, TW=Traditional Chinese)"
            },
            "emotion": {
                "type": "string",
                "description": "Primary emotion driving your decision (in native language)"
            },
            "key_factors": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Top 3 factors driving your decision (in native language)"
            }
        },
        "required": ["sentiment_level", "conviction", "action", "reasoning", "emotion", "key_factors"]
    }
}

# ============================================================
# US Agent — English, Robinhood/WSB retail persona
# ============================================================
US_SYSTEM_PROMPT = """You are a typical American retail investor in 2026. You trade on Robinhood, follow r/WallStreetBets and FinTwit, and watch CNBC occasionally.

## YOUR BEHAVIORAL PARAMETERS (from psychometric calibration)

LOSS_AVERSION: 2.0x
- A $100 loss hurts as much as missing a $200 gain
- You can tolerate moderate drawdowns before panicking

HERDING_WEIGHT: 0.60
- You're somewhat influenced by what others are doing
- Reddit sentiment and FinTwit trends matter to you

OVERCONFIDENCE: +0.30 (above average)
- You believe you're better than most retail investors
- You tend to hold losing positions too long ("diamond hands")

FOMO_THRESHOLD: 0.30 (easily triggered)
- When stocks rally and you're not in, you feel strong urge to buy
- "Buy the dip" is your instinct on any pullback

ANCHORING: 0.50 (moderate)
- You compare current prices to recent all-time highs
- 52-week high is your reference point

DEFAULT_STATE: CAUTIOUS_OPTIMISM
- Without strong signals, you lean slightly bullish
- "Stocks always go up in the long run" is your baseline belief

## DECISION RULES
1. If Fed is hawkish AND VIX > 25 → shift 2 levels toward fear
2. If market drops > 5% from high → your "buy the dip" instinct activates
3. If RSI < 30 → you see "oversold bounce" opportunity
4. If AI/tech narrative is strong → amplify conviction by 1 level

## LANGUAGE
- Respond in English
- Use casual American retail investor language
- Reference things like "the Fed", "buying the dip", "diamond hands", options terminology
- Sound like a real person posting on Reddit or Twitter, not a financial analyst

Analyze the market data and submit your sentiment using the tool."""

# ============================================================
# TW Agent — Traditional Chinese, PTT/LINE retail persona
# ============================================================
TW_SYSTEM_PROMPT = """你是一個 2026 年的台灣散戶投資者。你會看 PTT Stock 板、用 LINE 投資群組交流、關注三大法人進出和技術指標。

## 你的行為參數（心理測量校準結果）

損失趨避倍數: 2.8x
- 虧 100 元的痛苦 = 錯過 280 元獲利的遺憾
- 你對虧損非常敏感，尤其融資被斷頭的恐懼

羊群傾向: 0.80（高）
- 你很容易被群組風向和外資動態影響
- 「外資在買」= 強力買入信號；「外資在跑」= 恐慌信號

過度自信: 0.00（中等）
- 你不會特別高估自己的能力
- 但你相信技術分析和法人跟單

FOMO: 0.30（容易觸發）
- 群組裡大家都在賺，你會很焦慮
- 台積電創新高而你沒買，會非常後悔

錨定效應: 0.75（高）
- 你非常在意台積電和大盤的「整數關卡」
- 「台積電跌破 1900」或「大盤守不住萬八」會讓你恐慌

預設狀態: NEUTRAL（中性）
- 沒有強烈信號時，你傾向觀望
- 比美國散戶更謹慎

## 決策規則
1. 外資連續賣超 > 3 天 → 恐慌加 1 級
2. 融資餘額增加 + 台積電上漲 → 你會跟著加碼
3. 融資餘額增加但股價下跌 → 「散戶套牢」恐慌
4. 台積電是你的信仰 — 台積電好壞直接決定你 60% 的情緒

## 語言
- 用繁體中文回答
- 用台灣散戶的口吻，像在 PTT 發文或 LINE 群組聊天
- 可以用鄉民用語、表情符號
- 提到「存股」「融資」「外資」「法人」「技術面」「均線」「KD」等台灣散戶常用術語
- 不要像分析師，要像真正的散戶在聊天

分析以下市場數據，然後用工具提交你的情緒判斷。"""

# ============================================================
# Control Agent — No persona, neutral analysis
# ============================================================
CONTROL_SYSTEM_PROMPT = """You are a neutral market analyst with no cultural bias or behavioral tendencies.
Analyze the market data objectively and submit your assessment.
Respond in English. Be factual and balanced."""


def run_agent(name, system_prompt, market_data):
    """Run a single agent and return structured output"""
    print(f"\n{'='*50}")
    print(f"Running: {name}")
    print(f"{'='*50}")

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1000,
            temperature=0.3,
            system=system_prompt,
            tools=[SENTIMENT_TOOL],
            tool_choice={"type": "tool", "name": "submit_sentiment"},
            messages=[{"role": "user", "content": market_data}]
        )

        # Extract tool use result
        for block in response.content:
            if block.type == "tool_use":
                result = block.input
                print(f"\nSentiment: {result['sentiment_level']}")
                print(f"Conviction: {result['conviction']}")
                print(f"Action: {result['action']}")
                print(f"Emotion: {result['emotion']}")
                print(f"Key Factors:")
                for f in result.get('key_factors', []):
                    print(f"  - {f}")
                print(f"\nReasoning:")
                print(f"  {result['reasoning']}")
                return result

        print("No tool use in response")
        return None

    except Exception as e:
        print(f"ERROR: {e}")
        return None


def main():
    print("=" * 60)
    print("GRISI Agent Test — Bilingual Cultural Agents")
    print(f"Date: {snapshot['date']}")
    print("Using REAL market data from today")
    print("=" * 60)

    results = {}

    # Run all 3 agents
    for name, prompt in [
        ("US_RETAIL (English)", US_SYSTEM_PROMPT),
        ("TW_RETAIL (Traditional Chinese)", TW_SYSTEM_PROMPT),
        ("CONTROL (Neutral)", CONTROL_SYSTEM_PROMPT),
    ]:
        result = run_agent(name, prompt, MARKET_DATA)
        if result:
            results[name] = result

    # Compare
    if len(results) >= 2:
        print(f"\n{'='*60}")
        print("COMPARISON")
        print(f"{'='*60}")

        sentiment_map = {
            "EXTREME_FEAR": 0, "FEAR": 17, "CAUTIOUS": 33,
            "NEUTRAL": 50, "CAUTIOUS_OPTIMISM": 67, "OPTIMISM": 83, "EUPHORIA": 100
        }

        for name, r in results.items():
            score = sentiment_map.get(r['sentiment_level'], 50)
            bar = '#' * (score // 5) + '.' * (20 - score // 5)
            print(f"  {name:40s} [{bar}] {score:3d} {r['sentiment_level']} / {r['action']}")

    # Save results
    output_path = "data/agent_test_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to: {output_path}")


if __name__ == "__main__":
    main()
