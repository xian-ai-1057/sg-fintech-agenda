"""No network, AI provider, production credentials or external services needed."""
import concurrent.futures
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from team.server import Store, Server, Problem, agenda

class TeamTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'calendar.sqlite3')
        self.store.setup('only-a-test-password')
        self.ids = {s['id'] for s in agenda()}
        self.sid = sorted(self.ids)[0]
        self.owner = self.call('/api/login', {'password':'only-a-test-password'})['token']
        self.invite = self.call('/api/admin', {'action':'rotate_invite'}, self.owner)['invite']
        self.a = self.call('/api/join', {'invite': self.invite, 'nickname':'Alice'})['token']
        self.b = self.call('/api/join', {'invite': self.invite, 'nickname':'Bob'})['token']
    def tearDown(self):
        self.tmp.cleanup()
    def call(self, route, data=None, token='', method='POST'):
        return self.store.request(method, route, data or {}, token, self.ids)
    def state(self, token):
        return self.call('/api/state', token=token, method='GET')
    def rejects(self, status, *args, **kwargs):
        with self.assertRaises(Problem) as ctx:
            self.call(*args, **kwargs)
        self.assertEqual(ctx.exception.status, status)
    def test_agenda_count_unique(self):
        self.assertEqual(len(agenda()),124)
        self.assertEqual(len(self.ids),124)
    def test_no_first_visitor_admin(self):
        self.rejects(401,'/api/state',method='GET')
        self.rejects(403,'/api/login',{'password':'bad'})
        self.assertEqual(self.state(self.a)['me']['role'],'member')
        with self.assertRaises(ValueError): self.store.setup('replacement-password')
    def test_shared_persistence_and_restart(self):
        self.call('/api/picks',{'session_id':self.sid,'selected':True},self.a)
        self.assertEqual(len(self.state(self.b)['picks']),1)
        self.store = Store(self.store.path)
        self.assertEqual(len(self.state(self.b)['picks']),1)
    def test_member_cannot_change_other_or_admin(self):
        aid = self.state(self.a)['me']['id']
        self.rejects(403,'/api/picks',{'member_id':aid,'session_id':self.sid,'selected':True},self.b)
        self.rejects(403,'/api/admin',{'action':'rotate_invite'},self.a)
    def test_owner_controls_all(self):
        aid = self.state(self.a)['me']['id']
        self.call('/api/picks',{'member_id':aid,'session_id':self.sid,'selected':True},self.owner)
        self.call('/api/picks',{'member_id':aid,'session_id':self.sid,'selected':False},self.owner)
        self.assertFalse(self.state(self.a)['picks'])
    def test_revoke_blocks_read_write_and_hides_picks(self):
        aid = self.state(self.a)['me']['id']
        self.call('/api/picks',{'session_id':self.sid,'selected':True},self.a)
        self.call('/api/admin',{'action':'revoke','member_id':aid},self.owner)
        self.rejects(401,'/api/state',token=self.a,method='GET')
        self.rejects(401,'/api/picks',{'session_id':self.sid,'selected':False},self.a)
        self.assertFalse(self.state(self.b)['picks'])
    def test_invite_rotation_and_join_lock(self):
        new = self.call('/api/admin',{'action':'rotate_invite'},self.owner)['invite']
        self.rejects(403,'/api/join',{'invite':self.invite,'nickname':'Charlie'})
        self.assertEqual(self.state(self.a)['me']['nickname'],'Alice')
        self.call('/api/admin',{'action':'lock','locked':True},self.owner)
        self.rejects(403,'/api/join',{'invite':new,'nickname':'Charlie'})
    def test_invalid_inputs_duplicate_nickname(self):
        self.rejects(400,'/api/picks',{'session_id':'invented','selected':True},self.a)
        self.rejects(400,'/api/picks',{'session_id':self.sid,'selected':'yes'},self.a)
        self.rejects(409,'/api/join',{'invite':self.invite,'nickname':'Alice'})
    def test_parallel_writes(self):
        def pick(token): return self.call('/api/picks',{'session_id':self.sid,'selected':True},token)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(pick,[self.a,self.b,self.a,self.b]))
        self.assertEqual(len(self.state(self.a)['picks']),2)
    def test_hashes_only_and_logout(self):
        with self.store.connect() as db:
            dump='\n'.join(db.iterdump())
        for secret in [self.a,self.b,self.owner,self.invite,'only-a-test-password']:
            self.assertNotIn(secret,dump)
        self.call('/api/logout',token=self.a)
        self.rejects(401,'/api/state',token=self.a,method='GET')
    def test_backup(self):
        self.call('/api/picks',{'session_id':self.sid,'selected':True},self.a)
        backup=Path(self.tmp.name)/'backup.sqlite3'
        with self.store.connect() as src,sqlite3.connect(backup) as dst: src.backup(dst)
        restored=Store(backup)
        self.assertEqual(len(restored.request('GET','/api/state',{},self.b,self.ids)['picks']),1)
    def test_http_guardrails_and_two_clients(self):
        server=Server(('127.0.0.1',0),self.store,[])
        port=server.server_port
        base=f'http://127.0.0.1:{port}'
        server.origins={base}
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def request(path,body=None,token='',origin=None,host=None):
            headers={'Authorization':'Bearer '+token}
            if origin: headers['Origin']=origin
            if host: headers['Host']=host
            data=None
            if body is not None: headers['Content-Type']='application/json';data=json.dumps(body).encode()
            try:
                with urlopen(Request(base+path,data=data,headers=headers)) as r: return r.status,r.read()
            except HTTPError as e: return e.code,e.read()
        try:
            status, health = request('/api/health')
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(health)['instance'], server.instance)
            status, home = request('/')
            self.assertEqual(status, 200)
            self.assertIn(b'data-selfhost="true"', home)
            self.assertEqual(home.count(b'href="./team/"'), 2)  # desktop + mobile
            self.assertNotIn(b'data-selfhost="true"', (Path(__file__).resolve().parents[1] / 'index.html').read_bytes().split(b'<style>')[0])
            status, team_page = request('/team/')
            self.assertEqual(status, 200)
            self.assertIn(b'href="../"', team_page)
            self.assertEqual(request('/agenda.csv')[0], 200)
            self.assertEqual(request('/api/state')[0],401)
            self.assertEqual(request('/api/state',token=self.a,origin='https://evil.test')[0],403)
            self.assertEqual(request('/api/state',token=self.a,host='evil.test')[0],403)
            self.assertEqual(request('/.git/config')[0],401)
            self.assertEqual(request('/api/picks',{'session_id':self.sid,'selected':True},self.a)[0],200)
            status,body=request('/api/state',token=self.b)
            self.assertEqual(status,200);self.assertEqual(len(json.loads(body)['picks']),1)
            self.assertEqual(request('/api/export',token=self.b)[0],200)
            for _ in range(15):request('/api/join',{'invite':'wrong','nickname':'x'})
            self.assertEqual(request('/api/join',{'invite':'wrong','nickname':'x'})[0],429)
        finally:
            server.shutdown();server.server_close();thread.join()

if __name__=='__main__': unittest.main()
