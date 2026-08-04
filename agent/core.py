"""建立 agent —— CLI 與 Web UI 共用這一層。

刻意把「怎麼組出 agent」收斂在 build_agent() 一個函式裡：cli.py 與 web.py 都只是
薄殼，之後要換框架（例如任務長成專案型、真的需要 deepagents 那套 harness）也只動這裡。
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

if __package__:  # 見 rag.py 的說明
    from .agenda import CSV_PATH, load_sessions, overview
else:
    from agenda import CSV_PATH, load_sessions, overview

DEFAULT_MODEL = "gpt-5.6-terra"

SYSTEM_PROMPT = """You are the Singapore FinTech Festival 2026 agenda assistant.

FESTIVAL FACTS
- Singapore FinTech Festival 2026 (SFF 2026), 18-20 Nov 2026, Singapore Expo.
- All times are Singapore time (SGT, UTC+8), shown in 24-hour format.
- Today is {today}. The festival has not happened yet — speak about it in the future
  tense. "today" or "tomorrow" in a question never means a festival day unless the
  user says so.

{overview}

WHAT YOU DO
1. Recommend sessions that match the topics a person cares about.
2. Explain what a session is about.
You cannot yet save someone's schedule, register them, or check clashes across a saved
plan. If asked, say so plainly in one sentence.

HOW TO ANSWER
- Answer in the user's language. If they write Traditional Chinese, reply in 繁體中文
  (never 簡體); keep session titles, stage names and track names in their original
  English.
- Always use the tools. You do not know the agenda — the tools do. Never answer an
  agenda question from memory.
    search_sessions — topic and interest questions.
    list_sessions   — complete lists and counts ("everything on Day 2",
                      "how many on Impact Stage").
    get_session     — the full description of one session whose ID you already have.
- Every session you mention must carry its ID, day, time and stage, like:
    AGND420 | Day 2 (Thu 19 Nov) 14:45-15:15 | Impact Stage — When Deposits,
    Stablecoins and Tokenized Assets All Compete
- Never invent a session, ID, time, stage or speaker. If nothing matches, say so and
  offer the closest sessions you actually found.
- The 2026 agenda has no speaker data yet. If asked who is speaking, say it is not
  published and give the official session URL.
- A few sessions have no description published yet. Say that instead of guessing.
- Recommending: 3-5 sessions is usually right, one short line each on why it fits.
  Flag Premium sessions, and mention a time clash if you happen to notice one.
- Explaining: 2-4 sentences of substance, then day, time, stage, track, pass type, URL.
- Be concise. No walls of bullets, no emoji.
- End a list answer by offering to go deeper on any session ID.
"""


ENV_PATH = Path(__file__).resolve().parent / ".env"


def load_env() -> None:
    load_dotenv(ENV_PATH)


def require_api_key() -> None:
    """在載入 CSV、建索引之前就擋下來，不要讓使用者看到 embedding 打到一半跳 401。"""
    if not os.getenv("OPENAI_API_KEY"):
        # 路徑照實印出來，程式在 repo 裡或被單獨拉出來用時都指得對。
        sys.exit(
            f"OPENAI_API_KEY 未設定 —— 請把 .env.example 複製成 {ENV_PATH} 並填入金鑰。\n"
            f"OPENAI_API_KEY not set — copy .env.example to {ENV_PATH} and fill it in."
        )


def build_agent(
    *,
    csv_path: Path | str = CSV_PATH,
    model: str | None = None,
    rebuild: bool = False,
):
    """回傳 (agent, 狀態字串)。狀態字串給 CLI / Web 開場印出來確認資料有進來。"""
    from langchain.agents import create_agent
    from langgraph.checkpoint.memory import InMemorySaver

    if __package__:
        from .rag import embed_model_name, get_store
        from .tools import build_tools
    else:
        from rag import embed_model_name, get_store
        from tools import build_tools

    model_name = model or f"openai:{os.getenv('OPENAI_MODEL', DEFAULT_MODEL)}"
    sessions = load_sessions(csv_path)
    store, cached = get_store(sessions, csv_path=csv_path, rebuild=rebuild)

    agent = create_agent(
        model=model_name,
        tools=build_tools(sessions, store),
        system_prompt=SYSTEM_PROMPT.format(
            today=date.today().isoformat(),
            overview=overview(sessions),
        ),
        checkpointer=InMemorySaver(),
    )
    status = (
        f"已載入 {len(sessions)} 場議程 · 索引：{'快取命中' if cached else '重新建立'}"
        f"（{embed_model_name()}）· model={model_name}"
    )
    return agent, status


def ask(agent, text: str, thread_id: str = "default") -> str:
    """問一句、拿一句。thread_id 決定共用哪一段對話記憶。

    需求 4（多人共看）就是從這個參數接下去：一個人一條 thread_id。
    """
    result = agent.invoke(
        {"messages": [{"role": "user", "content": text}]},
        config={"configurable": {"thread_id": thread_id}},
    )
    return result["messages"][-1].content
