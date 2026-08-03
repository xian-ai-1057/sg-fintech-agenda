# SFF 2025 議程（封存版）

Singapore FinTech Festival **2025**（11/10–11/14，Sands Expo Insights Forum + Singapore Expo
主舞台）的議程視覺化，**已凍結**，僅保留供查閱。

線上網址：<https://xian-ai-1057.github.io/sg-fintech-agenda/archive/2025/>

## 檔案

| 檔案 | 說明 |
|---|---|
| `index.html` | 2025 年版視覺化，**雙擊即可開啟**（資料寫在檔案內，不需伺服器）|
| `agenda.csv` | 2025 年的原始抓取結果，353 筆場次 |
| `merge_zh_desc.py` | 把 `index.html` 裡的中文說明回填到 `agenda.csv` 的一次性工具 |

## 與根目錄新版的差異

這一版把 **348 筆場次與全部說明文字硬寫在 `index.html` 的行內 JS**
（`window.SFF` 的 `S` 陣列與 `window.SFF_DESC`），`agenda.csv` 只是上游來源、
網頁執行時並不讀取它。

根目錄的 2026 版改成**執行時載入 `agenda.csv`**，資料與介面分離。因此：

- 本封存版**不需要**任何伺服器，直接用瀏覽器開 `index.html` 即可；
- `merge_zh_desc.py` 依賴 `window.SFF_DESC = {…};` 這個行內結構，只對本版本有效，
  對新版沒有意義。

要修改本封存版的議程內容，得改 `index.html` 裡的行內物件（不是 `agenda.csv`）。
一般情況下不應該再修改它 —— 它就是當年的樣子。
