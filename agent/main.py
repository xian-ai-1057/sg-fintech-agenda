"""本機完整應用的 ASGI 進入點。

    uvicorn agent.main:app --host 127.0.0.1 --port 8765    # 只供本機使用
    uvicorn agent.main:app --host 0.0.0.0 --port 8765      # 同網路的手機也可開啟

這個 app 同時提供前端與 Agent API：

    GET  /                 index.html（桌機／響應式手機介面）
    GET  /agenda.csv       前端議程資料，與 Agent 使用同一份 CSV
    GET  /mobile-preview   手機外框預覽
    GET  /health           Agent 狀態
    POST /ask              Agent 問答

`python3 -m agent.api` 仍是 API-only 的開發入口，不提供前端檔案。

**workers 請維持 1。** 每個 worker 都會各自載一份議程與向量索引（記憶體翻倍），而且
對話記憶走的是行程內的 `InMemorySaver` —— 多開 worker 會讓同一個 `thread_id` 的上下文
隨機掉在不同 worker 上。要橫向擴展就得先把 checkpointer 換成外部儲存（例如
`SqliteSaver`），那是 `core.build_agent()` 裡的一行。

設定一律走環境變數，讀 `agent/.env`，也可以由部署環境直接注入：

    OPENAI_API_KEY / OPENAI_MODEL / OPENAI_EMBED_MODEL / OPENAI_BASE_URL
    SFF_AGENT_ORIGINS   允許連進來的網頁來源（本機來源預設就通）
    SFF_AGENDA_CSV      議程 CSV 不在預設位置時指過去
    SFF_AGENT_REBUILD   設成 1 就強制重建向量索引（等同 CLI 的 --rebuild-index）
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import HTTPException
from fastapi.responses import FileResponse


MODULE_DIR = Path(__file__).resolve().parent

# 必須在 import api/agenda 之前載入，SFF_AGENDA_CSV 才能影響 agenda.CSV_PATH；
# SFF_AGENT_REBUILD 也能直接寫在 agent/.env，而不必另外 export 到 shell。
load_dotenv(MODULE_DIR / ".env")

if __package__:  # 見 rag.py 的說明
    from .agenda import CSV_PATH
    from .api import create_app
else:
    from agenda import CSV_PATH
    from api import create_app

# 在 import 時就把 agent 建好：第一個請求不用等建索引，而且金鑰沒設會在啟動當下就失敗，
# 而不是等到有人來問才炸。uvicorn 的 --reload 每次重載都會重跑這一行，索引有快取所以很快。
app = create_app(rebuild=os.getenv("SFF_AGENT_REBUILD") == "1")


def _frontend_file(name: str) -> Path:
    """支援 repo package 與把 agent 檔案攤平後的兩種目錄配置。"""
    roots = (MODULE_DIR.parent, MODULE_DIR) if __package__ else (MODULE_DIR, MODULE_DIR.parent)
    for root in roots:
        candidate = root / name
        if candidate.is_file():
            return candidate
    raise HTTPException(status_code=404, detail=f"frontend file not found: {name}")


def _file_response(path: Path) -> FileResponse:
    return FileResponse(path, headers={"Cache-Control": "no-cache"})


@app.get("/", include_in_schema=False)
def frontend_index() -> FileResponse:
    return _file_response(_frontend_file("index.html"))


@app.get("/index.html", include_in_schema=False)
def frontend_index_file() -> FileResponse:
    return _file_response(_frontend_file("index.html"))


@app.get("/agenda.csv", include_in_schema=False)
def frontend_agenda() -> FileResponse:
    path = Path(CSV_PATH)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"agenda CSV not found: {path}")
    return _file_response(path)


@app.get("/mobile-preview", include_in_schema=False)
@app.get("/Mobile Preview.html", include_in_schema=False)
def mobile_preview() -> FileResponse:
    return _file_response(_frontend_file("Mobile Preview.html"))
