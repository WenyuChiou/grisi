"""
MoodRing Auto-Recalibration System
====================================
Monitors prediction accuracy (IC, hit rate) over rolling windows and
automatically recalibrates signal weights when performance degrades.

Recalibration strategy
----------------------
The composite score is built from three sub-signals:
  1. RSI-14  (0-100)
  2. vs_52w_high  mapped via  (vs_high - floor) * (100 / h_range)
  3. 20d momentum mapped via  (mom + m_range/2) * (100 / m_range)

When IC decays below the "poor" threshold (|IC| < 0.05), this module:
  1. Fetches 2 years of price history per market (yfinance)
  2. Recomputes the three sub-signals historically
  3. Grid-searches weight combinations and mapping ranges to maximise |IC|
  4. Stores the best parameters in  data/calibration_params.json
  5. Logs the event to             data/recalibration_log.json

The next run of daily_update.py reads calibration_params.json and
applies the updated weights automatically via compute_score().

Usage
-----
  python recalibrate.py                      # auto-recalibrate all poor markets
  python recalibrate.py --markets us tw      # specific markets
  python recalibrate.py --force              # recalibrate even if healthy
  python recalibrate.py --status             # print current calibration status
"""

import sys
import os
import json
import math
from datetime import datetime, timedelta

os.environ['PYTHONUTF8'] = '1'

from utils import rsi as _rsi_series, safe_clamp  # noqa: E402

DATA_DIR        = os.path.join(os.path.dirname(__file__), '..', 'data')
RECAL_LOG_PATH  = os.path.join(DATA_DIR, 'recalibration_log.json')
PARAMS_PATH     = os.path.join(DATA_DIR, 'calibration_params.json')

# IC health thresholds (absolute value).
# SE for Spearman IC with n=60 is ~1/sqrt(57) ≈ 0.132, so thresholds below that are
# statistically meaningless.  Use 0.10 / 0.15 as actionable floors.
IC_POOR_THRESH  = 0.10   # below this → poor
IC_WARN_THRESH  = 0.15   # below this → warning

# Walk-forward OOS split: first TRAIN_FRAC of rows are training data
TRAIN_FRAC      = 0.75   # ≈ 18 months train / 6 months validation out of 2 years

# Minimum aligned pairs required for optimisation
MIN_PAIRS = 40

# Default signal parameters (match the hardcoded values in daily_update.py)
DEFAULT_PARAMS = {
    "rsi_weight":        1 / 3,
    "vs_high_weight":    1 / 3,
    "momentum_weight":   1 / 3,
    "vs_high_floor":     80.0,   # lower bound of vs_52w_high range
    "vs_high_range":     20.0,   # width of the vs_52w_high mapping (80-100 = 20)
    "momentum_range":    20.0,   # full range of momentum (±10 % → range = 20)
}

TICKER_MAP = {
    'us': 'SPY',
    'tw': '^TWII',
    'jp': '^N225',
    'kr': '^KS11',
    'eu': '^STOXX50E',
}


# ── helpers ───────────────────────────────────────────────────────────────────

def _load(path, default):
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return default


def _save(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _spearman(x, y):
    from scipy.stats import spearmanr
    if len(x) < 10:
        return None
    ic, _ = spearmanr(x, y)
    return None if math.isnan(ic) else round(float(ic), 4)


def _compute_rsi14(prices):
    """SMA-based RSI-14 matching daily_update.py's pandas rolling-mean computation.

    Delegates to the shared utils.rsi() (aliased _rsi_series) so this stays
    byte-for-byte identical to what compute_score() sees in the live pipeline;
    only wraps it to return a plain list of rounded values (or None) instead
    of a pandas Series.
    """
    import pandas as pd
    s = pd.Series(prices, dtype=float)
    rsi_vals = _rsi_series(s, period=14)
    return [None if pd.isna(v) else round(float(v), 2) for v in rsi_vals]


def _fetch_prices(ticker, start, end):
    """Download adjusted close prices from yfinance. Returns (dates, prices)."""
    import yfinance as yf
    import pandas as pd
    try:
        raw = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
        if raw is None or raw.empty:
            return [], []
        close = raw['Close']
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        close = close.dropna()
        if hasattr(close.index, 'tz') and close.index.tz is not None:
            close.index = close.index.tz_localize(None)
        dates  = [d.strftime('%Y-%m-%d') for d in close.index]
        prices = [float(v) for v in close.values]
        return dates, prices
    except Exception as exc:
        print(f"[RECAL] yfinance error for {ticker}: {exc}")
        return [], []


def _score_with_params(rsi, vs_high, mom, p):
    """Compute composite score from sub-signals using parameter dict p.

    Returns None (instead of a silently-clamped 100.0) if any sub-signal
    is NaN. Callers must filter out None scores before feeding them to
    _spearman / IC calculations.
    """
    w_rsi  = p.get('rsi_weight',      1 / 3)
    w_high = p.get('vs_high_weight',  1 / 3)
    w_mom  = p.get('momentum_weight', 1 / 3)
    floor  = p.get('vs_high_floor',   80.0)
    h_rng  = p.get('vs_high_range',   20.0)
    m_rng  = p.get('momentum_range',  20.0)

    try:
        rsi_s  = safe_clamp(rsi, label="rsi")
        high_s = safe_clamp((vs_high - floor) * (100.0 / max(h_rng, 1)), label="vs_high")
        mom_s  = safe_clamp((mom + m_rng / 2)  * (100.0 / max(m_rng, 1)), label="momentum")
    except ValueError as exc:
        print(f"[RECAL] WARNING: {exc} — dropping this data point")
        return None

    total_w = w_rsi + w_high + w_mom
    if total_w == 0:
        return 50.0
    return round((w_rsi * rsi_s + w_high * high_s + w_mom * mom_s) / total_w, 1)


def _filter_paired(scores, fwd):
    """Drop (score, fwd) pairs where score is None (NaN sub-signal)."""
    pairs = [(s, f) for s, f in zip(scores, fwd) if s is not None]
    if not pairs:
        return [], []
    filtered_scores, filtered_fwd = zip(*pairs)
    return list(filtered_scores), list(filtered_fwd)


# ── historical sub-signal builder ─────────────────────────────────────────────

def _build_history(market, lookback_years=2):
    """
    Fetch price history and compute daily RSI-14, vs_52w_high, 20d-momentum,
    and forward 20d return for each day.

    Returns a list of dicts: {date, rsi, vs_high, mom, fwd_20d}
    """
    ticker = TICKER_MAP.get(market)
    if not ticker:
        return []

    end_dt   = datetime.now()
    # Extra buffer: 252 warmup + 30 padding
    start_dt = end_dt - timedelta(days=lookback_years * 365 + 282)

    print(f"[RECAL] {market.upper()}: downloading {ticker} price history ({start_dt.date()} → {end_dt.date()})...")
    dates, prices = _fetch_prices(ticker, start_dt.strftime('%Y-%m-%d'), end_dt.strftime('%Y-%m-%d'))

    if len(prices) < 300:
        print(f"[RECAL] {market.upper()}: insufficient price data ({len(prices)} bars)")
        return []

    rsi_vals = _compute_rsi14(prices)

    rows = []
    for i in range(252, len(prices) - 20):
        if rsi_vals[i] is None:
            continue
        p_now      = prices[i]
        p_fwd      = prices[i + 20]
        p_20d_ago  = prices[i - 20] if i >= 20 else p_now
        high_252d  = max(prices[i - 252:i + 1])

        vs_high = (p_now / high_252d * 100.0) if high_252d > 0 else 90.0
        mom     = (p_now / p_20d_ago - 1.0) * 100.0 if p_20d_ago > 0 else 0.0
        fwd_20d = (p_fwd / p_now - 1.0) * 100.0     if p_now > 0 else None

        if fwd_20d is None:
            continue
        rows.append({
            'date':    dates[i],
            'rsi':     rsi_vals[i],
            'vs_high': vs_high,
            'mom':     mom,
            'fwd_20d': fwd_20d,
        })

    print(f"[RECAL] {market.upper()}: {len(rows)} usable data points")
    return rows


# ── parameter grid search ─────────────────────────────────────────────────────

def _grid_search(rows):
    """
    Walk-forward OOS grid search.

    Splits rows into train (first TRAIN_FRAC) and validation (remaining).
    Grid-searches on train only to avoid in-sample bias.  Evaluates the winner
    on the held-out validation set to produce an honest OOS IC estimate.

    Returns (best_params_dict, ic_insample, ic_oos) or (None, None, None) if
    insufficient data.  ic_insample is the best IC on the training split;
    ic_oos is that same parameter set evaluated on the validation split.
    """
    if len(rows) < MIN_PAIRS:
        return None, None, None

    split = max(MIN_PAIRS, int(len(rows) * TRAIN_FRAC))
    train_rows = rows[:split]
    val_rows   = rows[split:]

    if len(train_rows) < MIN_PAIRS:
        return None, None, None

    fwd_train = [r['fwd_20d'] for r in train_rows]

    # Weight triplets that sum to 1.0
    weight_combos = [
        (1/3,  1/3,  1/3),
        (0.50, 0.25, 0.25),
        (0.25, 0.50, 0.25),
        (0.25, 0.25, 0.50),
        (0.60, 0.20, 0.20),
        (0.20, 0.60, 0.20),
        (0.20, 0.20, 0.60),
        (0.40, 0.40, 0.20),
        (0.40, 0.20, 0.40),
        (0.20, 0.40, 0.40),
    ]

    floor_opts   = [70.0, 75.0, 80.0, 85.0]
    h_range_opts = [15.0, 20.0, 25.0, 30.0]
    m_range_opts = [10.0, 15.0, 20.0, 30.0]   # full range: ±5 %, ±7.5 %, ±10 %, ±15 %

    best_ic_train = None
    best_params   = dict(DEFAULT_PARAMS)

    for w_rsi, w_high, w_mom in weight_combos:
        for floor in floor_opts:
            for h_rng in h_range_opts:
                for m_rng in m_range_opts:
                    p = {
                        'rsi_weight':      w_rsi,
                        'vs_high_weight':  w_high,
                        'momentum_weight': w_mom,
                        'vs_high_floor':   floor,
                        'vs_high_range':   h_rng,
                        'momentum_range':  m_rng,
                    }
                    scores_train = [_score_with_params(r['rsi'], r['vs_high'], r['mom'], p)
                                    for r in train_rows]
                    scores_train_f, fwd_train_f = _filter_paired(scores_train, fwd_train)
                    ic_train = _spearman(scores_train_f, fwd_train_f)
                    if ic_train is not None and (best_ic_train is None or abs(ic_train) > abs(best_ic_train)):
                        best_ic_train = ic_train
                        best_params   = dict(p)

    if best_params is None or best_ic_train is None:
        return None, None, None

    # OOS evaluation on validation rows
    ic_oos = None
    if len(val_rows) >= MIN_PAIRS:
        fwd_val    = [r['fwd_20d'] for r in val_rows]
        scores_val = [_score_with_params(r['rsi'], r['vs_high'], r['mom'], best_params)
                      for r in val_rows]
        scores_val_f, fwd_val_f = _filter_paired(scores_val, fwd_val)
        ic_oos = _spearman(scores_val_f, fwd_val_f)
        print(f"[RECAL] OOS validation: train IC={best_ic_train}, val IC={ic_oos} "
              f"(train={len(train_rows)} rows, val={len(val_rows)} rows)")
    else:
        print(f"[RECAL] Skipping OOS eval — val set too small ({len(val_rows)} < {MIN_PAIRS})")

    return best_params, best_ic_train, ic_oos


# ── per-market recalibration ───────────────────────────────────────────────────

def recalibrate_market(market, current_ic, current_params, force=False):
    """
    Run recalibration for a single market.

    Args:
        market:        market code ('us', 'tw', 'jp', 'kr', 'eu')
        current_ic:    most-recent IC from self_improve.json
        current_params: existing calibration params dict for this market
        force:         if True, apply even with marginal improvement

    Returns:
        new_params dict if improvement found, else None.
    """
    rows = _build_history(market, lookback_years=2)
    if not rows:
        return None

    split = max(MIN_PAIRS, int(len(rows) * TRAIN_FRAC))
    train_rows = rows[:split]
    val_rows   = rows[split:]

    # Current-params baseline — measure on same splits for apples-to-apples OOS comparison
    merged_params = {**DEFAULT_PARAMS, **current_params}
    cur_scores_val = [_score_with_params(r['rsi'], r['vs_high'], r['mom'], merged_params)
                      for r in val_rows]
    fwd_val        = [r['fwd_20d'] for r in val_rows]
    cur_scores_val_f, fwd_val_f = _filter_paired(cur_scores_val, fwd_val)
    baseline_oos_ic = _spearman(cur_scores_val_f, fwd_val_f) if len(val_rows) >= MIN_PAIRS else None

    # In-sample IC with current params (for logging)
    cur_scores_all = [_score_with_params(r['rsi'], r['vs_high'], r['mom'], merged_params)
                      for r in rows]
    cur_scores_all_f, fwd_all_f = _filter_paired(cur_scores_all, [r['fwd_20d'] for r in rows])
    ic_now = _spearman(cur_scores_all_f, fwd_all_f)

    # Walk-forward grid search (trains on first TRAIN_FRAC, evaluates on remainder)
    best_params, best_ic_insample, best_ic_oos = _grid_search(rows)

    print(f"[RECAL] {market.upper()}: current IC={ic_now} (baseline OOS={baseline_oos_ic}), "
          f"best in-sample IC={best_ic_insample}, best OOS IC={best_ic_oos}")

    if best_params is None:
        print(f"[RECAL] {market.upper()}: grid search failed (insufficient data)")
        return None

    # Contrarian indicator: IC should be negative (high score predicts lower
    # forward returns). A positive OOS IC means the candidate weights are
    # trend-following, not contrarian — reject outright regardless of
    # magnitude, since |IC| improving is meaningless if the sign is wrong.
    if best_ic_oos is not None and best_ic_oos > 0:
        print(f"[RECAL] {market.upper()}: WARNING — OOS IC is positive ({best_ic_oos:.4f}) "
              f"— trend-following, not contrarian. Rejecting recalibration.")
        return None

    # Sign-flip guard: in-sample and OOS IC must agree in sign. Disagreement
    # means the grid search overfit to in-sample noise rather than finding a
    # genuine contrarian signal that generalizes out-of-sample.
    if best_ic_oos is not None and best_ic_insample is not None:
        if (best_ic_oos > 0) != (best_ic_insample > 0):
            print(f"[RECAL] {market.upper()}: WARNING — in-sample IC ({best_ic_insample:.4f}) "
                  f"and OOS IC ({best_ic_oos:.4f}) disagree in sign. Rejecting recalibration.")
            return None

    # Accept new params ONLY if OOS IC improves over baseline OOS IC.
    # Fall back to in-sample comparison if val set was too small for OOS eval.
    if best_ic_oos is not None and baseline_oos_ic is not None:
        improvement = abs(best_ic_oos) > abs(baseline_oos_ic)
        comparison_label = f"OOS {best_ic_oos} vs baseline OOS {baseline_oos_ic}"
    else:
        # OOS not available — fall back to in-sample comparison, but still
        # enforce the contrarian sign requirement before accepting.
        if best_ic_insample is not None and best_ic_insample > 0:
            print(f"[RECAL] {market.upper()}: WARNING — in-sample IC is positive "
                  f"({best_ic_insample:.4f}) — trend-following, not contrarian. Rejecting.")
            return None
        # Fallback: require 10% relative improvement in-sample
        improvement = best_ic_insample is not None and abs(best_ic_insample) > abs(ic_now or 0) * 1.10
        comparison_label = f"in-sample {best_ic_insample} vs {ic_now} (OOS not available)"

    if not force and not improvement:
        print(f"[RECAL] {market.upper()}: no OOS improvement ({comparison_label}) — no change")
        return None

    best_params['calibration_date']  = datetime.now().strftime('%Y-%m-%d')
    best_params['ic_at_calibration'] = ic_now
    best_params['ic_insample']       = best_ic_insample
    best_params['ic_oos']            = best_ic_oos
    # Keep legacy field for backward-compat with any consumers that still read it
    best_params['ic_estimated_post'] = best_ic_oos if best_ic_oos is not None else best_ic_insample
    best_params['reason'] = (
        f"Auto-recalibrated: IC {ic_now} → OOS {best_ic_oos} (in-sample {best_ic_insample}) | "
        f"weights rsi={best_params['rsi_weight']:.2f} "
        f"high={best_params['vs_high_weight']:.2f} "
        f"mom={best_params['momentum_weight']:.2f} | "
        f"floor={best_params['vs_high_floor']} "
        f"h_range={best_params['vs_high_range']} "
        f"m_range={best_params['momentum_range']}"
    )

    # Append to recalibration log
    log = _load(RECAL_LOG_PATH, [])
    log.append({
        "timestamp":         datetime.now().isoformat(),
        "date":              datetime.now().strftime('%Y-%m-%d'),
        "market":            market,
        "trigger":           "manual_force" if force else "auto_ic_decay",
        "ic_before":         ic_now,
        "ic_insample":       best_ic_insample,
        "ic_oos":            best_ic_oos,
        "ic_estimated_post": best_params['ic_estimated_post'],   # backward-compat
        "old_params":        current_params,
        "new_params":        best_params,
    })
    _save(RECAL_LOG_PATH, log)
    print(f"[RECAL] {market.upper()}: recalibration event logged")

    return best_params


# ── main entry point ───────────────────────────────────────────────────────────

def run_recalibration(markets=None, force=False):
    """
    Auto-recalibration entry point.

    For each target market with poor IC health, fetches price history,
    grid-searches for better signal parameters, and saves them to
    data/calibration_params.json.  The next daily_update.py run picks
    up the new weights automatically via compute_score().

    Args:
        markets: list of market codes or None (→ all five markets)
        force:   recalibrate regardless of current health status

    Returns:
        dict mapping market → updated params
    """
    si_path = os.path.join(DATA_DIR, 'self_improve.json')
    if not os.path.exists(si_path):
        print("[RECAL] data/self_improve.json not found. Run daily_update.py first.")
        return {}

    with open(si_path, 'r', encoding='utf-8') as f:
        si = json.load(f)

    all_params   = _load(PARAMS_PATH, {})
    targets      = markets or ['us', 'tw', 'jp', 'kr', 'eu']
    # Start from existing params so markets NOT in targets are preserved unchanged
    updated      = dict(all_params)
    recalibrated = []

    for market in targets:
        mkt_si    = si.get('markets', {}).get(market, {})
        health    = mkt_si.get('health', 'unknown')
        recent_ic = mkt_si.get('recent_ic_20d')

        needs_recal = (
            force or
            health == 'poor' or
            (health == 'warning' and recent_ic is not None and abs(recent_ic) < 0.03)
        )

        cur_mkt_params = all_params.get(market, {})

        if not needs_recal:
            print(f"[RECAL] {market.upper()}: health={health} IC={recent_ic} — healthy, skipping")
            updated[market] = cur_mkt_params
            continue

        print(f"\n[RECAL] {market.upper()}: health={health} IC={recent_ic} — triggering recalibration ...")
        new_params = recalibrate_market(market, recent_ic or 0.0, cur_mkt_params, force=force)

        if new_params:
            updated[market] = new_params
            recalibrated.append(market)
        else:
            cur_mkt_params['last_checked']    = datetime.now().strftime('%Y-%m-%d')
            cur_mkt_params['health_at_check'] = health
            updated[market] = cur_mkt_params

    _save(PARAMS_PATH, updated)
    print(f"\n[RECAL] calibration_params.json saved → {PARAMS_PATH}")

    # Patch self_improve.json with recalibration metadata
    if recalibrated:
        if 'system_health' not in si:
            si['system_health'] = {}
        si['system_health']['last_recalibration'] = {
            "date":     datetime.now().strftime('%Y-%m-%d'),
            "markets":  recalibrated,
            "trigger":  "manual" if force else "auto",
        }
        _save(si_path, si)

    # Sync updated files to docs/data/ for GitHub Pages
    import shutil
    docs_data = os.path.normpath(os.path.join(DATA_DIR, '..', 'docs', 'data'))
    for fname in ('calibration_params.json', 'recalibration_log.json', 'self_improve.json'):
        src = os.path.join(DATA_DIR, fname)
        dst = os.path.join(docs_data, fname)
        if os.path.exists(src):
            shutil.copy2(src, dst)
    print("[RECAL] docs/data/ synced")

    if recalibrated:
        print(f"\n[OK] Recalibrated: {', '.join(m.upper() for m in recalibrated)}")
        print("  Re-run daily_update.py to apply new weights to today's scores.")
    else:
        print("\n[OK] Done — no parameter changes (no improvement found or all markets healthy)")

    return updated


# ── status reporter ────────────────────────────────────────────────────────────

def get_calibration_status():
    """Return a JSON-serialisable dict summarising calibration state for all markets."""
    params = _load(PARAMS_PATH, {})
    log    = _load(RECAL_LOG_PATH, [])

    status = {
        "generated":           datetime.now().strftime('%Y-%m-%d %H:%M'),
        "total_recalibrations": len(log),
        "recent_events":       log[-3:] if log else [],
        "markets":             {},
    }

    for mkt in ['us', 'tw', 'jp', 'kr', 'eu']:
        p  = params.get(mkt, {})
        is_recal = (
            abs(p.get('rsi_weight',      1/3) - 1/3)  > 0.001 or
            abs(p.get('vs_high_weight',  1/3) - 1/3)  > 0.001 or
            abs(p.get('momentum_weight', 1/3) - 1/3)  > 0.001 or
            p.get('vs_high_floor',   80.0) != 80.0    or
            p.get('vs_high_range',   20.0) != 20.0    or
            p.get('momentum_range',  20.0) != 20.0
        )
        status["markets"][mkt] = {
            "is_recalibrated":     is_recal,
            "calibration_date":    p.get('calibration_date', 'never'),
            "ic_at_calibration":   p.get('ic_at_calibration'),
            "ic_insample":         p.get('ic_insample', p.get('ic_estimated_post')),
            "ic_oos":              p.get('ic_oos'),
            "ic_estimated_post":   p.get('ic_estimated_post'),   # backward-compat
            "active_weights": {
                "rsi":     round(p.get('rsi_weight',      1/3), 3),
                "vs_high": round(p.get('vs_high_weight',  1/3), 3),
                "momentum":round(p.get('momentum_weight', 1/3), 3),
            } if is_recal else "default (equal)",
        }

    return status


# ── apply helper (used by daily_update.py's compute_score) ────────────────────

def load_calibration_params():
    """Load calibration_params.json if it exists; returns {} otherwise."""
    return _load(PARAMS_PATH, {})


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description='MoodRing Auto-Recalibration — optimises signal weights to restore IC'
    )
    parser.add_argument(
        '--markets', nargs='+', choices=['us', 'tw', 'jp', 'kr', 'eu'],
        help='Markets to recalibrate (default: all poor-health markets)'
    )
    parser.add_argument(
        '--force', action='store_true',
        help='Recalibrate even if current health is OK'
    )
    parser.add_argument(
        '--status', action='store_true',
        help='Print current calibration status and exit'
    )

    args = parser.parse_args()

    if args.status:
        status = get_calibration_status()
        print(json.dumps(status, indent=2, ensure_ascii=False))
    else:
        run_recalibration(markets=args.markets, force=args.force)
