#!/usr/bin/env python3
"""Scrape the Singapore FinTech Festival agenda into a CSV.

The agenda page (https://www.fintechfestival.sg/agenda) is rendered with
JavaScript, so we drive a real Chrome via Selenium.

The October 2026 listing contains all dates, speakers, venues and descriptions
in li.sff26-aglist__r. Read its data-day and verify the published session count.
The older layout remains supported as a two-stage fallback:
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
    python3 scrape_sff_agenda.py -o out.csv --limit-days 1  # first day (debug)
    python3 scrape_sff_agenda.py -o out.csv --skip-details # listing only
    python3 scrape_sff_agenda.py -o out.csv --max-sessions 5
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import tempfile
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

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
MODERN_CARD = "li.sff26-aglist__r"

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


def text_or_blank(parent, css: str, *, hidden: bool = False) -> str:
    """First matching element's trimmed text, or '' if none/unreadable."""
    try:
        element = parent.find_element(By.CSS_SELECTOR, css)
        return ((element.get_attribute("textContent") if hidden else element.text) or "").strip()
    except Exception:
        return ""


def normalize_time_range(value: str) -> str:
    """官網省略重複的 am/pm；CSV 兩端都要完整，避免下午被當成早上。"""
    match = re.fullmatch(
        r"\s*(\d{1,2}):(\d{2})\s*(am|pm)?\s*[-–—]\s*"
        r"(\d{1,2}):(\d{2})\s*(am|pm)?\s*", value, re.I)
    if not match:
        raise ValueError(f"Unrecognised agenda time: {value!r}")
    ah, am, ap, bh, bm, bp = match.groups()
    if not (1 <= int(ah) <= 12 and 1 <= int(bh) <= 12
            and 0 <= int(am) < 60 and 0 <= int(bm) < 60 and (ap or bp)):
        raise ValueError(f"Invalid agenda time: {value!r}")
    def minutes(h, m, p):
        return (int(h) % 12 + (12 if p.lower() == "pm" else 0)) * 60 + int(m)
    start = minutes(ah, am, ap or bp)
    end = minutes(bh, bm, bp or ap)
    if start >= end and not ap:
        start -= 12 * 60
    elif start >= end and not bp:
        end += 12 * 60
    if not 0 <= start < end < 24 * 60:
        raise ValueError(f"Invalid or overnight agenda time: {value!r}")
    def display(total):
        h, m = divmod(total, 60)
        return f"{h % 12 or 12}:{m:02d} {'PM' if h >= 12 else 'AM'}"
    return f"{display(start)} - {display(end)}"


def description_text(html: str) -> str:
    """收合的 details 仍可讀 HTML；保留 p/li/br 邊界，避免 work.AI 黏字。"""
    class PlainText(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts = []
        def handle_starttag(self, tag, attrs):
            if tag in ("p", "li", "div", "br"):
                self.parts.append("\n\n")
        def handle_endtag(self, tag):
            if tag in ("p", "li", "div"):
                self.parts.append("\n\n")
        def handle_data(self, data):
            self.parts.append(data)
    parser = PlainText()
    parser.feed(html)
    return "\n\n".join(" ".join(p.split()) for p in "".join(parser.parts).split("\n\n") if p.strip())


def parse_modern_card(card) -> dict:
    """2026 改版後，日期、場地、講者與說明都在同一張列表卡片。"""
    stamp = card.get_attribute("data-day")
    day = next((d for d in FESTIVAL_DAYS if str(d[2]) == stamp), None)
    if not day:
        raise ValueError(f"Unknown festival day: {stamp!r}")
    iso, label, _ = day
    link = card.find_element(By.CSS_SELECTOR, ".sff26-aglist__rha")
    descriptions = card.find_elements(By.CSS_SELECTOR, ".sff26-aglist__syn-b")
    description = description_text(descriptions[0].get_attribute("innerHTML") or "") if descriptions else ""
    if descriptions and not description:
        raise ValueError("An existing session description could not be read")
    speakers = [e.get_attribute("textContent").strip()
                for e in card.find_elements(By.CSS_SELECTOR, ".sff26-aglist__rpn b")]
    url = urljoin(AGENDA_URL, link.get_attribute("href") or "")
    parsed_url = urlparse(url)
    if (parsed_url.scheme != "https" or parsed_url.netloc != "www.fintechfestival.sg"
            or not re.fullmatch(r"AGND\d+", session_key(url))):
        raise ValueError(f"Invalid official session URL: {url!r}")
    return {
        "date": iso, "day": label,
        "datetime": f"{label} | {normalize_time_range(text_or_blank(card, '.sff26-aglist__rw b', hidden=True))}",
        "title": (link.get_attribute("textContent") or "").strip(),
        "stage": text_or_blank(card, ".sff26-aglist__rws", hidden=True),
        "location": text_or_blank(card, ".sff26-aglist__rwh", hidden=True),
        "event_type": text_or_blank(card, ".sff26-aglist__rwp", hidden=True),
        "track": text_or_blank(card, ".sff26-aglist__rt", hidden=True),
        "speakers": "; ".join(dict.fromkeys(s for s in speakers if s)),
        "description": description,
        "url": url,
    }


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
    driver.get(AGENDA_URL)
    try:
        WebDriverWait(driver, 30).until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, f"{MODERN_CARD}, {CARD}")))
    except TimeoutException:
        return []
    modern_cards = driver.find_elements(By.CSS_SELECTOR, MODERN_CARD)
    if modern_cards:
        # 改版後所有天數都在 DOM；不能靠網址篩選後把全部卡片標成同一天。
        count_text = text_or_blank(driver, ".sff26-aglist__n", hidden=True)
        if not count_text.isdigit() or len(modern_cards) != int(count_text):
            raise ValueError(f"Incomplete listing: {len(modern_cards)} cards; official count {count_text!r}")
        rows = [parse_modern_card(card) for card in modern_cards]
        if len({session_key(r['url']) for r in rows}) != len(rows):
            raise ValueError("Duplicate session IDs in official listing")
        requested = {d[0] for d in days}
        rows = [r for r in rows if r["date"] in requested]
        if {r["date"] for r in rows} != requested:
            raise ValueError("A requested festival day is missing; existing CSV left untouched")
        for row in rows:
            if any(not row[key] for key in ("title", "stage", "location", "event_type", "url")):
                raise ValueError(f"Incomplete session metadata: {row['url']}")
        print(f"  Current listing: {len(rows)} sessions across {len(requested)} day(s)")
        return rows

    by_url: dict[str, dict] = {}
    for iso, label, ts in days:
        print(f"  Day {label} ({iso}) ...")
        driver.get(f"{AGENDA_URL}?startDate={ts}")
        try:
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, CARD)))
        except TimeoutException:
            print(f"    {label}: no sessions found (timeout)")
            raise ValueError(f"No sessions for {iso}; existing CSV left untouched")
        time.sleep(2)
        scroll_to_load_all(driver, label)
        found = 0
        for card in driver.find_elements(By.CSS_SELECTOR, CARD):
            row = parse_card(card)
            if not row:
                raise ValueError(f"Unparseable session card on {iso}; CSV left untouched")
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
        raise ValueError(f"Detail load failed for {url}; CSV left untouched") from exc

    paras = driver.find_elements(By.CSS_SELECTOR, ".custom-agenda-post-content p")
    if paras:
        out["description"] = "\n\n".join(p.text.strip() for p in paras if p.text.strip())
    else:
        out["description"] = text_or_blank(driver, ".custom-agenda-post-content")

    out["stage"] = text_or_blank(driver, ".custom-agenda-post-location h5")
    out["location"] = text_or_blank(driver, ".custom-agenda-post-location-title")
    out["event_type"] = text_or_blank(driver, ".custom-agenda-post-eventtype")
    return out


def slug_of(url: str) -> str:
    """場次網址 -> 唯一識別。官網用過兩種格式，兩種都要吃得下：
         2026  https://…/agenda?session=AGND441-welcome-…
         2025  https://…/agenda/agn90-trusted-data-…
    （index.html 的 slugOf() 是同一套規則）"""
    m = re.search(r"[?&]session=([^&#]+)", url or "", re.I)
    slug = m.group(1) if m else (url or "").split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    return unquote(slug)


def session_key(url: str) -> str:
    """標題改名會換 slug；AGND 編號才是穩定識別。"""
    slug = slug_of(url)
    match = re.match(r"(AGND?\d+)\b", slug, re.I)
    return match.group(1).upper() if match else slug


def carry_over_zh(path: str, rows: list[dict]) -> int:
    """把現有 CSV 的 description_zh 搬到這次抓到的資料列上。

    中文說明是人工維護的、官網沒有這個欄位，所以每次重跑都得從舊檔接手，
    以穩定的 AGND 編號比對，且只有英文未改動時才保留，避免顯示過時翻譯。
    """
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            old = list(csv.DictReader(fh))
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return 0

    by_key = {}
    for r in old:
        zh = (r.get("description_zh") or "").strip()
        if not zh:
            continue
        if r.get("url"):
            by_key[session_key(r["url"])] = r

    kept = 0
    for row in rows:
        if row.get("description_zh", "").strip():
            continue
        previous = by_key.get(session_key(row.get("url", "")))
        # 舊翻譯只對舊英文負責；英文改寫或撤下時留白，讓介面顯示最新英文。
        same_english = previous and " ".join(previous.get("description", "").split()) == " ".join(row.get("description", "").split())
        if same_english and row.get("description", "").strip():
            row["description_zh"] = previous["description_zh"]
            kept += 1
    return kept


def write_csv(path: str, rows: list[dict]) -> None:
    """先寫同目錄暫存檔再原子替換，寫入失敗不截斷原有議程。"""
    destination = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", newline="", encoding="utf-8-sig",
                                         dir=destination.parent, delete=False) as fh:
            temporary = Path(fh.name)
            writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, destination)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


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

    if (args.limit_days or args.max_sessions or args.skip_details) and Path(args.output).resolve() == Path("agenda.csv").resolve():
        ap.error("Partial/debug crawls need a separate output, e.g. -o out.csv; agenda.csv is protected")

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
                if "description" in row and "stage" in row:
                    continue  # 新版列表已含說明；不要用舊版詳情選擇器覆蓋成空白。
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

    kept = carry_over_zh(args.output, rows)
    if kept:
        print(f"  保留了 {kept} 筆英文未改動的 description_zh；改動或新增的說明需重新翻譯")

    write_csv(args.output, rows)

    no_desc = sum(1 for r in rows if not r["description"])
    print(f"\nDone. Wrote {len(rows)} sessions to {args.output}")
    if no_desc:
        print(f"  注意：{no_desc} 筆沒有 description（詳情頁可能載入失敗）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
