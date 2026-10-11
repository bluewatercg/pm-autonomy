"""异常失败去重及跨版本复发 gate 实测，无 Docker 或模型替身声明。"""
import json,secrets,shutil,time
from pathlib import Path
import approval_api as a,bridge,remote_store
from roles_gate import Governance,write_new,canonical
R=Path(__file__).resolve().parent

def main():
 root=R/'private/exception-flow';root.mkdir();(root/'private').mkdir()
 p=json.loads((R/'pending-proposal.json').read_bytes());p.update(task='TEST-EXCEPTION-FLOW',expiry=time.time()+3600);p['governance_policy']={'large_plan':False,'long_task':False,'repeat_threshold':2}
 for n in set(p['control_sources'])|{'roles.json','model-id-observation.json','FIXTURE_ONLY'}:shutil.copyfile(R/n,root/n)
 write_new(root/'pending-proposal.json',p);(root/'private/bridge-key.bin').write_bytes(secrets.token_bytes(32))
 s=remote_store.load_store(root);g=Governance(root)
 s.baseline=lambda card:{'base':p['base'],'before_sha':p['before_sha']}
 sid,csrf=s.register({'capability':s.bootstrap_capability(),'challenge':s.challenge,'confirm_capability_holder':True})
 d={'request_id':'test-exception-0001','decision':'approve','version':1,'proposal_hash':s.card()['proposal_hash'],'scope':p['scope'],'base':p['base'],'expiry':p['expiry'],'actions':p['actions'],'note':''};s.decide(sid,csrf,d)
 rows=[]
 def record(name,fn):
  try:rows.append({'name':name,'status':'PASS','detail':fn()})
  except Exception as e:rows.append({'name':name,'status':'FAIL','error':str(e)})
 def attempt(code):
  try:s.execute(sid,csrf,d['request_id'])
  except a.Denied as e:assert e.code==code,(e.code,code);return {'denied':code,'errors':g.snapshot(p)['errors']}
  raise AssertionError('未阻塞')
 s.recover=None;s.dispatch=lambda receipt:(_ for _ in ()).throw(ConnectionError('测试派发中断'))
 record('exception:dispatch-failure-recorded',lambda:attempt('DISPATCH_INTERRUPTED_RETRY_SAME_REQUEST'))
 def replay_same():
  with s.connect() as con:con.execute('UPDATE failure_occurrences SET applied=0')
  s.replay_failures();assert g.snapshot(p)['errors']=={'DISPATCH_ConnectionError':1};return {'same_occurrence_deduplicated':True}
 record('exception:same-occurrence-deduplicated',replay_same)
 record('exception:new-failure-same-request-counted',lambda:attempt('DISPATCH_INTERRUPTED_RETRY_SAME_REQUEST'))
 assert g.snapshot(p)['errors']=={'DISPATCH_ConnectionError':2}
 record('exception:third-dispatch-blocked-by-repeat-gate',lambda:attempt('ARCHITECT_REQUIRED:repeated_similar_error'))
 s.recover=lambda receipt:(_ for _ in ()).throw(ValueError('DISPATCH_RESULT_UNKNOWN_MANUAL_REVIEW_NO_RESEND'))
 record('exception:unknown-recovery-recorded-no-resend',lambda:attempt('DISPATCH_RESULT_UNKNOWN_MANUAL_REVIEW_NO_RESEND'))
 record('exception:unknown-recovery-deduplicated',lambda:attempt('DISPATCH_RESULT_UNKNOWN_MANUAL_REVIEW_NO_RESEND'))
 # 后继属于同任务；相同异常类型的新版本证据应累加，并触发下一次执行 gate。
 s.recover=None
 c=s.card();v=c['proposal'];change={**d,'request_id':'test-exception-change','decision':'change','note':'修订后重试'};s.decide(sid,csrf,change)
 successor=s.card()['proposal'];assert g.snapshot(successor)['errors']['DISPATCH_ConnectionError']==2
 record('exception:successor-retains-error-history',lambda:{'errors':g.snapshot(successor)['errors'],'version':successor['version']})
 g.failure(successor,'DISPATCH_ConnectionError',{'request':'another-bound-operation','version':2})
 def repeat_gate():
  before=g.export_events()
  try:g.check(successor,'execute')
  except Exception as e:assert e.code=='ARCHITECT_REQUIRED:repeated_similar_error'
  else:raise AssertionError('复发 gate 缺失')
  assert g.export_events()==before;return {'denied':'ARCHITECT_REQUIRED:repeated_similar_error','history_count':2}
 record('exception:repeat-gate-blocks-across-version',repeat_gate)
 result={'checks':rows,'passed':sum(r['status']=='PASS' for r in rows),'failed':sum(r['status']=='FAIL' for r in rows),'events':g.export_events(),'identity':'SYNTHETIC_TEST_BOUNDARY_ONLY'}
 write_new(R/'exception-flow-results.json',result);print(canonical({'passed':result['passed'],'failed':result['failed']}));return result['failed']!=0
if __name__=='__main__':raise SystemExit(main())
