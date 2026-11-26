"""Run with python3 -m team.server. No third-party packages or API key required."""
from __future__ import annotations
import argparse
import csv
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from . import assistant

ROOT = Path(__file__).resolve().parent.parent
MIN_PASSWORD = 6
MIN_MEMBER_PASSWORD = 4   # 成員的「重新登入密碼」只防同事誤用，不必和管理員一樣長
SESSION_DAYS = 7
RELOGIN_LINK_DAYS = 7
SCHEMA = '''
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS members (id TEXT PRIMARY KEY, nickname TEXT NOT NULL, role TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sessions (hash TEXT PRIMARY KEY, member_id TEXT NOT NULL REFERENCES members(id), expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS picks (member_id TEXT NOT NULL REFERENCES members(id), session_id TEXT NOT NULL, PRIMARY KEY(member_id,session_id));
CREATE TABLE IF NOT EXISTS relogin_links (hash TEXT PRIMARY KEY, member_id TEXT NOT NULL REFERENCES members(id), expires REAL NOT NULL);
'''
# 給前端看的成員欄位：絕不能把 secret（密碼雜湊）送出去
MEMBER_COLUMNS = "id, nickname, role, revoked, secret != '' AS has_password"


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600000).hex()

def member_secret(password):
    """成員重新登入密碼：salt$PBKDF2，存在 members.secret。"""
    salt = secrets.token_hex(16)
    return salt + '$' + password_hash(password, salt)

def secret_matches(secret, password):
    salt, _, expected = (secret or '').partition('$')
    if not salt or not expected:
        password_hash(password, '00' * 16)  # 帳號不存在也花一樣的時間，避免猜出誰有設密碼
        return False
    return hmac.compare_digest(password_hash(password, salt), expected)

def check_member_password(password):
    if not isinstance(password, str) or not MIN_MEMBER_PASSWORD <= len(password) <= 200:
        raise Problem(400, f'重新登入密碼需 {MIN_MEMBER_PASSWORD}–200 字元。')
    return password

def lan_ip():
    """Best-effort LAN IP of this machine. A UDP connect sends no packets."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(('10.255.255.255', 1))
            ip = probe.getsockname()[0]
    except OSError:
        return ''
    return '' if ip.startswith('127.') else ip

def default_origins(port):
    """Allowed Host/Origin values when --origin is not given: localhost plus this machine's LAN IP."""
    origins = [f'http://127.0.0.1:{port}', f'http://localhost:{port}']
    ip = lan_ip()
    if ip:
        origins.append(f'http://{ip}:{port}')
    return origins

def agenda(path=ROOT / 'agenda.csv'):
    result = []
    with open(path, encoding='utf-8-sig', newline='') as source:
        for row in csv.DictReader(source):
            times = row['datetime'].split('|', 1)[1].strip().split(' - ')
            def minutes(t):
                parsed = time.strptime(t.strip(), '%I:%M %p')
                return parsed.tm_hour * 60 + parsed.tm_min
            row.pop('recap_zh', None)  # 回放重點只給公開議程頁用，不塞進團隊 API
            result.append({**row, 'id': re.search(r'AGND\d+', row['url']).group(),
                           'start': minutes(times[0]), 'end': minutes(times[1]),
                           'access': row['event_type'], 'title_zh': ''})
    return result

class Problem(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message

class Store:
    def __init__(self, path, wal=True):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.wal = wal
        self.migrate()
        if os.name != 'nt':
            self.path.chmod(0o600)

    def migrate(self):
        """建表並補上新版欄位；舊資料庫照常開啟。"""
        with self.connect() as db:
            db.executescript(SCHEMA)
            columns = {row[1] for row in db.execute('PRAGMA table_info(members)')}
            if 'secret' not in columns:
                db.execute("ALTER TABLE members ADD COLUMN secret TEXT NOT NULL DEFAULT ''")
            # Vercel 版把整個資料庫檔存到 Blob，必須是單一檔案，不能用 WAL
            db.execute('PRAGMA journal_mode=' + ('WAL' if self.wal else 'DELETE'))

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        return db

    @staticmethod
    def get(db, key, default=''):
        row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return row[0] if row else default

    @staticmethod
    def put(db, key, value):
        db.execute('INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value)))

    def setup(self, password):
        if len(password) < MIN_PASSWORD:
            raise ValueError(f'管理員密碼至少 {MIN_PASSWORD} 字元。')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if self.get(db, 'password_hash'):
                raise ValueError('已設定管理員，不能重新初始化。')
            self.set_owner_password(db, password)
            self.put(db, 'join_locked', '1')
            db.execute("INSERT INTO members (id, nickname, role, revoked) VALUES ('owner', '管理員', 'owner', 0)")

    def is_setup(self):
        with self.connect() as db:
            return bool(self.get(db, 'password_hash'))

    def set_owner_password(self, db, password):
        salt = secrets.token_hex(16)
        self.put(db, 'salt', salt)
        self.put(db, 'password_hash', password_hash(password, salt))

    def import_picks(self, rows, valid_ids):
        """命令列／啟動時匯入用（不經 HTTP）；回傳與管理員匯入相同的統計。"""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            return self.import_rows(db, rows, valid_ids)

    @staticmethod
    def import_rows(db, rows, valid_ids):
        """把「暱稱 + 場次編號」列表併入團隊行程。

        暱稱不存在就建立成員（沒有密碼，之後由管理員發重新登入連結給本人），
        已存在就沿用；與管理員同名的列歸給管理員。只會新增選擇，不會刪除既有選擇。
        """
        if not isinstance(rows, list) or not 1 <= len(rows) <= 2000:
            raise Problem(400, '匯入資料需為 1–2000 列。')
        stats = {'members_created': [], 'picks_added': 0, 'already_selected': 0, 'unknown_sessions': []}
        by_name = {r['nickname']: r['id'] for r in db.execute('SELECT id, nickname FROM members WHERE revoked=0')}
        for row in rows:
            if not isinstance(row, dict):
                raise Problem(400, '匯入資料格式不正確。')
            nickname = str(row.get('nickname', '')).strip()
            match = re.search(r'AGND\d+', str(row.get('session_id', '')) or str(row.get('url', '')))
            if not 1 <= len(nickname) <= 40 or any(ord(c) < 32 for c in nickname):
                raise Problem(400, f'暱稱不正確：{nickname[:40]!r}')
            if not match or match.group() not in valid_ids:
                label = (match.group() if match else str(row.get('session_id', '')))[:40]
                if label not in stats['unknown_sessions']:
                    stats['unknown_sessions'].append(label)
                continue
            mid = by_name.get(nickname)
            if not mid:
                mid = by_name[nickname] = secrets.token_hex(12)
                db.execute("INSERT INTO members (id, nickname, role, revoked) VALUES (?,?,'member',0)", (mid, nickname))
                stats['members_created'].append(nickname)
            added = db.execute('INSERT OR IGNORE INTO picks VALUES (?,?)', (mid, match.group())).rowcount
            stats['picks_added' if added else 'already_selected'] += 1
        return stats

    def member(self, token):
        """目前登入的成員（唯讀查詢，不開寫入交易）；未登入丟 Problem(401)。"""
        with self.connect() as db:
            return self.authenticate(db, token)

    def authenticate(self, db, token):
        member = db.execute('SELECT m.* FROM sessions s JOIN members m ON s.member_id=m.id WHERE s.hash=? AND s.expires>? AND m.revoked=0', (digest(token), time.time())).fetchone()
        if not member:
            raise Problem(401, '登入已失效，請重新登入或向管理員取得邀請。')
        return dict(member)

    def token(self, db, member_id):
        token = secrets.token_urlsafe(32)
        db.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
        db.execute('INSERT INTO sessions VALUES (?,?,?)', (digest(token), member_id, time.time() + SESSION_DAYS * 86400))
        return token

    def request(self, method, route, data, token, valid_ids):
        with self.connect() as db:
            # Serialize checks and mutations: revocation/rotation cannot race a write.
            db.execute('BEGIN IMMEDIATE')
            if route == '/api/login' and method == 'POST':
                salt, expected = self.get(db, 'salt'), self.get(db, 'password_hash')
                if not salt or not hmac.compare_digest(password_hash(str(data.get('password', '')), salt), expected):
                    raise Problem(403, '管理員密碼不正確。')
                return {'token': self.token(db, 'owner')}
            if route == '/api/relogin' and method == 'POST':
                # 重新登入：管理員發的一次性連結，或「暱稱 + 自己設定的密碼」
                link = str(data.get('link', ''))
                if link:
                    db.execute('DELETE FROM relogin_links WHERE expires<?', (time.time(),))
                    row = db.execute('SELECT l.member_id FROM relogin_links l JOIN members m ON m.id=l.member_id WHERE l.hash=? AND m.revoked=0', (digest(link),)).fetchone()
                    if not row:
                        raise Problem(403, '重新登入連結已失效或已使用過，請向管理員索取新連結。')
                    db.execute('DELETE FROM relogin_links WHERE hash=?', (digest(link),))
                    return {'token': self.token(db, row[0]), 'via_link': True}
                nickname = str(data.get('nickname', '')).strip()
                password = str(data.get('password', ''))
                row = db.execute("SELECT id, secret FROM members WHERE nickname=? AND revoked=0 AND role='member'", (nickname,)).fetchone()
                if not secret_matches(row['secret'] if row else '', password) or not row:
                    raise Problem(403, '暱稱或密碼不正確。沒有設過密碼的話，請向管理員索取重新登入連結。')
                return {'token': self.token(db, row['id'])}
            if route == '/api/join' and method == 'POST':
                invite = str(data.get('invite', ''))
                if self.get(db, 'join_locked', '1') == '1' or not invite or not hmac.compare_digest(digest(invite), self.get(db, 'invite_hash')):
                    raise Problem(403, '邀請已失效或已停止加入，請聯絡管理員。')
                nickname = str(data.get('nickname', '')).strip()
                if not 1 <= len(nickname) <= 40 or any(ord(c) < 32 for c in nickname):
                    raise Problem(400, '請輸入 1–40 字的暱稱。')
                if db.execute('SELECT 1 FROM members WHERE nickname=? AND revoked=0', (nickname,)).fetchone():
                    raise Problem(409, '此暱稱已有人使用。若是你本人，請改用「重新登入」；否則請加上部門或其他識別。')
                password = data.get('password')
                secret = member_secret(check_member_password(password)) if password not in (None, '') else ''
                mid = secrets.token_hex(12)
                db.execute('INSERT INTO members (id, nickname, role, revoked, secret) VALUES (?,?,?,0,?)', (mid, nickname, 'member', secret))
                return {'token': self.token(db, mid)}
            me = self.authenticate(db, token)
            if route in ('/api/state', '/api/export') and method == 'GET':
                members = [dict(x) for x in db.execute(f'SELECT {MEMBER_COLUMNS} FROM members WHERE revoked=0 OR ?="owner" ORDER BY rowid', (me['role'],))]
                picks = [dict(x) for x in db.execute('SELECT p.* FROM picks p JOIN members m ON m.id=p.member_id WHERE m.revoked=0')]
                return {'me': me, 'members': members, 'picks': picks, 'join_locked': self.get(db, 'join_locked', '1') == '1'}
            if route == '/api/password' and method == 'POST':
                # 設定／更改自己的重新登入密碼；管理員則是更改管理員密碼（需輸入目前密碼）
                password = data.get('password')
                if me['role'] == 'owner':
                    current = str(data.get('current', ''))
                    if not hmac.compare_digest(password_hash(current, self.get(db, 'salt')), self.get(db, 'password_hash')):
                        raise Problem(403, '目前的管理員密碼不正確。')
                    if not isinstance(password, str) or not MIN_PASSWORD <= len(password) <= 200:
                        raise Problem(400, f'管理員密碼需 {MIN_PASSWORD}–200 字元。')
                    self.set_owner_password(db, password)
                    return {'ok': True}
                db.execute('UPDATE members SET secret=? WHERE id=?', (member_secret(check_member_password(password)), me['id']))
                return {'ok': True}
            if route == '/api/logout' and method == 'POST':
                db.execute('DELETE FROM sessions WHERE hash=?', (digest(token),))
                return {'ok': True}
            if route == '/api/picks' and method == 'POST':
                mid = data.get('member_id', me['id'])
                if mid != me['id'] and me['role'] != 'owner':
                    raise Problem(403, '只能修改自己的選擇。')
                if not db.execute('SELECT 1 FROM members WHERE id=? AND revoked=0', (mid,)).fetchone():
                    raise Problem(400, '成員不存在或已被撤銷。')
                sid = data.get('session_id')
                if sid not in valid_ids or type(data.get('selected')) is not bool:
                    raise Problem(400, '活動或選擇狀態不正確。')
                if data['selected']:
                    db.execute('INSERT OR IGNORE INTO picks VALUES (?,?)', (mid, sid))
                else:
                    db.execute('DELETE FROM picks WHERE member_id=? AND session_id=?', (mid, sid))
                return {'ok': True}
            if route == '/api/admin' and method == 'POST':
                if me['role'] != 'owner':
                    raise Problem(403, '需要管理員權限。')
                action = data.get('action')
                if action == 'relogin_link':
                    mid = data.get('member_id')
                    if mid == 'owner' or not db.execute("SELECT 1 FROM members WHERE id=? AND revoked=0", (mid,)).fetchone():
                        raise Problem(400, '成員不存在或已被撤銷。')
                    link = secrets.token_urlsafe(32)
                    # 每人同時只留最新一條，舊連結立即失效
                    db.execute('DELETE FROM relogin_links WHERE member_id=? OR expires<?', (mid, time.time()))
                    db.execute('INSERT INTO relogin_links VALUES (?,?,?)', (digest(link), mid, time.time() + RELOGIN_LINK_DAYS * 86400))
                    return {'relogin': link}
                if action == 'import':
                    return self.import_rows(db, data.get('rows'), valid_ids)
                if action == 'rotate_invite':
                    invite = secrets.token_urlsafe(32)
                    self.put(db, 'invite_hash', digest(invite))
                    self.put(db, 'join_locked', '0')
                    return {'invite': invite}
                if action == 'lock' and type(data.get('locked')) is bool:
                    self.put(db, 'join_locked', '1' if data['locked'] else '0')
                    return {'ok': True}
                if action == 'revoke' and data.get('member_id') != 'owner':
                    mid = data.get('member_id')
                    db.execute('UPDATE members SET revoked=1 WHERE id=?', (mid,))
                    db.execute('DELETE FROM sessions WHERE member_id=?', (mid,))
                    db.execute('DELETE FROM relogin_links WHERE member_id=?', (mid,))
                    return {'ok': True}
                raise Problem(400, '管理操作不正確。')
            raise Problem(404, '找不到此操作。')

class Server(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, store, origins, agent_url=assistant.DEFAULT_AGENT_URL):
        self.store = store
        self.agent_url = agent_url.rstrip('/')
        self.instance = secrets.token_hex(16)
        self.agenda = agenda()
        self.ids = {s['id'] for s in self.agenda}
        self.origins = set(origins)
        self.attempts = {}
        self.rate_lock = threading.Lock()
        super().__init__(address, Handler)

    def check_host(self, host, origin):
        # Explicit allowed origins also defend against DNS rebinding.
        if not any(urlsplit(o).netloc == host for o in self.origins):
            raise Problem(403, '此主機位址未在 --origin 允許清單中。')
        if origin and origin not in self.origins:
            raise Problem(403, '不允許跨網站操作。')

    def client_ip(self, handler):
        return handler.client_address[0]

class Handler(BaseHTTPRequestHandler):
    @property
    def app(self):
        """共用狀態（store、議程、速率限制）。自架時就是 Server；Vercel 版改指向模組層級的 App。"""
        return self.server

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, fmt, *args):
        # Never log URLs, invite fragments, bodies, passwords or bearer tokens.
        pass

    def send(self, status, payload, content_type='application/json; charset=utf-8', export=False):
        raw = json.dumps(payload, ensure_ascii=False).encode() if isinstance(payload, dict) else payload
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        if self.path.startswith('/team'):
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if export:
            self.send_header('Content-Disposition', 'attachment; filename="sff-team-picks.json"')
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.dispatch('GET')

    def do_POST(self):
        self.dispatch('POST')

    def dispatch(self, method):
        # 不叫 handle_request：Vercel 的 Python runtime 在包裝類別上用了這個名字
        try:
            route = urlsplit(self.path).path
            self.app.check_host(self.headers.get('Host', ''), self.headers.get('Origin'))
            if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                raise Problem(403, '不允許跨網站操作。')
            if method == 'GET' and route in ('/', '/index.html', '/agenda.csv', '/team', '/team/'):
                path = ROOT / ('team/web/index.html' if route.startswith('/team') else 'agenda.csv' if route == '/agenda.csv' else 'index.html')
                raw = path.read_bytes()
                if path.name == 'index.html' and not route.startswith('/team'):
                    # Enable the original calendar's integrated entry only when
                    # a real same-origin collaboration backend is available.
                    raw = raw.replace(b'<html lang="en">', b'<html lang="en" data-selfhost="true">', 1)
                self.send(200, raw, 'text/csv; charset=utf-8' if route == '/agenda.csv' else 'text/html; charset=utf-8')
                return
            if method == 'GET' and route == '/api/health':
                self.send(200, {'service': 'sff-team-calendar', 'instance': self.app.instance})
                return
            if method == 'GET' and route == '/api/agenda':
                self.send(200, {'sessions': self.app.agenda})
                return
            if method == 'GET' and route == '/api/chat/status':
                self.send(200, {'ai': assistant.agent_ok(self.app.agent_url)})
                return
            data = {}
            if method == 'POST':
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise Problem(415, '請使用 JSON。')
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    # 管理員匯入行程時一次送多列，額度放寬
                    if not 0 < length <= (262144 if route == '/api/admin' else 8192):
                        raise ValueError()
                    data = json.loads(self.rfile.read(length))
                    if not isinstance(data, dict):
                        raise ValueError()
                except (ValueError, UnicodeDecodeError):
                    raise Problem(400, '請求內容不正確或太大。')
                # 登入／加入共用一個額度防暴力猜測；AI 問答另計，避免一直燒 API 額度
                bucket = {'/api/login': 'auth', '/api/join': 'auth', '/api/relogin': 'auth', '/api/password': 'auth', '/api/chat/ai': 'chat'}.get(route)
                if bucket:
                    with self.app.rate_lock:
                        now = time.time()
                        key = (bucket, self.app.client_ip(self))
                        attempts = [t for t in self.app.attempts.get(key, []) if now-t < 60]
                        if len(attempts) >= 15:
                            raise Problem(429, '嘗試次數過多，請稍候一分鐘。')
                        self.app.attempts[key] = attempts + [now]
            auth = self.headers.get('Authorization', '')
            token = auth[7:] if auth.startswith('Bearer ') else ''
            if method == 'POST' and route.startswith('/api/chat/'):
                self.send(200, self.chat(route, data, token))
                return
            result = self.app.store.request(method, route, data, token, self.app.ids)
            if route == '/api/export':
                result['sessions'] = self.app.agenda
                result['timezone'] = 'Asia/Singapore'
                result['exported_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            self.send(200, result, export=route == '/api/export')
        except Problem as exc:
            self.send(exc.status, {'error': exc.message})
        except (sqlite3.Error, OSError):
            self.send(503, {'error': '儲存暫時無法使用，請稍候重試；未確認成功前請勿視為已儲存。'})
        except Exception:
            self.send(500, {'error': '伺服器發生錯誤，請重新整理或聯絡管理員。'})

    def chat(self, route, data, token):
        """議程小幫手：關鍵字模式任何人可用；AI 模式需登入，因為會用到 Agent 的 API 額度。"""
        question = str(data.get('question', '')).strip()
        if not 1 <= len(question) <= 500:
            raise Problem(400, '請輸入 1–500 字的問題。')
        if route == '/api/chat/keyword':
            return {'ids': [s['id'] for s in assistant.recommend(self.app.agenda, question)]}
        if route == '/api/chat/ai':
            me = self.app.store.member(token)
            # 每位成員各自一段對話記憶；前端清除對話時換一組 thread_id
            thread = 'team-' + me['id'] + '-' + str(data.get('thread_id', ''))[:40]
            try:
                return {'answer': assistant.ask_agent(self.app.agent_url, question, thread)}
            except assistant.AgentError:
                raise Problem(502, '議程 Agent 暫時無法回答。')
        raise Problem(404, '找不到此操作。')

def format_import(result):
    lines = [f"新增 {result['picks_added']} 筆選擇（{result['already_selected']} 筆原本就有）。"]
    if result['members_created']:
        lines.append('新建成員：' + '、'.join(result['members_created']) + '（請在管理面板發重新登入連結給本人）')
    if result['unknown_sessions']:
        lines.append('議程裡找不到、已略過：' + '、'.join(result['unknown_sessions']))
    return '\n'.join(lines)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--data', type=Path, default=ROOT / 'team-data' / 'calendar.sqlite3')
    parser.add_argument('--origin', action='append', default=[], help='Exact browser origin, e.g. http://192.168.1.20:8766; repeatable')
    parser.add_argument('--setup', action='store_true', help='Initialize owner locally using hidden password prompt')
    parser.add_argument('--agent', default=os.environ.get('SFF_AGENT_URL', assistant.DEFAULT_AGENT_URL),
                        help='議程 Agent（agent/api.py）的位址，AI 問答會轉送到這裡')
    parser.add_argument('--backup', type=Path, help='Write a consistent SQLite backup to a NEW file, then exit')
    parser.add_argument('--import', dest='import_file', type=Path,
                        help='把團隊行程檔（.xlsx／.csv，需有 nickname 與 session_id 欄）併入資料庫後結束')
    args = parser.parse_args()
    os.umask(0o077)
    store = Store(args.data)
    if args.setup:
        password = getpass.getpass(f'設定管理員密碼（至少 {MIN_PASSWORD} 字元）: ')
        if password != getpass.getpass('再輸入一次: '):
            parser.error('兩次密碼不一致。')
        try:
            store.setup(password)
        except ValueError as exc:
            parser.error(str(exc))
        print('管理員已設定。請再執行 python3 -m team.server 啟動。')
        return
    if args.backup:
        # exclusive creation avoids silently overwriting a backup
        fd = os.open(args.backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with store.connect() as source, sqlite3.connect(args.backup) as target:
            source.backup(target)
        print('備份完成。備份含登入憑證雜湊，請妥善保管。')
        return
    if not store.is_setup():
        parser.error('請先在自己的電腦執行 python3 -m team.server --setup。')
    if args.import_file:
        from .importer import read_rows
        try:
            result = store.import_picks(read_rows(args.import_file), {s['id'] for s in agenda()})
        except (ValueError, Problem) as exc:
            parser.error(getattr(exc, 'message', str(exc)))
        print(format_import(result))
        return
    origins = args.origin or default_origins(args.port)
    server = Server((args.host, args.port), store, origins, args.agent)
    print(f'SFF: http://127.0.0.1:{args.port}/ — Team: /team/ — Ctrl+C 停止')
    print('允許的連線位址：' + '、'.join(origins))
    if args.host not in ('127.0.0.1', 'localhost'):
        print('注意：HTTP 不會加密密碼或邀請。僅限可信任內網；對外服務必須另設 HTTPS。')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == '__main__':
    main()
