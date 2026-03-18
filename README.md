# Moodring — Market Mood Indicator

> **When retail investors are greedy, markets tend to underperform. When they're fearful, markets outperform.**

[![Dashboard](https://img.shields.io/badge/Live_Dashboard-EN-blue)](https://wenyuchiou.github.io/openclaw-workspace/proposals/market-agent-thermometer/docs/index.html)
[![Dashboard](https://img.shields.io/badge/Live_Dashboard-中文-orange)](https://wenyuchiou.github.io/openclaw-workspace/proposals/market-agent-thermometer/docs/tw.html)

## What is Moodring?

A quantitative contrarian sentiment index for US, Taiwan, Japan, Korea, and Europe markets, backtested over 16 years (2010–2026).

**Core finding:** When the Moodring score is in the "Extreme Fear" quintile, SPY averages **+2.43%** over the next 20 days (73% win rate). In "Extreme Greed," only **+0.82%**.

## How It Works

```
Market Data (yfinance + FinMind)
    → Rolling Z-score normalization (252-day, no lookahead)
    → 5 components per market (equal weight)
    → Behavioral adjustment (loss aversion, FOMO, herding)
    → 0–100 score (high = greedy, low = fearful)
    → Forward return statistics at current level
```

### US Score Components
| Component | Signal | Source |
|-----------|--------|--------|
| VIX Complacency | Low VIX = complacent retail | yfinance |
| SPY Position | Near ATH = FOMO | yfinance |
| Momentum | Strong 20d return = chasing | yfinance |
| Correlation Regime | VIX-SPY decorrelation = stress | yfinance |
| Risk Appetite | Gold/SPY ratio = risk-off | yfinance |

### TW Score Components
| Component | Signal | Source |
|-----------|--------|--------|
| Rate Pressure | US 10Y yield = hot money flow | yfinance |
| Global Risk Appetite | Gold/SPY ratio | yfinance |
| Vol Complacency | Low realized vol = complacent | yfinance |
| TAIEX Position | Near ATH = greedy | yfinance |
| Volume Excitement | Volume surge = retail piling in | yfinance |

### JP Score Components
| Component | Signal | Source |
|-----------|--------|--------|
| RSI Position | High RSI = overbought retail | yfinance |
| Nikkei Position | Near ATH = FOMO | yfinance |
| Momentum | Strong 20d return = chasing | yfinance |

### KR Score Components
| Component | Signal | Source |
|-----------|--------|--------|
| RSI Position | High RSI = overbought retail | yfinance |
| KOSPI Position | Near ATH = FOMO | yfinance |
| Momentum | Strong 20d return = chasing | yfinance |

### EU Score Components
| Component | Signal | Source |
|-----------|--------|--------|
| RSI Position | High RSI = overbought retail | yfinance |
| STOXX50 Position | Near ATH = FOMO | yfinance |
| Momentum | Strong 20d return = chasing | yfinance |

### Behavioral Layer (Phase 2)
Psychometric parameters from behavioral finance literature amplify the base score at extremes:
- **Loss Aversion** (Kahneman & Tversky, 1979): US 2.0x, TW 2.8x
- **Herding** (Banerjee, 1992): US 0.60, TW 0.80
- **FOMO**: activates when momentum > 0 near ATH
- **Conditional gates**: adjustments only fire at extreme scores (>65 or <35), zero noise in normal markets

## Backtest Results

| Market | Horizon | IC | p-value | Significant |
|--------|---------|-----|---------|-------------|
| US (SPY) | 20d | **-0.175** | < 0.0001 | Yes |
| US (SPY) | 60d | **-0.180** | < 0.0001 | Yes |
| TW (TAIEX) | 20d | **-0.128** | < 0.0001 | Yes |
| TW (TAIEX) | 60d | **-0.098** | < 0.0001 | Yes |

Negative IC = contrarian signal works (high greed → low future returns).

## Project Structure

```
├── docs/
│   ├── index.html          # EN dashboard (GitHub Pages)
│   ├── tw.html             # TW dashboard (GitHub Pages)
│   ├── landing.html        # Landing page
│   └── research/           # Proposal & theoretical docs
├── data/
│   ├── base_features.csv   # Market features (2010-2026)
│   ├── target_returns.csv  # Forward returns (5/10/20/60d)
│   ├── phase2_scores.csv   # Base + behavioral scores
│   ├── forward_outlook.json # Conditional returns at current level
│   ├── dashboard_data.json # Dashboard display data
│   └── ...                 # Backtest results, snapshots
└── src/
    ├── backtest.py         # Scoring engine + evaluation
    ├── phase2_agents.py    # Behavioral model + narrative generator
    ├── historical_data.py  # Data download pipeline
    ├── daily_update.py     # Daily auto-update pipeline
    └── social_data.py      # TW margin/institutional data
```

## Quick Start

```bash
# 1. Download historical data
python src/historical_data.py

# 2. Run backtest
python src/backtest.py

# 3. Run Phase 2 (behavioral + agents)
python src/phase2_agents.py
```

## Tech Stack

- **Data**: yfinance (US), FinMind (TW), pandas, numpy
- **Statistics**: scipy (Spearman IC, p-values)
- **Behavioral Model**: Prospect Theory parameters, conditional activation gates
- **Dashboard**: Chart.js, vanilla HTML/CSS/JS, GitHub Pages
- **Narrative**: Claude (claude-opus-4-6) for cultural sentiment interpretation

## License

Research project — not financial advice.
