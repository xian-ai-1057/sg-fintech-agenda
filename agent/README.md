# 議程問答 Agent

用自然語言問 **SFF 2026** 的議程：「我對穩定幣有興趣，有哪些場次？」「AGND329 在講什麼？」
中英文都可以，它會**用你提問的語言回答**。

資料來源就是上一層的 `agenda.csv`，**唯讀**，不會去動它，也不會動 `index.html`。

```
agenda.csv ──► agent/（LangChain + OpenAI，語意檢索）──► CLI / Gradio / HTTP API
                                                                        │
                                            index.html 的對話框 ────────┘
```

> `index.html` 裡本來就有一個「小幫手 Bot」，那是**純前端關鍵字比對**、零相依、可離線。
> 這支 Agent 需要 API key，但看得懂語意。兩者並存：`api.py` 有跑，網頁就用 Agent 回答；
> 沒跑就退回關鍵字，網頁單獨開、放在 GitHub Pages 上都照常能用。

## 安裝

```bash
python3 -m pip install -r agent/requirements.txt
cp agent/.env.example agent/.env      # 然後填入 OPENAI_API_KEY
```

`agent/.env` 與向量索引快取都在 `.gitignore` 裡，不會進版控。

## 用法

```bash
# 互動模式（/exit 離開、/reset 開新對話）
python3 -m agent.cli

# 問一次就結束
python3 -m agent.cli "有哪些跟穩定幣和代幣化資產有關的場次？"

# 最小 Web UI，然後開 http://127.0.0.1:7860
python3 -m agent.web

# HTTP API，給 index.html 的對話框用（見下）
python3 -m agent.api
```

其他選項：

| 參數 | 用途 |
|---|---|
| `--user alice` | 對話識別。不同人各自一份記憶（需求 4 的接縫） |
| `--rebuild-index` | 強制重建向量索引 |
| `--csv path.csv` | 換一份議程資料，測試用 |

## 接到議程網頁的對話框

`index.html` 右下角的小幫手可以直接用這支 Agent 回答。開兩個終端機：

```bash
python3 -m agent.api           # Agent，預設 http://127.0.0.1:8765
python3 -m http.server 8000    # 議程網頁，然後開 http://localhost:8000/
```

網頁開啟對話框時會先打 `GET /health`：通了就切成 **AI 模式**（標題列膠囊鈕顯示 `AI`），
提問改走 `POST /ask`；沒通、或問到一半失敗，就自動退回內建的關鍵字比對，不會開天窗。
膠囊鈕可以手動切回關鍵字模式，選擇記在 `localStorage`。

回答裡的場次編號（`AGND441`）會變成可點的連結，點了開網頁本來就有的場次詳情 ——
system prompt 已經要求每一場都要附編號，所以不用另外設計一套回傳格式。

| 端點 | 進 | 出 |
|---|---|---|
| `GET /health` | — | `{"ok": true, "status": "已載入 107 場議程 …"}` |
| `POST /ask` | `{"question": "…", "thread_id": "…"}` | `{"answer": "…", "thread_id": "…"}` |

| 設定 | 預設 | 說明 |
|---|---|---|
| `--host` / `--port` | `127.0.0.1` / `8765` | 要讓同網段的手機連得到就 `--host 0.0.0.0` |
| `SFF_AGENT_ORIGINS` | 空 | 允許連進來的網頁來源，逗號分隔。**本機來源預設就通** |
| 網頁端 `?agent=<url>` | 本機 `http://127.0.0.1:8765` | Agent 跑在別台機器時用，會記在 `localStorage` |

Agent 每個瀏覽器帶一組 `thread_id`（清除對話就換一組），對應到 `ask()` 的同一個參數 ——
跟 CLI 的 `--user`、Gradio 的名字欄位是同一條通道。

**API key 只留在跑 `api.py` 的這一側**，前端拿到的永遠只有問答文字。線上版
（GitHub Pages）沒有後端，網頁就維持純關鍵字模式。

### 用 uvicorn／gunicorn 部署

`main.py` 就是 ASGI 進入點，裡面只有一個模組層的 `app`：

```bash
uvicorn agent.main:app --host 0.0.0.0 --port 8765          # 在 repo 裡
uvicorn main:app --host 0.0.0.0 --port 8765                # 這幾支被攤平拉出來時
gunicorn -k uvicorn.workers.UvicornWorker main:app -b 0.0.0.0:8765
```

`python3 -m agent.api` 是同一個 app，只是自己開 server，適合本機隨手跑；
要交給程序管理器（systemd、Docker、supervisor）就用上面這種掛載法。

> **workers 維持 1。** 每個 worker 會各自載一份議程與向量索引，而且對話記憶走行程內的
> `InMemorySaver` —— 多開 worker 會讓同一個 `thread_id` 的上下文隨機掉在不同 worker 上。
> 要橫向擴展得先把 checkpointer 換成外部儲存（`core.build_agent()` 裡的一行）。

部署時設定一律走環境變數（讀 `agent/.env`，或由部署環境直接注入）：

| 變數 | 用途 |
|---|---|
| `OPENAI_API_KEY` / `OPENAI_MODEL` / `OPENAI_EMBED_MODEL` / `OPENAI_BASE_URL` | 模型與端點 |
| `SFF_AGENT_ORIGINS` | 允許連進來的網頁來源，逗號分隔（本機來源預設就通） |
| `SFF_AGENDA_CSV` | 議程 CSV 不在預設位置時指過去 |
| `SFF_AGENT_REBUILD=1` | 強制重建向量索引（等同 CLI 的 `--rebuild-index`） |

agent 在 **import 時就建好**，所以第一個請求不用等建索引，金鑰沒設也會在啟動當下就失敗
——而不是等到有人來問才炸。向量索引寫在 `agent/.index/`，容器化時記得把這一層掛成
volume，否則每次重啟都要重新 embedding 一次。

### 把這幾支程式單獨拉出來用

不一定要留在這個 repo 裡。把 `.py`、`requirements.txt`、`.env.example` 連同
**`agenda.csv`** 一起複製到任何一個資料夾（不需要 `__init__.py`），就能直接跑：

```bash
python -m cli               # 或 python cli.py
python -m web
python -m api               # 或 uvicorn main:app --host 0.0.0.0 --port 8765
```

`agenda.csv` 會**先找程式同一層、再找上一層**；都不在的話用 `--csv` 指路徑，
或設 `SFF_AGENDA_CSV` 環境變數。`.env` 一律讀程式同一層的那份。

模型與端點都走環境變數，寫在 `agent/.env`：

| 變數 | 預設 | 說明 |
|---|---|---|
| `OPENAI_MODEL` | `gpt-5.6-terra` | |
| `OPENAI_EMBED_MODEL` | `text-embedding-3-small` | |
| `OPENAI_BASE_URL` | OpenAI 官方 | OpenAI 相容端點，chat 與 embedding 共用 |

> OpenAI 的 model id 會換。遇到 `model not found` 就把 `OPENAI_MODEL` 改成你帳號可用的。

要接自架的 vLLM 或公司內部閘道，就這三行：

```bash
OPENAI_BASE_URL=http://<你的主機>:<port>/v1
OPENAI_MODEL=<該端點上的 chat model>
OPENAI_EMBED_MODEL=<該端點上的 embedding model>
```

這種端點多半不驗金鑰，`OPENAI_API_KEY` 隨便填一個非空字串即可（例如 `EMPTY`）——
程式在做任何事之前會先檢查它有沒有設，免得跑到一半才跳 401。端點也算進索引指紋，
所以換端點會自動重建向量索引：同一個模型名稱在不同服務上吐的向量並不通用。

## 它怎麼運作

一場議程 = 一份 Document，**不切塊**（最長的一場也才約 400 tokens），英文與中文說明
放在同一份裡去 embed —— `text-embedding-3-*` 本身跨語言，所以中文提問一樣找得到，
而且「top-k 份文件」永遠等於「top-k 場議程」，不用去重。

Agent 有三個工具：

| 工具 | 什麼時候用 |
|---|---|
| `search_sessions` | 主題／興趣類的問題（語意搜尋）。可加日期、track、票種篩選 |
| `list_sessions` | 要**完整清單或數量**時（「第二天全部」「Impact Stage 有幾場」）。語意搜尋會因為 top-k 截斷而少報，這種問題一定要走這個 |
| `get_session` | 已經知道 session id，要完整說明時 |

全場統計（三天各幾場、各 track 幾場、有哪些舞台）是啟動時從 CSV 算出來塞進 system
prompt 的，所以不用為了這種問題多跑一輪工具，**換年份、換 CSV 也不必改 prompt**。

**向量索引快取**在 `agent/.index/index-<指紋>.json`。指紋涵蓋 CSV 內容、embedding 模型
與樣板版本，而且寫在檔名裡 —— CSV 一改就自動重建，換 embedding 模型也不可能載到維度
不相容的舊索引。

## 成本

建索引是一次性的 107 次 embedding，約 27k tokens（`text-embedding-3-small` 大概
US$0.0005），之後都走本機快取。每次提問是一次本機相似度搜尋（numpy，零成本）加上
一次 LLM 呼叫。工具輸出刻意壓過：搜尋結果每筆只附 150 字元摘要，清單最多 60 行。

## 目前做到哪、之後怎麼接

已完成**需求 1（依主題找場次）**與**需求 2（介紹活動內容）**。
需求 3（記錄想去的活動 + 衝堂提醒）與需求 4（多人互相看到彼此的行程）**還沒做**，
但資料模型已經先做對了，之後是「加檔案」而不是「改結構」：

1. **`Session.session_id`（`AGND441`）穩定唯一** → 行程表只要存 `(user_id, session_id)`，
   靠 `by_id()` 換回完整資料。
2. **`start` / `end` 載入時就已經是 `datetime`** → 衝堂偵測是一行：
   `a.start < b.end and b.start < a.end`。不必重新解析字串。
3. **`ask()` 已經吃 `thread_id`**，CLI 有 `--user`、Web 有名字欄位 →「誰是誰」的通道
   已經打通。想要跨重啟的對話記憶，把 `InMemorySaver` 換成 `SqliteSaver` 即可。
4. 需求 3 = 新增一個 `agent/store.py`（一個 JSON 檔，`add_pick` / `remove_pick` /
   `list_picks`）＋ 在 `tools.py` 加三個工具。需求 4 幾乎是免費的 ——
   `list_picks(user_id=None)` 讀同一個共享檔就是「大家的行程」。

現有三個工具維持對 `agenda.csv` 唯讀，RAG 層永遠不需要知道使用者是誰。

## 為什麼用 `create_agent` 而不是 `deepagents`

評估過（讀 `deepagents 0.7.3` 的原始碼），對這個工具**不划算**：

- 它**無條件**塞進 `ls`、`read_file`、`write_file`、`edit_file`、`glob`、`grep`、
  `execute`、`task` 八個工具，而且官方 docstring 明寫傳 `tools=` 是**附加、不會移除
  內建工具**，要拿掉得另外註冊 `HarnessProfile`。我們只需要三個唯讀工具。
- 每輪多付約 1.5k–3k tokens 的內建 prompt 與工具 schema。
- 相依包含 `langchain-anthropic` 與 `langchain-google-genai` —— 一個只用 OpenAI 的
  小工具會被裝進 Anthropic 與 Google 的 SDK。
- LangChain 官方自己的建議就是：*"For simple Q&A or single-tool tasks, a basic agent
  is fine. Deep agents shine when the task feels more like a project than a question."*

如果之後真的長成「幫我排完三天的完整行程」那種專案型任務，換過去很便宜：同一組
`@tool`、同一個 model 字串、同一個 checkpointer，只是把 `core.py` 裡的
`create_agent(...)` 換成 `create_deep_agent(...)`。這也是為什麼建立 agent 的程式
全部收在 `build_agent()` 一個函式裡。

## 檔案

每個模組匯入相鄰模組時都會先看 `__package__`：在 repo 裡走相對匯入
（`from .agenda import …`），被攤平拉出來時走絕對匯入（`from agenda import …`），
所以兩種擺法都不用改程式。

| 檔案 | 責任 |
|---|---|
| `agenda.py` | 讀 CSV → `Session`；篩選、日期解析、指紋。**沒有任何 LLM import**，不用 API key 就能單獨驗 |
| `rag.py` | Document 組裝 + 向量庫建置／快取。唯一碰 embedding 的檔 |
| `tools.py` | 三個 `@tool` 與它們的輸出格式 |
| `core.py` | system prompt、`build_agent()`、`ask()` |
| `cli.py` | 終端機介面 |
| `web.py` | Gradio Web UI |
| `api.py` | HTTP API（FastAPI），`index.html` 的對話框接這一支 |
| `main.py` | ASGI 進入點，`uvicorn main:app` 掛的那個 `app` |

資料層可以不花錢單獨驗證：

```bash
python3 -c "from agent.agenda import load_sessions, by_id; s=load_sessions(); \
print(len(s), by_id(s)['AGND441'].start, by_id(s)['AGND441'].end)"
# 107 2026-11-18 09:15:00 2026-11-18 09:30:00
```
