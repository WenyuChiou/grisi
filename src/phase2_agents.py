"""
Moodring Phase 2: Cultural Behavioral Agents
==========================================
Two layers:
  1. Behavioral Adjustment Model (deterministic, backtestable)
     - Applies psychometric parameters (loss aversion, herding, FOMO, anchoring)
     - Nonlinear transformation of base score → culturally-adjusted score
  2. LLM Cultural Agents (real-time narrative)
     - US: English, Robinhood/WSB persona
     - TW: Traditional Chinese, PTT/LINE persona
     - Receive base score + margin context → produce narrative + conviction

The behavioral model IS the alpha source; the LLM adds interpretability.
"""

import sys
import io
import os
import json
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from datetime import datetime

# Note: don't re-wrap stdout here — backtest.py import also wraps it, causing conflicts

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')


# ============================================================
# Psychometric Parameters (from agent calibration)
# ============================================================

US_PARAMS = {
    'loss_aversion': 2.0,       # losses hurt 2x gains
    'herding': 0.60,            # moderate crowd-following
    'overconfidence': 0.30,     # above average self-belief
    'fomo_threshold': 0.30,     # easily triggered by rallies
    'anchoring': 0.50,          # moderate reference-point fixation
    'default_bias': +5,         # slight bullish lean (CAUTIOUS_OPTIMISM)
}

TW_PARAMS = {
    'loss_aversion': 2.8,       # losses hurt 2.8x gains (more risk-averse)
    'herding': 0.80,            # strong crowd-following (PTT/LINE groups)
    'overconfidence': 0.00,     # neutral self-assessment
    'fomo_threshold': 0.30,     # same FOMO trigger as US
    'anchoring': 0.75,          # strong round-number fixation
    'default_bias': 0,          # neutral baseline (more conservative)
}


# ============================================================
# Behavioral Adjustment Functions
# ============================================================

def compute_fomo_adjustment(base_score, momentum, position_vs_high, params):
    """
    FOMO effect: when market rallies and you're not fully in, greed amplifies.
    - Triggers when: momentum > 0 AND position near ATH
    - Strength: proportional to fomo_threshold (lower = more easily triggered)
    - Effect: pushes score HIGHER (more greedy)
    """
    # momentum is fractional (0.05 = 5%), position is 0-100 pct of 52W high
    mom_pct = momentum * 100  # convert to percentage
    fomo_signal = np.where(
        (mom_pct > 1) & (position_vs_high > 92),
        mom_pct * (1 - params['fomo_threshold']),
        0
    )
    # Each 1% momentum above threshold adds ~1-2 points
    adjustment = np.clip(fomo_signal * 1.5, 0, 12)
    return adjustment


def compute_loss_aversion_adjustment(base_score, drawdown_pct, vol, params):
    """
    Loss aversion: drawdowns trigger disproportionate fear.
    - Asymmetric: a 10% drawdown shifts score MORE than a 10% rally
    - Strength: proportional to loss_aversion multiplier
    - Effect: pushes score LOWER (more fearful) during drawdowns
    - Only activates on meaningful drawdowns (>8%), not normal noise
    """
    # drawdown_pct = position vs 52W high (100 = at high, 80 = 20% drawdown)
    drawdown = np.clip(100 - drawdown_pct, 0, 50)  # 0 = at high, 50 = 50% off

    # Threshold: normal markets fluctuate 3-8% from highs
    # Only trigger loss aversion on meaningful drawdowns
    threshold = 8.0

    fear_factor = np.where(
        drawdown > threshold,
        -(drawdown - threshold) * (params['loss_aversion'] - 1) * 0.5,
        0  # no effect within normal range
    )

    # High vol amplifies fear (uncertainty)
    vol_z = (vol - vol.rolling(252, min_periods=60).mean()) / vol.rolling(252, min_periods=60).std().replace(0, np.nan)
    vol_multiplier = np.clip(1 + vol_z.fillna(0) * 0.3, 0.5, 2.0)
    adjustment = fear_factor * vol_multiplier

    return np.clip(adjustment, -20, 0)


def compute_herding_adjustment(base_score, institutional_signal, params):
    """
    Herding: retail follows institutional/crowd signals.
    - When institutions are buying → retail follows → amplifies greed
    - When institutions are selling → retail panics → amplifies fear
    - Strength: proportional to herding weight
    - TW herding (0.80) much stronger than US (0.60)
    """
    # institutional_signal: z-score of crowd proxy (volume surge for TW, corr for US)
    # Stronger z-scores → stronger herding effect
    herd_effect = institutional_signal * params['herding'] * 4

    return np.clip(herd_effect, -12, 12)


def compute_anchoring_adjustment(base_score, price_vs_round, params):
    """
    Anchoring: fixation on round numbers and reference points.
    - Breaking above a round number → euphoria burst
    - Breaking below → panic burst
    - TW retail especially anchored to TSMC round numbers (1000, 1500, 2000)
    """
    # price_vs_round: distance from nearest psychological level (z-scored)
    # Positive = just broke above, Negative = just broke below
    anchor_effect = price_vs_round * params['anchoring'] * 3

    return np.clip(anchor_effect, -8, 8)


def compute_overconfidence_adjustment(base_score, recent_win_rate, params):
    """
    Overconfidence: after winning streaks, retail becomes too bullish.
    - Recent wins → "I'm good at this" → more aggressive
    - Only activates if overconfidence > 0
    """
    if params['overconfidence'] <= 0:
        return 0

    # recent_win_rate: fraction of positive days in last 20 days
    confidence_boost = np.where(
        recent_win_rate > 0.6,
        (recent_win_rate - 0.5) * params['overconfidence'] * 20,
        0
    )

    return np.clip(confidence_boost, 0, 8)


# ============================================================
# Main Behavioral Score
# ============================================================

def compute_behavioral_score(base_score, features, params, market='US'):
    """
    Apply behavioral adjustments to base quantitative score.

    Key design: behavioral adjustments only activate at EXTREME base scores.
    In normal territory (35-65), adjustments are suppressed.
    This prevents noise during normal markets while amplifying signal at extremes.

    Returns: adjusted_score (0-100), adjustment_details dict
    """
    adj = pd.DataFrame(index=features.index)

    # ── Activation gate: how extreme is the base score? ──
    # 0 at center (score=50), 1 at extremes (score=0 or 100)
    # Uses smooth sigmoid-like ramp: zero in [35,65], linear ramp outside
    extremity = np.clip((np.abs(base_score - 50) - 15) / 15, 0, 1)
    # Direction: positive when score > 50 (greedy), negative when < 50 (fearful)
    direction = np.sign(base_score - 50)

    # ── Extract signals from features ──
    # Note: vs_high stored as ratio (0.95 = 95%), momentum as fraction (0.05 = 5%)
    if market == 'US':
        momentum = features.get('us_spy_mom_20d', pd.Series(0, index=features.index))
        position = features.get('us_spy_vs_high', pd.Series(0.95, index=features.index)) * 100
        vol = features.get('us_vix', pd.Series(18, index=features.index))
        inst_signal = features.get('us_vix_spy_corr_20d', pd.Series(0, index=features.index))
        inst_z = (inst_signal - inst_signal.rolling(252, min_periods=60).mean()) / \
                 inst_signal.rolling(252, min_periods=60).std().replace(0, np.nan)
    else:
        momentum = features.get('tw_taiex_mom_20d', pd.Series(0, index=features.index))
        position = features.get('tw_taiex_vs_high', pd.Series(0.95, index=features.index)) * 100
        vol = features.get('tw_realized_vol_20d', pd.Series(0.15, index=features.index)) * 100
        inst_signal = features.get('tw_volume_surge', pd.Series(1, index=features.index))
        inst_z = (inst_signal - inst_signal.rolling(252, min_periods=60).mean()) / \
                 inst_signal.rolling(252, min_periods=60).std().replace(0, np.nan)

    # ── Compute raw behavioral adjustments ──
    raw_fomo = compute_fomo_adjustment(base_score, momentum, position, params)
    raw_loss = compute_loss_aversion_adjustment(base_score, position, vol, params)
    raw_herd = compute_herding_adjustment(base_score, inst_z.fillna(0), params)

    anchor_signal = (position - position.rolling(60, min_periods=20).mean()) / \
                    position.rolling(60, min_periods=20).std().replace(0, np.nan)
    raw_anchor = compute_anchoring_adjustment(base_score, anchor_signal.fillna(0), params)

    if market == 'US':
        price_col = features.get('us_spy_vs_high', pd.Series(0.95, index=features.index))
    else:
        price_col = features.get('tw_taiex_vs_high', pd.Series(0.95, index=features.index))
    daily_change = price_col.diff()
    win_proxy = (daily_change > 0).rolling(20, min_periods=10).mean()
    raw_overconf = compute_overconfidence_adjustment(base_score, win_proxy.fillna(0.5), params)

    # ── Apply extremity gate: suppress adjustments in normal territory ──
    # Greedy adjustments (FOMO, overconfidence) only when base score > 65
    # Fearful adjustments (loss aversion) only when base score < 35
    # Herding/anchoring: activate in proportion to extremity (either direction)
    greedy_gate = np.clip((base_score - 60) / 10, 0, 1)   # 0 at score<60, 1 at score>70
    fear_gate = np.clip((40 - base_score) / 10, 0, 1)     # 0 at score>40, 1 at score<30

    adj['fomo'] = raw_fomo * greedy_gate
    adj['loss_aversion'] = raw_loss * fear_gate
    adj['herding'] = raw_herd * extremity
    adj['anchoring'] = raw_anchor * extremity
    adj['overconfidence'] = raw_overconf * greedy_gate

    # No constant cultural bias — behavioral adjustments are conditional only
    adj['cultural_bias'] = 0

    # ── Total adjustment ──
    total_adj = adj.sum(axis=1)

    # Final score: base + behavioral adjustment, clipped to 0-100
    adjusted_score = np.clip(base_score + total_adj, 0, 100)

    return adjusted_score, adj


# ============================================================
# Phase 2 Backtest: Compare base vs behavioral scores
# ============================================================

def evaluate_signal(score, target, label=''):
    """IC + quintile evaluation (same as backtest.py)."""
    aligned = pd.DataFrame({'score': score, 'target': target}).dropna()
    if len(aligned) < 100:
        return {'error': f'insufficient data ({len(aligned)})', 'label': label}

    s, t = aligned['score'], aligned['target']
    ic, pval = spearmanr(s, t)

    # Quintile spread
    try:
        aligned['q'] = pd.qcut(s, 5, labels=[1, 2, 3, 4, 5], duplicates='drop')
        qr = aligned.groupby('q')['target'].mean()
        spread = qr.iloc[0] - qr.iloc[-1]
        horizon_days = max(5, int(''.join(filter(str.isdigit, label.split('_')[-1]))) if any(c.isdigit() for c in label) else 20)
        spread_annual = spread * (252 / horizon_days)
    except Exception:
        spread = spread_annual = np.nan

    # L/S Sharpe
    ls = []
    for _, row in aligned.iterrows():
        if row['score'] < 30:
            ls.append(row['target'])
        elif row['score'] > 70:
            ls.append(-row['target'])
    if len(ls) > 20:
        ls_s = pd.Series(ls)
        ls_sharpe = (ls_s.mean() / ls_s.std() * np.sqrt(252 / horizon_days)) if ls_s.std() > 0 else 0
    else:
        ls_sharpe = np.nan

    return {
        'label': label,
        'n_obs': len(aligned),
        'ic': round(ic, 4),
        'ic_pval': round(pval, 6),
        'ic_significant': pval < 0.05,
        'spread_annual_pct': round(spread_annual * 100, 1) if not np.isnan(spread_annual) else None,
        'ls_sharpe': round(ls_sharpe, 3) if not np.isnan(ls_sharpe) else None,
    }


def evaluate_robustness(score, target):
    """IC by time period."""
    aligned = pd.DataFrame({'score': score, 'target': target}).dropna()
    periods = {
        '2012-2015': ('2012-01-01', '2015-12-31'),
        '2016-2019': ('2016-01-01', '2019-12-31'),
        '2020-2022': ('2020-01-01', '2022-12-31'),
        '2023-2025': ('2023-01-01', '2025-12-31'),
    }
    results = {}
    for name, (s, e) in periods.items():
        mask = (aligned.index >= s) & (aligned.index <= e)
        sub = aligned[mask]
        if len(sub) > 50:
            ic, pval = spearmanr(sub['score'], sub['target'])
            results[name] = {'n': len(sub), 'ic': round(ic, 4), 'pval': round(pval, 4), 'significant': pval < 0.05}
    return results


# ============================================================
# LLM Cultural Agent (real-time narrative)
# ============================================================

def build_agent_context(market, base_score, components, snapshot, margin_data=None):
    """Build context string for LLM agent with score + raw data."""
    ctx = f"""## Moodring Quantitative Score Report

### Base Score: {base_score:.1f}/100
Interpretation: {"GREEDY" if base_score > 65 else "FEARFUL" if base_score < 35 else "NEUTRAL"}

### Score Components:
"""
    for comp, val in components.items():
        ctx += f"- {comp}: {val:.1f}/100\n"

    if market == 'TW' and margin_data:
        ctx += f"""
### Taiwan Retail Activity (Phase 1C data):
- Margin balance: {margin_data.get('margin_balance', 'N/A'):,} shares
- Margin 5-day trend: {margin_data.get('margin_5d_trend', 'N/A')} ({margin_data.get('margin_5d_change_pct', 0):+.2f}%)
- TSMC margin: {margin_data.get('TSMC_margin_balance', 'N/A'):,} shares ({margin_data.get('TSMC_margin_30d_change_pct', 0):+.1f}% in 30d)
- Foreign investors: {margin_data.get('foreign_consecutive_days', 0)} consecutive days {margin_data.get('foreign_consecutive_direction', 'N/A')}
- Retail estimated net: {margin_data.get('retail_net_est_TWD', 0):+.1f} billion TWD
"""

    ctx += f"""
### Market Snapshot ({snapshot.get('date', 'today')}):
"""
    if market == 'US':
        us = snapshot.get('us_market', {})
        ctx += f"""- SPY: ${us.get('SPY_close', 'N/A')} (RSI: {us.get('SPY_RSI14', 'N/A')}, {us.get('SPY_vs_52w_high_pct', 'N/A')}% of 52W high)
- VIX: {us.get('VIX', 'N/A')}
- US 10Y: {us.get('US_10Y_yield', 'N/A')}%
- 5d return: {us.get('SPY_5d_return_pct', 'N/A')}%
- 20d return: {us.get('SPY_20d_return_pct', 'N/A')}%
"""
    else:
        tw = snapshot.get('tw_market', {})
        ctx += f"""- TAIEX: {tw.get('TAIEX_close', 'N/A'):.0f} (RSI: {tw.get('TAIEX_RSI14', 'N/A')}, {tw.get('TAIEX_vs_52w_high_pct', 'N/A')}% of 52W high)
- TSMC: {tw.get('TSMC_close', 'N/A'):.0f} ({tw.get('TSMC_vs_52w_high_pct', 'N/A')}% of 52W high)
"""

    gl = snapshot.get('global_context', {})
    ctx += f"""
### Global:
- Gold: ${gl.get('Gold', 'N/A'):.0f}
- USD/JPY: {gl.get('USDJPY', 'N/A')}
- USD/TWD: {gl.get('USDTWD', 'N/A')}
"""
    return ctx


# Agent system prompts with score-adjustment capability
US_AGENT_SYSTEM = """You are a typical American retail investor in 2026 analyzing the Moodring sentiment score.

You've been given the QUANTITATIVE base score (0-100, high=greedy). Your job is to:
1. React to the score and market data AS A RETAIL INVESTOR would
2. Decide if the quantitative score UNDER-estimates or OVER-estimates retail greed
3. Provide a conviction adjustment

## YOUR BEHAVIORAL PROFILE
- Loss aversion: 2.0x (moderate)
- Herding: 0.60 (somewhat crowd-following)
- Overconfidence: +0.30 (you think you're smarter than average)
- FOMO threshold: 0.30 (easily triggered by rallies)
- Default state: CAUTIOUS_OPTIMISM (slight bullish lean)

## ADJUSTMENT GUIDELINES
- If you feel the quant score misses something (e.g., narrative momentum, meme energy, Fed vibes), adjust it
- Adjustment range: -15 to +15 points
- Positive adjustment = you think retail is MORE greedy than the number shows
- Negative adjustment = you think retail is MORE fearful than the number shows

Respond in casual American retail investor English. Be authentic — sound like Reddit/FinTwit."""

TW_AGENT_SYSTEM = """你是一個 2026 年的台灣散戶投資者，正在分析 Moodring 情緒指數。

你收到了量化基礎分數（0-100，高=貪婪）。你的任務：
1. 以散戶的角度回應這個分數和市場數據
2. 判斷量化分數是否低估或高估了散戶的貪婪程度
3. 提供信心調整值

## 你的行為側寫
- 損失趨避: 2.8x（比美國散戶更怕虧錢）
- 羊群效應: 0.80（很容易被群組風向影響）
- 過度自信: 0.00（不會高估自己）
- FOMO: 0.30（容易被漲勢觸發）
- 預設狀態: NEUTRAL（沒有明確方向時傾向觀望）

## 調整範圍
- 調整範圍: -15 到 +15 分
- 正向調整 = 你覺得散戶比數字顯示的更貪婪
- 負向調整 = 你覺得散戶比數字顯示的更恐懼
- 特別注意：融資餘額變化、外資動向、台積電的影響

用繁體中文回答，像在 PTT Stock 板發文的語氣。"""


AGENT_TOOL = {
    "name": "submit_adjusted_sentiment",
    "description": "Submit your sentiment analysis with score adjustment.",
    "input_schema": {
        "type": "object",
        "properties": {
            "adjusted_score_delta": {
                "type": "number",
                "description": "Your adjustment to the base score (-15 to +15). Positive = more greedy, negative = more fearful."
            },
            "sentiment_level": {
                "type": "string",
                "enum": ["EXTREME_FEAR", "FEAR", "CAUTIOUS", "NEUTRAL",
                         "CAUTIOUS_OPTIMISM", "OPTIMISM", "EUPHORIA"],
            },
            "conviction": {
                "type": "string",
                "enum": ["LOW", "MEDIUM", "HIGH"],
            },
            "narrative": {
                "type": "string",
                "description": "2-3 sentence narrative explaining your view in your native language/style"
            },
            "key_factors": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Top 3 factors (in native language)"
            },
            "what_quant_misses": {
                "type": "string",
                "description": "What the quantitative score fails to capture (in native language)"
            }
        },
        "required": ["adjusted_score_delta", "sentiment_level", "conviction",
                     "narrative", "key_factors", "what_quant_misses"]
    }
}


def run_llm_agent(market, system_prompt, context, model="qwen2.5:14b"):
    """Run LLM cultural agent via Ollama (local) for real-time narrative."""
    import re
    try:
        import requests

        # Build prompt that asks for JSON output
        json_schema = json.dumps({
            "adjusted_score_delta": "number (-15 to +15)",
            "sentiment_level": "EXTREME_FEAR|FEAR|CAUTIOUS|NEUTRAL|CAUTIOUS_OPTIMISM|OPTIMISM|EUPHORIA",
            "conviction": "LOW|MEDIUM|HIGH",
            "narrative": "2-3 sentences in native language",
            "key_factors": ["factor1", "factor2", "factor3"],
            "what_quant_misses": "string in native language"
        }, indent=2, ensure_ascii=False)

        full_prompt = f"""{system_prompt}

You MUST respond with ONLY a JSON object in this exact format:
```json
{json_schema}
```

Here is the market data and score report:

{context}

Respond with ONLY the JSON object, no other text."""

        response = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": model,
                "prompt": full_prompt,
                "stream": False,
                "options": {"temperature": 0.4, "num_predict": 800}
            },
            timeout=120
        )

        if response.status_code != 200:
            print(f"  Ollama error ({market}): HTTP {response.status_code}")
            return None

        text = response.json().get("response", "")

        # Extract JSON from response (may be wrapped in ```json ... ```)
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            result = json.loads(json_match.group())
            # Validate required fields
            required = ["adjusted_score_delta", "sentiment_level", "conviction",
                       "narrative", "key_factors", "what_quant_misses"]
            if all(k in result for k in required):
                return result
            else:
                print(f"  Missing fields ({market}): {[k for k in required if k not in result]}")
                return result  # return partial

        print(f"  No JSON found in response ({market})")
        return None

    except Exception as e:
        print(f"  LLM agent error ({market}): {e}")
        return None


# ============================================================
# Main: Phase 2 Backtest + Real-time Demo
# ============================================================

def main():
    print("=" * 60)
    print("Moodring Phase 2: Cultural Behavioral Agents")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    # ── Load data ──
    features = pd.read_csv(os.path.join(DATA_DIR, 'base_features.csv'),
                           index_col=0, parse_dates=True)
    targets = pd.read_csv(os.path.join(DATA_DIR, 'target_returns.csv'),
                          index_col=0, parse_dates=True)

    # Import scoring functions from backtest.py
    sys.path.insert(0, os.path.dirname(__file__))
    from backtest import compute_us_retail_score, compute_tw_retail_score

    # ── Compute base scores ──
    print("\n[1/4] Computing base quantitative scores...")
    us_base, us_comp = compute_us_retail_score(features)
    tw_base, tw_comp = compute_tw_retail_score(features)
    print(f"  US base: mean={us_base.mean():.1f}, std={us_base.std():.1f}")
    print(f"  TW base: mean={tw_base.mean():.1f}, std={tw_base.std():.1f}")

    # ── Compute behavioral scores ──
    print("\n[2/4] Applying behavioral adjustments...")
    us_adj_score, us_adj_details = compute_behavioral_score(us_base, features, US_PARAMS, 'US')
    tw_adj_score, tw_adj_details = compute_behavioral_score(tw_base, features, TW_PARAMS, 'TW')

    print(f"  US adjusted: mean={us_adj_score.mean():.1f}, std={us_adj_score.std():.1f}")
    print(f"  TW adjusted: mean={tw_adj_score.mean():.1f}, std={tw_adj_score.std():.1f}")

    # Show adjustment breakdown
    print("\n  US adjustment components (mean):")
    for col in us_adj_details.columns:
        print(f"    {col}: {us_adj_details[col].mean():+.2f}")

    print("\n  TW adjustment components (mean):")
    for col in tw_adj_details.columns:
        print(f"    {col}: {tw_adj_details[col].mean():+.2f}")

    # ── Backtest: base vs adjusted ──
    print("\n[3/4] Backtesting: base vs behavioral-adjusted scores...")
    print(f"\n{'':>40s} {'IC':>8s} {'Sig':>5s} {'Spread%':>8s} {'Sharpe':>8s}")
    print("-" * 75)

    all_results = {}

    for horizon in ['5d', '10d', '20d', '60d']:
        # US
        spy_col = f'spy_fwd_{horizon}'
        if spy_col in targets.columns:
            base_r = evaluate_signal(us_base, targets[spy_col], f'US_base_{horizon}')
            adj_r = evaluate_signal(us_adj_score, targets[spy_col], f'US_behavioral_{horizon}')
            all_results[f'US_base_{horizon}'] = base_r
            all_results[f'US_behavioral_{horizon}'] = adj_r

            ic_delta = adj_r['ic'] - base_r['ic']
            sig_b = '*' if base_r.get('ic_significant') else ' '
            sig_a = '*' if adj_r.get('ic_significant') else ' '
            sp_b = f"{base_r['spread_annual_pct']:>7.1f}" if base_r.get('spread_annual_pct') is not None else '    N/A'
            sh_b = f"{base_r['ls_sharpe']:>7.3f}" if base_r.get('ls_sharpe') is not None else '    N/A'
            sp_a = f"{adj_r['spread_annual_pct']:>7.1f}" if adj_r.get('spread_annual_pct') is not None else '    N/A'
            sh_a = f"{adj_r['ls_sharpe']:>7.3f}" if adj_r.get('ls_sharpe') is not None else '    N/A'
            print(f"  US base     {horizon:>4s}: IC={base_r['ic']:+.4f}{sig_b} Spread={sp_b}% Sharpe={sh_b}")
            print(f"  US behavior {horizon:>4s}: IC={adj_r['ic']:+.4f}{sig_a} Spread={sp_a}% Sharpe={sh_a}  (dIC={ic_delta:+.4f})")

        # TW
        tw_col = f'taiex_fwd_{horizon}'
        if tw_col in targets.columns:
            base_r = evaluate_signal(tw_base, targets[tw_col], f'TW_base_{horizon}')
            adj_r = evaluate_signal(tw_adj_score, targets[tw_col], f'TW_behavioral_{horizon}')
            all_results[f'TW_base_{horizon}'] = base_r
            all_results[f'TW_behavioral_{horizon}'] = adj_r

            ic_delta = adj_r['ic'] - base_r['ic']
            sig_b = '*' if base_r.get('ic_significant') else ' '
            sig_a = '*' if adj_r.get('ic_significant') else ' '
            sp_b = f"{base_r['spread_annual_pct']:>7.1f}" if base_r.get('spread_annual_pct') is not None else '    N/A'
            sh_b = f"{base_r['ls_sharpe']:>7.3f}" if base_r.get('ls_sharpe') is not None else '    N/A'
            sp_a = f"{adj_r['spread_annual_pct']:>7.1f}" if adj_r.get('spread_annual_pct') is not None else '    N/A'
            sh_a = f"{adj_r['ls_sharpe']:>7.3f}" if adj_r.get('ls_sharpe') is not None else '    N/A'
            print(f"  TW base     {horizon:>4s}: IC={base_r['ic']:+.4f}{sig_b} Spread={sp_b}% Sharpe={sh_b}")
            print(f"  TW behavior {horizon:>4s}: IC={adj_r['ic']:+.4f}{sig_a} Spread={sp_a}% Sharpe={sh_a}  (dIC={ic_delta:+.4f})")

    # ── Robustness by period ──
    print("\n[3.5/4] Robustness by period (20d horizon)...")
    for name, base, adj, target_col in [
        ('US', us_base, us_adj_score, 'spy_fwd_20d'),
        ('TW', tw_base, tw_adj_score, 'taiex_fwd_20d'),
    ]:
        if target_col in targets.columns:
            rob_base = evaluate_robustness(base, targets[target_col])
            rob_adj = evaluate_robustness(adj, targets[target_col])
            all_results[f'{name}_robustness_base'] = rob_base
            all_results[f'{name}_robustness_behavioral'] = rob_adj

            print(f"\n  {name} (20d):")
            for period in rob_base:
                b_ic = rob_base[period]['ic']
                a_ic = rob_adj.get(period, {}).get('ic', 0)
                b_sig = '*' if rob_base[period]['significant'] else ' '
                a_sig = '*' if rob_adj.get(period, {}).get('significant', False) else ' '
                delta = a_ic - b_ic
                print(f"    {period}: base={b_ic:+.4f}{b_sig}  behavioral={a_ic:+.4f}{a_sig}  delta={delta:+.4f}")

    # ── Sanity check: key dates ──
    print("\n[3.6/4] Sanity check — behavioral adjustment at key events...")
    events = {
        '2020-03-23': 'COVID bottom',
        '2021-11-19': 'Meme/crypto peak',
        '2022-06-16': 'Bear market low',
        '2024-07-10': 'TSMC 1000 era',
    }
    for date, desc in events.items():
        try:
            idx = us_base.index.get_indexer([date], method='nearest')[0]
            d = us_base.index[idx]
            print(f"\n  {date} — {desc}:")
            print(f"    US: base={us_base.iloc[idx]:.1f} → adjusted={us_adj_score.iloc[idx]:.1f} (delta={us_adj_score.iloc[idx]-us_base.iloc[idx]:+.1f})")
            print(f"    TW: base={tw_base.iloc[idx]:.1f} → adjusted={tw_adj_score.iloc[idx]:.1f} (delta={tw_adj_score.iloc[idx]-tw_base.iloc[idx]:+.1f})")
            # Show adjustment breakdown
            print(f"    US adjustments: {', '.join(f'{c}={us_adj_details[c].iloc[idx]:+.1f}' for c in us_adj_details.columns)}")
            print(f"    TW adjustments: {', '.join(f'{c}={tw_adj_details[c].iloc[idx]:+.1f}' for c in tw_adj_details.columns)}")
        except Exception:
            pass

    # ── Save adjusted scores ──
    scores_df = pd.DataFrame({
        'us_base': us_base,
        'us_behavioral': us_adj_score,
        'us_adj_total': us_adj_score - us_base,
        'tw_base': tw_base,
        'tw_behavioral': tw_adj_score,
        'tw_adj_total': tw_adj_score - tw_base,
        'divergence_base': (us_base - tw_base).abs(),
        'divergence_behavioral': (us_adj_score - tw_adj_score).abs(),
    })
    scores_path = os.path.join(DATA_DIR, 'phase2_scores.csv')
    scores_df.to_csv(scores_path, encoding='utf-8')
    print(f"\nScores saved to: {scores_path}")

    # Save results
    results_path = os.path.join(DATA_DIR, 'phase2_results.json')
    with open(results_path, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False, default=str)
    print(f"Results saved to: {results_path}")

    # ── LLM Agent Demo (real-time, optional) ──
    print("\n[4/4] LLM Cultural Agent Demo (real-time)...")

    snapshot_path = os.path.join(os.path.dirname(__file__), 'snapshot_20260316.json')
    if os.path.exists(snapshot_path):
        with open(snapshot_path, 'r', encoding='utf-8') as f:
            snapshot = json.load(f)

        # Get latest base scores
        latest_us = us_base.iloc[-1]
        latest_tw = tw_base.iloc[-1]
        latest_us_comp = {c: us_comp[c].iloc[-1] for c in us_comp.columns}
        latest_tw_comp = {c: tw_comp[c].iloc[-1] for c in tw_comp.columns}

        # Build context for agents
        us_ctx = build_agent_context('US', latest_us, latest_us_comp, snapshot)
        tw_ctx = build_agent_context('TW', latest_tw, latest_tw_comp, snapshot,
                                     margin_data=snapshot.get('tw_retail_indicators'))

        print(f"\n  US base score: {latest_us:.1f}")
        print(f"  TW base score: {latest_tw:.1f}")

        # Run US agent
        print("\n  Running US cultural agent...")
        us_result = run_llm_agent('US', US_AGENT_SYSTEM, us_ctx)
        if us_result:
            try:
                delta = float(us_result.get('adjusted_score_delta', 0))
            except (ValueError, TypeError):
                delta = 0
            final = np.clip(latest_us + delta, 0, 100)
            print(f"  US Agent: {us_result.get('sentiment_level','?')} ({us_result.get('conviction','?')})")
            print(f"  Adjustment: {delta:+.0f} -> Final: {final:.1f}")
            print(f"  Narrative: {us_result.get('narrative','')}")
            print(f"  What quant misses: {us_result.get('what_quant_misses','')}")

        # Run TW agent
        print("\n  Running TW cultural agent...")
        tw_result = run_llm_agent('TW', TW_AGENT_SYSTEM, tw_ctx)
        if tw_result:
            try:
                delta = float(tw_result.get('adjusted_score_delta', 0))
            except (ValueError, TypeError):
                delta = 0
            final = np.clip(latest_tw + delta, 0, 100)
            print(f"  TW Agent: {tw_result.get('sentiment_level','?')} ({tw_result.get('conviction','?')})")
            print(f"  Adjustment: {delta:+.0f} -> Final: {final:.1f}")
            print(f"  Narrative: {tw_result.get('narrative','')}")
            print(f"  What quant misses: {tw_result.get('what_quant_misses','')}")

        # Save agent results
        agent_results = {
            'date': snapshot.get('date'),
            'us_base_score': round(latest_us, 1),
            'tw_base_score': round(latest_tw, 1),
            'us_agent': us_result,
            'tw_agent': tw_result,
        }
        agent_path = os.path.join(DATA_DIR, 'phase2_agent_results.json')
        with open(agent_path, 'w', encoding='utf-8') as f:
            json.dump(agent_results, f, indent=2, ensure_ascii=False)
        print(f"\n  Agent results saved to: {agent_path}")
    else:
        print("  No snapshot file found — skipping LLM demo")

    # ── Gate 2 Verdict ──
    print(f"\n{'=' * 60}")
    print("GATE 2 VERDICT")
    print(f"{'=' * 60}")

    # Check if behavioral adjustment improves IC
    improvements = 0
    degradations = 0
    for horizon in ['5d', '10d', '20d', '60d']:
        for mkt in ['US', 'TW']:
            base_key = f'{mkt}_base_{horizon}'
            adj_key = f'{mkt}_behavioral_{horizon}'
            if base_key in all_results and adj_key in all_results:
                b_ic = abs(all_results[base_key]['ic'])
                a_ic = abs(all_results[adj_key]['ic'])
                if a_ic > b_ic:
                    improvements += 1
                else:
                    degradations += 1

    print(f"  IC improvements: {improvements}/{improvements + degradations}")
    print(f"  IC degradations: {degradations}/{improvements + degradations}")

    if improvements > degradations:
        print(f"\n  PASS: Behavioral adjustments improve IC in majority of cases.")
        print(f"  -> Cultural agents add value beyond quantitative scoring.")
    elif improvements == degradations:
        print(f"\n  MARGINAL: Mixed results — behavioral adjustments don't consistently help.")
        print(f"  -> Review parameter calibration; agents may need regime-specific tuning.")
    else:
        print(f"\n  FAIL: Behavioral adjustments degrade IC in majority of cases.")
        print(f"  -> Parameters may be overfit or culturally miscalibrated.")

    return all_results


if __name__ == '__main__':
    main()
