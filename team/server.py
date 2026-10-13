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
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = '''
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS members (id TEXT PRIMARY KEY, nickname TEXT NOT NULL, role TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sessions (hash TEXT PRIMARY KEY, member_id TEXT NOT NULL REFERENCES members(id), expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS picks (member_id TEXT NOT NULL REFERENCES members(id), session_id TEXT NOT NULL, PRIMARY KEY(member_id,session_id));
'''

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600000).hex()

def agenda(path=ROOT / 'agenda.csv'):
    result = []
    with open(path, encoding='utf-8-sig', newline='') as source:
        for row in csv.DictReader(source):
            times = row['datetime'].split('|', 1)[1].strip().split(' - ')
            def minutes(t):
                parsed = time.strptime(t.strip(), '%I:%M %p')
                return parsed.tm_hour * 60 + parsed.tm_min
            result.append({**row, 'id': re.search(r'AGND\d+', row['url']).group(),
                           'start': minutes(times[0]), 'end': minutes(times[1]),
                           'access': row['event_type'], 'title_zh': ''})
    return result

class Problem(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message

class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)
            db.execute('PRAGMA journal_mode=WAL')
        if os.name != 'nt':
            self.path.chmod(0o600)

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
        if len(password) < 12:
            raise ValueError('管理員密碼至少 12 字元。')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if self.get(db, 'password_hash'):
                raise ValueError('已設定管理員，不能重新初始化。')
            salt = secrets.token_hex(16)
            self.put(db, 'salt', salt)
            self.put(db, 'password_hash', password_hash(password, salt))
            self.put(db, 'join_locked', '1')
            db.execute('INSERT INTO members VALUES (?,?,?,0)', ('owner', '管理員', 'owner'))

    def authenticate(self, db, token):
        member = db.execute('SELECT m.* FROM sessions s JOIN members m ON s.member_id=m.id WHERE s.hash=? AND s.expires>? AND m.revoked=0', (digest(token), time.time())).fetchone()
        if not member:
            raise Problem(401, '登入已失效，請重新登入或向管理員取得邀請。')
        return dict(member)

    def token(self, db, member_id):
        token = secrets.token_urlsafe(32)
        db.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
        db.execute('INSERT INTO sessions VALUES (?,?,?)', (digest(token), member_id, time.time() + 7 * 86400))
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
            if route == '/api/join' and method == 'POST':
                invite = str(data.get('invite', ''))
                if self.get(db, 'join_locked', '1') == '1' or not invite or not hmac.compare_digest(digest(invite), self.get(db, 'invite_hash')):
                    raise Problem(403, '邀請已失效或已停止加入，請聯絡管理員。')
                nickname = str(data.get('nickname', '')).strip()
                if not 1 <= len(nickname) <= 40 or any(ord(c) < 32 for c in nickname):
                    raise Problem(400, '請輸入 1–40 字的暱稱。')
                if db.execute('SELECT 1 FROM members WHERE nickname=? AND revoked=0', (nickname,)).fetchone():
                    raise Problem(409, '此暱稱已有人使用，請加上部門或其他識別。')
                mid = secrets.token_hex(12)
                db.execute('INSERT INTO members VALUES (?,?,?,0)', (mid, nickname, 'member'))
                return {'token': self.token(db, mid)}
            me = self.authenticate(db, token)
            if route in ('/api/state', '/api/export') and method == 'GET':
                members = [dict(x) for x in db.execute('SELECT * FROM members WHERE revoked=0 OR ?="owner"', (me['role'],))]
                picks = [dict(x) for x in db.execute('SELECT p.* FROM picks p JOIN members m ON m.id=p.member_id WHERE m.revoked=0')]
                return {'me': me, 'members': members, 'picks': picks, 'join_locked': self.get(db, 'join_locked', '1') == '1'}
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
                    return {'ok': True}
                raise Problem(400, '管理操作不正確。')
            raise Problem(404, '找不到此操作。')

class Server(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, store, origins):
        self.store = store
        self.agenda = agenda()
        self.ids = {s['id'] for s in self.agenda}
        self.origins = set(origins)
        self.attempts = {}
        self.rate_lock = threading.Lock()
        super().__init__(address, Handler)

class Handler(BaseHTTPRequestHandler):
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
        self.handle_request('GET')

    def do_POST(self):
        self.handle_request('POST')

    def handle_request(self, method):
        try:
            route = urlsplit(self.path).path
            host = self.headers.get('Host', '')
            # Explicit allowed origins also defend against DNS rebinding.
            if not any(urlsplit(o).netloc == host for o in self.server.origins):
                raise Problem(403, '此主機位址未在 --origin 允許清單中。')
            origin = self.headers.get('Origin')
            if origin and origin not in self.server.origins:
                raise Problem(403, '不允許跨網站操作。')
            if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                raise Problem(403, '不允許跨網站操作。')
            if method == 'GET' and route in ('/', '/index.html', '/agenda.csv', '/team', '/team/'):
                path = ROOT / ('team/web/index.html' if route.startswith('/team') else 'agenda.csv' if route == '/agenda.csv' else 'index.html')
                raw = path.read_bytes()
                if path.name == 'index.html' and not route.startswith('/team'):
                    # Inject only in selfhost response; GitHub Pages file remains unchanged.
                    raw = raw.replace(b'</body>', '<a href="/team/" style="position:fixed;bottom:16px;left:16px;z-index:9999;background:#0a84ff;color:white;padding:10px 16px;border-radius:980px;font:14px system-ui;text-decoration:none">Team calendar · 團隊共編</a></body>'.encode())
                self.send(200, raw, 'text/csv; charset=utf-8' if route == '/agenda.csv' else 'text/html; charset=utf-8')
                return
            if method == 'GET' and route == '/api/agenda':
                self.send(200, {'sessions': self.server.agenda})
                return
            data = {}
            if method == 'POST':
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise Problem(415, '請使用 JSON。')
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 8192:
                        raise ValueError()
                    data = json.loads(self.rfile.read(length))
                    if not isinstance(data, dict):
                        raise ValueError()
                except (ValueError, UnicodeDecodeError):
                    raise Problem(400, '請求內容不正確或太大。')
                if route in ('/api/login', '/api/join'):
                    with self.server.rate_lock:
                        now = time.time()
                        key = self.client_address[0]
                        attempts = [t for t in self.server.attempts.get(key, []) if now-t < 60]
                        if len(attempts) >= 15:
                            raise Problem(429, '嘗試次數過多，請稍候一分鐘。')
                        self.server.attempts[key] = attempts + [now]
            auth = self.headers.get('Authorization', '')
            token = auth[7:] if auth.startswith('Bearer ') else ''
            result = self.server.store.request(method, route, data, token, self.server.ids)
            if route == '/api/export':
                result['sessions'] = self.server.agenda
                result['timezone'] = 'Asia/Singapore'
                result['exported_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            self.send(200, result, export=route == '/api/export')
        except Problem as exc:
            self.send(exc.status, {'error': exc.message})
        except (sqlite3.Error, OSError):
            self.send(503, {'error': '儲存暫時無法使用，請稍候重試；未確認成功前請勿視為已儲存。'})
        except Exception:
            self.send(500, {'error': '伺服器發生錯誤，請重新整理或聯絡管理員。'})

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--data', type=Path, default=ROOT / 'team-data' / 'calendar.sqlite3')
    parser.add_argument('--origin', action='append', default=[], help='Exact browser origin, e.g. http://192.168.1.20:8766; repeatable')
    parser.add_argument('--setup', action='store_true', help='Initialize owner locally using hidden password prompt')
    parser.add_argument('--backup', type=Path, help='Write a consistent SQLite backup to a NEW file, then exit')
    args = parser.parse_args()
    os.umask(0o077)
    store = Store(args.data)
    if args.setup:
        password = getpass.getpass('設定管理員密碼（至少 12 字元）: ')
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
    with store.connect() as db:
        if not store.get(db, 'password_hash'):
            parser.error('請先在自己的電腦執行 python3 -m team.server --setup。')
    origins = args.origin or [f'http://127.0.0.1:{args.port}', f'http://localhost:{args.port}']
    server = Server((args.host, args.port), store, origins)
    print(f'SFF: http://{args.host}:{args.port}/ — Team: /team/ — Ctrl+C 停止')
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
