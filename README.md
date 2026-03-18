# 💍 Moodring — 全球散戶情緒指數

[English](#english) | 中文

每日更新的**反向指標**，覆蓋五大全球市場。
**散戶恐懼時，市場傾向上漲。散戶貪婪時，市場容易下跌。**

> 打開 Dashboard → 看分數 → 看歷史勝率 → 做決定。

**[🇹🇼 中文儀表板](https://wenyuchiou.github.io/moodring/tw.html)** · **[🇺🇸 EN Dashboard](https://wenyuchiou.github.io/moodring/index.html)**

---

## 怎麼用？

1. **看分數**：0-100，越高代表散戶越貪婪
2. **看情緒區間**：極度恐慌（<20）/ 恐慌 / 謹慎 / 中性 / 貪婪 / 極度貪婪（>80）
3. **看歷史勝率**：「在目前分數下進場，過去 20 天的平均報酬和勝率是多少？」
4. **做決定**：分數低 = 歷史上好的進場時機，分數高 = 應該保守

## 最新讀數

| 市場 | 分數 | 情緒 | 20天預期報酬 | 勝率 |
|------|------|------|-------------|------|
| 🇺🇸 美國 (SPY) | 33.5 | 謹慎 | +2.18% | 73% |
| 🇹🇼 台灣 (加權) | 41.2 | 中性偏低 | +0.82% | 63% |
| 🇯🇵 日本 (日經) | 32.2 | 恐慌 | +1.56% | 64% |
| 🇰🇷 韓國 (KOSPI) | 47.2 | 中性 | +0.76% | 57% |
| 🇪🇺 歐洲 (STOXX50) | 36.4 | 謹慎 | +1.06% | 61% |

---

## 台股投資人專區

Moodring 對台股特別有用，因為台灣散戶的行為特徵非常鮮明：

### 台股看什麼指標？

| 指標 | 為什麼重要 |
|------|-----------|
| **融資餘額變化** | 散戶在借錢買股票嗎？融資暴增 = 過度樂觀 |
| **外資連續買賣天數** | 外資連買 or 連賣幾天了？散戶常反著做 |
| **加權指數離高點位置** | 越接近前高，FOMO 和過度自信越強 |
| **成交量異常放大** | 爆量 = 散戶從眾跟風 |
| **波動度** | 波動太低 = 集體失去警覺心 |

### 台股極度恐慌進場結果（回測 16 年）

在 Moodring 分數 < 20 時進場：
- **20 天平均報酬：+6.45%**
- **勝率：87%**

這代表歷史上台股大恐慌時，進場 20 天後有 87% 的機率是正報酬，平均賺 6.45%。

### 行為調整（台灣 vs 美國）

台灣散戶的行為偏誤比美國更強（學術研究支持）：

| 參數 | 美國 | 台灣 | 含義 |
|------|------|------|------|
| 損失趨避 | 2.0x | **2.8x** | 台灣散戶更怕賠錢，恐慌時反應更劇烈 |
| 羊群效應 | 0.60 | **0.80** | 台灣散戶更容易跟風 |
| 錨定效應 | 0.50 | **0.75** | 台灣散戶更容易被前高錨定 |

---

## 跟傳統指標的差異

| | CNN 恐懼貪婪 | AAII 調查 | **Moodring** |
|--|-------------|----------|-------------|
| 方法 | 7 個固定指標 | 每週問卷 | **Z-score + 行為調整** |
| 市場 | 僅美國 | 僅美國 | **美、台、日、韓、歐** |
| 預測能力 | 無 | 無 | **有（歷史條件報酬）** |
| 行為模型 | 無 | 無 | **損失趨避、FOMO、羊群、錨定** |
| 台股資料 | 無 | 無 | **有（融資、法人、成交量）** |
| 回測 IC | 未公開 | 弱 | **IC = -0.175 (p < 0.0001)** |

## 極度恐懼進場報酬（所有市場，回測 16 年）

| 市場 | 20天平均 | 勝率 |
|------|---------|------|
| 🇰🇷 韓國 | **+9.16%** | **85%** |
| 🇹🇼 台灣 | **+6.45%** | **87%** |
| 🇯🇵 日本 | +5.81% | 75% |
| 🇪🇺 歐洲 | +4.28% | 77% |
| 🇺🇸 美國 | +4.17% | 72% |

## 運作原理

```
市場數據（Yahoo Finance + FinMind）
  → 5 個 Z-score 指標（252天滾動，無前瞻偏差）
  → 行為調整（損失趨避 × FOMO × 羊群效應）
  → 分數 0-100（高 = 散戶貪婪，低 = 散戶恐懼）
  → 歷史條件報酬查詢（「現在進場，過去勝率多少？」）
  → 每日更新敘事分析
```

## 資料來源

| 資料 | 來源 |
|------|------|
| 美、日、韓、歐股價 | Yahoo Finance |
| 台股 + 融資餘額 + 三大法人 | Yahoo Finance + FinMind (TWSE 公開資料) |
| 行為參數 | 展望理論文獻 (Kahneman & Tversky, 1979) |

---

*非投資建議。過去表現不代表未來結果。本工具為研究用途。*

---

<a id="english"></a>

## English

### 💍 Moodring — Global Retail Investor Sentiment Index

A daily contrarian indicator for 5 global markets, powered by behavioral finance.

When retail investors are fearful, markets rise. When greedy, markets underperform.

**[Dashboard (中文)](https://wenyuchiou.github.io/moodring/tw.html)** · **[Dashboard (EN)](https://wenyuchiou.github.io/moodring/index.html)**

### How to Use

1. **Check the score**: 0-100, higher = more retail greed
2. **See the zone**: Extreme Fear (<20) → Fear → Cautious → Neutral → Greedy → Extreme Greed (>80)
3. **Check historical win rate**: "If I enter at this score, what's the 20-day avg return?"
4. **Decide**: Low score = historically good entry. High score = be cautious.

### What Makes This Different

| | CNN Fear & Greed | AAII Survey | **Moodring** |
|--|-----------------|------------|-------------|
| Method | 7 fixed indicators | Weekly poll | **Z-score + behavioral adjustment** |
| Markets | US only | US only | **US, TW, JP, KR, EU** |
| Forward returns | No | No | **Yes (conditional on current score)** |
| Behavioral model | No | No | **Loss aversion, FOMO, herding, anchoring** |
| Backtested IC | Not published | Weak | **IC = -0.175 (p < 0.0001)** |

### Extreme Fear Returns (16yr backtest)

| Market | 20d Avg | Win Rate |
|--------|---------|----------|
| Korea (KOSPI) | **+9.16%** | **85%** |
| Taiwan (TAIEX) | **+6.45%** | **87%** |
| Japan (Nikkei) | +5.81% | 75% |
| Europe (STOXX50) | +4.28% | 77% |
| US (SPY) | +4.17% | 72% |

### Data Sources

| Data | Source |
|------|--------|
| US, JP, KR, EU prices | Yahoo Finance |
| TW prices + margin + institutional | Yahoo Finance + FinMind (TWSE) |
| Behavioral parameters | Prospect Theory literature |

*Not financial advice. Past performance ≠ future results.*
