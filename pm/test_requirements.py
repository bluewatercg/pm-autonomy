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
        import fusion
        with self.store.connect() as con:
            con.execute('BEGIN IMMEDIATE');value=fusion.state(con);fusion.change(con,self.store.session(self.sid),{'operation':'configure','expected_epoch':value['epoch'],'level':'L2','project':fusion.PROJECT,'environment':fusion.ENVIRONMENT,'observation_scope':['history.py'],'control_scope':['history.py'],'tasks':[self.store.card()['proposal']['task']],'actors':[self.store.session(self.sid)['subject']]})
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
        from test_approval import authorize_l2
        authorize_l2(restored,self.sid,self.csrf)
        result=restored.decide(self.sid,self.csrf,d)
        self.assertEqual(result['status'],'APPROVED_EXECUTION_PENDING');self.assertEqual(len(self.snapshot()['outbox']),1)
        self.assertEqual(self.snapshot()['outbox'][0][-1],'QUEUED')
        self.assertEqual(self.store.requirements(self.sid,'R-1')['status'],'converted')

    def recovery_snapshot(self):
        with self.store.connect() as con:
            approval={name:con.execute('SELECT * FROM '+name).fetchall() for name in ('cards','outbox','decisions','failure_occurrences','requirement_events','requirement_requests')}
        db=self.root/'private/governance.sqlite'
        import sqlite3
        with sqlite3.connect(db) as con: events=con.execute('SELECT * FROM events').fetchall()
        projection=self.root/'private/active-proposal.json'
        checkpoint=self.root/'private/governance-checkpoint.json'
        return approval,events,projection.read_bytes() if projection.exists() else None,checkpoint.read_bytes() if checkpoint.exists() else None

    def recovery_denied(self, code):
        before=self.recovery_snapshot()
        with self.assertRaises((Denied,GateDenied)) as error: remote_store.load_store(self.root)
        self.assertEqual(error.exception.code,code)
        self.assertEqual(self.recovery_snapshot(),before)

    def test_terminal_permission_denial_and_normal_destructive_controls(self):
        sid,csrf=self.register('discussion')
        for privilege in ('normal','destructive'):
            control_sid,control_csrf=self.register(privilege)
            for target in ('rejected','superseded'):
                rid='R-'+privilege+'-'+target
                self.write(self.add(rid,'add-'+rid),sid,csrf)
                self.write(self.add(rid+'-next','add-'+rid+'-next'))
                payload=self.command('state',rid=rid,status=target,reason='明确终态决定',superseded_by=rid+'-next' if target=='superseded' else None)
                before=self.snapshot()
                with self.assertRaises(Denied) as error:self.write(payload,sid,csrf)
                self.assertEqual(error.exception.code,'REQUIREMENT_TERMINAL_CAPABILITY_REQUIRED');self.assertEqual(self.snapshot(),before)
                self.assertEqual(self.write(payload,control_sid,control_csrf)['status'],target)
                # 终态缓存也不得绕过权限检查。
                before=self.snapshot()
                with self.assertRaises(Denied):self.write(payload,sid,csrf)
                self.assertEqual(self.snapshot(),before)
        self.write(self.add('R-discussion','add-discussion'),sid,csrf)
        self.write(self.command('state',rid='R-discussion',status='awaiting_decision',reason=None,superseded_by=None),sid,csrf)
        self.write(self.command('defer',2,rid='R-discussion',reason='等待讨论',after_task=[]),sid,csrf)
        self.write(self.command('note',3,rid='R-discussion',note='保留讨论'),sid,csrf)

    def complete_fixture(self,card):
        from roles_gate import write_new,review_binding
        import time
        g=Governance(self.root)
        state=g.snapshot(card)
        path='reviews/complete-'+str(card['version'])+'-plan.json'
        write_new(self.root/path,{'binding':review_binding(state,'large_plan_before_approval'),'verdict':'PASS','pid':os.getpid(),'model':'fixture/model'})
        g.record_review(card,'large_plan_before_approval',path,'SYNTHETIC_TEST_SUBPROCESS',time.time()+3600)
        g.accept(card,{'rc':0,'artifact':card['source_sha'],'base':card['base'],'test_sha':'synthetic-protocol-only'})
        state=g.snapshot(card);path='reviews/complete-'+str(card['version'])+'-close.json'
        write_new(self.root/path,{'binding':review_binding(state,'long_task_closeout'),'verdict':'PASS','pid':os.getpid(),'model':'fixture/model'})
        g.record_review(card,'long_task_closeout',path,'SYNTHETIC_TEST_SUBPROCESS',time.time()+3600);g.close(card)
        with self.store.connect() as con:
            con.execute("UPDATE cards SET status='COMPLETED_SYNTHETIC' WHERE version=?",(card['version'],))
            con.execute('INSERT INTO outbox VALUES(?,?,?,?)',('closed-'+str(card['version']),canonical({'body':{'version':card['version'],'proposal_hash':digest(card)}}),canonical({'ok':True}),'CLOSED'))

    def test_recovery_preserves_completed_dependency_and_closed_receipt(self):
        import bridge
        parent=self.store.card()['proposal'];self.complete_fixture(parent)
        self.write(self.add());self.write(self.command('defer',reason='等待历史闭合',after_task=[parent['task']]))
        self.write(self.conversion(2));self.store=remote_store.load_store(self.root)
        current=self.store.card()['proposal']
        g=Governance(self.root)
        successor={**current,'version':current['version']+1,'parent_proposal_hash':digest(current),'revision_required':True}
        g.register(successor,successor['governance_policy'])
        with self.store.connect() as con:
            con.execute('INSERT INTO outbox VALUES(?,?,NULL,?)',('parent-queued',canonical({'body':{'version':current['version'],'proposal_hash':digest(current)}}),'QUEUED'))
            con.execute('INSERT INTO outbox VALUES(?,?,NULL,?)',('other-queued',canonical({'body':{'version':parent['version'],'proposal_hash':digest(parent)}}),'QUEUED'))
        before=self.snapshot();self.store=remote_store.load_store(self.root)
        self.assertEqual(self.store.card()['proposal'],successor)
        self.assertEqual(bridge.read_proposal(self.root),successor)
        rows=self.snapshot();self.assertEqual(rows['cards'][0],before['cards'][0]);self.assertEqual(rows['outbox'][0],before['outbox'][0])
        self.assertEqual(rows['cards'][1][-1],'SUPERSEDED');self.assertEqual(rows['outbox'][1][-1],'REVOKED');self.assertEqual(rows['outbox'][2],before['outbox'][2])
        self.assertTrue(self.store.requirements(self.sid,'R-1')['ready_for_decision'] is False)  # 已转换保持终态。
        self.write(self.add('R-after','add-after'))
        self.write(self.command('defer',rid='R-after',reason='保留历史完成依赖',after_task=[parent['task']]))
        self.assertTrue(self.store.requirements(self.sid,'R-after')['ready_for_decision'])
        out=self.child("import sys,json;from remote_store import load_store;s=load_store(sys.argv[1]);sid=json.load(open('private/test-session.json'))['sid'];assert s.requirements(sid,'R-after')['ready_for_decision'];print('HISTORICAL_DEPENDENCY_RECOVERED')")
        self.assertEqual(out.returncode,0,out.stderr)
        before=self.recovery_snapshot();remote_store.load_store(self.root);self.assertEqual(self.recovery_snapshot(),before)

    def test_invalid_recovery_zero_writes(self):
        import bridge
        parent=self.store.card()['proposal']
        path=self.root/'private/active-proposal.json'
        for bad in ({**parent,'version':3,'parent_proposal_hash':digest(parent)}, {**parent,'version':2,'parent_proposal_hash':'0'*64}, {**parent,'goal':'同版本漂移'}):
            bridge.save(path,bad)
            self.recovery_denied('REMOTE_PROJECTION_DRIFT' if bad['version']==1 else 'REMOTE_VERSION_CONFLICT')
        bridge.save(path,parent)
        new={**parent,'version':2,'parent_proposal_hash':digest(parent),'revision_required':True}
        Governance(self.root).register(new,new['governance_policy'])
        (self.root/'requirements.py').write_text('漂移的控制源码')
        self.recovery_denied('CONTROL_SOURCE_DRIFT')

    def test_durable_chain_drift_zero_writes(self):
        parent=self.store.card()['proposal']
        for new in ({**parent,'version':3,'parent_proposal_hash':digest(parent)}, {**parent,'version':2,'parent_proposal_hash':'0'*64}):
            with self.store.connect() as con:con.execute('INSERT INTO cards VALUES(?,?,?)',(new['version'],canonical(new),'PENDING'))
            self.recovery_denied('REMOTE_VERSION_CONFLICT')
            with self.store.connect() as con:con.execute('DELETE FROM cards WHERE version=?',(new['version'],))
        with self.store.connect() as con:con.execute('UPDATE cards SET body=? WHERE version=1',(canonical({**parent,'goal':'SQLite 同版本漂移'}),))
        self.recovery_denied('REMOTE_PROJECTION_DRIFT')

    def test_change_sqlite_crash_zero_projection_and_committed_recovery(self):
        import bridge
        parent=self.store.card()['proposal']
        payload={'request_id':'change-crash-request','decision':'change','version':parent['version'],'proposal_hash':digest(parent),'scope':parent['scope'],'base':parent['base'],'expiry':parent['expiry'],'actions':parent['actions'],'note':'中断后恢复'}
        (self.root/'private/change-payload.json').write_text(canonical(payload))
        before=self.recovery_snapshot()
        code="""import sys,os,json,contextlib
from remote_store import load_store
s=load_store(sys.argv[1]);old=s.connect
@contextlib.contextmanager
def crash_connect():
 with old() as con:
  con.create_function('crash_now',0,lambda:os._exit(87));yield con
s.connect=crash_connect
with s.connect() as con:con.execute("CREATE TRIGGER crash_change BEFORE INSERT ON decisions BEGIN SELECT crash_now(); END")
v=json.load(open('private/test-session.json'));s.decide(v['sid'],v['csrf'],json.load(open('private/change-payload.json')))
"""
        out=self.child(code);self.assertEqual(out.returncode,87,out.stderr)
        self.assertEqual(self.recovery_snapshot(),before)
        with self.store.connect() as con:con.execute('DROP TRIGGER crash_change')
        from unittest.mock import patch
        with patch.object(self.store,'sync_version',side_effect=RuntimeError('提交后模拟中断')):
            with self.assertRaises(RuntimeError):self.store.decide(self.sid,self.csrf,payload)
        self.assertEqual(bridge.read_proposal(self.root),parent)
        self.store=remote_store.load_store(self.root)
        self.assertEqual(self.store.card()['proposal']['version'],2)
        self.assertEqual(bridge.read_proposal(self.root),self.store.card()['proposal'])
        self.assertTrue(self.store.decide(self.sid,self.csrf,payload)['cached'])
        self.assertEqual(len(Governance(self.root).export_events()),2)

    def test_legacy_missing_governance_with_history_zero_writes(self):
        g=Governance(self.root);card=self.store.card()['proposal']
        g.failure(card,'ERROR_HISTORY',{'fixture':'must-not-reregister'})
        with self.store.connect() as con:
            con.execute('INSERT INTO failure_occurrences VALUES(?,?,?,1)',('historical-failure',canonical(card),canonical({'ok':False})))
        (self.root/'private/governance-checkpoint.json').unlink()
        (self.root/'private/governance.sqlite').unlink()
        with self.assertRaises(Denied) as error:remote_store.load_store(self.root)
        self.assertEqual(error.exception.code,'GOVERNANCE_HISTORY_LOST')
        self.assertFalse((self.root/'private/governance.sqlite').exists())

    def test_completed_same_task_recovery_is_denied(self):
        parent=self.store.card()['proposal'];self.complete_fixture(parent)
        new={**parent,'version':2,'parent_proposal_hash':digest(parent),'revision_required':True}
        Governance(self.root).register(new,new['governance_policy'])
        self.recovery_denied('COMPLETED_TASK_SUCCESSOR_DENIED')

    def test_governance_loss_and_truncated_history_fail_closed(self):
        g=Governance(self.root);card=self.store.card()['proposal']
        g.failure(card,'SYNTHETIC_ERROR',{'fixture':'history-must-survive'})
        db=self.root/'private/governance.sqlite';saved=db.read_bytes()
        db.unlink()
        with self.assertRaises((Denied,GateDenied)) as error:remote_store.load_store(self.root)
        self.assertEqual(error.exception.code,'GOVERNANCE_HISTORY_LOST');self.assertFalse(db.exists())
        db.write_bytes(saved)
        import sqlite3
        with sqlite3.connect(db) as con:con.execute('DELETE FROM events WHERE seq=(SELECT MAX(seq) FROM events)')
        self.recovery_denied('GOVERNANCE_HISTORY_LOST')
        db.write_bytes(saved)
        with sqlite3.connect(db) as con:con.execute('DELETE FROM events')
        self.recovery_denied('GOVERNANCE_HISTORY_LOST')

    def test_checkpoint_concurrent_commit_uses_one_snapshot(self):
        import sqlite3
        from unittest.mock import patch
        import bridge
        card=self.store.card()['proposal'];reader=Governance(self.root);writer=Governance(self.root)
        with sqlite3.connect(reader.db) as con: con.execute('PRAGMA journal_mode=WAL')
        start=threading.Event();committed=threading.Event();results=[];published=[]
        real_fold=reader.fold;real_save=writer.save_checkpoint;save=bridge.save
        def writer_save():
            committed.set();real_save()
        writer.save_checkpoint=writer_save
        def write():
            try:
                if not start.wait(10): raise AssertionError('并发写入未启动')
                writer.failure(card,'CHECKPOINT_RACE',{'fixture':'between-fold-and-count'})
                results.append('success')
            except Exception as error: results.append(repr(error))
        def paused_fold(con):
            value=real_fold(con);start.set()
            if not committed.wait(10): raise AssertionError('并发提交未完成')
            return value
        def capture(path,value):
            if path==reader.checkpoint: published.append(dict(value))
            save(path,value)
        reader.fold=paused_fold
        thread=threading.Thread(target=write,daemon=True);thread.start()
        with patch.object(bridge,'save',side_effect=capture):
            reader.save_checkpoint();thread.join(10)
        self.assertFalse(thread.is_alive(),'checkpoint 与 SQLite 锁死锁')
        self.assertEqual(results,['success'])
        events=Governance(self.root).export_events()
        self.assertEqual(len(events),2)
        self.assertEqual(sorted(value['seq'] for value in published),[1,2])
        for value in published: self.assertEqual(value['hash'],events[value['seq']-1]['hash'])
        self.assertEqual(json.loads(reader.checkpoint.read_bytes()),{'seq':2,'hash':events[-1]['hash']})
        self.assertEqual(Governance(self.root).snapshot(card)['errors'],{'CHECKPOINT_RACE':1})

    def test_fold_checkpoint_read_precedes_database_snapshot(self):
        import sqlite3
        card=self.store.card()['proposal'];reader=Governance(self.root);writer=Governance(self.root)
        with sqlite3.connect(reader.db) as con: con.execute('PRAGMA journal_mode=WAL')
        # 在 checkpoint 已读取、数据库快照尚未建立时，正常 writer 完整提交并推进 checkpoint。
        with reader.connect() as con:
            class InterleavedConnection:
                @property
                def in_transaction(self): return con.in_transaction
                def execute(proxy,sql,*args):
                    if sql=='BEGIN': writer.failure(card,'READ_RACE',{'fixture':'before-read-snapshot'})
                    return con.execute(sql,*args)
            state,hashed=reader.fold(InterleavedConnection())
        events=Governance(self.root).export_events()
        self.assertEqual(state['tasks'][card['task']]['errors'],{'READ_RACE':1})
        self.assertEqual(hashed,events[-1]['hash'])
        self.assertEqual(json.loads(reader.checkpoint.read_bytes()),{'seq':2,'hash':hashed})

    def test_export_validation_and_rows_share_snapshot(self):
        import sqlite3
        card=self.store.card()['proposal'];reader=Governance(self.root);writer=Governance(self.root)
        with sqlite3.connect(reader.db) as con: con.execute('PRAGMA journal_mode=WAL')
        real_fold=reader.fold
        def paused_fold(con):
            value=real_fold(con)
            writer.failure(card,'EXPORT_RACE',{'fixture':'after-validated-snapshot'})
            return value
        reader.fold=paused_fold
        events=reader.export_events()
        self.assertEqual(len(events),1)
        latest=Governance(self.root).export_events()
        self.assertEqual(len(latest),2);self.assertEqual(events,latest[:1])

    def test_parallel_checkpoint_append_and_failure_no_deadlock(self):
        from concurrent.futures import ThreadPoolExecutor
        card=self.store.card()['proposal'];barrier=threading.Barrier(8)
        def write(i):
            g=Governance(self.root);barrier.wait(timeout=10)
            if i%2: g.failure(card,'PARALLEL',{'fixture':i})
            else: g.append('failure',{'task':card['task'],'error_class':'PARALLEL','evidence':{'fixture':i}},lambda state: None)
            g.save_checkpoint()
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures=[pool.submit(write,i) for i in range(8)]
            for future in futures: future.result(timeout=30)
        g=Governance(self.root);events=g.export_events()
        self.assertEqual(len(events),9)
        self.assertEqual(g.snapshot(card)['errors'],{'PARALLEL':8})
        self.assertEqual(json.loads(g.checkpoint.read_bytes()),{'seq':9,'hash':events[-1]['hash']})

    def test_initialization_new_requirement_registration_and_postcommit_projection(self):
        from unittest.mock import patch
        import bridge
        self.assertEqual(len(Governance(self.root).export_events()),1)
        self.write(self.add());self.write(self.conversion())
        self.store=remote_store.load_store(self.root)
        self.assertEqual(len([e for e in Governance(self.root).export_events() if e['kind']=='register']),2)
        p=self.store.card()['proposal']
        payload={'request_id':'postcommit-change','decision':'change','version':p['version'],'proposal_hash':digest(p),'scope':p['scope'],'base':p['base'],'expiry':p['expiry'],'actions':p['actions'],'note':'修订待审批方案'}
        old_save=bridge.save
        def checked_save(path,value):
            with self.store.connect() as con:
                con.execute('BEGIN IMMEDIATE')
                if path.name=='active-proposal.json':
                    latest=json.loads(con.execute('SELECT body FROM cards ORDER BY version DESC LIMIT 1').fetchone()[0])
                    self.assertEqual(latest,value)
            old_save(path,value)
        with patch.object(bridge,'save',side_effect=checked_save):self.store.decide(self.sid,self.csrf,payload)
        self.assertEqual(bridge.read_proposal(self.root)['version'],p['version']+1)
        self.assertEqual(len(Governance(self.root).export_events()),3)

if __name__=='__main__': unittest.main(verbosity=2)
