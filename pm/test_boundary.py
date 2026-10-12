"""审批协议逐字段验证、回环 HTTP 和中断持久化测试，均非人类批准。"""
import copy,http.client,json,secrets,tempfile,threading,time,unittest
from pathlib import Path
from approval_api import Store,Denied,make_server,digest
from test_approval import FakeRemote,authorize_l2

class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.fake=FakeRemote(self.root);self.key=secrets.token_bytes(32)
        self.p={'schema':'approval-proposal/v1','task':'TEST-ONLY','version':1,'scope':['history.py'],'base':'abc','before_sha':'before','source_sha':'source','expiry':time.time()+600,'actions':['edit'],'change':'normal','source':{'request':'合成测试'},'acceptance':['固定行为'],'risks':['测试能力并非账户认证'],'project':'synthetic-history','environment':'dev / isolated synthetic Docker'}
        self.s=Store(self.root,self.p,self.key,self.fake.baseline,self.fake.dispatch,self.fake.sync)
        self.sid,self.csrf=self.register()
        authorize_l2(self.s,self.sid,self.csrf)
    def register(self,privilege='normal'):
        return self.s.register({'capability':self.s.bootstrap_capability(privilege),'challenge':self.s.challenge,'confirm_capability_holder':True})
    def data(self,decision='approve',rid='test-request-0001'):
        p=self.s.card()['proposal']
        return {'request_id':rid,'decision':decision,'version':p['version'],'proposal_hash':self.s.card()['proposal_hash'],'scope':p['scope'],'base':p['base'],'expiry':p['expiry'],'actions':p['actions'],'note':'补充验收' if decision=='change' else ''}
    def deny(self,code,fn):
        with self.assertRaises(Denied) as e:fn()
        self.assertEqual(e.exception.code,code)
    def test_unauthorized(self):self.deny('SESSION_REQUIRED',lambda:self.s.decide('',self.csrf,self.data()))
    def test_self_reported_human(self):self.deny('INVALID_DECISION',lambda:self.s.decide(self.sid,self.csrf,{**self.data(),'role':'human'}))
    def test_wrong_csrf(self):self.deny('CSRF_DENIED',lambda:self.s.decide(self.sid,'forged',self.data()))
    def test_version(self):self.deny('STALE_PROPOSAL',lambda:self.s.decide(self.sid,self.csrf,{**self.data(),'version':2}))
    def test_hash(self):self.deny('STALE_PROPOSAL',lambda:self.s.decide(self.sid,self.csrf,{**self.data(),'proposal_hash':'forged'}))
    def test_scope(self):self.deny('BINDING_SCOPE',lambda:self.s.decide(self.sid,self.csrf,{**self.data(),'scope':['../escape']}))
    def test_base(self):self.deny('BINDING_BASE',lambda:self.s.decide(self.sid,self.csrf,{**self.data(),'base':'changed'}))
    def test_expiry_binding(self):self.deny('BINDING_EXPIRY',lambda:self.s.decide(self.sid,self.csrf,{**self.data(),'expiry':self.p['expiry']+1}))
    def test_actions(self):self.deny('BINDING_ACTIONS',lambda:self.s.decide(self.sid,self.csrf,{**self.data(),'actions':['deploy']}))
    def test_bootstrap_replay_and_expiry(self):
        token=self.s.bootstrap_capability(ttl=-1)
        self.deny('INVALID_OR_EXPIRED_BOOTSTRAP',lambda:self.s.register({'capability':token,'challenge':self.s.challenge,'confirm_capability_holder':True}))
        token=self.s.bootstrap_capability();v={'capability':token,'challenge':self.s.challenge,'confirm_capability_holder':True};self.s.register(v)
        self.deny('INVALID_OR_EXPIRED_BOOTSTRAP',lambda:self.s.register(v))
    def test_session_expiry(self):
        for v in self.s.sessions.values():v['expiry']=time.time()-1
        self.deny('SESSION_REQUIRED',lambda:self.s.decide(self.sid,self.csrf,self.data()))
    def test_baseline_drift(self):
        self.s.baseline=lambda p:{'base':'drift','before_sha':p['before_sha']}
        self.deny('BASELINE_DRIFT',lambda:self.s.decide(self.sid,self.csrf,self.data()));self.assertEqual(self.s.card()['status'],'PENDING')
    def test_artifact_drift(self):
        self.s.baseline=lambda p:{'base':p['base'],'before_sha':'drift'}
        self.deny('BASELINE_DRIFT',lambda:self.s.decide(self.sid,self.csrf,self.data()))
    def test_reject(self):
        d=self.data('reject');self.s.decide(self.sid,self.csrf,d);self.assertEqual(self.s.card()['status'],'REJECTED')
        self.deny('APPROVAL_REQUIRED',lambda:self.s.execute(self.sid,self.csrf,d['request_id']))
    def test_change_old_authorization_and_repeat(self):
        d=self.data();self.s.decide(self.sid,self.csrf,d)
        c=self.data('change','test-change-0001');self.s.decide(self.sid,self.csrf,c)
        self.assertTrue(self.s.decide(self.sid,self.csrf,c)['cached']);self.assertEqual(self.s.card()['status'],'PENDING')
        self.deny('SUPERSEDED_AUTHORIZATION',lambda:self.s.execute(self.sid,self.csrf,d['request_id']))
        self.deny('PLAN_REVISION_REQUIRED',lambda:self.s.decide(self.sid,self.csrf,self.data(rid='test-reapprove-0001')))
    def test_repeated_click_conflict(self):
        d=self.data();self.s.decide(self.sid,self.csrf,d)
        self.deny('IDEMPOTENCY_CONFLICT',lambda:self.s.decide(self.sid,self.csrf,{**d,'note':'different'}))
    def test_cross_session_execution(self):
        d=self.data();self.s.decide(self.sid,self.csrf,d);sid,csrf=self.register()
        self.deny('SESSION_SUBJECT_MISMATCH',lambda:self.s.execute(sid,csrf,d['request_id']))
    def test_authorization_expiry(self):
        d=self.data();self.s.decide(self.sid,self.csrf,d)
        with self.s.connect() as con:
            r=json.loads(con.execute('SELECT receipt FROM outbox').fetchone()[0]);r['body']['expiry']=time.time()-1;con.execute('UPDATE outbox SET receipt=?',(json.dumps(r),))
        self.deny('AUTHORIZATION_EXPIRED',lambda:self.s.execute(self.sid,self.csrf,d['request_id']))
    def test_destructive_grant(self):
        p={**self.p,'change':'destructive'}
        with self.s.connect() as con:con.execute('UPDATE cards SET body=?',(json.dumps(p),))
        self.deny('DESTRUCTIVE_CAPABILITY_REQUIRED',lambda:self.s.decide(self.sid,self.csrf,self.data()))
        self.sid,self.csrf=self.register('destructive');authorize_l2(self.s,self.sid,self.csrf);self.assertEqual(self.s.decide(self.sid,self.csrf,self.data())['status'],'APPROVED_EXECUTION_PENDING')
    def test_interrupt_and_restart(self):
        d=self.data();self.s.decide(self.sid,self.csrf,d)
        calls=[]
        def interrupted(r):calls.append(r['body']['request_id']);raise ConnectionError('synthetic interruption')
        self.s.dispatch=interrupted
        self.deny('DISPATCH_INTERRUPTED_RETRY_SAME_REQUEST',lambda:self.s.execute(self.sid,self.csrf,d['request_id']))
        s=Store(self.root,self.p,self.key,self.fake.baseline,self.fake.dispatch,self.fake.sync);s.sessions=self.s.sessions
        self.deny('DISPATCH_RESULT_UNKNOWN_MANUAL_REVIEW_NO_RESEND',lambda:s.execute(self.sid,self.csrf,d['request_id']))
        self.assertFalse(any(c[0]=='dispatch' for c in self.fake.calls))
        self.assertEqual(calls,[d['request_id']])
    def test_full_task_card(self):
        p=json.loads(Path(__file__).with_name('pending-proposal.json').read_bytes())
        self.assertTrue({'source','version','scope','base','expiry','actions','acceptance','risks','worktree','branch','rollback','forbidden','remaining','source_sha','before_sha'}.issubset(p))
        self.assertIn('relation',p['source']);self.assertIn('predecessor',p['source'])
    def test_http_origin_host_csrf_cookie_and_no_self_report(self):
        server=make_server(self.s);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        port=server.server_port;host='127.0.0.1:'+str(port)
        def req(path,body=None,headers=None):
            c=http.client.HTTPConnection('127.0.0.1',port,timeout=5);h={'Host':host};h.update(headers or {})
            c.request('POST' if body is not None else 'GET',path,json.dumps(body) if body is not None else None,h);r=c.getresponse();out=(r.status,dict(r.getheaders()),r.read());c.close();return out
        self.assertEqual(server.server_address[0],'127.0.0.1')
        self.assertEqual(req('/api/card',headers={'Host':'evil.invalid'})[0],403)
        h={'Content-Type':'application/json','Origin':'http://evil.invalid'}
        self.assertEqual(req('/api/decision',self.data(),h)[0],403)
        token=self.s.bootstrap_capability();h['Origin']='http://'+host
        status,headers,raw=req('/api/register',{'capability':token,'challenge':self.s.challenge,'confirm_capability_holder':True},h)
        self.assertEqual(status,200);cookie=headers['Set-Cookie'];self.assertIn('HttpOnly',cookie);self.assertIn('SameSite=Strict',cookie)
        csrf=json.loads(raw)['csrf'];h['Cookie']=cookie.split(';')[0]
        authorize_l2(self.s,h['Cookie'].split('=',1)[1],csrf)
        self.assertEqual(req('/api/decision',self.data(),h)[0],403)
        h['X-CSRF-Token']=csrf
        self.assertEqual(req('/api/decision',{**self.data(),'actor':'user'},h)[0],400)
        self.assertEqual(req('/api/decision',self.data(),h)[0],200)

if __name__=='__main__':unittest.main(verbosity=2)
