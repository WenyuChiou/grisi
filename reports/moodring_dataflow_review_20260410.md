# Moodring 資料流架構審查報告
**審查日期：** 2026-04-10  
**審查範圍：** src/daily_update.py, src/rebuild_dashboard_daily.py, .github/workflows/daily-update.yml, src/fix_flatline_20260410.py, README.md

## 執行摘要

MoodRing 的每日資料更新流程在架構上存在幾個關鍵風險點，主要集中在**資料寫入的衝突、冪等性不足以及測試覆蓋率的缺乏**。雖然系統包含了數據抓取重試、節日異常處理和自動重新校準機制，但多個腳本對共享檔案（如 `dashboard_data.json` 和 `docs/data/` 目錄）的並發寫入，以及缺乏針對核心邏輯（如日期處理、假期閉市）的單元測試，增加了資料損壞和維護困難的風險。`src/fix_flatline_20260410.py` 腳本的存在，正是系統在異常情況下需要手動介入的證明。

## 1. 單一寫入者原則分析

### 1.1 每個資料檔案是否有明確的「唯一負責寫入者」？

**否。**

- `src/daily_update.py` 負責寫入：
  - `data/overlay_data.json`
  - `data/historical_scores.csv`
  - `data/snapshot_*.json`, `data/snapshot_latest.json`
  - `data/phase2_agent_results.json`
  - `data/forward_outlook.json`
  - 並將部分檔案同步至 `docs/data/`。
- `src/rebuild_dashboard_daily.py` 負責寫入：
  - `data/dashboard_data.json`
  - 並將多個檔案同步至 `docs/data/`（包含 overlay_data.json、historical_scores.csv）。
- `src/fix_flatline_20260410.py`（臨時修復腳本）直接修改並寫入：
  - `docs/data/overlay_data.json`
  - `docs/data/historical_scores.csv`

### 1.2 `daily_update.py` 和 `rebuild_dashboard_daily.py` 是否對同一檔案都有寫入權限？這種設計風險為何？

**是**，主要體現在：

1. **`dashboard_data.json`**: `daily_update.py` 會更新其內的 `snapshot` 欄位，而 `rebuild_dashboard_daily.py` 則是完全重寫 `dashboard_data.json`，包括 `snapshot`、`agents` 和主要的數據陣列。這可能導致其中一個腳本的更新被另一個覆蓋。
2. **`docs/data/` 目錄同步**: 兩個腳本都會同步到 `docs/data/`，且同步的檔案列表有重疊（`historical_scores.csv`、`overlay_data.json`）。

**風險：**
- **資料衝突與遺失**: 後者寫入的資料會覆蓋前者，導致資料不一致或遺失。
- **非預期行為**: 儀表板讀取 `docs/data/` 時，可能會讀到來自不同腳本、不同時間點的資料。
- **維護困難**: 追蹤哪個腳本負責修改哪個檔案變得複雜。

### 1.3 `data/` 和 `docs/data/` 之間的同步邏輯是否一致且可靠？

**不一致且不可靠。**

- `daily_update.py` 同步的檔案列表和 `rebuild_dashboard_daily.py` 同步的檔案列表有重疊，且重疊的兩個腳本各自的同步時間點不同。
- `rebuild_dashboard_daily.py` 的同步邏輯在腳本最後執行，表達了「最終資料狀態」的意圖，但若 `daily_update.py` 在其之後對 `data/` 的任何檔案進行了修改，`docs/data/` 就不再是最新狀態。
- `fix_flatline_20260410.py` 直接操作 `docs/data/`（跳過 `data/`），進一步增加了同步的複雜性。

## 2. 冪等性 (Idempotency)

### 2.1 如果同一天執行 pipeline 兩次，結果是否相同？

**部分是，部分否。**

- `daily_update.py` 透過覆寫（overwrite）機制來實現單日執行兩次的冪等性（如 `update_overlay_json` 中的 pop 再 append 邏輯，以及 `append_scores_to_csv` 中的「檢查今天是否存在」邏輯）。
- `rebuild_dashboard_daily.py` 根據 `data/` 中的靜態檔案重建，自身冪等性較好。
- **風險點**：如果 `daily_update.py` 在寫入過程中中斷，第二次執行時的覆寫機制是否能正確覆蓋部分寫入的狀態，沒有測試保護。

### 2.2 目前的「重複日期保護」（overwrite 邏輯）是否在所有情況下都能正確運作？

**看起來不夠穩健。** `fix_flatline_20260410.py` 腳本的出現，說明自動流程無法在重複執行或中斷後自動恢復到一致的正確狀態，需要手動腳本來矯正。

### 2.3 哪些操作是真正冪等的？哪些不是？

**真正冪等的：**
- `rebuild_dashboard_daily.py`：只要輸入資料一致，輸出就一致。
- `append_scores_to_csv` 的更新今天記錄邏輯（若能正確覆寫）。

**非冪等或潛在非冪等的：**
- `daily_update.py` 的整體寫入流程（中斷後第二次執行邏輯可能不完全相同）。
- `docs/data/` 同步（最終狀態取決於哪個腳本最後執行）。

## 3. 錯誤隔離 (Failure Isolation)

### 3.1 如果某個市場（例如 TW 的 FinMind API）取得失敗，是否會影響其他市場的寫入？

**可能會有間接影響，但直接寫入隔離性存疑。**

- 各市場的抓取函數是獨立調用的，包含重試機制。
- `compute_score` 使用預設值（如 `market_data.get(f'{prefix}_RSI14', 50)`），防止單一指標失敗導致整個分數計算停擺。
- `update_overlay_json` 透過 `us_open` 等標誌決定是否寫入價格，有助於避免寫入關閉市場的無效價格。
- **關鍵風險**：若某市場分數為 `None`，此值會被寫入 JSON/CSV，後續清理函數未必能處理所有情況。若整個腳本因某個市場資料問題崩潰，所有後續市場的寫入都可能失敗。

### 3.2 pipeline 是否有適當的 fallback 機制？

**有初步機制，但尚不完善：**
- 重試機制（`finmind_with_retry`, `yf_download_with_retry`）應對暫時性錯誤。
- `compute_score` 預設值防止單指標失敗。
- **未解決**：`daily_update.py` 在寫入 `overlay_data.json` 或 `historical_scores.csv` 過程中崩潰時，沒有原子寫入保護，可能留下損壞檔案。

### 3.3 目前的錯誤處理策略（try/except 結構）是否足夠？

**不足以應對所有關鍵風險。** 對於競爭寫入和部分寫入後中斷的場景，`try/except` 本身不足以保證資料完整性和一致性。需要更底層的機制（如原子寫入）。

## 4. 測試覆蓋率 (Test Coverage)

### 4.1 這個 pipeline 是否有任何自動化測試？

**未見顯著的自動化單元測試。** GitHub Actions CI 工作流程執行腳本後 git commit，屬於集成測試，而非針對核心邏輯的單元測試。

### 4.2 最容易出錯的部分是否有測試保護？

**極不可能。** 寫入順序、日期邊界（`validate_market_open`、`clean_holiday_anomalies`）、重複日期保護（`update_overlay_json` 的 pop 邏輯），皆無明顯的單元測試。

### 4.3 沒有測試的狀況下，如何確保修復不會引入 regression？

**極度困難，且風險很高。** `fix_flatline_20260410.py` 的存在，說明了在沒有測試的情況下，一旦出現問題，只能依靠手動分析和修復。

## 5. 重構建議 (Refactoring Recommendations)

### 優先級 1（最重要）：引入單一寫入者模式 + 原子寫入

**原因：** 解決資料衝突與競爭條件，提升冪等性。

**具體建議：**
1. `daily_update.py` 成為唯一的資料寫入者：所有市場資料在記憶體中組裝完畢後，**一次性**原子寫入（先寫臨時檔案，成功後重命名）。
2. `rebuild_dashboard_daily.py` 成為唯讀彙總者：只讀取 `data/`，生成 `dashboard_data.json`，**不寫入**任何其他主要資料檔案。
3. 將所有 `docs/data/` 同步操作集中到 `rebuild_dashboard_daily.py` 的最後一步（pipeline 的最終寫入者）。
4. 廢止 `src/fix_flatline_20260410.py`，通過健壯的主流程取代手動修復腳本。

### 優先級 2：為核心邏輯新增單元測試

**原因：** 確保正確性，加速重構，減少 regression 風險。

**具體建議：**
1. 測試 `validate_market_open`：驗證假日、週末、工作日的市場開放判斷。
2. 測試 `compute_score`：缺失值、極端值下的分數計算。
3. 測試 `clean_holiday_anomalies`：假期數據異常的處理邏輯（包含 pass 3 的邊界條件）。
4. 測試 `update_overlay_json` 的覆寫邏輯：已有今天數據時應覆寫、中斷後重跑的行為。
5. 在 CI 中加入對輸出檔案的 schema 驗證（日期連續性、分數範圍、陣列長度一致性）。

### 優先級 3：加強錯誤處理與日誌記錄

**原因：** 提高系統彈性與可觀察性，減少對手動介入的依賴。

**具體建議：**
1. 結構化日誌：記錄每個市場的資料抓取狀態、分數計算結果、寫入操作成功/失敗。
2. 寫入後驗證：在 `rebuild_dashboard_daily.py` 最後加入對生成資料的初步驗證（日期連續性、分數值域、陣列長度），若異常則不 git commit，讓 CI 失敗可見。
3. 將假期邏輯前移：盡量在資料抓取階段判斷市場是否開放，而非依賴事後 `clean_holiday_anomalies` 補救。

## 總結

**本架構的主要優點：**

MoodRing 系統利用 GitHub Actions 自動化每日資料更新，整合 `yfinance` 和 `FinMind` 等成熟函式庫，並引入重新校準機制以適應市場變化。儀表板結合 Chart.js、記憶類比與分位數分析，為使用者提供了有價值的市場情緒參考。

**最大風險：**

最大的風險在於**資料寫入的競爭與不可靠性**。多個腳本對共享檔案（尤其是 `dashboard_data.json` 和 `docs/data/` 目錄）的寫入，加上冪等性處理不夠穩健，以及缺乏關鍵邏輯的單元測試，使得系統容易出現資料損壞、不一致，且難以自動恢復。`src/fix_flatline_20260410.py` 的存在，正是這種架構風險的直接體現——當前自動化流程在面對異常情況時，需要額外的手動介入。

---
*報告由 Gemini CLI 產生。原始程式碼讀取時間：2026-04-10。*
