"""議程網站的 ASGI 進入點 —— 用 uvicorn 部署 index.html 與 agenda.csv。

    uvicorn serve:app --host 0.0.0.0 --port 8000        # 然後開 http://<主機>:8000/
    gunicorn -k uvicorn.workers.UvicornWorker serve:app -b 0.0.0.0:8000

本機隨手看看用 `python3 -m http.server 8000` 就夠了；這一支是要交給程序管理器
（systemd、Docker、supervisor）或跟議程問答 agent 擺在同一個 uvicorn 底下時用的。

**預設只發靜態檔案**，跟 agent 完全無關 —— 不需要 langchain，也不需要 API key。
把 `SFF_SERVE_AGENT=1` 打開，才會順便把 agent 掛在 `/agent` 底下：

    SFF_SERVE_AGENT=1 uvicorn serve:app --host 0.0.0.0 --port 8000

這時候網頁與 agent 同源，對話框自己就找得到 `/agent`，不必加 `?agent=`，也沒有 CORS
的事（agent 的相依、金鑰與 workers 只能開 1 的理由見 `agent/README.md`）。

相依只有 starlette 與 uvicorn：

    python3 -m pip install "uvicorn[standard]" starlette

（裝過 `agent/requirements.txt` 的話兩個都已經有了。爬蟲的 `requirements.txt` 不受影響。）
"""

from __future__ import annotations

import os
from pathlib import PurePosixPath

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

ROOT = os.path.dirname(os.path.abspath(__file__))


class SiteFiles(StaticFiles):
    """發整個 repo 目錄，但擋掉點開頭的路徑。

    GitHub Pages 發的是版控裡的東西，`agent/.env` 與 `agent/.index/` 根本不在裡面；
    這一支發的卻是你**工作目錄**的檔案，所以金鑰會不會外流全看這道關卡。`.git/` 同理。
    """

    async def get_response(self, path: str, scope):
        if any(part.startswith(".") for part in PurePosixPath(path).parts):
            raise HTTPException(status_code=404)
        return await super().get_response(path, scope)


routes = []

if os.getenv("SFF_SERVE_AGENT") == "1":
    # 只在需要時才 import：純靜態站不該為了 agent 去扛 langchain 那一整包相依。
    from agent.api import create_app

    routes.append(Mount("/agent", app=create_app()))

# 靜態檔案掛最後 —— Mount 是照順序比對的，"/" 會接走前面沒被吃掉的所有路徑。
# html=True 讓 "/" 直接出 index.html。
routes.append(Mount("/", app=SiteFiles(directory=ROOT, html=True)))

app = Starlette(routes=routes)
