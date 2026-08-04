"""ASGI 進入點 —— 給 uvicorn / gunicorn 直接掛載的那個 `app`。

    uvicorn agent.main:app --host 0.0.0.0 --port 8765      # 在 repo 裡
    uvicorn main:app --host 0.0.0.0 --port 8765            # 這幾支被攤平拉出來時
    gunicorn -k uvicorn.workers.UvicornWorker main:app -b 0.0.0.0:8765

只是想在本機跑起來的話用 `python3 -m agent.api`：同一個 app，還多了 `--host` /
`--port` / `--csv` / `--rebuild-index`。這一支存在的理由只有一個 —— 部署工具要的是
「模組裡有一個叫 app 的物件」，不是一個會自己 `uvicorn.run()` 的 `main()`。

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

if __package__:  # 見 rag.py 的說明
    from .api import create_app
else:
    from api import create_app

# 在 import 時就把 agent 建好：第一個請求不用等建索引，而且金鑰沒設會在啟動當下就失敗，
# 而不是等到有人來問才炸。uvicorn 的 --reload 每次重載都會重跑這一行，索引有快取所以很快。
app = create_app(rebuild=os.getenv("SFF_AGENT_REBUILD") == "1")
