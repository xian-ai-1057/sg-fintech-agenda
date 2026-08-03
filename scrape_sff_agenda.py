#!/usr/bin/env python3
"""Scrape the Singapore FinTech Festival agenda into a CSV.

The agenda page (https://www.fintechfestival.sg/agenda) is rendered with
JavaScript, so we drive a real Chrome via Selenium.

Two stages:
  1. Listing — the agenda shows one festival day at a time, selected via the
     ?startDate=<epoch-ms> query param, and loads more sessions on scroll
     (infinite scroll, not a button). We iterate the festival days
     (18-20 Nov 2026, see FESTIVAL_DAYS), scroll each to the bottom, and read
     each session card:
         title, datetime, track, speakers, detail-page URL.
  2. Detail — the listing card has no description or venue, so we open each
     session's detail page and read:
         description, stage, room/location, event type.

Output: agenda.csv (UTF-8 with BOM so Excel shows non-ASCII correctly).

Selenium 4 ships Selenium Manager, which auto-downloads a matching
chromedriver, so only Google Chrome needs to be installed.

Usage:
    python3 scrape_sff_agenda.py                 # full crawl -> agenda.csv
    python3 scrape_sff_agenda.py --no-headless   # watch the browser
    python3 scrape_sff_agenda.py --limit-days 1  # only the first day (debug)
    python3 scrape_sff_agenda.py --skip-details  # listing only, no detail pages
    python3 scrape_sff_agenda.py -o out.csv --max-sessions 5
"""
from __future__ import annotations

import argparse
import csv
import sys
import time

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

AGENDA_URL = "https://www.fintechfestival.sg/agenda"

# ---------------------------------------------------------------------------
# 換年份只要改這一段
# ---------------------------------------------------------------------------
# Singapore FinTech Festival 2026：11/18–11/20，Singapore Expo。
# 每天用 SGT（UTC+8）午夜的 epoch 毫秒當作議程頁 ?startDate 的值。
# 要算新的日期：
#   python3 -c "from datetime import datetime,timezone,timedelta; \
#     print(int(datetime(2026,11,18,tzinfo=timezone(timedelta(hours=8))).timestamp()*1000))"
FESTIVAL_YEAR = 2026
FESTIVAL_DAYS = [
    # (ISO 日期, 議程頁顯示的日期標籤, SGT 午夜 epoch ms)
    ("2026-11-18", "Wed, 18 Nov", 1794931200000),
    ("2026-11-19", "Thu, 19 Nov", 1795017600000),
    ("2026-11-20", "Fri, 20 Nov", 1795104000000),
]

CARD = "div.custom-agenda-listing-box"

# CSV 欄位順序。
#   date            ISO 日期（2026-11-18），index.html 用它排序日期、算星期，
#                   不必從 "Wed, 18 Nov" 猜年份。
#   description_zh  不從官網爬取，留白；index.html 在沒有中文時會自動顯示英文。
FIELDS = [
    "date", "day", "datetime", "title", "stage", "location",
    "event_type", "track", "speakers", "description", "description_zh", "url",
]


def build_driver(headless: bool) -> webdriver.Chrome:
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1440,2600")
    opts.add_argument("--lang=en-US")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(60)
    return driver


def text_or_blank(parent, css: str) -> str:
    """First matching element's trimmed text, or '' if none/unreadable."""
    try:
        return parent.find_element(By.CSS_SELECTOR, css).text.strip()
    except Exception:
        return ""


def scroll_to_load_all(driver, label: str) -> int:
    """Scroll to the bottom repeatedly until the card count stops growing."""
    last = -1
    stable = 0
    for _ in range(80):
        cards = driver.find_elements(By.CSS_SELECTOR, CARD)
        n = len(cards)
        if n == last:
            stable += 1
            if stable >= 2:  # two stable reads -> done
                break
        else:
            stable = 0
        last = n
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1.3)
    print(f"    {label}: loaded {last} sessions")
    return last


def parse_card(card) -> dict | None:
    """Extract listing fields from one session card. None if no detail link."""
    try:
        link = card.find_element(By.CSS_SELECTOR, ".custom-agenda-listing-title a")
    except Exception:
        return None
    url = link.get_attribute("href")
    if not url:
        return None

    title = text_or_blank(card, ".custom-agenda-listing-title h3") or link.text.strip()
    datetime_txt = " ".join(text_or_blank(card, ".custom-listing-agenda-date").split())

    tracks = [li.text.strip()
              for li in card.find_elements(By.CSS_SELECTOR, ".custom-agenda-listing-topic li")
              if li.text.strip()]

    # Speakers: prefer the photo alt text (clean name), fall back to link text.
    speakers = []
    for item in card.find_elements(By.CSS_SELECTOR, ".custom-agenda-author-item"):
        name = ""
        try:
            name = item.find_element(By.CSS_SELECTOR, "img").get_attribute("alt") or ""
        except Exception:
            pass
        if not name:
            name = item.text.strip().split("\n")[0]
        name = name.strip()
        if name and name not in speakers:
            speakers.append(name)

    return {
        "title": title,
        "datetime": datetime_txt,
        "track": "; ".join(tracks),
        "speakers": "; ".join(speakers),
        "url": url,
    }


def scrape_listing(driver, days) -> list[dict]:
    """Stage 1: collect every session card across the given days, deduped by URL."""
    by_url: dict[str, dict] = {}
    for iso, label, ts in days:
        print(f"  Day {label} ({iso}) ...")
        driver.get(f"{AGENDA_URL}?startDate={ts}")
        try:
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, CARD)))
        except TimeoutException:
            print(f"    {label}: no sessions found (timeout)")
            continue
        time.sleep(2)
        scroll_to_load_all(driver, label)
        found = 0
        for card in driver.find_elements(By.CSS_SELECTOR, CARD):
            row = parse_card(card)
            if not row:
                continue
            found += 1
            # First day a session appears under wins for `day`.
            if row["url"] not in by_url:
                row["date"] = iso
                row["day"] = label
                by_url[row["url"]] = row
        print(f"    {label}: parsed {found} cards")
    return list(by_url.values())


def scrape_detail(driver, url: str) -> dict:
    """Stage 2: description + venue fields from a session detail page."""
    out = {"description": "", "stage": "", "location": "", "event_type": ""}
    try:
        driver.get(url)
        WebDriverWait(driver, 25).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, ".custom-agenda-post-content, .detail-page-section")))
        time.sleep(0.8)
    except (TimeoutException, WebDriverException) as exc:
        print(f"      ! detail load failed: {exc.__class__.__name__}")
        return out

    paras = driver.find_elements(By.CSS_SELECTOR, ".custom-agenda-post-content p")
    if paras:
        out["description"] = "\n\n".join(p.text.strip() for p in paras if p.text.strip())
    else:
        out["description"] = text_or_blank(driver, ".custom-agenda-post-content")

    out["stage"] = text_or_blank(driver, ".custom-agenda-post-location h5")
    out["location"] = text_or_blank(driver, ".custom-agenda-post-location-title")
    out["event_type"] = text_or_blank(driver, ".custom-agenda-post-eventtype")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Scrape SFF agenda into a CSV.")
    ap.add_argument("-o", "--output", default="agenda.csv", help="output CSV path")
    ap.add_argument("--no-headless", action="store_true", help="show the browser window")
    ap.add_argument("--limit-days", type=int, default=0,
                    help="only scrape the first N festival days (0 = all)")
    ap.add_argument("--max-sessions", type=int, default=0,
                    help="cap total sessions, for quick debug runs (0 = no cap)")
    ap.add_argument("--skip-details", action="store_true",
                    help="skip detail pages (no description / venue)")
    args = ap.parse_args()

    days = FESTIVAL_DAYS[: args.limit_days] if args.limit_days else FESTIVAL_DAYS

    driver = build_driver(headless=not args.no_headless)
    try:
        print(f"Scraping Singapore FinTech Festival {FESTIVAL_YEAR} "
              f"({len(days)} day(s))")
        print("Stage 1/2: collecting session listing ...")
        rows = scrape_listing(driver, days)
        if args.max_sessions:
            rows = rows[: args.max_sessions]
        print(f"  -> {len(rows)} unique sessions")

        if args.skip_details:
            print("Stage 2/2: skipped (--skip-details)")
        else:
            print(f"Stage 2/2: fetching {len(rows)} detail pages ...")
            for i, row in enumerate(rows, 1):
                detail = scrape_detail(driver, row["url"])
                row.update(detail)
                if i % 10 == 0 or i == len(rows):
                    print(f"    {i}/{len(rows)} done")
                time.sleep(0.4)  # be polite
    finally:
        driver.quit()

    # 一筆都沒抓到通常代表官網改版、選擇器失效 —— 這種時候寧可大聲失敗，
    # 也不要用一個空檔覆蓋掉現有的 agenda.csv。
    if not rows:
        print(
            "\n找不到任何場次，沒有寫出檔案（保留現有的 "
            f"{args.output}）。\nNo sessions found — {args.output} left untouched.\n\n"
            "可能原因：\n"
            f"  1. {FESTIVAL_YEAR} 年的議程還沒上線，或 FESTIVAL_DAYS 的日期／epoch 不對；\n"
            "  2. 官網改版，CSS 選擇器失效 —— 先檢查這幾個：\n"
            f"       CARD = {CARD!r}\n"
            "       .custom-agenda-listing-title a / h3\n"
            "       .custom-listing-agenda-date、.custom-agenda-listing-topic li\n"
            "       .custom-agenda-post-content、.custom-agenda-post-location\n"
            "  用 --no-headless 開著瀏覽器跑一次最快找出是哪一種。",
            file=sys.stderr,
        )
        return 1

    # Normalise rows to the full field set.
    for row in rows:
        for f in FIELDS:
            row.setdefault(f, "")

    with open(args.output, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    no_desc = sum(1 for r in rows if not r["description"])
    print(f"\nDone. Wrote {len(rows)} sessions to {args.output}")
    if no_desc:
        print(f"  注意：{no_desc} 筆沒有 description（詳情頁可能載入失敗）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
