# GRISI

**Global Retail Investor Sentiment Index**

A daily contrarian indicator for global markets.
When retail investors are fearful, markets tend to rise. When greedy, markets underperform.

> Check the score. See the historical win rate. Decide.

**[US Dashboard (EN)](https://wenyuchiou.github.io/grisi/index.html)** · **[TW Dashboard (中文)](https://wenyuchiou.github.io/grisi/tw.html)**

---

## Latest Reading

| Market | Score | Sentiment | 20d Avg Return | Win Rate |
|--------|-------|-----------|---------------|----------|
| US (SPY) | 32 | Cautious | +1.80% | 66% |
| Taiwan (TAIEX) | 44 | Neutral | -0.21% | 60% |
| Japan (Nikkei) | 33 | Cautious | +1.69% | 67% |
| Korea (KOSPI) | 35 | Cautious | +1.17% | 65% |
| Europe (STOXX50) | 33 | Cautious | +1.38% | 65% |

---

## Backtest Results (2010–2026)

| Market | 20d IC | 60d IC | Significant |
|--------|--------|--------|-------------|
| US (SPY) | **-0.175** | -0.180 | p < 0.0001 |
| Taiwan (TAIEX) | **-0.128** | -0.098 | p < 0.0001 |
| Japan (Nikkei) | **-0.119** | -0.168 | p < 0.0001 |
| Korea (KOSPI) | **-0.179** | -0.166 | p < 0.0001 |
| Europe (STOXX50) | **-0.145** | -0.126 | p < 0.0001 |

Negative IC = contrarian signal works. High greed predicts lower returns.

## Extreme Fear Returns (10yr, 2016–2026)

| Market | 20d Avg | Win Rate | 60d Avg |
|--------|---------|----------|---------|
| Korea (KOSPI) | **+9.16%** | **85%** | — |
| Taiwan (TAIEX) | +6.45% | 87% | +14.5% |
| Japan (Nikkei) | +5.81% | 75% | — |
| Europe (STOXX50) | +4.28% | 77% | — |
| US (SPY) | +4.17% | 72% | +11.1% |

---

## How It Works

```
Market data (Yahoo Finance, FinMind)
  → 5 indicators per market, Z-score normalized (252-day rolling)
  → Behavioral adjustment at extremes (loss aversion, FOMO, herding)
  → Score 0–100
  → Historical forward return at current score level
```

## Data Sources

| Data | Source |
|------|--------|
| US, JP, KR, EU prices | Yahoo Finance |
| Taiwan prices + margin + institutional flows | Yahoo Finance + FinMind (TWSE) |
| Behavioral parameters | Prospect Theory (Kahneman & Tversky, 1979) |

---

*Not financial advice. Past performance does not guarantee future results.*
