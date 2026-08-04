"""SFF 2026 議程問答 —— 終端機介面。

    python3 -m agent.cli                          互動模式
    python3 -m agent.cli "有哪些穩定幣的場次？"      問一次就結束
"""

from __future__ import annotations

import argparse
import os

if __package__:  # 見 rag.py 的說明
    from .agenda import CSV_PATH
    from .core import ask, build_agent, load_env, require_api_key
else:
    from agenda import CSV_PATH
    from core import ask, build_agent, load_env, require_api_key


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Ask about the Singapore FinTech Festival 2026 agenda."
    )
    ap.add_argument("query", nargs="?", help="問一次就結束；省略則進入互動模式")
    ap.add_argument("--user", default=None, help="對話識別，不同人各自一份記憶")
    ap.add_argument("--csv", default=CSV_PATH, help=f"議程 CSV（預設 {CSV_PATH}）")
    ap.add_argument("--rebuild-index", action="store_true", help="強制重建向量索引")
    args = ap.parse_args()

    load_env()
    require_api_key()

    agent, status = build_agent(csv_path=args.csv, rebuild=args.rebuild_index)
    thread = args.user or os.getenv("USER") or "default"
    print(status)

    if args.query:
        print(ask(agent, args.query, thread))
        return 0

    print("輸入問題開始（/exit 離開、/reset 開新對話）")
    round_no = 0
    while True:
        try:
            line = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in ("/exit", "/quit"):
            return 0
        if line == "/reset":
            round_no += 1
            print("（已開始新的對話）")
            continue
        print()
        print(ask(agent, line, f"{thread}#{round_no}"))


if __name__ == "__main__":
    raise SystemExit(main())
