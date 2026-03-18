# 💍 MoodRing — 全球散戶情緒指數

[English](#english) | 中文

每日更新的反向指標，覆蓋五大全球市場。
**散戶恐懼時，市場傾向上漲。散戶貪婪時，市場容易下跌。**

> 打開 Dashboard → 看分數 → 看歷史勝率 → 做決定。

**[🇺🇸 Dashboard (EN)](https://wenyuchiou.github.io/moodring/index.html)** · **[🇹🇼 Dashboard (中文)](https://wenyuchiou.github.io/moodring/tw.html)**

---

## 最新讀數

| 市場 | 分數 | 情緒 | 20天預期報酬 | 勝率 |
|------|------|------|-------------|------|
| 🇺🇸 美國 (SPY) | 32 | 謹慎 | +1.80% | 66% |
| 🇹🇼 台灣 (TAIEX) | 44 | 中性 | -0.21% | 60% |
| 🇯🇵 日本 (Nikkei) | 33 | 謹慎 | +1.69% | 67% |
| 🇰🇷 韓國 (KOSPI) | 35 | 謹慎 | +1.17% | 65% |
| 🇪🇺 歐洲 (STOXX50) | 33 | 謹慎 | +1.38% | 65% |

---

## 跟傳統指標的差異

| | CNN 恐懼貪婪 | AAII 調查 | **MoodRing** |
|--|-------------|----------|-------------|
| 方法 | 7 個固定指標 | 每週問卷 | **Z-score + 行為調整** |
| 市場 | 僅美國 | 僅美國 | **美、台、日、韓、歐** |
| 未來預測 | 無 | 無 | **有（歷史條件報酬）** |
| 行為模型 | 無 | 無 | **損失趨避、FOMO、羊群、錨定** |
| 散戶心態 | 無 | 無 | **AI 模擬散戶怎麼想** |
| 賣出信號 | 弱 | 無 | **台股貪婪 >70 = 報酬趨零** |
| 回測 IC | 未公開 | 弱 | **IC = -0.175 (p < 0.0001)** |

### 行為指標

不只是價格指標，MoodRing 加入學術研究的**行為調整層**：

| 參數 | 學術來源 | 美國 | 台灣 |
|------|---------|------|------|
| 損失趨避 | Kahneman & Tversky (1979) | 2.0x | 2.8x |
| 羊群效應 | Banerjee (1992) | 0.60 | 0.80 |
| FOMO | 行為金融學 | 0.30 | 0.30 |
| 錨定效應 | Tversky & Kahneman (1974) | 0.50 | 0.75 |
| 過度自信 | Barber & Odean (2001) | 0.30 | 0.00 |

## 極度恐懼時進場報酬（近10年）

| 市場 | 20天平均 | 勝率 |
|------|---------|------|
| 🇰🇷 韓國 | **+9.16%** | **85%** |
| 🇹🇼 台灣 | +6.45% | 87% |
| 🇯🇵 日本 | +5.81% | 75% |
| 🇪🇺 歐洲 | +4.28% | 77% |
| 🇺🇸 美國 | +4.17% | 72% |

## 運作原理

```
市場數據（Yahoo Finance + FinMind）
  → 5 個 Z-score 指標（252天滾動，無前瞻偏差）
  → 行為調整（損失趨避 × FOMO × 羊群效應）
  → 分數 0-100
  → 歷史條件報酬查詢
  → AI 散戶模擬：散戶現在在想什麼？
```

## 資料來源

| 資料 | 來源 |
|------|------|
| 美、日、韓、歐股價 | Yahoo Finance |
| 台股 + 融資 + 三大法人 | Yahoo Finance + FinMind (TWSE) |
| 行為參數 | 展望理論文獻 |

---

*非投資建議。過去表現不代表未來結果。*

---

<a id="english"></a>

## English

### 💍 MoodRing — Global Retail Investor Sentiment Index

A daily contrarian indicator for 5 global markets, powered by behavioral finance + AI.

When retail investors are fearful, markets rise. When greedy, markets underperform.

**[Dashboard (EN)](https://wenyuchiou.github.io/moodring/index.html)** · **[Dashboard (中文)](https://wenyuchiou.github.io/moodring/tw.html)**

### What Makes This Different

| | CNN Fear & Greed | AAII Survey | **MoodRing** |
|--|-----------------|------------|-------------|
| Method | 7 fixed indicators | Weekly poll | **Z-score + behavioral adjustment** |
| Markets | US only | US only | **US, TW, JP, KR, EU** |
| Forward returns | No | No | **Yes** |
| Behavioral model | No | No | **Loss aversion, FOMO, herding, anchoring** |
| Backtested IC | Not published | Weak | **IC = -0.175 (p < 0.0001)** |

### Extreme Fear Returns (10yr)

| Market | 20d Avg | Win Rate |
|--------|---------|----------|
| Korea (KOSPI) | **+9.16%** | **85%** |
| Taiwan (TAIEX) | +6.45% | 87% |
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
