"""Vercel 版的共編儲存：同一個 SQLite 資料庫檔，放在私有的 Vercel Blob。

Vercel Function 沒有持久磁碟，所以每次處理請求時：

1. 把 Blob 上的資料庫檔拉到 /tmp（帶 If-None-Match，沒變就不重新下載；
   唯讀請求在 READ_TTL 秒內直接用本機副本，省 Blob 讀取次數）；
2. 照自架版完全相同的 Store 邏輯（同一份 SQL、同一套權限檢查）執行；
3. 檔案內容有變才上傳，並帶 x-if-match（上次讀到的 ETag）。
   有別的執行個體搶先寫入就會被拒（412），此時丟掉本機副本、重新拉、整段重做。

所以並行寫入不會互相覆蓋，代價是寫入多一次往返。只用標準函式庫。
需要環境變數 BLOB_READ_WRITE_TOKEN（在 Vercel 專案連上 Blob store 時自動提供）。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

from .server import Store

API_URL = (os.environ.get('VERCEL_BLOB_API_URL') or 'https://vercel.com/api/blob').rstrip('/')
API_VERSION = '12'
READ_TTL = 15          # 秒：唯讀請求可接受的最舊本機副本
ATTEMPTS = 5


class BlobStore(Store):
    def __init__(self, pathname='team/calendar.sqlite3', token=None, local=None, read_ttl=READ_TTL):
        self.blob_token = (token or os.environ.get('BLOB_READ_WRITE_TOKEN') or '').strip()
        match = re.fullmatch(r'vercel_blob_rw_([A-Za-z0-9]+)_.+', self.blob_token)
        if not match:
            raise RuntimeError('缺少或格式不正確的 BLOB_READ_WRITE_TOKEN：請在 Vercel 專案連上 Blob store。')
        self.store_id = match.group(1).lower()
        self.pathname = pathname
        self.read_ttl = read_ttl
        self.etag = None
        self.pulled = 0.0
        self.lock = threading.RLock()
        # 不呼叫 Store.__init__：本機檔要等第一次從 Blob 拉下來才建立
        self.path = Path(local or Path(tempfile.gettempdir()) / 'sff-team' / 'calendar.sqlite3')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.wal = False

    # ---- Blob HTTP --------------------------------------------------------
    def _http(self, method, url, data=None, headers=None):
        request = urllib.request.Request(url, data=data, method=method, headers={
            'authorization': 'Bearer ' + self.blob_token, **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers, error.read()
        except urllib.error.URLError as error:
            raise OSError(f'Blob 連線失敗：{error.reason}') from None

    def pull(self, force=False):
        if not force and self.path.exists() and time.time() - self.pulled < self.read_ttl:
            return
        url = f'https://{self.store_id}.private.blob.vercel-storage.com/{quote(self.pathname)}?cache=0'
        headers = {'If-None-Match': self.etag} if self.etag and self.path.exists() else {}
        status, response_headers, body = self._http('GET', url, headers=headers)
        if status == 200:
            temporary = self.path.with_suffix('.download')
            temporary.write_bytes(body)
            os.replace(temporary, self.path)
            self.etag = response_headers.get('ETag')
        elif status == 404:       # 第一次使用：還沒有資料庫
            self.path.unlink(missing_ok=True)
            self.etag = None
        elif status != 304:
            raise OSError(f'Blob 讀取失敗（HTTP {status}）')
        self.pulled = time.time()
        self.migrate()

    def push(self, data):
        """上傳成功回傳 True；別人先寫入（ETag 不符）回傳 False。"""
        headers = {
            'x-api-version': API_VERSION,
            'x-vercel-blob-access': 'private',
            'x-content-type': 'application/vnd.sqlite3',
            'x-add-random-suffix': '0',
            'content-type': 'application/octet-stream',
        }
        if self.etag:
            headers.update({'x-allow-overwrite': '1', 'x-if-match': self.etag})
        else:
            headers['x-allow-overwrite'] = '0'   # 只在還不存在時建立
        status, _, body = self._http('PUT', f'{API_URL}/?pathname={quote(self.pathname)}', data, headers)
        if status == 200:
            try:
                self.etag = json.loads(body).get('etag') or None
            except ValueError:
                self.etag = None
            if not self.etag:      # 沒拿到 ETag：下次強制重新下載
                self.pulled = 0.0
            return True
        if status in (400, 409, 412):
            return False
        raise OSError(f'Blob 寫入失敗（HTTP {status}）')

    # ---- 包住 Store 的每個進入點 ------------------------------------------
    def _run(self, action, fresh):
        with self.lock:
            for attempt in range(ATTEMPTS):
                self.pull(force=fresh or attempt > 0)
                before = self.path.read_bytes() if self.path.exists() else b''
                result = action()
                after = self.path.read_bytes()
                if after == before or self.push(after):
                    return result
                # 別的執行個體先寫入：丟掉這次的本機變更，重拉後整段重做
                self.path.unlink(missing_ok=True)
                self.etag = None
                time.sleep(0.05 * (attempt + 1))
            raise OSError('Blob 寫入衝突次數過多')

    def request(self, method, route, data, token, valid_ids):
        return self._run(lambda: Store.request(self, method, route, data, token, valid_ids), method == 'POST')

    def member(self, token):
        return self._run(lambda: Store.member(self, token), False)

    def setup(self, password):
        return self._run(lambda: Store.setup(self, password), True)

    def is_setup(self):
        return self._run(lambda: Store.is_setup(self), True)

    def import_picks(self, rows, valid_ids):
        return self._run(lambda: Store.import_picks(self, rows, valid_ids), True)
