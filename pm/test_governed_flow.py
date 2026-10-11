"""新治理路径真实 Docker、进程中断、验收复算测试；批准均为合成主体。"""
import copy,json,os,secrets,shutil,subprocess,sys,time
from pathlib import Path
import approval_api as a,bridge,remote_store
from roles_gate import Governance,GateDenied,review_binding,write_new,canonical,digest
R=Path(__file__).resolve().parent

def main():
 rows=[];serial=0
 def record(name,fn):
  try:rows.append({'name':name,'status':'PASS','detail':fn()})
  except Exception as e:rows.append({'name':name,'status':'FAIL','error':str(e),'type':type(e).__name__})
 root=R/'private/governed-flow';root.mkdir(mode=0o700);(root/'private').mkdir(mode=0o700)
 p=json.loads((R/'pending-proposal.json').read_bytes());p.update(task='TEST-GOVERNED-FLOW',expiry=time.time()+3600)
 p['source']={**p['source'],'request':'SYNTHETIC_TEST_BOUNDARY_ONLY'}
 for n in set(p['control_sources'])|{'roles.json','model-id-observation.json','FIXTURE_ONLY','next_kernel_v3.py','candidate-history.py','synthetic.bundle','deny-tools.mjs'}:shutil.copyfile(R/n,root/n)
 subprocess.run(['git','clone',str(root/'synthetic.bundle'),str(root/'synthetic-repo')],check=True,capture_output=True)
 subprocess.run(['git','-C',str(root/'synthetic-repo'),'remote','remove','origin'],check=True)
 write_new(root/'pending-proposal.json',p);(root/'private/bridge-key.bin').write_bytes(secrets.token_bytes(32))
 s=remote_store.load_store(root);g=Governance(root)
 sid,csrf=s.register({'capability':s.bootstrap_capability(),'challenge':s.challenge,'confirm_capability_holder':True})
 bridge.save(root/'private/sessions.json',{'sessions':s.sessions,'capabilities':s.capabilities,'challenge':s.challenge})
 def decide(rid,action='approve'):
  c=s.card();v=c['proposal'];return {'request_id':rid,'decision':action,'version':v['version'],'proposal_hash':c['proposal_hash'],'scope':v['scope'],'base':v['base'],'expiry':v['expiry'],'actions':v['actions'],'note':'合成修订' if action=='change' else ''}
 def denied(code,fn):
  before=g.export_events()
  try:fn()
  except (a.Denied,GateDenied,ValueError) as e:assert getattr(e,'code',str(e))==code,(str(e),code)
  else:raise AssertionError('拒绝未生效')
  assert g.export_events()==before
  return {'denied':code,'events_unchanged':True}
 def review(trigger):
  nonlocal serial
  serial+=1;req=root/f'request-{serial}.json';out=root/'reviews'/f'fixture-{serial}.json'
  write_new(req,{'binding':review_binding(g.snapshot(p),trigger),'verdict':'PASS'})
  proc=subprocess.run([sys.executable,str(R/'synthetic_reviewer.py'),'--request',str(req),'--output',str(out)],capture_output=True,timeout=20);assert proc.returncode==0
  return g.record_review(p,trigger,str(out.relative_to(root)),'SYNTHETIC_TEST_SUBPROCESS',time.time()+3600)
 rid='test-governed-0001';d=decide(rid)
 record('approval:large-plan-missing-review-denied',lambda:denied('ARCHITECT_REQUIRED:large_plan_before_approval',lambda:s.decide(sid,csrf,d)))
 record('approval:large-plan-new-review-process',lambda:review('large_plan_before_approval'))
 record('approval:legal-control',lambda:s.decide(sid,csrf,d))
 def accepted_not_closed():
  try:s.execute(sid,csrf,rid)
  except a.Denied as e:assert e.code=='ARCHITECT_REQUIRED:long_task_closeout',e.code
  else:raise AssertionError('自动结案')
  with s.connect() as con:row=con.execute('SELECT result,phase FROM outbox').fetchone()
  out=json.loads(row[0]);assert row[1]=='CLOSE_REVIEW_BLOCKED' and out['ok']
  assert [e['kind'] for e in out['evidence']['events']]==['intent','written','accepted']
  assert g.snapshot(p)['acceptance'] and not g.snapshot(p)['closed']
  write_new(R/'governed-docker-result.json',out)
  return {'phase':row[1],'kernel_events':[e['kind'] for e in out['evidence']['events']],'independent_verifier':g.snapshot(p)['acceptance']}
 record('execution:real-docker-acceptance-blocks-without-closeout',accepted_not_closed)
 from verify_acceptance import verify
 stored=json.loads((root/'private/dispatch'/f'{rid}.json').read_bytes())
 def tamper(field,code):
  bad=copy.deepcopy(stored);bad['evidence'][field]='tampered'
  return denied(code,lambda:verify(root,p,bad))
 record('acceptance:exported-result-tamper-denied',lambda:tamper('test_raw','ACCEPTANCE_RESULT_DRIFT'))
 def raw_tamper():
  path=root/'private/dispatch'/f'{rid}.stdout';old=path.read_bytes();path.write_bytes(old+b' ')
  try:return denied('ACCEPTANCE_RUNTIME_RAW_DRIFT',lambda:verify(root,p,stored))
  finally:path.write_bytes(old)
 record('acceptance:runtime-raw-tamper-denied',raw_tamper)
 record('acceptance:legal-recomputation',lambda:verify(root,p,stored))
 record('close:independent-bound-review',lambda:review('long_task_closeout'))
 def recover_expired():
  # 测试时间前移，不修改签名回执或真实用户任务。
  import unittest.mock
  old=bridge.sha((root/'private/dispatch'/f'{rid}.intent.json').read_bytes())
  dispatch=s.dispatch;s.dispatch=lambda receipt:(_ for _ in ()).throw(AssertionError('重复派发'))
  with unittest.mock.patch.object(a.time,'time',return_value=time.time()+1000):out=s.execute(sid,csrf,rid)
  s.dispatch=dispatch
  assert out['ok'] and g.snapshot(p)['closed'] and bridge.sha((root/'private/dispatch'/f'{rid}.intent.json').read_bytes())==old
  return {'closed':True,'expired_execution_authority_recovery':True,'no_resend':True}
 record('recovery:completed-result-after-authority-expiry-no-resend',recover_expired)
 record('close:repeat-cached',lambda:s.execute(sid,csrf,rid))
 def crash_after_dispatch():
  # 用独立 TEST 任务复用真实容器结果缓存，真实退出发生在持久结果返回后、outbox保存前。
  with s.connect() as con:con.execute('UPDATE outbox SET result=NULL,phase=?',('DISPATCHING',))
  code="import sys,os;sys.path.insert(0,%r);from remote_store import load_store;s=load_store(%r);s.recover=lambda receipt:os._exit(87);s.execute(%r,%r,%r)"%(str(R),str(root),sid,csrf,rid)
  proc=subprocess.run([sys.executable,'-I','-c',code],capture_output=True,timeout=20);assert proc.returncode==87
  recovered=remote_store.load_store(root);recovered.dispatch=lambda receipt:(_ for _ in ()).throw(AssertionError('重复派发'))
  out=recovered.execute(sid,csrf,rid);assert out['ok']
  return {'actual_exit':87,'bridge_cache_recovered':True,'no_resend':True}
 record('recovery:actual-process-exit87-result-cache',crash_after_dispatch)
 def change_crash():
  current=s.card()['proposal'];new={**current,'version':2,'parent_proposal_hash':digest(current),'revision_required':True,'change_note':'合成恢复测试'}
  req=root/'successor-fixture.json';write_new(req,new)
  code="import sys,os,json;sys.path.insert(0,%r);from roles_gate import Governance;from pathlib import Path;p=json.loads(Path(%r).read_bytes());Governance(%r).register(p,p['governance_policy']);os._exit(87)"%(str(R),str(req),str(root))
  proc=subprocess.run([sys.executable,'-I','-c',code],capture_output=True,timeout=20);assert proc.returncode==87
  recovered=remote_store.load_store(root);assert recovered.card()['proposal']['version']==2
  return {'actual_exit':87,'governance_before_active_reconciled':True,'pending_successor':recovered.card()['status']}
 record('recovery:governance-successor-before-active-crash',change_crash)
 record('bootstrap:destructive-unconfigured-denied',lambda:denied('BOOTSTRAP_PRIVILEGE_NOT_CONFIGURED',lambda:remote_store.call('bootstrap','destructive') if remote_store.call('bootstrap','destructive')['ok'] else (_ for _ in ()).throw(a.Denied('BOOTSTRAP_PRIVILEGE_NOT_CONFIGURED'))))
 write_new(R/'governed-flow-results.json',{'checks':rows,'passed':sum(v['status']=='PASS' for v in rows),'failed':sum(v['status']=='FAIL' for v in rows),'events':g.export_events(),'identity':'SYNTHETIC_TEST_BOUNDARY_ONLY','reviewer':'SYNTHETIC_TEST_SUBPROCESS','docker':'REAL_ISOLATED_CONTAINER'})
 print(canonical({'passed':sum(v['status']=='PASS' for v in rows),'failed':sum(v['status']=='FAIL' for v in rows)}));return any(v['status']=='FAIL' for v in rows)
if __name__=='__main__':raise SystemExit(main())
