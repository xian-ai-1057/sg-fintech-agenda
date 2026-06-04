# SFF Agenda Scraper

用 Selenium 抓取 [Singapore FinTech Festival 議程頁](https://www.fintechfestival.sg/agenda)
所有場次資訊，輸出成 CSV。

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
