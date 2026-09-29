# S&P 500 漲跌家數儀表板

免費的 GitHub Actions + GitHub Pages 方案。每次執行會抓取 S&P 500 現有成分股，以「本交易日最新價 vs. 前一交易日收盤」計算上漲、下跌和平盤家數。

## 最快設定（約 10 分鐘）

1. 在 GitHub 建立一個 **Public** repository，不要勾選自動新增 README。
2. 把本專案的所有檔案上傳到 repository；請確認 `.github/workflows/update.yml` 沒有漏掉。
3. 到 **Actions → Update S&P 500 breadth → Run workflow**，手動跑第一次。
4. 到 **Settings → Pages**，Source 選 `Deploy from a branch`，Branch 選 `main`，Folder 選 `/docs`，按 Save。
5. 等一兩分鐘，Pages 畫面會顯示固定網址。手機瀏覽器可用「加入主畫面」。

若 Actions 無法把 JSON 推回 repository，到 **Settings → Actions → General → Workflow permissions** 選 `Read and write permissions`。

## 排程與盤中模式

- 工作日約每 30 分鐘執行。排程故意同時涵蓋美國夏令、冬令時間；腳本會使用 NYSE 日曆判斷真正的交易日與狀態。
- 開盤期間的最新點標為 `intraday`（盤中暫定）；收盤後執行會把該交易日寫進 `history.json`。
- GitHub 排程不是即時系統，尖峰時可能延遲；Yahoo 報價也可能延遲或暫時拒絕請求。
- 可隨時在 Actions 頁面按 **Run workflow** 手動更新。

## 本機測試

```bash
python -m venv .venv
python -m pip install -r requirements.txt
python indicators/sp500_updown.py
python -m http.server 8000 --directory docs
```

打開 `http://localhost:8000`。正式資料會寫入 `docs/data/`。

## 指標定義

`上漲比例 = 上漲檔數 ÷ 有有效價格的成分股檔數 × 100%`

資料不足 450 檔時程式會直接失敗，不會用殘缺資料覆蓋網頁。成分股名單來自 Wikipedia，價格來自 Yahoo Finance；本工具僅供資訊參考，不構成投資建議。

## PCE 通膨頁（`docs/pce.html`）

PCE、核心 PCE 年增率，以及 PCE 細項中年增率 > 3% 的比例（Fed 主席 Warsh 看的廣度指標）。資料來自 BEA，每個工作日台灣 21:45 檢查一次，BEA 公布新資料時自動更新。

**需要一次性設定 BEA 金鑰：**
1. **Settings → Secrets and variables → Actions → New repository secret**
2. Name 填 `BEA_API_KEY`，Secret 貼上 BEA 金鑰 → **Add secret**
3. **Actions → 更新 PCE 通膨 → Run workflow** 跑第一次

金鑰不要寫進任何檔案（repo 是公開的）。執行紀錄最後會印出找到的細項數，正常約 200 個。

細項認定：BEA 表 2.4.4U / 2.4.5U 最底層的消費項目（不含非營利機構產出與銷售調整）。BEA API 不提供表格階層，程式用「父項金額 = 子項加總」反推，推不出完整階層時直接報錯、不寫數字。
