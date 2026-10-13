"""共編頁議程小幫手：關鍵字推薦與 AI 轉送。用假的 Agent，不需要 API key 或網路。"""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from team.assistant import recommend
from team.server import Server, Store, agenda


class FakeAgent(BaseHTTPRequestHandler):
    """模擬 agent/api.py：/health 回 200，/ask 回一段含場次編號的答案並記下收到的內容。"""
    received = []

    def log_message(self, *args):
        pass

    def reply(self, payload):
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.reply({'ok': True})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        FakeAgent.received.append(body)
        self.reply({'answer': '推薦 **' + body['question'] + '**：\n- AGND441', 'thread_id': body['thread_id']})


def serve(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


class RecommendTests(unittest.TestCase):
    sessions = agenda()

    def test_empty_query_returns_nothing(self):
        self.assertEqual(recommend(self.sessions, '   '), [])

    def test_speaker_name_finds_their_session(self):
        found = recommend(self.sessions, 'Ravi Menon')
        self.assertIn('Ravi Menon', found[0]['speakers'])

    def test_chinese_track_name_matches_track(self):
        found = recommend(self.sessions, '人才')
        self.assertTrue(found)
        self.assertTrue(all(s['track'] == 'Talent Rewiring' for s in found))

    def test_limit_and_relevance(self):
        found = recommend(self.sessions, 'AI', limit=3)
        self.assertEqual(len(found), 3)
        for s in found:
            text = (s['title'] + s['description'] + s['description_zh']).lower()
            self.assertIn('ai', text)


class ChatHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        store = Store(Path(self.tmp.name) / 'calendar.sqlite3')
        store.setup('123456')
        self.token = store.request('POST', '/api/login', {'password': '123456'}, '', set())['token']

        self.agent = HTTPServer(('127.0.0.1', 0), FakeAgent)
        self.agent_thread = serve(self.agent)
        FakeAgent.received = []

        self.server = Server(('127.0.0.1', 0), store, [], f'http://127.0.0.1:{self.agent.server_port}')
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.server.origins = {self.base}
        self.thread = serve(self.server)

    def tearDown(self):
        for server, thread in ((self.server, self.thread), (self.agent, self.agent_thread)):
            server.shutdown()
            server.server_close()
            thread.join()
        self.tmp.cleanup()

    def request(self, path, body=None, token=''):
        headers = {'Authorization': 'Bearer ' + token}
        data = None
        if body is not None:
            headers['Content-Type'] = 'application/json'
            data = json.dumps(body).encode()
        try:
            with urlopen(Request(self.base + path, data=data, headers=headers)) as r:
                return r.status, json.loads(r.read())
        except HTTPError as e:
            return e.code, json.loads(e.read())

    def test_keyword_needs_no_login(self):
        status, body = self.request('/api/chat/keyword', {'question': 'Ravi Menon'})
        self.assertEqual(status, 200)
        self.assertTrue(body['ids'][0].startswith('AGND'))
        self.assertEqual(self.request('/api/chat/keyword', {'question': ''})[0], 400)

    def test_ai_requires_login_and_is_relayed(self):
        self.assertEqual(self.request('/api/chat/status'), (200, {'ai': True}))
        self.assertEqual(self.request('/api/chat/ai', {'question': 'hi'})[0], 401)
        self.assertEqual(FakeAgent.received, [])

        status, body = self.request('/api/chat/ai', {'question': 'Day 2?', 'thread_id': 'abc'}, self.token)
        self.assertEqual(status, 200)
        self.assertIn('AGND441', body['answer'])
        # 每位成員各自一段記憶：thread_id 前面加上成員 id
        self.assertEqual(FakeAgent.received[0], {'question': 'Day 2?', 'thread_id': 'team-owner-abc'})

    def test_agent_down(self):
        self.server.agent_url = 'http://127.0.0.1:9'  # 沒有服務的連接埠
        self.assertEqual(self.request('/api/chat/status'), (200, {'ai': False}))
        self.assertEqual(self.request('/api/chat/ai', {'question': 'hi'}, self.token)[0], 502)


if __name__ == '__main__':
    unittest.main()
