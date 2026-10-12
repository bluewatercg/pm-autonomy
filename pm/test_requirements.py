"""真实 HTTP、SQLite、新进程与崩溃恢复；合成 reviewer 仅验证协议。"""
import contextlib
import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from approval_api import Denied, canonical, digest, make_server
from initialize import initialize
import remote_store
from requirements import fold
from roles_gate import Governance, GateDenied

class RequirementsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)/'runtime'; initialize(self.root, fixture=True)
        self.previous_root = remote_store.R; remote_store.R = self.root
        self.addCleanup(setattr, remote_store, 'R', self.previous_root)
        self.store = remote_store.load_store(self.root)
        self.sid, self.csrf = self.register('normal')
        self.persist()

    def persist(self):
        import bridge
        bridge.save(self.root/'private/sessions.json', {'sessions': self.store.sessions, 'capabilities': self.store.capabilities, 'challenge': self.store.challenge})
        (self.root/'private/test-session.json').write_text(canonical({'sid': self.sid, 'csrf': self.csrf}))

    def register(self, privilege):
        return self.store.register({'capability': self.store.bootstrap_capability(privilege), 'challenge': self.store.challenge, 'confirm_capability_holder': True})

    def add(self, rid='R-1', request='request-add-1'):
        return {'request_id': request, 'operation': 'add', 'requirement_id': rid, 'expected_version': 0,
                'source': {'ref': '合成测试输入，非用户历史批准', 'time': '2026-10-11T12:00:00+00:00', 'uncertain': True},
                'goal': '讨论并绑定合成排序合同', 'owner': None, 'priority': None, 'open_questions': ['是否决定进入审批？']}

    def command(self, op, version=1, rid='R-1', **values):
        return {'request_id': 'request-'+op+'-'+str(version)+'-'+rid, 'operation': op,
                'requirement_id': rid, 'expected_version': version, **values}

    def write(self, data, sid=None, csrf=None):
        return self.store.requirement_write(sid or self.sid, csrf or self.csrf, data)

    def snapshot(self):
        with self.store.connect() as con:
            return {name: con.execute('SELECT * FROM '+name).fetchall() for name in ('requirement_events', 'requirement_requests', 'cards', 'decisions', 'outbox')}

    def denied(self, data, code):
        before = self.snapshot()
        with self.assertRaises(Denied) as error: self.write(data)
        self.assertEqual(error.exception.code, code); self.assertEqual(self.snapshot(), before)

    def conversion(self, version=1, rid='R-1', **extra):
        card = self.store.card()
        return self.command('convert', version, rid, proposal_version=card['proposal']['version'], proposal_hash=card['proposal_hash'], scope=['history.py'], **extra)

    def child(self, code, *args):
        return subprocess.run([sys.executable, '-c', code, str(self.root), *args], cwd=self.root, capture_output=True, text=True, timeout=30)

    def test_lifecycle_replay_history_and_source(self):
        self.write(self.add())
        self.write(self.command('note', note='暂未决定，保留问题'))
        self.write(self.command('update', 2, owner=None, priority='high', open_questions=['排序验收？'], after_task=[]))
        self.write(self.command('defer', 3, reason='先讨论验收', after_task=['TEST-PENDING-DEMO']))
        value = self.store.requirements(self.sid, 'R-1')
        self.assertEqual(value['status_label'], '暂缓'); self.assertFalse(value['ready_for_decision'])
        self.assertEqual(len(value['events']), 4); self.assertIsNone(value['task_id'])
        self.write(self.command('state', 4, status='awaiting_decision', reason=None, superseded_by=None))
        with self.store.connect() as con:
            state, history, _ = fold(con)
        self.assertEqual(state['R-1']['version'], 5); self.assertEqual(state['R-1']['source'], self.add()['source'])
        self.assertEqual(len(history['R-1']), 5)
        out = self.child("import sys,json;from remote_store import load_store; s=load_store(sys.argv[1]);sid=json.load(open('private/test-session.json'))['sid'];v=s.requirements(sid,'R-1');assert v['version']==5;print('REPLAY_OK')")
        self.assertEqual(out.returncode, 0, out.stderr); self.assertIn('REPLAY_OK', out.stdout)

    def test_bad_payload_zero_writes_versions_and_idempotency(self):
        add = self.add(); self.write(add); self.assertTrue(self.write(add)['cached'])
        changed = {**add, 'goal': '不同内容'}; self.denied(changed, 'IDEMPOTENCY_CONFLICT')
        self.denied(self.command('note', 0, note='旧版本'), 'REQUIREMENT_VERSION_CONFLICT')
        for bad in [self.command('note', note=''), self.command('state', status='complete', reason='自报完成', superseded_by=None), self.command('defer', reason='', after_task=[])]:
            self.denied(bad, {'note':'REQUIREMENT_TEXT_INVALID','state':'REQUIREMENT_STATE_INVALID','defer':'REQUIREMENT_TEXT_INVALID'}[bad['operation']])
        self.denied(self.command('defer', reason='等待未知任务', after_task=['UNKNOWN']), 'REQUIREMENT_UNKNOWN_TASK')
        self.denied({**self.command('note', note='内容'), 'actor':'admin'}, 'REQUIREMENT_PAYLOAD_INVALID')
        self.denied(self.command('state', status='rejected', reason=None, superseded_by=None), 'REQUIREMENT_TEXT_INVALID')
        bad = self.add('R-new', 'request-bad-source'); bad['source']['time']='unknown'
        self.denied(bad, 'REQUIREMENT_SOURCE_TIME_INVALID')
        self.denied(self.command('defer', reason='坏依赖形状', after_task=[[]]), 'REQUIREMENT_DEPENDENCIES_INVALID')
        self.denied(self.command('update', owner=None, priority=[], open_questions=[], after_task=[]), 'REQUIREMENT_PRIORITY_INVALID')

    def test_supersession_exists_self_and_cycle(self):
        self.write(self.add()); self.write(self.add('R-2', 'request-add-2'))
        self.denied(self.command('state', status='superseded', reason='取代', superseded_by='R-1'), 'REQUIREMENT_SUPERSESSION_CYCLE')
        self.denied(self.command('state', status='superseded', reason='取代', superseded_by='R-none'), 'REQUIREMENT_REPLACEMENT_UNKNOWN')
        self.write(self.command('state', status='superseded', reason='由 R-2 承接', superseded_by='R-2'))
        self.denied(self.command('state', rid='R-2', status='superseded', reason='环', superseded_by='R-1'), 'REQUIREMENT_SUPERSESSION_CYCLE')
        self.denied(self.command('note', 2, note='重开旧需求'), 'REQUIREMENT_TERMINAL')
        self.assertEqual(len(self.store.requirements(self.sid, 'R-1')['events']), 2)

    def test_conversion_pending_bidirectional_no_authorization(self):
        self.write(self.add()); convert = self.conversion(); result = self.write(convert)
        self.assertEqual(result['status'], 'converted'); self.assertTrue(self.write(convert)['cached'])
        card = self.store.card(); req = self.store.requirements(self.sid, 'R-1')
        self.assertEqual(card['status'], 'PENDING'); self.assertEqual(card['proposal']['source_requirement']['requirement_id'], 'R-1')
        self.assertEqual(card['proposal']['source_requirement']['version'], req['version'])
        self.assertEqual(req['proposal']['proposal_hash'], card['proposal_hash'])
        self.assertEqual(req['task_id'], card['proposal']['task']); self.assertIsNone(req['decision_reason'])
        self.assertEqual(len(self.snapshot()['decisions']), 0); self.assertEqual(len(self.snapshot()['outbox']), 0)
        with self.assertRaises(Denied) as error: self.store.execute(self.sid, self.csrf, 'no-approval')
        self.assertEqual(error.exception.code, 'APPROVAL_REQUIRED')
        out = self.child("import sys,json;import bridge;from remote_store import load_store;s=load_store(sys.argv[1]);assert s.card()['status']=='PENDING';assert bridge.read_proposal(__import__('pathlib').Path(sys.argv[1]))==s.card()['proposal'];print('PROJECTION_RECOVERED')")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn('PROJECTION_RECOVERED', out.stdout)

    def test_conversion_scope_version_actor_and_approval_permission(self):
        self.write(self.add()); bad = self.conversion(); bad['scope']=['business.py']
        self.denied(bad, 'REQUIREMENT_SYNTHETIC_SCOPE_ONLY')
        bad = self.conversion(); bad['proposal_version']=999; self.denied(bad, 'STALE_PROPOSAL')
        sid, csrf = self.register('discussion')
        data = self.command('note', note='仅讨论权限'); self.write(data, sid, csrf)
        before = self.snapshot()
        for operation in [lambda: self.write(self.conversion(2), sid, csrf), lambda: self.store.decide(sid, csrf, {}), lambda: self.store.execute(sid, csrf, 'x')]:
            with self.assertRaises(Denied): operation()
            self.assertEqual(self.snapshot(), before)
        conflict = {**data, 'request_id': data['request_id']}
        self.denied(conflict, 'IDEMPOTENCY_CONFLICT')

    def test_trusted_completion_query_only_no_execute_and_forgery(self):
        self.write(self.add()); self.write(self.command('defer', reason='等前序验收', after_task=['TEST-PENDING-DEMO']))
        self.denied(self.conversion(2), 'REQUIREMENT_DEPENDENCIES_BLOCKED')
        card = self.store.card()['proposal']; governance = Governance(self.root)
        with self.assertRaises(GateDenied): governance.close(card)
        with self.store.connect() as con:
            con.execute("UPDATE cards SET status='COMPLETED_SYNTHETIC'")
        self.assertFalse(self.store.requirements(self.sid, 'R-1')['ready_for_decision'])
        # 已有 gate 的确定性协议夹具；不声称执行器或真实模型验收通过。
        state = governance.snapshot(card)
        from roles_gate import review_binding, write_new
        for trigger in ('large_plan_before_approval',):
            path='reviews/trusted-'+trigger+'.json'
            write_new(self.root/path, {'binding':review_binding(state, trigger), 'verdict':'PASS', 'pid':os.getpid(), 'model':'fixture/model'})
            governance.record_review(card, trigger, path, 'SYNTHETIC_TEST_SUBPROCESS', __import__('time').time()+3600)
        governance.accept(card, {'rc':0,'artifact':card['source_sha'],'base':card['base'],'test_sha':'synthetic-protocol-test'})
        state = governance.snapshot(card); trigger='long_task_closeout'; path='reviews/trusted-close.json'
        write_new(self.root/path, {'binding':review_binding(state, trigger), 'verdict':'PASS', 'pid':os.getpid(), 'model':'fixture/model'})
        governance.record_review(card, trigger, path, 'SYNTHETIC_TEST_SUBPROCESS', __import__('time').time()+3600); governance.close(card)
        self.assertFalse(self.store.requirements(self.sid, 'R-1')['ready_for_decision'])
        with self.store.connect() as con:
            con.execute('INSERT INTO outbox VALUES(?,?,?,?)', ('fixture-accepted', canonical({'body':{'proposal_hash':digest(card)}}), canonical({'ok':True}), 'CLOSED'))
        before = self.snapshot(); req = self.store.requirements(self.sid, 'R-1')
        self.assertTrue(req['ready_for_decision']); self.assertEqual(req['status'], 'deferred'); self.assertFalse(req['automatic_execution'])
        self.assertEqual(self.snapshot(), before)
        self.write(self.conversion(2))
        self.assertEqual(self.store.requirements(self.sid, 'R-1')['task_id'], 'TEST-REQ-R-1')
        with self.store.connect() as con:
            self.assertEqual(con.execute('SELECT status FROM cards WHERE version=1').fetchone()[0], 'COMPLETED_SYNTHETIC')
        self.write(self.add('R-2', 'request-add-2'))
        self.write(self.command('defer', rid='R-2', reason='保留可信前序', after_task=['TEST-PENDING-DEMO']))
        self.assertTrue(self.store.requirements(self.sid,'R-2')['ready_for_decision'])
        with self.store.connect() as con: con.execute('DELETE FROM cards WHERE version=1')
        self.assertFalse(self.store.requirements(self.sid, 'R-2')['ready_for_decision'])

    def test_sqlite_conversion_crash_rolls_back_and_retry(self):
        self.write(self.add()); payload = self.conversion(); (self.root/'private/payload.json').write_text(canonical(payload)); before = self.snapshot()
        code = """import os,sys,json,contextlib
from remote_store import load_store
s=load_store(sys.argv[1]); old=s.connect
@contextlib.contextmanager
def crash_connect():
 with old() as con:
  con.create_function('crash_now',0,lambda:os._exit(87))
  yield con
s.connect=crash_connect
with s.connect() as con:con.execute(\"CREATE TRIGGER crash_conversion BEFORE INSERT ON requirement_events WHEN NEW.kind='convert' BEGIN SELECT crash_now(); END\")
v=json.load(open('private/test-session.json'));s.requirement_write(v['sid'],v['csrf'],json.load(open('private/payload.json')))
"""
        proc = self.child(code); self.assertEqual(proc.returncode, 87, proc.stderr); self.assertEqual(self.snapshot(), before)
        with self.store.connect() as con: con.execute('DROP TRIGGER crash_conversion')
        result = self.write(payload); self.assertEqual(result['status'], 'converted')
        self.assertEqual(len(self.snapshot()['cards']), 2); self.assertEqual(len(self.snapshot()['requirement_events']), 2)

    def test_new_process_concurrent_same_and_conflicting_requests(self):
        self.write(self.add()); payload=self.command('note', note='并发讨论')
        code="import sys,json;from remote_store import load_store;from approval_api import Denied;s=load_store(sys.argv[1]);v=json.load(open('private/test-session.json'));d=json.loads(sys.argv[2]);\ntry:print(json.dumps(s.requirement_write(v['sid'],v['csrf'],d)))\nexcept Denied as e:print(json.dumps({'code':e.code}))"
        processes=[subprocess.Popen([sys.executable,'-c',code,str(self.root),canonical(payload)],cwd=self.root,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(4)]
        results=[]
        for proc in processes:
            out, err=proc.communicate(timeout=30); self.assertEqual(proc.returncode,0,err);results.append(json.loads(out))
        self.assertEqual(sum(not x['cached'] for x in results),1);self.assertEqual(self.store.requirements(self.sid,'R-1')['version'],2)
        processes=[subprocess.Popen([sys.executable,'-c',code,str(self.root),canonical(self.command('note',2,note='写者 '+str(i)))],cwd=self.root,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for i in range(2)]
        results=[]
        for proc in processes:
            out,err=proc.communicate(timeout=30); self.assertEqual(proc.returncode,0,err);results.append(json.loads(out))
        self.assertEqual(sum(x.get('code')=='IDEMPOTENCY_CONFLICT' for x in results),1)
        with self.store.connect() as con: state,_,_=fold(con)
        self.assertEqual(state['R-1']['version'],3)
        payloads=[{**self.command('note',3,note='版本竞争 '+str(i)), 'request_id':'distinct-request-'+str(i)} for i in range(2)]
        processes=[subprocess.Popen([sys.executable,'-c',code,str(self.root),canonical(data)],cwd=self.root,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for data in payloads]
        results=[]
        for proc in processes:
            out,err=proc.communicate(timeout=30);self.assertEqual(proc.returncode,0,err);results.append(json.loads(out))
        self.assertEqual(sum(x.get('code')=='REQUIREMENT_VERSION_CONFLICT' for x in results),1)
        self.assertEqual(self.store.requirements(self.sid,'R-1')['version'],4)

    def test_audit_tamper_detected_no_new_write(self):
        self.write(self.add())
        with self.store.connect() as con: con.execute("UPDATE requirement_events SET hash=?", ('a'*64,))
        before = self.snapshot()
        with self.assertRaises(Denied) as error: self.write(self.command('note', note='不应追加'))
        self.assertEqual(error.exception.code,'REQUIREMENT_CHAIN_BROKEN');self.assertEqual(self.snapshot(),before)

    def test_http_real_server_permissions_legal_control_and_malformed(self):
        server=make_server(self.store); thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        port=server.server_port
        def request(method,path,data=None,sid=None,csrf=None,host=None,origin=None,raw=None):
            con=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
            headers={'Host':host or '127.0.0.1:'+str(port),'Origin':origin or 'http://127.0.0.1:'+str(port),'Content-Type':'application/json','Cookie':'approval_session='+(sid or self.sid),'X-CSRF-Token':self.csrf if csrf is None else csrf}
            body=raw if raw is not None else canonical(data) if data is not None else None
            con.request(method,path,body.encode('utf-8') if body is not None else None,headers)
            response=con.getresponse();body=response.read();status=response.status;con.close();return status,body
        self.assertEqual(request('GET','/requirements')[0],200)
        self.assertEqual(request('GET','/requirements.js')[0],200)
        before=self.snapshot()
        for kwargs in [{'csrf':'wrong'},{'origin':'http://invalid.example'},{'host':'invalid.example'},{'sid':'forged'}]:
            self.assertIn(request('POST','/api/requirements',self.add(),**kwargs)[0],(401,403));self.assertEqual(self.snapshot(),before)
        for raw in ('[]','null','{',canonical({**self.add(),'task_complete':True})):
            self.assertEqual(request('POST','/api/requirements',raw=raw)[0],400);self.assertEqual(self.snapshot(),before)
        self.assertEqual(request('POST','/api/requirements',self.add())[0],200)
        status,body=request('GET','/api/requirements/R-1');self.assertEqual(status,200);self.assertEqual(json.loads(body)['status'],'discussing')
        self.assertEqual(request('POST','/api/requirements',self.conversion())[0],200)
        self.assertEqual(self.store.card()['status'],'PENDING');self.assertEqual(len(self.snapshot()['outbox']),0)

    def test_http_separate_process_persistent_store_and_restart(self):
        code="import sys;import remote_store;remote_store.R=__import__('pathlib').Path(sys.argv[1]);from approval_api import RemoteStore,make_server;s=make_server(RemoteStore());print(s.server_port,flush=True);s.serve_forever()"
        def start():
            proc=subprocess.Popen([sys.executable,'-c',code,str(self.root)],cwd=self.root,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            self.addCleanup(lambda: proc.wait(timeout=10));self.addCleanup(proc.terminate)
            return proc,int(proc.stdout.readline())
        def request(port,method,data=None):
            c=http.client.HTTPConnection('127.0.0.1',port,timeout=10)
            headers={'Content-Type':'application/json','Cookie':'approval_session='+self.sid,'X-CSRF-Token':self.csrf,'Origin':'http://127.0.0.1:'+str(port)}
            c.request(method,'/api/requirements',canonical(data).encode() if data else None,headers)
            response=c.getresponse();value=json.loads(response.read());status=response.status;c.close();return status,value
        proc,port=start();self.assertEqual(request(port,'POST',self.add())[0],200)
        self.assertEqual(request(port,'POST',self.conversion())[0],200)
        proc.terminate();proc.wait(timeout=10)
        _,port=start();status,value=request(port,'GET');self.assertEqual(status,200)
        self.assertEqual(value['requirements'][0]['status'],'converted');self.assertEqual(len(value['requirements'][0]['events']),2)
        self.assertEqual(len(self.snapshot()['outbox']),0)

    def test_cli_help_read_archive_and_legitimate_approval_fixture(self):
        before=self.snapshot()
        for command in [[],['convert','--help'],['--help']]:
            proc=subprocess.run([sys.executable,str(self.root/'requirements_cli.py'),*command],capture_output=True,text=True,timeout=5)
            self.assertIn(proc.returncode,(0,2))
        self.assertEqual(self.snapshot(),before)
        self.write(self.add());self.write(self.conversion())
        for command in [['read','R-1'],['list'],['archive']]:
            proc=subprocess.run([sys.executable,str(self.root/'requirements_cli.py'),'--root',str(self.root),'--session-file',str(self.root/'private/test-session.json'),*command],cwd=self.root,capture_output=True,text=True,timeout=30)
            self.assertEqual(proc.returncode,0,proc.stderr+proc.stdout)
        archive=json.loads((self.root/'private/requirement-archives/R-1.json').read_bytes())
        self.assertEqual(archive['status'],'converted');self.assertEqual(len(archive['events']),2)
        from roles_gate import write_new,review_binding
        restored=remote_store.load_store(self.root);card=restored.card();p=card['proposal'];g=Governance(self.root);state=g.snapshot(p)
        path='reviews/converted-approval.json';write_new(self.root/path,{'binding':review_binding(state,'large_plan_before_approval'),'verdict':'PASS','pid':os.getpid(),'model':'fixture/model'})
        g.record_review(p,'large_plan_before_approval',path,'SYNTHETIC_TEST_SUBPROCESS',__import__('time').time()+3600)
        d={'request_id':'fixture-approve-converted','decision':'approve','version':p['version'],'proposal_hash':card['proposal_hash'],'scope':p['scope'],'base':p['base'],'expiry':p['expiry'],'actions':p['actions'],'note':'仅协议夹具批准'}
        result=restored.decide(self.sid,self.csrf,d)
        self.assertEqual(result['status'],'APPROVED_EXECUTION_PENDING');self.assertEqual(len(self.snapshot()['outbox']),1)
        self.assertEqual(self.snapshot()['outbox'][0][-1],'QUEUED')
        self.assertEqual(self.store.requirements(self.sid,'R-1')['status'],'converted')

if __name__=='__main__': unittest.main(verbosity=2)
