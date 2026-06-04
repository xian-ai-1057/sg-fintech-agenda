# SFF Agenda Scraper

用 Selenium 抓取 [Singapore FinTech Festival 議程頁](https://www.fintechfestival.sg/agenda)
所有場次資訊，輸出成 CSV。

## 議程視覺化（index.html）

`index.html` 是 **Apple Calendar 風格**的互動式時間軸日曆，直接用瀏覽器雙擊開啟即可
（純前端、自包含單檔、無需後端或建置流程）：

- **簡約通透的設計**：白底、髮絲級格線、系統字體、大量留白；九大主題 track
  以柔和淡彩 + 細色條呈現，降低視覺雜訊；
- **兩種視圖可切換**：`Calendar`（時間軸 × 會議室／主舞台，色塊長度對應場次時長）
  與 `List`（時間軸清單），右上角一鍵切換；
- **深色模式**：右上角 ☾／☀ 一鍵切換；
- **Tweaks 微調面板**：強調色、色彩濃度（淡雅／中等／鮮明）、時段高度、欄寬、
  15 分鐘格線等皆可即時調整；
- 五日完整收錄：11/10–11/11 Insights Forum（Sands Expo 各會議室）、
  11/12–11/14 Singapore Expo 各主舞台 + 各 Lounge；
- 以顏色區分九大主題 track，🔒 標示邀請制、◆ 標示付費（Premium）場次；
- 同一會議室若有並行場次，會自動拆成多個子欄並排；短場次給最小高度、滑鼠移上展開；
- 點擊任一場次可看講者名單與官方場次連結。

> 排版修正：時間軸採系統字體並重新對齊（修正冒號渲染與文字裁切）；場次改以
> **分鐘級絕對定位**，任何時長（含 5／10 分鐘快講）都精準對位，不再因 grid
> 行號取整而錯位或在底部消失。

技術上以行內 React + Babel 渲染，維持「雙擊即開」特性。
資料由 `agenda.csv` 產生（11/12–11/14 場次自動轉出，11/10–11/11 為含講者所屬機構的手動精修版）。

#### 手機版（響應式，參考 g0v 手機版）

**同一個 `index.html` 會依螢幕寬度（≤ 720px）自動切換版型**，手機上改用更適合直拿瀏覽的
單一直列時間軸：

- **極簡頂部**：一行小標題 + 深色切換鈕，不再被大標題／參數／圖例佔掉半個畫面；
- **單一直列時間軸**：依起始時段分組（每組標出時段與場次數），每張卡片清楚顯示
  「時間 · 時長 · 主題色點＋名稱 · 房間／舞台」，一眼看出每個時段有什麼、是什麼主題；
- **緊湊卡片**：標題最多兩行，5／10 分鐘的短場次也不會佔過多空間；
- **搜尋 + 篩選**：頂部常駐搜尋框（講題／講者／場地），「篩選」底部彈出面板可多選
  主題與房間／舞台，主題色點同時兼作圖例；
- **詳情**：點卡片在手機上以底部彈出式（bottom sheet）開啟，桌機則維持置中視窗；
- 手機僅保留深色切換，其餘 Tweaks 收起，維持畫面清爽；
- 頂部留白與標題列同為一致的底色，視覺連貫不分段。

> 桌機版的 `Calendar` / `List` 視圖與既有功能完全不變。
> 在電腦上可開 `Mobile Preview.html`（手機外框預覽）或 `index.html?m=1`（強制手機版型）
> 直接預覽手機畫面。

### 線上部署（GitHub Pages）

repo 內含 `.github/workflows/pages.yml`，推送到 `main` 後會自動把網站發佈到 GitHub Pages。
首次使用需到 repo 的 **Settings → Pages → Build and deployment → Source** 選擇 **GitHub Actions**。
之後任何人都能透過下列網址開啟議程表：

```
https://xian-ai-1057.github.io/sg-fintech-agenda/
```


該頁為 JavaScript 動態渲染，且：
- 每次只顯示一天，透過 `?startDate=<epoch-ms>` 切換（10–14 Nov 2025）；
- 場次以**無限捲動**載入（非按鈕分頁）；
- 列表卡片只有標題/時間/track/講者，**完整描述與地點在各場次詳情頁**。

腳本因此分兩階段：先逐日捲動收集列表，再逐一進入詳情頁補抓描述與場地。

## 安裝

```bash
python3 -m pip install -r requirements.txt
```

需已安裝 Google Chrome。Selenium 4 內建 Selenium Manager 會自動下載對應的
chromedriver，無需手動安裝 driver。

## 執行

```bash
python3 scrape_sff_agenda.py                 # 完整抓取 -> agenda.csv
python3 scrape_sff_agenda.py --no-headless   # 顯示瀏覽器視窗（除錯）
python3 scrape_sff_agenda.py --limit-days 1  # 只抓第一天
python3 scrape_sff_agenda.py --skip-details  # 只抓列表，不進詳情頁
python3 scrape_sff_agenda.py -o out.csv --max-sessions 5   # 快速測試
```

## CSV 欄位

| 欄位 | 說明 |
|------|------|
| `day` | 議程日（如 `Mon, 10 Nov`） |
| `datetime` | 完整日期與起訖時間 |
| `title` | 場次標題 |
| `stage` | 舞台/論壇（如 `Insights Forum`, `Festival Stage`） |
| `location` | 場地/房間（如 `Design Thinking Room`, `Hall 1, Singapore Expo`） |
| `event_type` | 場次類型（`Open` / `Invite-Only` / `Premium`） |
| `track` | 分類標籤（如 `Next-Gen Transactions`） |
| `speakers` | 講者姓名（以 `; ` 分隔） |
| `description` | 詳情頁完整活動內容 |
| `url` | 詳情頁網址 |

CSV 以 `utf-8-sig` 編碼輸出，Excel 開啟不會亂碼。
