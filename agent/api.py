"""SFF 2026 議程問答 —— HTTP 介面，給 index.html 的對話框用。

    python3 -m agent.api                    起在 http://127.0.0.1:8765
    python3 -m agent.api --port 9000        換連接埠
    python3 -m agent.api --host 0.0.0.0     讓同一個網段的手機也連得到

兩個端點，都吃 / 回 JSON：

    GET  /health                                   -> {"ok": true, "status": "已載入 107 場議程 …"}
    POST /ask  {"question": "…", "thread_id": "…"} -> {"answer": "…", "thread_id": "…"}

跟 cli.py / web.py 一樣只是 core.py 的薄殼：agent 在啟動時就建好（索引也一起），
之後每個請求共用同一個 —— 三個工具都對 CSV 唯讀，對話則靠 thread_id 分開。

API key 只留在伺服器這一側。網頁拿到的永遠只有問答文字，不會碰到金鑰。
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

if __package__:  # 見 rag.py 的說明
    from .agenda import CSV_PATH
    from .core import ask, build_agent, load_env, require_api_key
else:
    from agenda import CSV_PATH
    from core import ask, build_agent, load_env, require_api_key

DEFAULT_PORT = 8765

# 預設只信任本機開的網頁（python3 -m http.server 8000 / file:// 之外的來源）。
# 要讓別的網域連進來（例如 GitHub Pages 上的那份 index.html），設：
#     SFF_AGENT_ORIGINS="https://xian-ai-1057.github.io"
LOCAL_ORIGIN_RE = r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"


class AskIn(BaseModel):
    question: str
    thread_id: str = "web"


class AskOut(BaseModel):
    answer: str
    thread_id: str


def allowed_origins() -> list[str]:
    raw = os.getenv("SFF_AGENT_ORIGINS", "")
    return [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]


def create_app(*, csv_path: Path | str = CSV_PATH, rebuild: bool = False) -> FastAPI:
    """建好 agent 再回傳 app —— 起動慢一點，換第一個問題不用等建索引。"""
    load_env()
    require_api_key()
    agent, status = build_agent(csv_path=csv_path, rebuild=rebuild)
    print(status)

    app = FastAPI(title="SFF 2026 agenda agent", version="1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins(),
        allow_origin_regex=LOCAL_ORIGIN_RE,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.get("/health")
    def health() -> dict:
        """網頁開啟對話框時先打這一支，決定要顯示 AI 模式還是關鍵字模式。"""
        return {"ok": True, "status": status}

    @app.post("/ask", response_model=AskOut)
    def ask_endpoint(body: AskIn) -> AskOut:
        question = (body.question or "").strip()
        if not question:
            raise HTTPException(status_code=400, detail="question is empty")

        # 一個 thread_id 一段對話記憶。網頁每個瀏覽器自己帶一組（存在 localStorage），
        # 這也是之後「多人互相看到彼此行程」要接上去的同一條通道。
        thread_id = (body.thread_id or "web").strip() or "web"
        try:
            answer = ask(agent, question, thread_id)
        except Exception as exc:  # 模型 / 網路出事就回 502，讓前端能退回關鍵字模式
            raise HTTPException(status_code=502, detail=f"agent failed: {exc}") from exc
        return AskOut(answer=answer, thread_id=thread_id)

    return app


def main() -> int:
    ap = argparse.ArgumentParser(description="Serve the SFF 2026 agenda agent over HTTP.")
    ap.add_argument("--host", default="127.0.0.1", help="預設只綁本機")
    ap.add_argument("--port", type=int, default=int(os.getenv("SFF_AGENT_PORT", DEFAULT_PORT)))
    ap.add_argument("--csv", default=CSV_PATH, help=f"議程 CSV（預設 {CSV_PATH}）")
    ap.add_argument("--rebuild-index", action="store_true", help="強制重建向量索引")
    args = ap.parse_args()

    import uvicorn

    app = create_app(csv_path=args.csv, rebuild=args.rebuild_index)
    print(f"議程 Agent API：http://{args.host}:{args.port}  （網頁用 ?agent= 指過來）")
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
