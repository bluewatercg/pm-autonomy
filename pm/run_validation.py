"""远端真实审批到合成门验证。测试授权仅存 private/tests，正式任务不批准。"""
import base64,copy,hashlib,json,os,secrets,shutil,sqlite3,subprocess,sys,time,unittest
from pathlib import Path
import approval_api as a
import bridge
import remote_store
R=Path(__file__).resolve().parent
rows=[]
def record(name,fn):
    try:rows.append({'name':name,'status':'PASS','detail':fn()})
    except Exception as e:rows.append({'name':name,'status':'FAIL','error':str(e),'type':type(e).__name__})
def saved(name,value):
    with (R/name).open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2)
def command(args):
    p=subprocess.run(args,capture_output=True,text=True,timeout=60)
    if p.returncode:raise RuntimeError(p.stderr[-500:])
    return p.stdout.strip()

suite=unittest.defaultTestLoader.discover(str(R),pattern='test_*.py')
with (R/'protocol-tests.log').open('x') as log:
    result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
rows.append({'name':'protocol:standard-library-unittest','status':'PASS' if result.wasSuccessful() else 'FAIL','detail':{'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'log_sha':bridge.sha((R/'protocol-tests.log').read_bytes())}})
private=R/'private/tests';private.mkdir(mode=0o700)
fixtures={}
def fixture(name,change='normal'):
    root=private/name;root.mkdir(mode=0o700);(root/'private').mkdir(mode=0o700)
    for n in ['next_kernel_v3.py','secured_kernel.py','container_task.py','model_adapter.py','candidate-history.py','synthetic.bundle','fusion.py']:shutil.copyfile(R/n,root/n)
    command(['git','clone',str(root/'synthetic.bundle'),str(root/'synthetic-repo')]);command(['git','-C',str(root/'synthetic-repo'),'remote','remove','origin'])
    p=json.loads((R/'pending-proposal.json').read_bytes());p.pop('governance_policy',None);p.update(task='TEST-'+name.upper(),expiry=time.time()+1800,change=change)
    p['source']={**p['source'],'request':'SYNTHETIC_TEST_BOUNDARY_ONLY: 自动回归不是人类批准'}
    if change=='destructive':
        data=b'def select_records(records):\n    return [dict(row) for row in sorted(records, key=lambda row: row["id"], reverse=True)]\n'
        (root/'candidate-history.py').write_bytes(data);p['source_sha']=bridge.sha(data);p['source_code']=data.decode();p['goal']='合成测试反转排序合同';p['replaces']=['TEST-ASCENDING-CONTRACT']
    (root/'pending-proposal.json').write_text(json.dumps(p,ensure_ascii=False));key=secrets.token_bytes(32);(root/'private/bridge-key.bin').write_bytes(key);os.chmod(root/'private/bridge-key.bin',0o600)
    def sync(new):bridge.save(root/'private/active-proposal.json',new)
    s=a.Store(root/'private/approval',p,key,lambda card:bridge.baseline(root),lambda receipt:bridge.dispatch(root,receipt,key,synthetic_tests=True),sync)
    return root,p,key,s

def register(s,priv='normal'):
    sid,csrf=s.register({'capability':s.bootstrap_capability(priv),'challenge':s.challenge,'confirm_capability_holder':True})
    from test_approval import authorize_l2
    authorize_l2(s,sid,csrf)
    return sid,csrf
def decision(s,action='approve',rid='test-request-0001'):
    c=s.card();p=c['proposal']
    return {'request_id':rid,'decision':action,'version':p['version'],'proposal_hash':c['proposal_hash'],'scope':p['scope'],'base':p['base'],'expiry':p['expiry'],'actions':p['actions'],'note':'补充方案验收' if action=='change' else ''}
def expect(code,fn):
    try:fn()
    except (a.Denied,ValueError) as exc:
        actual=exc.code if isinstance(exc,a.Denied) else str(exc)
        assert actual==code,(actual,code);return {'denied':code}
    raise AssertionError('未拒绝 '+code)

root,p,key,s=fixture('normal');sid,csrf=register(s);d=decision(s);s.decide(sid,csrf,d)
with s.connect() as con:receipt=json.loads(con.execute('SELECT receipt FROM outbox').fetchone()[0])
record('bridge:signature-forgery-denied',lambda:expect('INVALID_APPROVAL_SIGNATURE',lambda:bridge.validate_receipt(root,{**receipt,'hmac':'0'*64},key)))
def signature_binding(field,value):
    r=copy.deepcopy(receipt);r['body'][field]=value;r['hmac']=a.signature(key,r['body'])
    return expect('BINDING_'+field.upper(),lambda:bridge.validate_receipt(root,r,key))
for f,v in [('scope',['../escape']),('base','0'*40),('actions',['deploy']),('source_sha','0'*64)]:record('bridge:binding-'+f,lambda f=f,v=v:signature_binding(f,v))
def expiry():
    r=copy.deepcopy(receipt);r['body']['expiry']=time.time()-1;r['hmac']=a.signature(key,r['body'])
    return expect('EXPIRED_OR_INVALID_APPROVAL',lambda:bridge.validate_receipt(root,r,key))
record('bridge:expired-receipt',expiry)
def source_drift():
    target=root/'candidate-history.py';original=target.read_bytes();target.write_bytes(b'changed')
    try:return expect('SOURCE_DIGEST_MISMATCH',lambda:bridge.dispatch(root,receipt,key))
    finally:target.write_bytes(original)
record('bridge:source-drift',source_drift)
def base_drift():
    repo=root/'synthetic-repo';command(['git','-C',str(repo),'config','user.name','fixture']);command(['git','-C',str(repo),'config','user.email','fixture@localhost']);command(['git','-C',str(repo),'commit','--allow-empty','-m','Test drift'])
    try:return expect('BASELINE_DRIFT',lambda:bridge.validate_receipt(root,receipt,key))
    finally:command(['git','-C',str(repo),'checkout','--detach',p['base']])
record('bridge:actual-git-head-drift',base_drift)
def actual_execution():
    out=s.execute(sid,csrf,d['request_id']);assert out['ok'],out
    again=s.execute(sid,csrf,d['request_id']);assert again['cached']
    saved('synthetic-normal-result.json',out)
    fixtures['normal']={'request':d['request_id'],'receipt_sha':bridge.sha(a.canonical(receipt).encode()),'repeat_cached':True,'checks':len(out['checks'])}
    return fixtures['normal']
record('e2e:normal-approval-real-git-cross-uid-recovery',actual_execution)

def destructive():
    root,p,key,s=fixture('destructive','destructive');sid,csrf=register(s);d=decision(s,rid='test-destructive-0001')
    expect('DESTRUCTIVE_CAPABILITY_REQUIRED',lambda:s.decide(sid,csrf,d))
    sid,csrf=register(s,'destructive');s.decide(sid,csrf,d);out=s.execute(sid,csrf,d['request_id']);assert out['ok'],out
    assert out['evidence']['test']['rc']==0;saved('synthetic-destructive-result.json',out)
    return {'normal_denied':True,'destructive_fixed_descending_test':True,'approval_origin':out['approval_origin'],'checks':len(out['checks'])}
record('e2e:separate-destructive-capability-real-git',destructive)

def invalidation():
    root,p,key,s=fixture('version');sid,csrf=register(s);d=decision(s,rid='test-old-auth-0001');s.decide(sid,csrf,d)
    with s.connect() as con:r=json.loads(con.execute('SELECT receipt FROM outbox').fetchone()[0])
    c=decision(s,'change','test-change-0001');s.decide(sid,csrf,c)
    expect('SUPERSEDED_AUTHORIZATION',lambda:bridge.validate_receipt(root,r,key));expect('SUPERSEDED_AUTHORIZATION',lambda:s.execute(sid,csrf,d['request_id']))
    assert s.decide(sid,csrf,c)['cached'];assert s.card()['status']=='PENDING'
    return {'old_signed_receipt_denied':True,'new_version':s.card()['proposal']['version'],'duplicate_change_cached':True,'revision_required':True}
record('e2e:version-old-receipt-invalidated',invalidation)

def unknown():
    root,p,key,s=fixture('unknown');sid,csrf=register(s);d=decision(s,rid='test-unknown-0001');s.decide(sid,csrf,d)
    with s.connect() as con:r=json.loads(con.execute('SELECT receipt FROM outbox').fetchone()[0])
    folder=root/'private/dispatch';folder.mkdir();bridge.save(folder/(d['request_id']+'.intent.json'),{'fingerprint':bridge.sha(a.canonical(r).encode())})
    return expect('DISPATCH_RESULT_UNKNOWN_MANUAL_REVIEW_NO_RESEND',lambda:bridge.dispatch(root,r,key))
record('recovery:unknown-dispatch-no-resend',unknown)

def real_interruption():
    root,p,key,s=fixture('crash');sid,csrf=register(s);d=decision(s,rid='test-durable-approval-0001');s.decide(sid,csrf,d)
    state={'sessions':s.sessions,'capabilities':s.capabilities,'challenge':s.challenge};bridge.save(root/'private/sessions.json',state)
    code="import sys,os;sys.path.insert(0,%r);from remote_store import load_store;s=load_store(%r);s.dispatch=lambda receipt:os._exit(87);s.execute(%r,%r,%r)"%(str(R),str(root),sid,csrf,d['request_id'])
    proc=subprocess.run([sys.executable,'-I','-c',code],capture_output=True,timeout=20);assert proc.returncode==87
    recovered=remote_store.load_store(root)
    with recovered.connect() as con:phase=con.execute('SELECT phase FROM outbox').fetchone()[0]
    assert phase=='DISPATCHING'
    denied=expect('DISPATCH_RESULT_UNKNOWN_MANUAL_REVIEW_NO_RESEND',lambda:recovered.execute(sid,csrf,d['request_id']))
    saved('synthetic-recovery-result.json',denied)
    return {'actual_crash_rc':87,'durable_phase':phase,'unknown_no_resend':True}
record('recovery:real-approval-process-exit87',real_interruption)

def version_crash():
    root,p,key,s=fixture('version-crash');sid,csrf=register(s);state={'sessions':s.sessions,'capabilities':s.capabilities,'challenge':s.challenge};bridge.save(root/'private/sessions.json',state)
    d=decision(s,'change','test-version-crash-0001')
    code="import sys,os;sys.path.insert(0,%r);from remote_store import load_store;import bridge;from pathlib import Path;s=load_store(%r);s.sync_version=lambda new:(bridge.save(Path(%r)/'private/active-proposal.json',new),os._exit(87));s.decide(%r,%r,%r)"%(str(R),str(root),str(root),sid,csrf,d)
    proc=subprocess.run([sys.executable,'-I','-c',code],capture_output=True,timeout=20);assert proc.returncode==87
    recovered=remote_store.load_store(root);c=recovered.card();assert c['proposal']['version']==2 and c['status']=='PENDING' and c['proposal']['revision_required']
    return {'actual_crash_rc':87,'reconciled_remote_version':2,'old_version_superseded':True,'new_pending':True}
record('recovery:version-sync-transaction-interruption',version_crash)

status=remote_store.call('status')['result'];assert status['status']=='PENDING' and status['decisions']==status['outbox']==status['sessions']==0
saved('pending-state.json',status)
record('pending:real-task-no-human-approval-no-outbox',lambda:status)
saved('results.json',{'checks':rows,'passed':sum(x['status']=='PASS' for x in rows),'failed':sum(x['status']=='FAIL' for x in rows),'protocol_tests':result.testsRun,'test_approval_origin':'SYNTHETIC_TEST_BOUNDARY_ONLY','pending':status,'remote':str(R),'runtime_python':sys.executable})
print(json.dumps({'passed':sum(x['status']=='PASS' for x in rows),'failed':sum(x['status']=='FAIL' for x in rows),'protocol_tests':result.testsRun,'pending':status},ensure_ascii=False))
sys.exit(1 if any(x['status']=='FAIL' for x in rows) else 0)
