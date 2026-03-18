"""
GRISI Agent Test — Local Ollama Models
=======================================
US Agent (English) + TW Agent (繁體中文) + Control
Using qwen2.5:14b (best multilingual + structured output)
Runs each agent 5 times for statistical comparison.
"""
import sys, io, json, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import ollama

MODEL = "qwen2.5:14b"  # Best balance of quality + multilingual + speed

# Load real market snapshot
with open("data/snapshot_20260316.json", "r", encoding="utf-8") as f:
    snapshot = json.load(f)

# ============================================================
# Market data context (same for all agents)
# ============================================================
MARKET_DATA = f"""
## Market Data — {snapshot['date']}

### US Market
- SPY: ${snapshot['us_market']['SPY_close']} (RSI: {snapshot['us_market']['SPY_RSI14']}, {snapshot['us_market']['SPY_vs_52w_high_pct']}% of 52W high)
- SPY 5d return: {snapshot['us_market']['SPY_5d_return_pct']}%, 20d return: {snapshot['us_market']['SPY_20d_return_pct']}%
- SPY below SMA20 ({snapshot['us_market']['SPY_SMA20']}) and SMA60 ({snapshot['us_market']['SPY_SMA60']})
- VIX: {snapshot['us_market']['VIX']}
- US 10Y: {snapshot['us_market']['US_10Y_yield']}%

### Taiwan Market
- TAIEX: {snapshot['tw_market']['TAIEX_close']:.0f} (RSI: {snapshot['tw_market']['TAIEX_RSI14']}, {snapshot['tw_market']['TAIEX_vs_52w_high_pct']}% of 52W high)
- TSMC: {snapshot['tw_market']['TSMC_close']:.0f} ({snapshot['tw_market']['TSMC_vs_52w_high_pct']}% of 52W high)

### Taiwan Retail Indicators
- Margin balance: {snapshot['tw_retail_indicators']['margin_balance']:,} shares, 5d trend: {snapshot['tw_retail_indicators']['margin_5d_trend']} ({snapshot['tw_retail_indicators']['margin_5d_change_pct']:+.2f}%)
- TSMC margin: {snapshot['tw_retail_indicators']['TSMC_margin_balance']:,} shares (+{snapshot['tw_retail_indicators']['TSMC_margin_30d_change_pct']:.1f}% in 30 days)
- Institutional net: {snapshot['tw_retail_indicators']['institutional_net_TWD']:+.1f} billion TWD
- Foreign investors: {snapshot['tw_retail_indicators']['foreign_consecutive_days']} consecutive days {snapshot['tw_retail_indicators']['foreign_consecutive_direction']}
- Retail estimated net: {snapshot['tw_retail_indicators']['retail_net_est_TWD']:+.1f} billion TWD

### Global
- USD/JPY: {snapshot['global_context']['USDJPY']}, USD/TWD: {snapshot['global_context']['USDTWD']}, Gold: ${snapshot['global_context']['Gold']:.0f}
"""

OUTPUT_FORMAT = """
You MUST respond in EXACTLY this JSON format, nothing else:
{
  "sentiment_level": "<one of: EXTREME_FEAR, FEAR, CAUTIOUS, NEUTRAL, CAUTIOUS_OPTIMISM, OPTIMISM, EUPHORIA>",
  "conviction": "<one of: LOW, MEDIUM, HIGH>",
  "action": "<one of: STRONG_BUY, BUY, HOLD, SELL, STRONG_SELL>",
  "reasoning": "<your reasoning in the specified language, 2-3 sentences>",
  "emotion": "<primary emotion, one or two words>",
  "key_factors": ["<factor 1>", "<factor 2>", "<factor 3>"]
}
"""

# ============================================================
# Agent prompts with psychometric behavioral parameters
# ============================================================

US_PROMPT = f"""You are a typical American retail investor (Robinhood/Reddit user).

BEHAVIORAL PARAMETERS:
- LOSS_AVERSION: 2.0x (moderate — losses hurt 2x as much as equivalent gains feel good)
- HERDING: 0.60 (moderate — influenced by Reddit/FinTwit trends)
- OVERCONFIDENCE: +0.30 (you think you're better than average)
- FOMO_THRESHOLD: 0.30 (easily triggered — you hate missing rallies)
- DEFAULT_STATE: CAUTIOUS_OPTIMISM (you lean bullish by default)

DECISION RULES:
1. VIX > 25 AND market declining → shift toward FEAR
2. RSI < 30 → "buy the dip" instinct activates → shift toward OPTIMISM
3. You heavily weight Fed policy and US-centric data
4. When in doubt between two levels, choose the MORE OPTIMISTIC one

LANGUAGE: Respond in English. Sound like a real Reddit/Twitter retail investor, not an analyst.

{MARKET_DATA}

{OUTPUT_FORMAT}"""

TW_PROMPT = f"""你是一個典型的台灣散戶投資者（會看PTT Stock板、用LINE群組、關注三大法人）。

行為參數：
- 損失趨避: 2.8x（高 — 虧100元的痛苦等於錯過280元獲利的遺憾）
- 羊群傾向: 0.80（高 — 外資動態和群組風向強烈影響你）
- 過度自信: 0.00（中等 — 你不特別高估自己）
- FOMO: 0.30（容易觸發 — 群組裡大家都在賺你會焦慮）
- 錨定效應: 0.75（高 — 你很在意台積電和大盤的關鍵價位）
- 預設狀態: NEUTRAL（沒信號時傾向觀望，比美國散戶謹慎）

決策規則：
1. 外資連續賣超 > 3天 → 恐慌加重
2. 融資餘額增加 + 股價上漲 → 跟著加碼
3. 融資增加但股價下跌 → 「散戶套牢」恐慌
4. 台積電好壞決定你60%的情緒
5. 模糊時選擇更保守的那個選項

語言：用繁體中文回答。用PTT鄉民或LINE群組的口吻，像真的散戶在聊天。用「存股」「融資」「外資」「法人」「均線」等術語。

{MARKET_DATA}

{OUTPUT_FORMAT}"""

CONTROL_PROMPT = f"""You are a neutral market analyst with no cultural bias or behavioral tendencies.
Analyze objectively. No personality, no emotion. Pure data interpretation.
Respond in English.

{MARKET_DATA}

{OUTPUT_FORMAT}"""


def parse_json_response(text):
    """Extract JSON from LLM response, handling markdown code blocks"""
    # Try to find JSON block
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start)
        text = text[start:end].strip()
    elif "```" in text:
        start = text.index("```") + 3
        end = text.index("```", start)
        text = text[start:end].strip()

    # Find first { and last }
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1:
        text = text[first_brace:last_brace + 1]

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def run_agent(name, prompt, runs=5):
    """Run agent multiple times and collect results"""
    results = []
    print(f"\n{'='*55}")
    print(f"  {name} ({runs} runs)")
    print(f"{'='*55}")

    for i in range(runs):
        try:
            t0 = time.time()
            response = ollama.chat(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.3, "num_predict": 500}
            )
            elapsed = time.time() - t0
            text = response["message"]["content"]
            parsed = parse_json_response(text)

            if parsed and "sentiment_level" in parsed:
                results.append(parsed)
                print(f"  Run {i+1}: {parsed['sentiment_level']:20s} | {parsed['action']:10s} | {parsed['conviction']:6s} | {elapsed:.1f}s")
            else:
                print(f"  Run {i+1}: PARSE ERROR | {elapsed:.1f}s")
                print(f"    Raw: {text[:150]}...")
        except Exception as e:
            print(f"  Run {i+1}: ERROR: {str(e)[:80]}")

    return results


def analyze_results(all_results):
    """Compare agents statistically"""
    sentiment_map = {
        "EXTREME_FEAR": 0, "FEAR": 17, "CAUTIOUS": 33,
        "NEUTRAL": 50, "CAUTIOUS_OPTIMISM": 67, "OPTIMISM": 83, "EUPHORIA": 100
    }

    print(f"\n{'='*55}")
    print("  STATISTICAL COMPARISON")
    print(f"{'='*55}")

    for name, results in all_results.items():
        if not results:
            continue
        scores = [sentiment_map.get(r["sentiment_level"], 50) for r in results]
        sentiments = [r["sentiment_level"] for r in results]
        actions = [r["action"] for r in results]

        from collections import Counter
        sent_dist = Counter(sentiments)
        act_dist = Counter(actions)
        avg_score = sum(scores) / len(scores)

        print(f"\n  {name}:")
        print(f"    Avg Score: {avg_score:.1f}/100")
        print(f"    Sentiment Distribution: {dict(sent_dist)}")
        print(f"    Action Distribution:    {dict(act_dist)}")

        # Show one reasoning sample
        if results:
            print(f"    Sample Reasoning: {results[0].get('reasoning', 'N/A')[:120]}...")
            print(f"    Sample Emotion:   {results[0].get('emotion', 'N/A')}")
            kf = results[0].get('key_factors', [])
            if kf:
                for f in kf[:3]:
                    print(f"      - {f}")

    # Divergence
    agent_names = list(all_results.keys())
    if len(agent_names) >= 2:
        scores_per_agent = {}
        for name, results in all_results.items():
            if results:
                scores_per_agent[name] = sum(
                    sentiment_map.get(r["sentiment_level"], 50) for r in results
                ) / len(results)

        if len(scores_per_agent) >= 2:
            vals = list(scores_per_agent.values())
            import statistics
            divergence = statistics.stdev(vals) if len(vals) > 1 else 0
            print(f"\n  Cross-Agent Divergence (stdev): {divergence:.1f}")
            print(f"  Agent Scores: {', '.join(f'{k}: {v:.0f}' for k,v in scores_per_agent.items())}")

            if divergence < 5:
                print("  --> LOW divergence: agents agree (may need better persona differentiation)")
            elif divergence < 15:
                print("  --> MODERATE divergence: some cultural difference detected")
            else:
                print("  --> HIGH divergence: strong cultural differentiation!")


def main():
    print("=" * 55)
    print("  GRISI Agent Test — Local Ollama")
    print(f"  Model: {MODEL}")
    print(f"  Date: {snapshot['date']}")
    print("=" * 55)

    # Warm up model
    print(f"\nLoading {MODEL}...")
    try:
        ollama.chat(model=MODEL, messages=[{"role": "user", "content": "Hi"}],
                    options={"num_predict": 5})
        print("Model ready.")
    except Exception as e:
        print(f"Error loading model: {e}")
        print("Make sure Ollama is running: ollama serve")
        return

    RUNS = 5

    all_results = {}
    all_results["US_RETAIL"] = run_agent("US RETAIL (English)", US_PROMPT, RUNS)
    all_results["TW_RETAIL"] = run_agent("TW RETAIL (繁體中文)", TW_PROMPT, RUNS)
    all_results["CONTROL"] = run_agent("CONTROL (Neutral)", CONTROL_PROMPT, RUNS)

    analyze_results(all_results)

    # Save
    output_path = "data/agent_test_results_local.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to: {output_path}")


if __name__ == "__main__":
    main()
