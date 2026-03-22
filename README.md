# MoodRing 市場情緒指標

> 透過多維度情緒指標分析美股與台股市場方向的每日儀表板

**[美股儀表板 (US)](https://wenyuchiou.github.io/moodring/)** · **[台股儀表板 (TW)](https://wenyuchiou.github.io/moodring/tw.html)**

---

## 什麼是 MoodRing？

MoodRing 是一個逆向情緒指標系統，每日將市場衍生信號整合為 0–100 的情緒分數：

- **分數 < 30**：市場情緒悲觀（零售投資人恐慌） → 歷史上後續報酬高於平均
- **分數 30–70**：中性區間
- **分數 > 70**：市場情緒樂觀（零售投資人貪婪） → 歷史上後續報酬低於平均

情緒分數基於 252 個交易日滾動 Z-score 標準化，不使用任何未來資料（no lookahead bias）。

---

## 功能特色

### 美股情緒分析
- **VIX 恐慌指數**：市場隱含波動率
- **Put/Call Ratio**：選擇權買賣比率（零售情緒代理指標）
- **信用利差（HY Spread）**：高收益債與美國公債利差
- **RSI 超買超賣**：S&P 500 技術指標
- **保證金餘額變化**：融資使用程度

### 台股情緒分析
- **加權指數（TAIEX）**：台灣大盤走勢
- **融資餘額變化**：散戶槓桿參與程度
- **融券餘額變化**：市場做空強度
- **外資買賣超**：外國機構資金流向
- **MSCI 台灣 ETF（EWT）**：境外台股情緒參考

### 歷史走勢與記憶類比
- 情緒分數歷史折線圖，疊加加權指數走勢
- **記憶類比（Memory Analogy）**：自動搜尋歷史上情緒最相似的日期，顯示當時後續市場表現
- 分位數條件報酬表：依情緒分位數統計歷史勝率與平均報酬

### 每日自動更新
- 由 GitHub Actions 排程執行，無需人工介入
- 資料寫入 `docs/dashboard_data.json`，靜態網頁直接讀取

---

## 儀表板連結

| 市場 | 連結 |
|------|------|
| 美股（US） | https://wenyuchiou.github.io/moodring/ |
| 台股（TW） | https://wenyuchiou.github.io/moodring/tw.html |

---

## 技術架構

```
Python 資料管線
    ↓ yfinance / FinMind API 拉取原始數據
    ↓ 計算情緒分數（Z-score 標準化 + 加權合成）
    ↓ 記憶類比搜尋（餘弦相似度）
    ↓ 輸出 docs/dashboard_data.json
GitHub Pages 靜態儀表板
    ↓ 讀取 JSON
    ↓ Chart.js 渲染折線圖、分位數表、記憶類比卡片
```

**主要技術元件：**
- `Python 3.x`：資料處理與指標計算
- `yfinance`：美股市場數據（VIX、S&P 500、HY Spread ETF 等）
- `FinMind`：台股市場數據（融資融券、外資買賣超）
- `GitHub Actions`：每日自動排程執行
- `GitHub Pages`：靜態網頁托管
- `Chart.js`：互動式圖表

---

## 每日排程

| 任務 | 排程時間（台北）| 說明 |
|------|----------------|------|
| 美股更新 | 每日 22:00 | 美股收盤後（美東 10:00 AM）拉取當日數據 |
| 台股更新 | 每日 09:30 | 亞股開盤後確認前日收盤數據完整性 |

排程定義於 `.github/workflows/` 內的 GitHub Actions YAML 檔。

---

## 資料來源

- **yfinance**：S&P 500、VIX、HY Spread（HYG/LQD）、Put/Call Ratio 代理指標
- **FinMind**：台股融資餘額、融券餘額、外資買賣超（公開市場數據）
- **公開市場數據**：TAIEX（加權指數）、EWT（MSCI 台灣 ETF）

---

## 本地執行

```bash
# 安裝相依套件
pip install -r requirements.txt

# 執行美股數據更新
python update_us.py

# 執行台股數據更新
python update_tw.py

# 本地預覽儀表板（開啟 docs/index.html）
```

---

## 免責聲明

本專案所有資料、指標與分析結果均為**歷史觀察與統計呈現**，不構成任何投資建議或預測保證。

- 歷史報酬不代表未來績效
- 情緒指標反映過去統計規律，不保證未來相同結果
- 本儀表板僅供研究與學習用途，不應作為投資決策依據
- 投資有風險，請自行評估風險承受能力並諮詢專業財務顧問

---

*MoodRing — 市場情緒的溫度計，不是水晶球。*
