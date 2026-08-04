"""向量索引：一場議程一份 Document，建好後存成本機快取。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from langchain_core.documents import Document
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_openai import OpenAIEmbeddings

# 在 repo 裡是 agent 這個 package（python3 -m agent.cli）；把這幾支 .py 單獨拉出來
# 攤平放時就沒有 package 了（python -m cli），兩種都要能跑。
if __package__:
    from .agenda import CSV_PATH, Session, csv_fingerprint
else:
    from agenda import CSV_PATH, Session, csv_fingerprint

INDEX_DIR = Path(__file__).resolve().parent / ".index"
DEFAULT_EMBED_MODEL = "text-embedding-3-small"

# document_of() 的樣板一改就 +1，讓舊索引自動失效。
SCHEMA_VERSION = 1


def embed_model_name() -> str:
    return os.getenv("OPENAI_EMBED_MODEL", DEFAULT_EMBED_MODEL)


def document_of(s: Session) -> Document:
    """一場議程 = 一份 Document，中英文說明放在同一份裡。

    text-embedding-3-* 本身跨語言，把兩種語言放進同一個向量，中文提問會靠近中文段、
    英文提問靠近英文段，而且「top-k 份文件」永遠等於「top-k 場議程」，不必去重。
    說明最長的一場也才約 400 tokens，所以不需要切塊（text splitter）。
    """
    meta = (
        f"{s.session_id} | Day {s.day_index} {s.day_label} "
        f"{s.start:%H:%M}-{s.end:%H:%M} | {s.stage or 'TBA'} | {s.location or 'TBA'} | "
        f"{s.track} | {s.event_type or 'TBA'}"
    )
    parts = [s.title, meta, s.description or "(No description published yet.)"]
    if s.description_zh:
        parts.append(f"中文說明：{s.description_zh}")

    # metadata 的值必須全是 JSON scalar —— .dump() 走 json.dump，放 date/datetime 會炸。
    # 只存 session_id，其餘欄位一律回 Session 物件去查，避免兩份資料走鐘。
    return Document(page_content="\n".join(parts), metadata={"session_id": s.session_id})


def _index_path(csv_path: Path | str) -> Path:
    """索引檔名帶指紋，所以失效判斷就只是「這個檔在不在」。

    指紋涵蓋 CSV 內容、embedding 模型與樣板版本：CSV 一改會重建，換成
    text-embedding-3-large（維度不同）也不可能載到不相容的舊索引。
    """
    seed = f"{csv_fingerprint(csv_path)}|{embed_model_name()}|{SCHEMA_VERSION}"
    return INDEX_DIR / f"index-{hashlib.sha256(seed.encode()).hexdigest()[:12]}.json"


def get_store(
    sessions: list[Session],
    *,
    csv_path: Path | str = CSV_PATH,
    rebuild: bool = False,
) -> tuple[InMemoryVectorStore, bool]:
    """建立或載入向量庫。回傳 (store, 是否命中快取)。"""
    embeddings = OpenAIEmbeddings(model=embed_model_name())
    path = _index_path(csv_path)

    if path.exists() and not rebuild:
        return InMemoryVectorStore.load(str(path), embeddings), True

    store = InMemoryVectorStore.from_documents([document_of(s) for s in sessions], embeddings)
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    store.dump(str(path))
    for stale in INDEX_DIR.glob("index-*.json"):  # 只留當前這一份
        if stale != path:
            stale.unlink()
    return store, False
