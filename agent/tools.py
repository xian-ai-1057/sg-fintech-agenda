"""Agent 的三個工具，全部對 agenda.csv 唯讀。

工具的 docstring 就是給模型看的說明書，改動時請一併想「模型會不會挑錯工具」。
"""

from __future__ import annotations

from langchain_core.documents import Document
from langchain_core.tools import tool
from langchain_core.vectorstores import InMemoryVectorStore

if __package__:  # 見 rag.py 的說明
    from .agenda import TIMEZONE_LABEL, Session, by_id, matches, resolve_day
else:
    from agenda import TIMEZONE_LABEL, Session, by_id, matches, resolve_day

LIST_LIMIT = 60  # list_sessions 最多列幾行（107 行大約 4k tokens）
SNIPPET_CHARS = 150  # search_sessions 每筆附的說明摘要長度
MAX_TOP_K = 20

_NOT_A_DAY = "'{value}' is not a festival day. Use 1, 2 or 3 (18-20 Nov 2026)."


def _oneline(text: str) -> str:
    """壓掉換行 —— 有一場議程的說明裡真的有 \\n。"""
    return " ".join((text or "").split())


def format_line(s: Session) -> str:
    """清單用的單行格式。開頭一定是 session_id，後續追問才接得上。"""
    return " | ".join(
        [
            s.session_id,
            f"D{s.day_index} {s.day_label} {s.start:%H:%M}-{s.end:%H:%M}",
            s.stage or "stage TBA",
            s.track or "TBA",
            s.event_type or "TBA",
            _oneline(s.title),
        ]
    )


def format_full(s: Session) -> str:
    """單場詳細資料，需求 2（介紹活動內容）就靠這個。"""
    lines = [
        f"{s.session_id}  {_oneline(s.title)}",
        f"Day {s.day_index} ({s.day_label}) {s.start:%H:%M}-{s.end:%H:%M} {TIMEZONE_LABEL}"
        f" · {s.stage or 'stage not published'} · {s.location or 'location not published'}",
        f"Track: {s.track or 'TBA'} · Pass: {s.event_type or 'not published'}"
        f" · Speakers: {'; '.join(s.speakers) if s.speakers else 'not published'}",
        f"EN: {s.description or '(no description published yet)'}",
    ]
    if s.description_zh:
        lines.append(f"ZH: {s.description_zh}")
    if s.url:
        lines.append(f"URL: {s.url}")
    return "\n".join(lines)


def build_tools(sessions: list[Session], store: InMemoryVectorStore) -> list:
    """把工具綁到這批議程資料上（closure，不用模組層全域變數）。"""
    index = by_id(sessions)

    @tool
    def search_sessions(
        query: str,
        top_k: int = 6,
        day: str = "",
        track: str = "",
        event_type: str = "",
    ) -> str:
        """Find SFF 2026 sessions by topic or interest, in English or Chinese.

        Use this for open-ended questions like "anything about stablecoins?" or
        「我對 AI 監理有興趣」. Pass the user's own wording as the query.
        Optional filters: day ("1"/"2"/"3" or "2026-11-19"), track (e.g. "Policy"),
        event_type ("Open" or "Premium").
        Returns one line per session, each starting with its session_id.
        """
        picked = resolve_day(day, sessions) if day else None
        if day and picked is None:
            return _NOT_A_DAY.format(value=day)

        def keep(doc: Document) -> bool:
            return matches(
                index[doc.metadata["session_id"]],
                day=picked,
                track=track,
                event_type=event_type,
            )

        docs = store.similarity_search(query, k=max(1, min(top_k, MAX_TOP_K)), filter=keep)
        if not docs:
            return "No sessions matched. Try a broader query, or drop the day/track filters."

        lines = []
        for doc in docs:
            s = index[doc.metadata["session_id"]]
            lines.append(format_line(s))
            snippet = _oneline(s.description or s.description_zh)
            if snippet:
                clipped = snippet[:SNIPPET_CHARS]
                lines.append(f"    {clipped}{'…' if len(snippet) > SNIPPET_CHARS else ''}")
        return "\n".join(lines)

    @tool
    def list_sessions(
        day: str = "",
        track: str = "",
        stage: str = "",
        event_type: str = "",
    ) -> str:
        """List ALL sessions matching exact filters, without semantic search.

        Use this for complete lists and counts: everything on a day, every session on a
        stage, every session in a track, or "how many ...". Prefer this over
        search_sessions whenever the user wants a full list or a number, because
        search_sessions only returns the closest few matches.
        Leaving every filter empty lists the whole agenda.
        """
        picked = resolve_day(day, sessions) if day else None
        if day and picked is None:
            return _NOT_A_DAY.format(value=day)

        hits = [
            s
            for s in sessions
            if matches(s, day=picked, track=track, stage=stage, event_type=event_type)
        ]
        if not hits:
            return "No sessions matched those filters."

        lines = [f"{len(hits)} session(s) matched."]
        lines += [format_line(s) for s in hits[:LIST_LIMIT]]
        if len(hits) > LIST_LIMIT:
            lines.append(f"…and {len(hits) - LIST_LIMIT} more — narrow with day/track/stage.")
        return "\n".join(lines)

    @tool
    def get_session(session_id: str) -> str:
        """Get the full details of one session by its ID (e.g. "AGND441").

        Returns the complete English and Chinese descriptions plus the official URL.
        Use this before explaining a session in depth.
        """
        s = index.get((session_id or "").strip().upper())
        if s is None:
            return (
                f"No session with id '{session_id}'. "
                "Use search_sessions or list_sessions to find the correct id."
            )
        return format_full(s)

    return [search_sessions, list_sessions, get_session]
