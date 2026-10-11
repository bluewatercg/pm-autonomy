"""三触发真实 gate 新进程探针；合成 reviewer 明确不是模型。"""
import copy,json,os,subprocess,sys,tempfile,time
from pathlib import Path
from roles_gate import Governance,GateDenied,load_roles,review_binding,sha,canonical,write_new,digest
R=Path(__file__).resolve().parent

def main():
 root=R/'private/gate-tests';root.mkdir(mode=0o700)
 for n in ['roles.json','model-id-observation.json','FIXTURE_ONLY']: (root/n).write_bytes((R/n).read_bytes())
 g=Governance(root);rows=[];serial=0
 def record(name,fn):
  try: rows.append({'name':name,'status':'PASS','detail':fn()})
  except Exception as exc: rows.append({'name':name,'status':'FAIL','type':type(exc).__name__,'error':str(exc)})
 def fresh(name,large=False,long=False):
  c={'task':'TEST-'+name,'version':1,'scope':['history.py'],'base':json.loads((R/'pending-proposal.json').read_bytes())['base'],'source_sha':sha((R/'candidate-history.py').read_bytes())}
  g.register(c,{'large_plan':large,'long_task':long,'repeat_threshold':2});return c
 def run_gate(c,phase,code=None):
  nonlocal serial
  serial+=1;p=root/f'card-{serial}.json';write_new(p,c);before=g.export_events()
  proc=subprocess.run([sys.executable,str(R/'gate_cli.py'),'--root',str(root),'--card',str(p),'--phase',phase],capture_output=True,text=True,timeout=20)
  value=json.loads(proc.stdout);assert value['process']!=os.getpid()
  assert proc.returncode==(2 if code else 0),(proc.returncode,value)
  if code:assert value['code']==code,value
  assert g.export_events()==before
  return {'rc':proc.returncode,'reply':value,'events_unchanged':True}
 def review(c,tr,verdict='PASS',ttl=300):
  nonlocal serial
  serial+=1;name=f'synthetic-{serial}';req=root/f'{name}-request.json';path=root/'reviews'/(name+'.json')
  write_new(req,{'binding':review_binding(g.snapshot(c),tr),'verdict':verdict})
  proc=subprocess.run([sys.executable,str(R/'synthetic_reviewer.py'),'--request',str(req),'--output',str(path)],capture_output=True,text=True,timeout=20)
  assert proc.returncode==0,proc.stderr
  v=json.loads(path.read_bytes());assert v['pid']!=os.getpid()
  return g.record_review(c,tr,str(path.relative_to(root)),'SYNTHETIC_TEST_SUBPROCESS',time.time()+ttl)
 a=fresh('LARGE',large=True)
 record('large:missing-review',lambda:run_gate(a,'approve','ARCHITECT_REQUIRED:large_plan_before_approval'))
 record('large:review-new-process',lambda:review(a,'large_plan_before_approval'))
 record('large:legal-control',lambda:run_gate(a,'approve'))
 for field,value in [('version',2),('scope',['else.py']),('base','0'*40),('source_sha','0'*64)]:
  record('binding:'+field,lambda field=field,value=value:run_gate({**a,field:value},'approve','GOVERNANCE_BINDING_DRIFT'))
 repeat=fresh('REPEATED')
 g.failure(repeat,'FIXED_TEST_FAILED',{'fixture':'first'})
 record('repeat:first-error-legal',lambda:run_gate(repeat,'execute'))
 g.failure(repeat,'FIXED_TEST_FAILED',{'fixture':'second'})
 record('repeat:second-blocks',lambda:run_gate(repeat,'execute','ARCHITECT_REQUIRED:repeated_similar_error'))
 record('repeat:review-new-process',lambda:review(repeat,'repeated_similar_error'))
 record('repeat:legal-control',lambda:run_gate(repeat,'execute'))
 g.failure(repeat,'FIXED_TEST_FAILED',{'fixture':'third'})
 record('repeat:old-epoch-stale',lambda:run_gate(repeat,'execute','ARCHITECT_BINDING:repeated_similar_error'))
 successor={**repeat,'version':2,'parent_proposal_hash':digest(repeat)}
 g.register(successor,{'large_plan':False,'long_task':False,'repeat_threshold':2})
 record('repeat:history-survives-successor',lambda:run_gate(successor,'execute','ARCHITECT_REQUIRED:repeated_similar_error'))
 for verdict in ['FAIL','TIMEOUT']:
  c=fresh(verdict,large=True);review(c,'large_plan_before_approval',verdict)
  record('review:'+verdict,lambda c=c:run_gate(c,'approve','ARCHITECT_BLOCKED:large_plan_before_approval'))
 c=fresh('EXPIRED',large=True);review(c,'large_plan_before_approval',ttl=-1)
 record('review:expired',lambda:run_gate(c,'approve','ARCHITECT_EXPIRED:large_plan_before_approval'))
 long=fresh('LONG',long=True)
 record('long:execute-before-close-legal',lambda:run_gate(long,'execute'))
 record('long:missing-review',lambda:run_gate(long,'close','ARCHITECT_REQUIRED:long_task_closeout'))
 review(long,'long_task_closeout')
 def review_not_acceptance():
  before=g.export_events()
  try:g.close(long)
  except GateDenied as exc:assert exc.code=='REVIEW_IS_NOT_ACCEPTANCE'
  else:raise AssertionError('review substituted acceptance')
  assert g.export_events()==before;return {'code':'REVIEW_IS_NOT_ACCEPTANCE','events_unchanged':True}
 record('long:review-is-not-acceptance',review_not_acceptance)
 g.accept(long,{'rc':0,'artifact':long['source_sha'],'base':long['base'],'test_sha':'synthetic-fixed-test-digest'})
 record('long:pre-acceptance-review-stale',lambda:run_gate(long,'close','ARCHITECT_BINDING:long_task_closeout'))
 review(long,'long_task_closeout')
 record('long:legal-close-control',lambda:run_gate(long,'close'))
 g.close(long)
 record('long:closed-with-separate-acceptance',lambda:{'closed':g.snapshot(long)['closed']})
 def unavailable():
  p=root/'unavailable.json';v=json.loads((root/'roles.json').read_bytes());v['roles']['architect']['model']='unverified/model';write_new(p,v)
  try:load_roles(p,root/'model-id-observation.json')
  except GateDenied as exc:assert exc.code=='MODEL_UNAVAILABLE_STOP';return {'code':exc.code,'review_not_skipped':True}
  raise AssertionError('unavailable model accepted')
 record('roles:unavailable-stop',unavailable)
 def fallback():
  p=root/'fallback.json';v=json.loads((root/'unavailable.json').read_bytes());v['model_policy']['backup_model']='fixture/model';write_new(p,v)
  cfg=load_roles(p,root/'model-id-observation.json');assert cfg['roles']['architect']['resolved_model']=='fixture/model';return {'explicit_backup':True}
 record('roles:explicit-verified-backup',fallback)
 def tamper():
  r=g.snapshot(a)['reviews']['large_plan_before_approval'];path=root/r['path'];path.write_bytes(path.read_bytes()+b' ')
  return run_gate(a,'approve','ARCHITECT_FILE_DRIFT')
 record('review:file-tamper',tamper)
 result={'checks':rows,'passed':sum(x['status']=='PASS' for x in rows),'failed':sum(x['status']=='FAIL' for x in rows),'events':g.export_events(),'identity':'SYNTHETIC_TEST_SUBPROCESS; not model review'}
 write_new(R/'gate-test-results.json',result)
 print(canonical({'passed':result['passed'],'failed':result['failed']}))
 return bool(result['failed'])
if __name__=='__main__':raise SystemExit(main())
