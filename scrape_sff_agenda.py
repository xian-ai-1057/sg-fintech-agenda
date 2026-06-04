#!/usr/bin/env python3
"""Scrape the Singapore FinTech Festival agenda into a CSV.

The agenda page (https://www.fintechfestival.sg/agenda) is rendered with
JavaScript, so we drive a real Chrome via Selenium.

Two stages:
  1. Listing — the agenda shows one festival day at a time, selected via the
     ?startDate=<epoch-ms> query param, and loads more sessions on scroll
     (infinite scroll, not a button). We iterate the 5 days (10-14 Nov 2025),
     scroll each to the bottom, and read each session card:
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

# Festival days -> SGT-midnight epoch (ms), which is what the page's
# ?startDate filter expects. Verified against the live site.
DAYS = [
    ("Mon, 10 Nov", 1762704000000),
    ("Tue, 11 Nov", 1762790400000),
    ("Wed, 12 Nov", 1762876800000),
    ("Thu, 13 Nov", 1762963200000),
    ("Fri, 14 Nov", 1763049600000),
]

CARD = "div.custom-agenda-listing-box"

# CSV column order.
FIELDS = [
    "day", "datetime", "title", "stage", "location",
    "event_type", "track", "speakers", "description", "url",
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
    for label, ts in days:
        print(f"  Day {label} ...")
        driver.get(f"{AGENDA_URL}?startDate={ts}")
        try:
            WebDriverWait(driver, 30).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, CARD)))
        except TimeoutException:
            print(f"    {label}: no sessions found (timeout)")
            continue
        time.sleep(2)
        scroll_to_load_all(driver, label)
        for card in driver.find_elements(By.CSS_SELECTOR, CARD):
            row = parse_card(card)
            if not row:
                continue
            row.setdefault("day", label)
            # First day a session appears under wins for `day`.
            if row["url"] not in by_url:
                row["day"] = label
                by_url[row["url"]] = row
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

    days = DAYS[: args.limit_days] if args.limit_days else DAYS

    driver = build_driver(headless=not args.no_headless)
    try:
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

    # Normalise rows to the full field set.
    for row in rows:
        for f in FIELDS:
            row.setdefault(f, "")

    with open(args.output, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nDone. Wrote {len(rows)} sessions to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
