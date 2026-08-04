"""議程資料層：把 agenda.csv 讀成 Session 物件。

這個模組刻意不 import 任何 LLM 相關套件，所以不需要 API key 也能單獨驗證解析結果：

    python3 -c "from agent.agenda import load_sessions; print(len(load_sessions()))"
"""

from __future__ import annotations

import csv
import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import unquote

CSV_PATH = Path(__file__).resolve().parents[1] / "agenda.csv"

# 議程時間全部是新加坡時間。活動只在一個場地、一個時區，所以用 naive datetime
# 就夠了，不引進 tzinfo —— 之後要算衝堂也只是 a.start < b.end and b.start < a.end。
TIMEZONE_LABEL = "SGT"

# CSV 的 datetime 欄位長這樣："Wed, 18 Nov | 9:15 AM - 9:30 AM"
_TIME_RE = re.compile(r"\|\s*(\d{1,2}:\d{2}\s*[AP]M)\s*-\s*(\d{1,2}:\d{2}\s*[AP]M)", re.I)


@dataclass(frozen=True, slots=True)
class Session:
    """一場議程。空字串代表官網還沒公布，不要拿來當「沒有」用。"""

    session_id: str  # "AGND441"，從網址推出來的穩定唯一鍵
    title: str
    date: date
    day_index: int  # 1..N，依日期排序推導，支援「第二天」這種問法
    day_label: str  # "Wed, 18 Nov"
    start: datetime  # naive，視為 SGT
    end: datetime
    stage: str
    location: str
    event_type: str  # "Open" / "Premium"
    track: str
    speakers: tuple[str, ...]  # 2026 的資料全空，欄位先留著
    description: str  # 英文
    description_zh: str  # 繁中，人工維護
    url: str


def session_id_of(url: str) -> str:
    """場次網址 -> 穩定識別碼（例：AGND441）。

    slug 規則與 scrape_sff_agenda.py 的 slug_of() 相同，但那個模組在 top-level
    import selenium，直接 import 會把爬蟲的相依一起拖進來，所以這裡自帶一份。
    """
    m = re.search(r"[?&]session=([^&#]+)", url or "", re.I)
    slug = m.group(1) if m else (url or "").split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    return unquote(slug).split("-")[0].upper()


def _parse_times(date_iso: str, datetime_cell: str) -> tuple[datetime, datetime]:
    """由 date 欄位與 datetime 欄位組出起訖時間。解析不到就直接拋錯 —— 靜默丟掉
    一場議程，比整支程式壞掉還難發現。"""
    m = _TIME_RE.search(datetime_cell or "")
    if not m:
        raise ValueError(f"無法解析場次時間：{datetime_cell!r}")
    day = date.fromisoformat(date_iso)
    start_t = datetime.strptime(m.group(1).strip().upper(), "%I:%M %p").time()
    end_t = datetime.strptime(m.group(2).strip().upper(), "%I:%M %p").time()
    return datetime.combine(day, start_t), datetime.combine(day, end_t)


def load_sessions(csv_path: Path | str = CSV_PATH) -> list[Session]:
    """讀 agenda.csv（UTF-8 with BOM），依開始時間排序。"""
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))

    day_index = {d: i + 1 for i, d in enumerate(sorted({r["date"] for r in rows}))}

    sessions = []
    for r in rows:
        start, end = _parse_times(r["date"], r["datetime"])
        sessions.append(
            Session(
                session_id=session_id_of(r["url"]),
                title=r["title"].strip(),
                date=date.fromisoformat(r["date"]),
                day_index=day_index[r["date"]],
                day_label=r["day"].strip(),
                start=start,
                end=end,
                stage=r["stage"].strip(),
                location=r["location"].strip(),
                event_type=r["event_type"].strip(),
                track=r["track"].strip(),
                speakers=tuple(x.strip() for x in r["speakers"].split(";") if x.strip()),
                description=r["description"].strip(),
                description_zh=r["description_zh"].strip(),
                url=r["url"].strip(),
            )
        )
    sessions.sort(key=lambda s: (s.start, s.stage, s.title))
    return sessions


def by_id(sessions: list[Session]) -> dict[str, Session]:
    return {s.session_id: s for s in sessions}


def resolve_day(value: str, sessions: list[Session]) -> date | None:
    """把各種日期寫法對應到活動的某一天，對不上就回 None。

    吃得下："2" / "day 2" / "d2" / "第二天" / "19" / "Thu" / "2026-11-19"。
    """
    v = (value or "").strip().lower()
    if not v:
        return None

    days = sorted({s.date for s in sessions})
    labels = {s.date: s.day_label.lower() for s in sessions}

    try:  # "2026-11-19"
        parsed = date.fromisoformat(v)
        return parsed if parsed in days else None
    except ValueError:
        pass

    for cjk, n in (("一", 1), ("二", 2), ("三", 3)):  # 「第二天」
        if cjk in v and n <= len(days):
            return days[n - 1]

    m = re.search(r"\d+", v)
    if m:
        n = int(m.group())
        if 1 <= n <= len(days):  # "2" / "day 2" / "d2"
            return days[n - 1]
        return next((d for d in days if d.day == n), None)  # "19"

    return next((d for d in days if labels[d].startswith(v[:3])), None)  # "thu"


def matches(
    s: Session,
    *,
    day: date | None = None,
    track: str = "",
    stage: str = "",
    event_type: str = "",
) -> bool:
    """篩選條件。空字串 / None 代表不篩；字串比對不分大小寫、可用子字串。

    list_sessions 與向量搜尋的 filter 共用這一個定義，所以「day=2 是什麼意思」
    只有一種答案。
    """
    if day is not None and s.date != day:
        return False
    for needle, haystack in ((track, s.track), (stage, s.stage), (event_type, s.event_type)):
        if needle and needle.strip().lower() not in haystack.lower():
            return False
    return True


def csv_fingerprint(csv_path: Path | str = CSV_PATH) -> str:
    return hashlib.sha256(Path(csv_path).read_bytes()).hexdigest()[:12]


def _counts(sessions: list[Session], field: str) -> str:
    counter = Counter(getattr(s, field) or "(not published)" for s in sessions)
    return ", ".join(f"{name} ({n})" for name, n in counter.most_common())


def overview(sessions: list[Session]) -> str:
    """給 system prompt 用的摘要，從 CSV 即時算出來。

    因為是推導的，換年份、換 CSV 都不必改 prompt —— 跟 index.html「所有東西
    都從 CSV 推導」的作法一致。
    """
    lines = [f"AGENDA OVERVIEW ({len(sessions)} sessions in total)"]
    for d in sorted({s.date for s in sessions}):
        same_day = [s for s in sessions if s.date == d]
        lines.append(
            f"- Day {same_day[0].day_index} = {same_day[0].day_label} "
            f"({d.isoformat()}), {len(same_day)} sessions"
        )
    lines.append(f"- Tracks: {_counts(sessions, 'track')}")
    lines.append(f"- Stages: {_counts(sessions, 'stage')}")
    lines.append(f"- Pass types: {_counts(sessions, 'event_type')}")
    return "\n".join(lines)
