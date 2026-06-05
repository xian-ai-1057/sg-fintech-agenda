#!/usr/bin/env python3
"""把繁體中文場次說明併入 agenda.csv。

英文 `description` 由 `scrape_sff_agenda.py` 從官網（英文）爬取；繁中說明則是
機器翻譯後維護在 `index.html` 的 `window.SFF_DESC`（以 slug 為 key，值為
`[英文, 繁中]`）。本腳本以 slug 為橋樑，把繁中說明回填到 CSV 的
`description_zh` 欄位（緊接在 `description` 之後）。

重新爬取（會覆寫 agenda.csv）後，再跑一次本腳本即可補回中文欄位：

    python3 scrape_sff_agenda.py
    python3 merge_zh_desc.py
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from urllib.parse import unquote

# 與 scrape_sff_agenda.py 的 FIELDS 對齊，並在 description 之後插入 description_zh。
FIELDS = [
    "day", "datetime", "title", "stage", "location",
    "event_type", "track", "speakers", "description", "description_zh", "url",
]


def load_desc(html_path: str) -> dict[str, list[str]]:
    """從 index.html 取出 window.SFF_DESC（合法 JSON）。"""
    html = open(html_path, encoding="utf-8").read()
    m = re.search(r"window\.SFF_DESC\s*=\s*(\{.*?\})\s*;", html, re.S)
    if not m:
        sys.exit("找不到 window.SFF_DESC，請確認 index.html 結構未變。")
    return json.loads(m.group(1))


def slug_of(url: str) -> str:
    return url.rstrip("/").split("/")[-1]


def zh_for(slug: str, desc: dict[str, list[str]]) -> str:
    """raw slug 找不到時，再試 URL-decode（救回 %C3%BC 之類的 slug）。"""
    for key in (slug, unquote(slug)):
        d = desc.get(key)
        if d and len(d) > 1 and d[1]:
            return d[1]
    return ""


def main() -> None:
    ap = argparse.ArgumentParser(description="把繁中場次說明併入 agenda.csv。")
    ap.add_argument("-c", "--csv", default="agenda.csv", help="目標 CSV（預設 agenda.csv）")
    ap.add_argument("--html", default="index.html", help="繁中說明來源（預設 index.html）")
    args = ap.parse_args()

    desc = load_desc(args.html)
    with open(args.csv, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))

    filled = 0
    for row in rows:
        zh = zh_for(slug_of(row.get("url", "")), desc)
        row["description_zh"] = zh
        if zh:
            filled += 1

    with open(args.csv, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"已寫入 {args.csv}：{len(rows)} 列，其中 {filled} 列補上繁中說明。")


if __name__ == "__main__":
    main()
