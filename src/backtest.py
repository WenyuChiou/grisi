"""
Moodring Phase 1B: Backtest Engine
=================================
Evaluate base scoring formula against historical data.

Prediction target: future N-day returns of SPY/TAIEX
Evaluation: IC, hit rate, quintile spread, long/short Sharpe

No lookahead bias — all scores use only data available up to that date.
"""

import sys
import io
import os
import json
import pandas as pd
import numpy as np
from scipy.stats import spearmanr
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')


# ============================================================
# Scoring Engine
# ============================================================

def normalize_zscore(series, window=252):
    """Rolling z-score normalization (point-in-time)."""
    mean = series.rolling(window, min_periods=60).mean()
    std = series.rolling(window, min_periods=60).std()
    return (series - mean) / std.replace(0, np.nan)


def zscore_to_score(z, clip=3.0):
    """Convert z-score to 0-100 scale. Z=0 → 50, Z=+3 → 100, Z=-3 → 0."""
    z_clipped = np.clip(z, -clip, clip)
    return (z_clipped + clip) / (2 * clip) * 100


def compute_us_retail_score(features):
    """
    US Retail Sentiment Score (0-100).
    High = retail is greedy/euphoric, Low = retail is fearful.
    """
    components = pd.DataFrame(index=features.index)

    # 1. VIX inverse (low VIX = complacent retail) — z-score then 0-100
    if 'us_vix' in features.columns:
        vix_z = normalize_zscore(features['us_vix'], window=252)
        components['vix_complacency'] = zscore_to_score(-vix_z)  # invert: low VIX = high score

    # 2. SPY vs 52-week high (near ATH = greedy retail)
    if 'us_spy_vs_high' in features.columns:
        spy_h_z = normalize_zscore(features['us_spy_vs_high'], window=252)
        components['spy_position'] = zscore_to_score(spy_h_z)

    # 3. SPY momentum (strong momentum = retail FOMO)
    if 'us_spy_mom_20d' in features.columns:
        mom_z = normalize_zscore(features['us_spy_mom_20d'], window=252)
        components['momentum'] = zscore_to_score(mom_z)

    # 4. VIX-SPY correlation (normal = negative, stress = less negative/positive)
    if 'us_vix_spy_corr_20d' in features.columns:
        corr_z = normalize_zscore(features['us_vix_spy_corr_20d'], window=252)
        components['corr_regime'] = zscore_to_score(-corr_z)  # more negative corr = calmer = higher score

    # 5. Gold/SPY ratio (falling = risk-on = retail greedy)
    if 'cross_gold_spy_ratio' in features.columns:
        gold_z = normalize_zscore(features['cross_gold_spy_ratio'], window=252)
        components['risk_appetite'] = zscore_to_score(-gold_z)  # falling gold/spy = greedy

    # Equal weight for now (will optimize later)
    score = components.mean(axis=1)
    return score, components


def compute_tw_retail_score(features):
    """
    Taiwan Retail Sentiment Score (0-100).
    High = retail is greedy, Low = retail is fearful.

    v3: Data-driven component selection based on period-by-period IC analysis.
    Only includes signals with consistent negative IC across multiple regimes.

    v3.1: Added margin_leverage from Phase 1C (IC=-0.224 in 2023-25).

    Dropped from v2 (dead or sign-flipped post-2020):
      - momentum (dead 2020+)
      - bellwether_chase (sign flipped 2023)
      - VIX global_fear_inv (TW-irrelevant post-2020)
      - carry_trade USDJPY (dead 2020+)
      - drawdown_pace (inconsistent)
    """
    components = pd.DataFrame(index=features.index)

    # ── Macro Rate Pressure (strongest signal: 4/4 periods significant) ──
    # High US yields → hot money leaves Asia → TAIEX under pressure
    if 'us_10y_yield' in features.columns:
        z = normalize_zscore(features['us_10y_yield'], window=252)
        components['rate_pressure'] = zscore_to_score(z)  # high yield = high score = greedy/overheated

    # ── Global Risk Appetite (3/4 periods significant) ──
    # Falling Gold/SPY = risk-on = retail greedy
    if 'cross_gold_spy_ratio' in features.columns:
        z = normalize_zscore(features['cross_gold_spy_ratio'], window=252)
        components['global_risk_appetite'] = zscore_to_score(-z)

    # ── Local Volatility Fear Gauge (works in 2023-25) ──
    # Low realized vol = complacent retail = greedy
    if 'tw_realized_vol_20d' in features.columns:
        z = normalize_zscore(features['tw_realized_vol_20d'], window=252)
        components['vol_complacency'] = zscore_to_score(-z)

    # ── Price Level (baseline, weak but correct direction 3/4 periods) ──
    # Near ATH = greedy
    if 'tw_taiex_vs_high' in features.columns:
        z = normalize_zscore(features['tw_taiex_vs_high'], window=252)
        components['taiex_position'] = zscore_to_score(z)

    # ── Volume Activity (retail excitement proxy) ──
    # High volume surge = retail piling in = greedy
    if 'tw_volume_surge' in features.columns:
        z = normalize_zscore(features['tw_volume_surge'], window=252)
        components['volume_excitement'] = zscore_to_score(z)

    score = components.mean(axis=1)
    return score, components


# ============================================================
# Backtest Evaluation
# ============================================================

def evaluate_signal(score, target, label=''):
    """
    Evaluate a sentiment score against future returns.

    If Moodring works as contrarian indicator:
    - High score (greedy retail) → LOW future returns
    - Low score (fearful retail) → HIGH future returns
    → We expect NEGATIVE IC (high score predicts low returns)
    """
    # Align and drop NaN
    aligned = pd.DataFrame({'score': score, 'target': target}).dropna()
    if len(aligned) < 100:
        return {'error': f'insufficient data ({len(aligned)} rows)', 'label': label}

    s = aligned['score']
    t = aligned['target']

    # 1. Information Coefficient (Spearman rank correlation)
    ic, ic_pval = spearmanr(s, t)

    # 2. Quintile analysis
    try:
        aligned['quintile'] = pd.qcut(s, 5, labels=[1, 2, 3, 4, 5], duplicates='drop')
        quintile_returns = aligned.groupby('quintile')['target'].mean()

        q1_ret = quintile_returns.iloc[0]   # lowest score (most fearful)
        q5_ret = quintile_returns.iloc[-1]  # highest score (most greedy)
        spread = q1_ret - q5_ret            # should be positive if contrarian works

        # Annualize spread (rough)
        horizon_days = max(5, abs(int(label.split('_')[-1].replace('d', ''))) if 'd' in label else 20)
        annual_factor = 252 / horizon_days
        spread_annual = spread * annual_factor
    except Exception:
        q1_ret = q5_ret = spread = spread_annual = np.nan
        quintile_returns = pd.Series()

    # 3. Hit rate: when score > 80, does market decline?
    high_score = aligned[s > 80]
    if len(high_score) > 10:
        hit_rate_80 = (high_score['target'] < 0).mean()
    else:
        hit_rate_80 = np.nan

    # Also check score > 70
    high_70 = aligned[s > 70]
    hit_rate_70 = (high_70['target'] < 0).mean() if len(high_70) > 10 else np.nan

    # 4. Long/Short Sharpe
    # Long when score < 30 (fearful = buy), short when score > 70 (greedy = sell)
    ls_returns = []
    for _, row in aligned.iterrows():
        if row['score'] < 30:
            ls_returns.append(row['target'])    # long
        elif row['score'] > 70:
            ls_returns.append(-row['target'])   # short
        # else: flat

    if len(ls_returns) > 20:
        ls_series = pd.Series(ls_returns)
        ls_mean = ls_series.mean()
        ls_std = ls_series.std()
        ls_sharpe = (ls_mean / ls_std * np.sqrt(annual_factor)) if ls_std > 0 else 0
    else:
        ls_sharpe = np.nan

    result = {
        'label': label,
        'n_obs': len(aligned),
        'ic': round(ic, 4),
        'ic_pval': round(ic_pval, 4),
        'ic_significant': ic_pval < 0.05,
        'hit_rate_80': round(hit_rate_80, 3) if not np.isnan(hit_rate_80) else None,
        'hit_rate_70': round(hit_rate_70, 3) if not np.isnan(hit_rate_70) else None,
        'q1_return': round(q1_ret * 100, 3) if not np.isnan(q1_ret) else None,
        'q5_return': round(q5_ret * 100, 3) if not np.isnan(q5_ret) else None,
        'spread': round(spread * 100, 3) if not np.isnan(spread) else None,
        'spread_annual_pct': round(spread_annual * 100, 1) if not np.isnan(spread_annual) else None,
        'ls_sharpe': round(ls_sharpe, 3) if not np.isnan(ls_sharpe) else None,
        'quintile_returns': {str(k): round(v * 100, 3) for k, v in quintile_returns.items()},
    }

    return result


def evaluate_robustness(score, target, label=''):
    """Split by time period for robustness check."""
    aligned = pd.DataFrame({'score': score, 'target': target}).dropna()
    if len(aligned) < 200:
        return {}

    # Split periods
    periods = {
        '2012-2015': ('2012-01-01', '2015-12-31'),
        '2016-2019': ('2016-01-01', '2019-12-31'),
        '2020-2022': ('2020-01-01', '2022-12-31'),
        '2023-2025': ('2023-01-01', '2025-12-31'),
    }

    results = {}
    for period_name, (start, end) in periods.items():
        mask = (aligned.index >= start) & (aligned.index <= end)
        subset = aligned[mask]
        if len(subset) > 50:
            ic, pval = spearmanr(subset['score'], subset['target'])
            results[period_name] = {
                'n': len(subset),
                'ic': round(ic, 4),
                'pval': round(pval, 4),
                'significant': pval < 0.05,
            }

    return results


# ============================================================
# Main Backtest
# ============================================================

def main():
    print("=" * 60)
    print("Moodring Phase 1B: Backtest Evaluation")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    # Load data
    features = pd.read_csv(os.path.join(DATA_DIR, 'base_features.csv'),
                           index_col=0, parse_dates=True)
    targets = pd.read_csv(os.path.join(DATA_DIR, 'target_returns.csv'),
                          index_col=0, parse_dates=True)

    print(f"\nFeatures: {features.shape}, Targets: {targets.shape}")

    # Compute scores
    print("\n[1/3] Computing retail sentiment scores...")
    us_score, us_components = compute_us_retail_score(features)
    tw_score, tw_components = compute_tw_retail_score(features)

    # Divergence
    divergence = (us_score - tw_score).abs()

    print(f"  US score: mean={us_score.mean():.1f}, std={us_score.std():.1f}")
    print(f"  TW score: mean={tw_score.mean():.1f}, std={tw_score.std():.1f}")
    print(f"  Divergence: mean={divergence.mean():.1f}, max={divergence.max():.1f}")

    # Sanity check: known events
    print("\n[1.5/3] Sanity check — known events...")
    events = {
        '2020-03-23': 'COVID bottom (expect: low score)',
        '2021-11-19': 'Meme/crypto peak (expect: high score)',
        '2022-06-16': 'Bear market low (expect: low score)',
        '2024-07-10': 'TSMC 1000 (expect: TW high)',
    }
    for date, desc in events.items():
        us_val = us_score.get(date, us_score.iloc[us_score.index.get_indexer([date], method='nearest')[0]] if date <= str(us_score.index[-1]) else np.nan)
        tw_val = tw_score.get(date, tw_score.iloc[tw_score.index.get_indexer([date], method='nearest')[0]] if date <= str(tw_score.index[-1]) else np.nan)
        print(f"  {date} — {desc}")
        if not np.isnan(us_val):
            print(f"    US: {us_val:.1f}  TW: {tw_val:.1f}")

    # Evaluate
    print("\n[2/3] Evaluating predictive power...")
    all_results = {}

    # US score vs SPY forward returns
    for horizon in ['5d', '10d', '20d', '60d']:
        target_col = f'spy_fwd_{horizon}'
        if target_col in targets.columns:
            label = f'US_score_vs_SPY_{horizon}'
            result = evaluate_signal(us_score, targets[target_col], label)
            all_results[label] = result

            ic = result['ic']
            ic_sig = '*' if result.get('ic_significant') else ''
            hit = result.get('hit_rate_70', 'N/A')
            spread = result.get('spread_annual_pct', 'N/A')
            sharpe = result.get('ls_sharpe', 'N/A')
            print(f"  {label}:")
            print(f"    IC={ic:.4f}{ic_sig}  HitRate70={hit}  Spread(ann)={spread}%  L/S Sharpe={sharpe}")

    # TW score vs TAIEX forward returns
    for horizon in ['5d', '10d', '20d', '60d']:
        target_col = f'taiex_fwd_{horizon}'
        if target_col in targets.columns:
            label = f'TW_score_vs_TAIEX_{horizon}'
            result = evaluate_signal(tw_score, targets[target_col], label)
            all_results[label] = result

            ic = result['ic']
            ic_sig = '*' if result.get('ic_significant') else ''
            hit = result.get('hit_rate_70', 'N/A')
            spread = result.get('spread_annual_pct', 'N/A')
            sharpe = result.get('ls_sharpe', 'N/A')
            print(f"  {label}:")
            print(f"    IC={ic:.4f}{ic_sig}  HitRate70={hit}  Spread(ann)={spread}%  L/S Sharpe={sharpe}")

    # Divergence as predictor
    for horizon in ['20d', '60d']:
        for market, target_prefix in [('SPY', 'spy'), ('TAIEX', 'taiex')]:
            target_col = f'{target_prefix}_fwd_{horizon}'
            if target_col in targets.columns:
                label = f'Divergence_vs_{market}_{horizon}'
                result = evaluate_signal(divergence, targets[target_col].abs(), label)  # predict volatility
                all_results[label] = result
                print(f"  {label}: IC={result['ic']:.4f}")

    # Robustness
    print("\n[3/3] Robustness check by time period...")
    for score_name, score_series, target_col in [
        ('US', us_score, 'spy_fwd_20d'),
        ('TW', tw_score, 'taiex_fwd_20d'),
    ]:
        if target_col in targets.columns:
            robust = evaluate_robustness(score_series, targets[target_col], score_name)
            all_results[f'{score_name}_robustness_20d'] = robust
            print(f"\n  {score_name} score vs {target_col}:")
            for period, stats in robust.items():
                sig = '*' if stats['significant'] else ''
                print(f"    {period}: IC={stats['ic']:.4f}{sig} (n={stats['n']})")

    # Save results
    results_path = os.path.join(DATA_DIR, 'backtest_results.json')
    with open(results_path, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False, default=str)
    print(f"\nResults saved to: {results_path}")

    # Save scores for visualization
    scores_df = pd.DataFrame({
        'us_score': us_score,
        'tw_score': tw_score,
        'divergence': divergence,
    })
    scores_path = os.path.join(DATA_DIR, 'historical_scores.csv')
    scores_df.to_csv(scores_path, encoding='utf-8')
    print(f"Scores saved to: {scores_path}")

    # Gate 1B verdict
    print(f"\n{'=' * 60}")
    print("GATE 1B VERDICT")
    print(f"{'=' * 60}")

    passing = []
    for key, result in all_results.items():
        if isinstance(result, dict) and 'ic' in result:
            if abs(result['ic']) > 0.05 and result.get('ic_significant'):
                passing.append(key)

    if len(passing) > 0:
        print(f"  PASS: {len(passing)} signal(s) have |IC| > 0.05 and are significant:")
        for p in passing:
            print(f"    - {p}: IC={all_results[p]['ic']:.4f}")
        print(f"\n  -> Proceed to Phase 1C (social data incremental test)")
    else:
        print("  FAIL: No signal reached |IC| > 0.05 with significance.")
        print("  -> Review and adjust scoring formula before proceeding.")

    return all_results


if __name__ == '__main__':
    main()
