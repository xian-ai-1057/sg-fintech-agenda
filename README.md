# SFF 2026 議程表

**Singapore FinTech Festival 2026**（11/18–11/20，Singapore Expo）的互動式議程視覺化，
外加一支把官網議程抓成 CSV 的 Selenium 爬蟲。

線上版：<https://xian-ai-1057.github.io/sg-fintech-agenda/>
2025 年的議程存檔：<https://xian-ai-1057.github.io/sg-fintech-agenda/archive/2025/>

## 資料流

```
scrape_sff_agenda.py  ──►  agenda.csv  ──►  index.html（開啟網頁時載入）
     （Selenium 爬蟲）        （唯一資料來源）        （純前端介面）
```

**議程內容一律改 `agenda.csv`，不要改 `index.html`。**
`index.html` 只負責顯示：開啟時 `fetch('agenda.csv')`，在瀏覽器裡解析、推導出日期、
場地欄位、主題配色，再交給 React 渲染。想換年份、補場次、修錯字，都只動 CSV。

> 2025 年版是把資料寫死在 HTML 裡的，那一版原封不動收在 `archive/2025/`。

## 開啟方式

因為要讀取旁邊的 `agenda.csv`，瀏覽器的 CORS 規則不允許 `file://` 頁面這樣做，
所以**建議用本機伺服器開**：

```bash
python3 -m http.server 8000
# 然後開 http://localhost:8000/
```

直接雙擊 `index.html` 也不會壞 —— 頁面會顯示說明，並提供**拖放／選擇 `agenda.csv`**
的方式手動載入，載入後功能完全一樣。

手機版預覽：`index.html?m=1` 強制手機版型，或開 `Mobile Preview.html`（手機外框預覽）。

## 介面功能

- **簡約通透的設計**：白底、髮絲級格線、系統字體、大量留白；各主題 track
  以柔和淡彩 + 細色條呈現，降低視覺雜訊；
- **兩種視圖可切換**：`Calendar`（時間軸 × 會議室／主舞台，色塊長度對應場次時長）
  與 `List`（時間軸清單），右上角一鍵切換；
- **深色模式**：右上角 ☾／☀ 一鍵切換；
- **Tweaks 微調面板**：強調色、色彩濃度（淡雅／中等／鮮明）、時段高度、欄寬、
  15 分鐘格線等皆可即時調整；
- 以顏色區分各主題 track，🔒 標示邀請制、◆ 標示付費（Premium）場次；
- 同一舞台若有並行場次，會自動拆成多個子欄並排；短場次給最小高度、滑鼠移上展開；
- 點擊任一場次可看**完整活動說明（description）**、講者名單與官方場次連結；
- **講者名單只先顯示前 5 位**，其餘以「… +N」收合，點一下即可展開全部，再點即收合；
- **中／英雙語說明切換**：右上角 `中／EN` 一鍵切換活動說明語言，偏好記在
  `localStorage`，**預設英文**；某場次若沒有中文翻譯，會自動顯示英文；
- **小幫手 Bot**：純前端關鍵字推薦，不對外送任何請求。

場次以**分鐘級絕對定位**排版，任何時長（含 5／10 分鐘快講）都精準對位。

### 手機版（響應式，參考 g0v 手機版）

**同一個 `index.html` 會依螢幕寬度（≤ 720px）自動切換版型**，手機上改用更適合直拿瀏覽的
單一直列時間軸：

- **極簡頂部**：一行小標題 + 深色切換鈕；
- **單一直列時間軸**：依起始時段分組（每組標出時段與場次數），每張卡片清楚顯示
  「時間 · 時長 · 主題色點＋名稱 · 舞台」；
- **搜尋 + 篩選**：頂部常駐搜尋框（講題／講者／場地），「篩選」底部彈出面板可多選
  主題與舞台，主題色點同時兼作圖例；
- **詳情**：點卡片以底部彈出式（bottom sheet）開啟，桌機則維持置中視窗。

### 線上部署（GitHub Pages）

repo 內含 `.github/workflows/pages.yml`，推送到 `main` 後會自動把整個 repo 根目錄
（含 `agenda.csv` 與 `archive/`）發佈到 GitHub Pages。首次使用需到 repo 的
**Settings → Pages → Build and deployment → Source** 選擇 **GitHub Actions**。

---

## 爬蟲

官網議程頁 <https://www.fintechfestival.sg/agenda> 為 JavaScript 動態渲染，且：

- 每次只顯示一天，透過 `?startDate=<epoch-ms>` 切換；
- 場次以**無限捲動**載入（非按鈕分頁）；
- 列表卡片只有標題/時間/track/講者，**完整描述與地點在各場次詳情頁**。

腳本因此分兩階段：先逐日捲動收集列表，再逐一進入詳情頁補抓描述與場地。

### 安裝

```bash
python3 -m pip install -r requirements.txt
```

需已安裝 Google Chrome。Selenium 4 內建 Selenium Manager 會自動下載對應的
chromedriver，無需手動安裝 driver。

### 執行

```bash
python3 scrape_sff_agenda.py                 # 完整抓取 -> agenda.csv
python3 scrape_sff_agenda.py --no-headless   # 顯示瀏覽器視窗（除錯）
python3 scrape_sff_agenda.py --limit-days 1  # 只抓第一天
python3 scrape_sff_agenda.py --skip-details  # 只抓列表，不進詳情頁
python3 scrape_sff_agenda.py -o out.csv --max-sessions 5   # 快速測試
```

抓完重新整理網頁即可看到新資料。

一筆場次都沒抓到時，腳本會**以非零狀態碼結束、且不覆蓋現有的 `agenda.csv`**，
並列出該檢查的 CSS 選擇器 —— 官網一改版通常就是這些選擇器失效。

### 換年份

改 `scrape_sff_agenda.py` 最上方的 `FESTIVAL_YEAR` 與 `FESTIVAL_DAYS` 即可，
每天需要 SGT（UTC+8）午夜的 epoch 毫秒：

```bash
python3 -c "from datetime import datetime,timezone,timedelta; \
  print(int(datetime(2026,11,18,tzinfo=timezone(timedelta(hours=8))).timestamp()*1000))"
```

`index.html` 不需要跟著改 —— 日期、天數、舞台欄位、主題配色都是從 CSV 推導出來的。

## CSV 欄位

| 欄位 | 說明 |
|------|------|
| `date` | ISO 日期（`2026-11-18`）；介面用它排序日期與計算星期 |
| `day` | 議程日標籤（如 `Wed, 18 Nov`） |
| `datetime` | 完整日期與起訖時間（如 `Wed, 18 Nov \| 9:00 AM - 10:30 AM`） |
| `title` | 場次標題 |
| `stage` | 舞台／論壇（如 `Festival Stage`、`Side Programmes`） |
| `location` | 場地／房間（如 `Hall 1, Singapore Expo`、`GFTN Lounge (Hall 4)`） |
| `event_type` | 場次類型（`Open` / `Invite-Only` / `Premium`） |
| `track` | 主題分類（如 `Next-Gen Transactions`） |
| `speakers` | 講者姓名，以 `; ` 分隔 |
| `description` | 詳情頁完整活動內容 |
| `description_zh` | 繁體中文說明；爬蟲不會產生，留白時介面自動顯示英文 |
| `url` | 詳情頁網址（結尾的 slug 同時是場次的唯一識別）|

CSV 以 `utf-8-sig`（帶 BOM）編碼輸出，Excel 開啟不會亂碼。

介面怎麼用這些欄位：

- **日曆欄位**＝`stage`；但若某個 `stage` 底下有多個不同的 `location`
  （例如 `Side Programmes` 涵蓋各個 Lounge），就改用 `location` 拆成獨立欄位。
- **代碼、短標籤、主題配色**在 `index.html` 的 `VENUE_CFG` / `TRACK_CFG` 裡設定。
  沒設定到的舞台或 track 會**自動推導代碼與配色**，不會漏掉場次，只是標籤比較陽春
  —— 想要好看的標籤就在那兩張表補一行。
