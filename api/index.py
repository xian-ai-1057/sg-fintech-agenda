"""Vercel 上的共編 API：/api/* 都改寫到這裡（見 vercel.json）。

和自架版（python3 -m team.server）共用同一個 Handler 與 Store 邏輯，差別只在：
- 資料庫檔存在私有 Vercel Blob（team/blobstore.py），因為 Function 沒有持久磁碟；
- 管理員密碼第一次由環境變數 SFF_ADMIN_PASSWORD 建立，之後可在網頁上更改；
- Host 由 Vercel 決定，只檢查 Origin 必須與 Host 同站；
- 速率限制以 x-real-ip 計算，只在同一個執行個體內有效。
靜態頁面（/、/team/、agenda.csv）由 Vercel 直接提供，不經過這裡。
"""
import os
import secrets
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from team import assistant  # noqa: E402
from team.server import MIN_PASSWORD, Handler, Problem, agenda  # noqa: E402


class App:
    def __init__(self):
        self.agenda = agenda()
        self.ids = {s['id'] for s in self.agenda}
        self.agent_url = os.environ.get('SFF_AGENT_URL', assistant.DEFAULT_AGENT_URL).rstrip('/')
        self.instance = secrets.token_hex(16)
        self.attempts = {}
        self.rate_lock = threading.Lock()
        self._store = None
        self._store_lock = threading.Lock()

    @property
    def store(self):
        """第一次用到才連 Blob；資料庫還沒有管理員時，用 SFF_ADMIN_PASSWORD 建立。"""
        with self._store_lock:
            if self._store is None:
                from team.blobstore import BlobStore
                try:
                    store = BlobStore()
                except RuntimeError as exc:
                    raise Problem(503, str(exc))
                if not store.is_setup():
                    password = os.environ.get('SFF_ADMIN_PASSWORD', '')
                    if len(password) < MIN_PASSWORD:
                        raise Problem(503, f'尚未設定管理員：請在 Vercel 環境變數設定 SFF_ADMIN_PASSWORD（至少 {MIN_PASSWORD} 字元）後重新部署。')
                    try:
                        store.setup(password)
                    except ValueError:
                        pass  # 另一個執行個體剛好先建立了
                self._store = store
            return self._store

    def check_host(self, host, origin):
        if origin and (urlsplit(origin).netloc != host or urlsplit(origin).scheme != 'https'):
            raise Problem(403, '不允許跨網站操作。')

    def client_ip(self, handler):
        forwarded = handler.headers.get('x-forwarded-for', '').split(',')[0].strip()
        return handler.headers.get('x-real-ip') or forwarded or handler.client_address[0]


APP = App()


class handler(Handler):
    @property
    def app(self):
        return APP

    def setup(self):
        super(Handler, self).setup()  # Vercel 自己管理連線逾時
