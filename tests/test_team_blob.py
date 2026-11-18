"""Vercel 版儲存（BlobStore）與重新登入／匯入。不需要網路：Blob 服務以記憶體假造。"""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.parse import parse_qs, urlsplit

from team.blobstore import BlobStore
from team.importer import read_rows, rows_from_table
from team.server import Problem, Store, agenda

TOKEN = 'vercel_blob_rw_TestStore123_secretpart'


class FakeBlob:
    """記一個 pathname -> (bytes, etag)，照 Vercel Blob 的 If-None-Match / x-if-match 語意回應。"""
    def __init__(self):
        self.files, self.version, self.lock = {}, 0, threading.Lock()
        self.gets = self.puts = self.conflicts = 0

    def handle(self, method, url, data, headers):
        parts = urlsplit(url)
        with self.lock:
            if method == 'GET':
                self.gets += 1
                assert parts.netloc == 'teststore123.private.blob.vercel-storage.com', parts.netloc
                current = self.files.get(parts.path.lstrip('/'))
                if not current:
                    return 404, {}, b'{}'
                if headers.get('If-None-Match') == current[1]:
                    return 304, {'ETag': current[1]}, b''
                return 200, {'ETag': current[1]}, current[0]
            self.puts += 1
            name = parse_qs(parts.query)['pathname'][0]
            assert headers['x-vercel-blob-access'] == 'private'
            current = self.files.get(name)
            if current and headers.get('x-allow-overwrite') != '1':
                self.conflicts += 1
                return 400, {}, b'{"error":{"code":"bad_request"}}'
            if current and headers.get('x-if-match') != current[1]:
                self.conflicts += 1
                return 412, {}, b'{"error":{"code":"precondition_failed"}}'
            self.version += 1
            etag = f'"v{self.version}"'
            self.files[name] = (bytes(data), etag)
            return 200, {}, json.dumps({'pathname': name, 'etag': etag}).encode()


def instance(blob, tmp, name, read_ttl=0):
    store = BlobStore(token=TOKEN, local=Path(tmp) / name / 'db.sqlite3', read_ttl=read_ttl)
    store._http = lambda method, url, data=None, headers=None: blob.handle(method, url, data, headers or {})
    return store


class BlobStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.blob = FakeBlob()
        self.a = instance(self.blob, self.tmp.name, 'a')
        self.b = instance(self.blob, self.tmp.name, 'b')
        self.ids = {s['id'] for s in agenda()}
        self.sids = sorted(self.ids)

    def tearDown(self):
        self.tmp.cleanup()

    def test_setup_once_and_shared_between_instances(self):
        self.assertFalse(self.a.is_setup())
        self.a.setup('vercel-admin-pass')
        self.assertTrue(self.b.is_setup())
        with self.assertRaises(ValueError):
            self.b.setup('another-password')
        token = self.b.request('POST', '/api/login', {'password': 'vercel-admin-pass'}, '', self.ids)['token']
        self.assertEqual(self.a.request('GET', '/api/state', {}, token, self.ids)['me']['role'], 'owner')

    def test_concurrent_instances_do_not_lose_writes(self):
        self.a.setup('vercel-admin-pass')
        owner = self.a.request('POST', '/api/login', {'password': 'vercel-admin-pass'}, '', self.ids)['token']
        # b 先讀一次（拿到舊 ETag），a 再寫，b 接著寫：b 必須偵測衝突並重做，不能蓋掉 a
        self.b.request('GET', '/api/state', {}, owner, self.ids)
        self.a.request('POST', '/api/picks', {'session_id': self.sids[0], 'selected': True}, owner, self.ids)
        self.b.pulled = 1e18  # 讓 b 以為本機副本還很新
        self.b.request('POST', '/api/picks', {'session_id': self.sids[1], 'selected': True}, owner, self.ids)
        picks = {p['session_id'] for p in self.a.request('GET', '/api/state', {}, owner, self.ids)['picks']}
        self.assertEqual(picks, {self.sids[0], self.sids[1]})

    def test_reads_do_not_upload_and_ttl_saves_reads(self):
        self.a.setup('vercel-admin-pass')
        owner = self.a.request('POST', '/api/login', {'password': 'vercel-admin-pass'}, '', self.ids)['token']
        cached = instance(self.blob, self.tmp.name, 'c', read_ttl=60)
        cached.request('GET', '/api/state', {}, owner, self.ids)
        puts, gets = self.blob.puts, self.blob.gets
        for _ in range(5):
            cached.request('GET', '/api/state', {}, owner, self.ids)
        self.assertEqual((self.blob.puts, self.blob.gets), (puts, gets))

    def test_import_through_blob(self):
        self.a.setup('vercel-admin-pass')
        rows = [{'nickname': 'KDY', 'session_id': self.sids[0]}, {'nickname': '管理員', 'session_id': self.sids[1]}]
        result = self.a.import_picks(rows, self.ids)
        self.assertEqual((result['picks_added'], result['members_created']), (2, ['KDY']))
        self.assertEqual(self.b.import_picks(rows, self.ids)['already_selected'], 2)


class ReloginAndImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'c.sqlite3')
        self.store.setup('only-a-test-password')
        self.ids = {s['id'] for s in agenda()}
        self.sid = sorted(self.ids)[0]
        self.owner = self.call('/api/login', {'password': 'only-a-test-password'})['token']
        self.invite = self.call('/api/admin', {'action': 'rotate_invite'}, self.owner)['invite']

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, route, data=None, token='', method='POST'):
        return self.store.request(method, route, data or {}, token, self.ids)

    def rejects(self, status, *args, **kwargs):
        with self.assertRaises(Problem) as ctx:
            self.call(*args, **kwargs)
        self.assertEqual(ctx.exception.status, status)

    def test_join_with_password_then_relogin(self):
        first = self.call('/api/join', {'invite': self.invite, 'nickname': 'Alice', 'password': 'abcd'})['token']
        self.call('/api/picks', {'session_id': self.sid, 'selected': True}, first)
        again = self.call('/api/relogin', {'nickname': 'Alice', 'password': 'abcd'})['token']
        state = self.call('/api/state', token=again, method='GET')
        self.assertEqual(state['me']['nickname'], 'Alice')
        self.assertEqual(len(state['picks']), 1)   # 同一個身分，選擇都還在
        self.rejects(403, '/api/relogin', {'nickname': 'Alice', 'password': 'wrong'})
        self.rejects(403, '/api/relogin', {'nickname': 'Nobody', 'password': 'abcd'})
        self.rejects(400, '/api/join', {'invite': self.invite, 'nickname': 'Bob', 'password': 'abc'})

    def test_relogin_works_after_join_is_locked(self):
        self.call('/api/join', {'invite': self.invite, 'nickname': 'Alice', 'password': 'abcd'})
        self.call('/api/admin', {'action': 'lock', 'locked': True}, self.owner)
        self.assertTrue(self.call('/api/relogin', {'nickname': 'Alice', 'password': 'abcd'})['token'])

    def test_owner_link_is_single_use_and_rotates(self):
        self.store.import_picks([{'nickname': 'KDY', 'session_id': self.sid}], self.ids)
        state = self.call('/api/state', token=self.owner, method='GET')
        kdy = next(m for m in state['members'] if m['nickname'] == 'KDY')
        self.assertFalse(kdy['has_password'])
        self.assertNotIn('secret', kdy)
        old = self.call('/api/admin', {'action': 'relogin_link', 'member_id': kdy['id']}, self.owner)['relogin']
        link = self.call('/api/admin', {'action': 'relogin_link', 'member_id': kdy['id']}, self.owner)['relogin']
        self.rejects(403, '/api/relogin', {'link': old})       # 新連結使舊連結失效
        token = self.call('/api/relogin', {'link': link})['token']
        self.rejects(403, '/api/relogin', {'link': link})      # 只能用一次
        me = self.call('/api/state', token=token, method='GET')
        self.assertEqual(me['me']['nickname'], 'KDY')
        self.assertEqual(len(me['picks']), 1)
        self.call('/api/password', {'password': 'kdy-pass'}, token)
        self.assertTrue(self.call('/api/relogin', {'nickname': 'KDY', 'password': 'kdy-pass'})['token'])
        self.rejects(400, '/api/admin', {'action': 'relogin_link', 'member_id': 'owner'}, self.owner)

    def test_revoked_member_cannot_relogin(self):
        token = self.call('/api/join', {'invite': self.invite, 'nickname': 'Alice', 'password': 'abcd'})['token']
        aid = self.call('/api/state', token=token, method='GET')['me']['id']
        link = self.call('/api/admin', {'action': 'relogin_link', 'member_id': aid}, self.owner)['relogin']
        self.call('/api/admin', {'action': 'revoke', 'member_id': aid}, self.owner)
        self.rejects(403, '/api/relogin', {'nickname': 'Alice', 'password': 'abcd'})
        self.rejects(403, '/api/relogin', {'link': link})

    def test_owner_password_change_needs_current(self):
        self.rejects(403, '/api/password', {'current': 'wrong', 'password': 'new-password'}, self.owner)
        self.call('/api/password', {'current': 'only-a-test-password', 'password': 'new-password'}, self.owner)
        self.rejects(403, '/api/login', {'password': 'only-a-test-password'})
        self.assertTrue(self.call('/api/login', {'password': 'new-password'})['token'])

    def test_member_cannot_import(self):
        token = self.call('/api/join', {'invite': self.invite, 'nickname': 'Alice'})['token']
        self.rejects(403, '/api/admin', {'action': 'import', 'rows': [{'nickname': 'x', 'session_id': self.sid}]}, token)

    def test_admin_import_reports_unknown_sessions(self):
        rows = [{'nickname': 'Mei', 'session_id': self.sid}, {'nickname': 'Mei', 'session_id': 'AGND000'},
                {'nickname': 'Mei', 'url': 'https://www.fintechfestival.sg/agenda?session=' + self.sid + '-x'}]
        result = self.call('/api/admin', {'action': 'import', 'rows': rows}, self.owner)
        self.assertEqual(result['picks_added'], 1)
        self.assertEqual(result['already_selected'], 1)
        self.assertEqual(result['unknown_sessions'], ['AGND000'])

    def test_importer_reads_csv_and_headers(self):
        rows = rows_from_table([['Nickname', 'URL'], ['Mei', 'https://x/agenda?session=AGND1-a'], ['', 'ignored']])
        self.assertEqual(rows, [{'nickname': 'Mei', 'session_id': 'https://x/agenda?session=AGND1-a'}])
        path = Path(self.tmp.name) / 'p.csv'
        path.write_text('﻿nickname,session_id\nKDY,AGND334\n', encoding='utf-8')
        self.assertEqual(read_rows(path), [{'nickname': 'KDY', 'session_id': 'AGND334'}])
        with self.assertRaises(ValueError):
            rows_from_table([['who', 'what']])


if __name__ == '__main__':
    unittest.main()
