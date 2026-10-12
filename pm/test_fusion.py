"""真实 HTTP/SQLite/子进程及确定性交错；不将假 dispatcher 标成容器闭环。"""
import http.client
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from approval_api import Store, Denied, canonical, digest, make_server
import fusion

class FusionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.calls=[]
        self.p={'task':'TEST-FUSION','version':1,'project':fusion.PROJECT,'environment':fusion.ENVIRONMENT,
                'scope':['history.py'],'base':'base','before_sha':'before','source_sha':'source',
                'expiry':time.time()+3600,'actions':['edit'],'change':'normal'}
        self.s=Store(self.root,self.p,secrets.token_bytes(32),lambda p:{'base':p['base'],'before_sha':p['before_sha']},self.dispatch,lambda p:None)
        self.sid,self.csrf=self.register('normal')
    def register(self,privilege):
        return self.s.register({'capability':self.s.bootstrap_capability(privilege),'challenge':self.s.challenge,'confirm_capability_holder':True})
    def config(self,level='L2',**extra):
        value=self.s.fusion_status(self.sid)
        return {'operation':'configure','expected_epoch':value['epoch'],'level':level,'project':fusion.PROJECT,'environment':fusion.ENVIRONMENT,
                'observation_scope':['history.py'],'control_scope':['history.py'] if level=='L2' else [],
                'tasks':[self.p['task']] if level=='L2' else [],'actors':[self.s.session(self.sid)['subject']] if level=='L2' else [],**extra}
    def configure(self,level='L2',**extra):return self.s.fusion_control(self.sid,self.csrf,self.config(level,**extra))
    def decision(self,rid='fusion-request-001'):
        card=self.s.card();p=card['proposal']
        return {'request_id':rid,'decision':'approve','version':p['version'],'proposal_hash':card['proposal_hash'],'scope':p['scope'],
                'base':p['base'],'expiry':p['expiry'],'actions':p['actions'],'note':''}
    def dispatch(self,receipt):
        with fusion.start(self.s.db,receipt['body']):self.calls.append(receipt['body']['request_id'])
        return {'ok':True,'fixture_dispatch_only':True}
    def assertDenied(self,code,fn):
        with self.assertRaises(Denied) as error:fn()
        self.assertEqual(error.exception.code,code)
    def approve(self):
        self.s.decide(self.sid,self.csrf,self.decision())
        with self.s.connect() as con:return json.loads(con.execute('SELECT receipt FROM outbox').fetchone()[0])
    def add(self):
        return {'request_id':'fusion-add','operation':'add','requirement_id':'R-1','expected_version':0,
                'source':{'ref':'合成测试','time':'2026-10-12T00:00:00Z','uncertain':True},'goal':'讨论排序合同','owner':None,'priority':None,'open_questions':[]}
    def test_default_l1_no_dispatch_and_l1_collaboration(self):
        self.assertEqual(self.s.fusion_status(self.sid)['level'],'L1')
        self.assertDenied('FUSION_EXECUTION_LEVEL_REQUIRED',lambda:self.s.decide(self.sid,self.csrf,self.decision()))
        sid,csrf=self.register('discussion')
        self.assertEqual(self.s.requirement_write(sid,csrf,self.add())['status'],'discussing')
        self.assertEqual(self.calls,[])
        with self.s.connect() as con:self.assertEqual(con.execute('SELECT count(*) FROM outbox').fetchone()[0],0)
    def test_l0_read_and_discussion_denied(self):
        self.configure('L0')
        self.assertEqual(self.s.requirements(self.sid)['requirements'],[])
        self.assertDenied('FUSION_L0_READ_ONLY',lambda:self.s.requirement_write(self.sid,self.csrf,self.add()))
    def test_l2_legal_control_and_scope_environment_actor_denied(self):
        self.configure();self.approve()
        self.assertTrue(self.s.execute(self.sid,self.csrf,'fusion-request-001')['ok'])
        self.assertEqual(self.calls,['fusion-request-001'])
        self.assertTrue(self.s.execute(self.sid,self.csrf,'fusion-request-001')['cached'])
        with self.s.connect() as con:
            for field,value,code in [('scope',['other.py'],'FUSION_SCOPE_DENIED'),('environment','prod','FUSION_ENVIRONMENT_DENIED'),('project','business','FUSION_PROJECT_DENIED')]:
                self.assertDenied(code,lambda field=field,value=value:fusion.binding(con,{**self.p,field:value},self.s.session(self.sid)['subject']))
            self.assertDenied('FUSION_ACTOR_DENIED',lambda:fusion.binding(con,self.p,'unregistered'))
    def test_discussion_cannot_upgrade_and_l3_generic_blocked(self):
        sid,csrf=self.register('discussion')
        self.assertDenied('FUSION_OWNER_CAPABILITY_REQUIRED',lambda:self.s.fusion_control(sid,csrf,self.config()))
        self.assertDenied('FUSION_OWNER_CAPABILITY_REQUIRED',lambda:self.s.fusion_control(sid,csrf,{'operation':'pause'}))
        self.assertDenied('BLOCKED_FLOW_ADAPTER_UNAVAILABLE',lambda:self.configure('L3'))
        self.assertDenied('BLOCKED_PROJECT_ADAPTER_UNAVAILABLE',lambda:self.configure(project='business'))
        self.assertDenied('FUSION_ENVIRONMENT_DENIED',lambda:self.configure(environment='prod'))
    def test_epoch_old_receipt_pause_downgrade_history(self):
        self.configure();receipt=self.approve();epoch=receipt['body']['fusion']['epoch']
        self.s.fusion_control(self.sid,self.csrf,{'operation':'pause'})
        with self.s.connect() as con:
            self.assertDenied('FUSION_EPOCH_STALE',lambda:fusion.verify(con,receipt['body']))
            self.assertEqual(con.execute('SELECT phase FROM outbox').fetchone()[0],'REVOKED')
        value=self.configure('L0');self.assertGreater(value['epoch'],epoch)
        with self.s.connect() as con:self.assertEqual(con.execute('SELECT count(*) FROM decisions').fetchone()[0],1)
        self.assertEqual(self.calls,[])
    def test_pause_between_reserve_and_start_zero_dispatch(self):
        self.configure();self.approve();reserved=threading.Event();release=threading.Event();errors=[]
        def delayed(receipt):
            reserved.set();self.assertTrue(release.wait(5));return self.dispatch(receipt)
        self.s.dispatch=delayed
        def execute():
            try:self.s.execute(self.sid,self.csrf,'fusion-request-001')
            except Denied as exc:errors.append(exc.code)
        t=threading.Thread(target=execute);t.start();self.assertTrue(reserved.wait(5))
        self.s.fusion_control(self.sid,self.csrf,{'operation':'pause'});release.set();t.join(5)
        self.assertFalse(t.is_alive());self.assertEqual(self.calls,[])
        self.assertEqual(self.s.fusion_status(self.sid)['inflight'],[])
    def test_running_pause_handoff_unknown_no_resend(self):
        self.configure();self.approve();started=threading.Event();release=threading.Event();errors=[]
        def running(receipt):
            with fusion.start(self.s.db,receipt['body']):self.calls.append('start')
            started.set();self.assertTrue(release.wait(5));raise ConnectionError('测试在途未知')
        self.s.dispatch=running
        def execute():
            try:self.s.execute(self.sid,self.csrf,'fusion-request-001')
            except Denied as exc:errors.append(exc.code)
        t=threading.Thread(target=execute);t.start();self.assertTrue(started.wait(5))
        value=self.s.fusion_control(self.sid,self.csrf,{'operation':'pause'})
        self.assertEqual(value['inflight'][0]['state'],'RUNNING');self.assertEqual(value['downgrade_status'],'HANDOFF_REQUIRED')
        release.set();t.join(5);self.assertFalse(t.is_alive())
        self.assertEqual(self.s.fusion_status(self.sid)['inflight'][0]['state'],'UNKNOWN')
        self.assertDenied('FUSION_EPOCH_STALE',lambda:self.s.execute(self.sid,self.csrf,'fusion-request-001'))
        value=self.configure('L1');self.assertEqual(value['inflight'][0]['state'],'UNKNOWN')
        self.assertDenied('FUSION_PAUSE_AND_HANDOFF_REQUIRED',lambda:self.configure())
        self.assertEqual(self.calls,['start'])
    def test_new_process_restart_pause_and_owner_session_recovery(self):
        self.configure();self.s.fusion_control(self.sid,self.csrf,{'operation':'pause'})
        code="import sys,json;from pathlib import Path;import fusion;with_db=Path(sys.argv[1]);c=__import__('sqlite3').connect(with_db);v=fusion.snapshot(c);assert v['paused'] and v['level']=='L2';print(json.dumps({'paused':v['paused'],'epoch':v['epoch']}))"
        p=subprocess.run([sys.executable,'-c',code,str(self.s.db)],capture_output=True,text=True,timeout=10)
        self.assertEqual(p.returncode,0,p.stderr)
        sid,csrf=self.register('normal')
        self.s.fusion_control(sid,csrf,self.config('L1'))
        self.assertEqual(self.s.fusion_status(sid)['level'],'L1')
    def test_pause_new_process_wins_before_start(self):
        self.configure();receipt=self.approve()
        with self.s.connect() as con:con.execute('INSERT INTO fusion_runs VALUES(?,?,?)',('fusion-request-001',canonical(receipt['body']['fusion']),'RESERVED'))
        ready=self.root/'ready';release=self.root/'release';started=self.root/'started';spec=self.root/'body.json';spec.write_bytes(canonical(receipt['body']).encode())
        code="import sys,json,time;from pathlib import Path;import fusion;db,spec,ready,release,started=map(Path,sys.argv[1:]);ready.touch();\nwhile not release.exists():time.sleep(.01)\ntry:\n with fusion.start(db,json.loads(spec.read_bytes())):started.touch()\nexcept Exception as e:print(getattr(e,'code',type(e).__name__))"
        proc=subprocess.Popen([sys.executable,'-c',code,str(self.s.db),str(spec),str(ready),str(release),str(started)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        deadline=time.monotonic()+5
        while not ready.exists() and time.monotonic()<deadline:time.sleep(.01)
        self.assertTrue(ready.exists());self.s.fusion_control(self.sid,self.csrf,{'operation':'pause'});release.touch()
        out,err=proc.communicate(timeout=5);self.assertEqual(proc.returncode,0,err);self.assertIn('FUSION_EPOCH_STALE',out);self.assertFalse(started.exists())
    def test_broker_write_transaction_blocks_pause_until_handoff(self):
        self.configure();receipt=self.approve()
        with self.s.connect() as con:con.execute('INSERT INTO fusion_runs VALUES(?,?,?)',('fusion-request-001',canonical(receipt['body']['fusion']),'RUNNING'))
        ready=self.root/'write-ready';release=self.root/'write-release';artifact=self.root/'artifact';spec=self.root/'body.json';spec.write_bytes(canonical(receipt['body']).encode())
        code="import sys,json,time;from pathlib import Path;import fusion;db,spec,ready,release,artifact=map(Path,sys.argv[1:]);\nwith fusion.transaction(db) as con:\n fusion.verify(con,json.loads(spec.read_bytes()),require_run=True);ready.touch()\n while not release.exists():time.sleep(.01)\n artifact.write_bytes(b'bounded write')"
        proc=subprocess.Popen([sys.executable,'-c',code,str(self.s.db),str(spec),str(ready),str(release),str(artifact)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        deadline=time.monotonic()+5
        while not ready.exists() and time.monotonic()<deadline:time.sleep(.01)
        self.assertTrue(ready.exists());done=threading.Event();entered=threading.Event()
        def pause():entered.set();self.s.fusion_control(self.sid,self.csrf,{'operation':'pause'});done.set()
        thread=threading.Thread(target=pause);thread.start();self.assertTrue(entered.wait(5));self.assertFalse(done.wait(.05))
        release.touch();out,err=proc.communicate(timeout=5);thread.join(5);self.assertEqual(proc.returncode,0,err);self.assertTrue(done.is_set());self.assertTrue(artifact.exists())
        with self.s.connect() as con:self.assertDenied('FUSION_EPOCH_STALE',lambda:fusion.verify(con,receipt['body'],require_run=True))
    def test_cli_pause_independent_of_approval_lock_and_model(self):
        from initialize import initialize
        import remote_store,bridge,fcntl
        root=self.root/'runtime';initialize(root,fixture=True);store=remote_store.load_store(root)
        sid,csrf=store.register({'capability':store.bootstrap_capability(),'challenge':store.challenge,'confirm_capability_holder':True})
        bridge.save(root/'private/sessions.json',{'sessions':store.sessions,'capabilities':store.capabilities,'challenge':store.challenge})
        auth=root/'private/cli-session.json';auth.write_bytes(canonical({'sid':sid,'csrf':csrf}).encode())
        with (root/'private/approval.lock').open('a+b') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            p=subprocess.run([sys.executable,str(root/'fusion_cli.py'),'--root',str(root),'--session-file',str(auth),'pause'],capture_output=True,text=True,timeout=5)
        self.assertEqual(p.returncode,0,p.stderr);self.assertTrue(json.loads(p.stdout)['result']['paused'])
        p=subprocess.run([sys.executable,str(root/'fusion_cli.py'),'--root',str(root),'--session-file',str(auth),'status'],capture_output=True,text=True,timeout=5)
        self.assertEqual(p.returncode,0,p.stderr);self.assertTrue(json.loads(p.stdout)['result']['paused'])
    def test_fusion_history_loss_and_legacy_no_silent_reset(self):
        import sqlite3
        for table in ['fusion_state','fusion_events','fusion_runs']:
            copy=self.root/(table+'.sqlite')
            with self.s.connect() as con,sqlite3.connect(copy) as other:con.backup(other)
            with sqlite3.connect(copy) as con:
                con.execute('DROP TABLE '+table)
                self.assertDenied('FUSION_HISTORY_LOST',lambda:fusion.schema(con))
        self.configure();self.approve()
        with self.s.connect() as con:
            for table in ['fusion_state','fusion_events','fusion_runs']:con.execute('DROP TABLE '+table)
            self.assertDenied('FUSION_LEGACY_HISTORY_REQUIRES_REVIEW',lambda:fusion.schema(con))

    def test_bridge_online_protocol_and_failure_fences(self):
        import bridge
        self.configure();receipt=self.approve();body=receipt['body']
        with self.s.connect() as con:con.execute('INSERT INTO fusion_runs VALUES(?,?,?)',(body['request_id'],canonical(body['fusion']),'RUNNING'))
        prelude="import sys,json,time;json.loads(sys.stdin.readline());\n"
        begin="print(json.dumps({'fusion_fence':'begin','request_id':'fusion-request-001'}),flush=True);assert json.loads(sys.stdin.readline())['permit'];\n"
        end="print(json.dumps({'fusion_fence':'end','request_id':'fusion-request-001'}),flush=True);assert json.loads(sys.stdin.readline())['permit'];\n"
        cases=[('legal',begin+end+"print(json.dumps({'ok':True}),flush=True)",None),
               ('open',begin+"print(json.dumps({'ok':True}),flush=True)",'DISPATCH_RESULT_UNKNOWN_OPEN_FENCE_NO_RESEND'),
               ('empty','pass','DISPATCH_RESULT_UNKNOWN_NO_RESULT_NO_RESEND'),
               ('nested',begin+begin,'FUSION_FENCE_PROTOCOL'),
               ('timeout',begin+'time.sleep(.5)','DISPATCH_RESULT_UNKNOWN_TIMEOUT_NO_RESEND')]
        for name,code,error in cases:
            with self.s.connect() as con:con.execute("UPDATE fusion_runs SET state='RUNNING'")
            folder=self.root/name;folder.mkdir()
            proc=subprocess.Popen([sys.executable,'-c',prelude+code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            if error:
                with self.assertRaisesRegex(ValueError,error):bridge.exchange(proc,{},self.s.db,body,folder,timeout=.2 if name=='timeout' else 5)
            else:self.assertEqual(bridge.exchange(proc,{},self.s.db,body,folder,timeout=5).returncode,0)
            proc.wait(timeout=5)
            self.assertTrue((folder/(body['request_id']+'.stdout')).exists())
            with fusion.transaction(self.s.db) as con:self.assertEqual(fusion.state(con)['level'],'L2')

    def test_stop_failures_release_with_persistent_unknown_and_late_write(self):
        import bridge
        self.configure();body=self.approve()['body']
        for failure in ('kill_failed','kill_nonzero','inspect_failed','still_running','unconfirmed_return'):
            with self.s.connect() as con:
                con.execute('INSERT OR REPLACE INTO fusion_runs VALUES(?,?,?)',(body['request_id'],canonical(body['fusion']),'RUNNING'))
            folder=self.root/failure;folder.mkdir();ready=folder/'ready';release=folder/'release';artifact=folder/'late-write'
            code="import sys,json,time;from pathlib import Path;ready,release,artifact=map(Path,sys.argv[1:]);json.loads(sys.stdin.readline());print(json.dumps({'fusion_fence':'begin','request_id':'fusion-request-001'}),flush=True);assert json.loads(sys.stdin.readline())['permit'];ready.touch();\nwhile not release.exists():time.sleep(.01)\nartifact.write_bytes(b'old permit after pause')"
            proc=subprocess.Popen([sys.executable,'-c',code,str(ready),str(release),str(artifact)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            errors=[]
            def abort():
                from unittest.mock import patch
                if failure=='unconfirmed_return': return None
                kill_error=subprocess.TimeoutExpired('docker kill',15) if failure=='kill_failed' else None
                kill_result=subprocess.CompletedProcess([],1) if failure=='kill_nonzero' else subprocess.CompletedProcess([],0)
                inspect_error=subprocess.CalledProcessError(1,'docker inspect') if failure=='inspect_failed' else None
                with patch.object(bridge.subprocess,'run',side_effect=kill_error,return_value=kill_result), patch.object(bridge.subprocess,'check_output',side_effect=inspect_error,return_value=b'[{"State":{"Running":true}}]'):
                    return bridge.abort_container('protocol-fixture-only',proc)
            def exchange():
                try:bridge.exchange(proc,{},self.s.db,body,folder,timeout=.5,abort=abort)
                except ValueError as exc:errors.append(str(exc))
            thread=threading.Thread(target=exchange);thread.start()
            try:
                deadline=time.monotonic()+5
                while not ready.exists() and time.monotonic()<deadline:time.sleep(.01)
                self.assertTrue(ready.exists());thread.join(5);self.assertFalse(thread.is_alive())
                self.assertEqual(errors,['DISPATCH_RESULT_UNKNOWN_TIMEOUT_NO_RESEND'])
                self.assertIsNone(proc.poll())
                paused=self.s.fusion_control(self.sid,self.csrf,{'operation':'pause'})
                self.assertTrue(paused['dispatch_stopped']);self.assertFalse(paused['write_quiescent'])
                self.assertTrue(paused['handoff_required']);self.assertFalse(paused['clean_pause'])
                self.assertEqual(paused['inflight'][0]['state'],'UNKNOWN')
                self.assertEqual(paused['downgrade_status'],'HANDOFF_REQUIRED')
                self.assertDenied('FUSION_PAUSE_AND_HANDOFF_REQUIRED',lambda:self.configure())
                self.assertFalse(artifact.exists());release.touch();proc.wait(timeout=5)
                self.assertEqual(artifact.read_bytes(),b'old permit after pause')
                restart=subprocess.run([sys.executable,'-c',"import sys,json,sqlite3,fusion;c=sqlite3.connect(sys.argv[1]);v=fusion.snapshot(c);assert v['handoff_required'] and not v['write_quiescent'] and not v['clean_pause'];print('UNKNOWN/HANDOFF_REQUIRED')",str(self.s.db)],capture_output=True,text=True,timeout=5)
                self.assertEqual(restart.returncode,0,restart.stderr)
            finally:
                release.touch()
                if proc.poll() is None:proc.kill();proc.wait(timeout=5)
                thread.join(5)
                proc.stdout.close();proc.stderr.close()
            # 下一场景使用新 epoch，旧 UNKNOWN 只在测试隔离库中重置。
            with self.s.connect() as con:con.execute("UPDATE fusion_runs SET state='BLOCKED'")
            self.configure()
            with self.s.connect() as con:body={**body,'fusion':fusion.binding(con,self.p,self.s.session(self.sid)['subject'])}

    def test_every_broker_begin_history_loss_zero_permit_no_rebuild(self):
        import bridge,sqlite3
        self.configure();body=self.approve()['body']
        with self.s.connect() as con:con.execute('INSERT INTO fusion_runs VALUES(?,?,?)',(body['request_id'],canonical(body['fusion']),'RUNNING'))
        for damage in ('fusion_state','fusion_events','fusion_runs','all_tables','empty_events','missing_initial','invalid_initial','missing_state'):
            folder=self.root/damage;folder.mkdir();db=folder/'approval.sqlite'
            with self.s.connect() as con,sqlite3.connect(db) as copy:con.backup(copy)
            with sqlite3.connect(db) as con:
                if damage in ('fusion_state','fusion_events','fusion_runs'):con.execute('DROP TABLE '+damage)
                elif damage=='all_tables':
                    for table in ('fusion_state','fusion_events','fusion_runs'):con.execute('DROP TABLE '+table)
                elif damage=='empty_events':con.execute('DELETE FROM fusion_events')
                elif damage=='missing_initial':con.execute('DELETE FROM fusion_events WHERE seq=1')
                elif damage=='invalid_initial':con.execute("UPDATE fusion_events SET body='{}' WHERE seq=1")
                else:con.execute('DELETE FROM fusion_state')
            with sqlite3.connect(db) as con:before=con.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall()
            code="import sys,json;json.loads(sys.stdin.readline());print(json.dumps({'fusion_fence':'begin','request_id':'fusion-request-001'}),flush=True);permit=json.loads(sys.stdin.readline())['permit'];assert permit is False;print(json.dumps({'ok':False,'permit':permit}),flush=True)"
            proc=subprocess.Popen([sys.executable,'-c',code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            result=bridge.exchange(proc,{},db,body,folder,timeout=5)
            self.assertEqual(result.returncode,0,result.stderr);self.assertFalse(json.loads(result.stdout.splitlines()[-1])['permit'])
            with sqlite3.connect(db) as con:
                self.assertEqual(before,con.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall())
                self.assertDenied('FUSION_HISTORY_LOST',lambda:fusion.verify(con,body))
        folder=self.root/'second-begin';folder.mkdir();db=folder/'approval.sqlite'
        with self.s.connect() as con,sqlite3.connect(db) as copy:con.backup(copy)
        code="import sys,json,sqlite3;json.loads(sys.stdin.readline());m={'fusion_fence':'begin','request_id':'fusion-request-001'};print(json.dumps(m),flush=True);assert json.loads(sys.stdin.readline())['permit'];m['fusion_fence']='end';print(json.dumps(m),flush=True);assert json.loads(sys.stdin.readline())['permit'];c=sqlite3.connect(sys.argv[1]);c.execute('DELETE FROM fusion_events WHERE seq=1');c.commit();c.close();m['fusion_fence']='begin';print(json.dumps(m),flush=True);assert json.loads(sys.stdin.readline())['permit'] is False;print(json.dumps({'ok':False,'second_permit':False}),flush=True)"
        proc=subprocess.Popen([sys.executable,'-c',code,str(db)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        result=bridge.exchange(proc,{},db,body,folder,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr);self.assertFalse(json.loads(result.stdout.splitlines()[-1])['second_permit'])

    def test_recovery_only_queued_unknown_results_and_api_first_start(self):
        self.configure();receipt=self.approve();rid=receipt['body']['request_id'];recover_calls=[]
        self.s.recover=lambda r:recover_calls.append(r['body']['request_id']) or None
        self.assertDenied('RECOVERY_NOT_STARTED',lambda:self.s.execute(self.sid,self.csrf,rid,recovery_only=True))
        self.assertEqual(self.calls,[]);self.assertEqual(recover_calls,[])
        with self.s.connect() as con:
            self.assertEqual(con.execute('SELECT phase FROM outbox').fetchone()[0],'QUEUED')
            self.assertEqual(con.execute('SELECT count(*) FROM fusion_runs').fetchone()[0],0)
            con.execute("UPDATE outbox SET phase='UNKNOWN'")
            con.execute('INSERT INTO fusion_runs VALUES(?,?,?)',(rid,canonical(receipt['body']['fusion']),'UNKNOWN'))
        self.assertDenied('DISPATCH_RESULT_UNKNOWN_MANUAL_REVIEW_NO_RESEND',lambda:self.s.execute(self.sid,self.csrf,rid,recovery_only=True))
        self.assertEqual(self.calls,[])
        self.s.recover=lambda r:{'ok':True,'persisted_fixture':True}
        self.assertTrue(self.s.execute(self.sid,self.csrf,rid,recovery_only=True)['persisted_fixture'])
        self.assertEqual(self.calls,[]);self.assertTrue(self.s.execute(self.sid,self.csrf,rid,recovery_only=True)['cached'])
        with self.s.connect() as con:
            con.execute("UPDATE outbox SET phase='RESULT_READY',result=?",(canonical({'ok':True,'stored_result':True}),))
            con.execute("UPDATE fusion_runs SET state='RESULT_READY'")
        self.s.recover=lambda r:(_ for _ in ()).throw(AssertionError('不应查询 bridge'))
        self.assertTrue(self.s.execute(self.sid,self.csrf,rid,recovery_only=True)['stored_result'])
        self.assertEqual(self.calls,[])
        # API 原入口仍允许首次执行；仅恢复参数默认为 False。
        with self.s.connect() as con:
            con.execute('DELETE FROM fusion_runs');con.execute("UPDATE outbox SET phase='QUEUED',result=NULL")
        self.s.recover=None
        self.assertTrue(self.s.execute(self.sid,self.csrf,rid)['ok']);self.assertEqual(self.calls,[rid])

    def test_cli_recover_queued_refuses_without_intent_or_run(self):
        from initialize import initialize
        import remote_store,bridge
        root=self.root/'cli-runtime';initialize(root,fixture=True);store=remote_store.load_store(root)
        sid,csrf=store.register({'capability':store.bootstrap_capability(),'challenge':store.challenge,'confirm_capability_holder':True})
        card=store.card();proposal=card['proposal'];subject=store.session(sid)['subject']
        store.fusion_control(sid,csrf,{'operation':'configure','expected_epoch':store.fusion_status(sid)['epoch'],'level':'L2','project':fusion.PROJECT,'environment':fusion.ENVIRONMENT,'observation_scope':['history.py'],'control_scope':['history.py'],'tasks':[proposal['task']],'actors':[subject]})
        from roles_gate import Governance,review_binding,write_new
        governance=Governance(root);state=governance.snapshot(proposal);trigger='large_plan_before_approval';path='reviews/cli-fixture.json'
        write_new(root/path,{'binding':review_binding(state,trigger),'verdict':'PASS','pid':os.getpid(),'model':'fixture/model'})
        governance.record_review(proposal,trigger,path,'SYNTHETIC_TEST_SUBPROCESS',time.time()+3600)
        rid='cli-recover-queued-001'
        store.decide(sid,csrf,{'request_id':rid,'decision':'approve','version':proposal['version'],'proposal_hash':card['proposal_hash'],'scope':proposal['scope'],'base':proposal['base'],'expiry':proposal['expiry'],'actions':proposal['actions'],'note':''})
        bridge.save(root/'private/sessions.json',{'sessions':store.sessions,'capabilities':store.capabilities,'challenge':store.challenge})
        auth=root/'private/cli-session.json';auth.write_bytes(canonical({'sid':sid,'csrf':csrf}).encode())
        cli=subprocess.run([sys.executable,str(root/'fusion_cli.py'),'--root',str(root),'--session-file',str(auth),'recover','--request-id',rid],cwd=root,capture_output=True,text=True,timeout=10)
        self.assertEqual(cli.returncode,2);self.assertEqual(json.loads(cli.stdout)['code'],'RECOVERY_NOT_STARTED')
        self.assertFalse((root/'private/dispatch').exists())
        with store.connect() as con:
            self.assertEqual(con.execute('SELECT phase FROM outbox').fetchone()[0],'QUEUED')
            self.assertEqual(con.execute('SELECT count(*) FROM fusion_runs').fetchone()[0],0)

    def test_http_real_control_card(self):
        server=make_server(self.s);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        host='127.0.0.1:'+str(server.server_port)
        def request(path,data=None):
            c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            headers={'Host':host,'Cookie':'approval_session='+self.sid,'Origin':'http://'+host,'Content-Type':'application/json','X-CSRF-Token':self.csrf}
            c.request('POST' if data else 'GET',path,canonical(data) if data else None,headers)
            r=c.getresponse();raw=r.read();status=r.status;c.close();return status,raw
        self.assertEqual(json.loads(request('/api/fusion')[1])['level'],'L1')
        status,raw=request('/api/fusion',self.config());self.assertEqual(status,200);self.assertEqual(json.loads(raw)['level'],'L2')
        self.assertEqual(request('/api/decision',self.decision())[0],200)
        self.assertEqual(request('/api/fusion',{'operation':'pause'})[0],200)
        self.assertEqual(request('/api/execute',{'request_id':'fusion-request-001'})[0],409)
        self.assertIn(b'fusion-pause',request('/')[1]);self.assertEqual(self.calls,[])

if __name__=='__main__':unittest.main(verbosity=2)
