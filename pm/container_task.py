"""批准回执驱动的真实合成 Git/worktree、跨 UID broker 和固定验收。"""
import base64,copy,hashlib,importlib.util,json,os,secrets,subprocess,sys,time
from pathlib import Path
payload=json.loads(sys.stdin.read())
R=Path('/outputs/candidate-next-approval');R.mkdir()
for name in ['private','provenance','intake','worktrees','tasks']: (R/name).mkdir()
(R/'model_adapter.py').write_bytes(base64.b64decode(payload['adapter']))
(R/'next_kernel_v3.py').write_bytes(base64.b64decode(payload['kernel']))
spec=importlib.util.spec_from_file_location('k',R/'next_kernel_v3.py');k=importlib.util.module_from_spec(spec);spec.loader.exec_module(k)
b=payload['receipt']['body'];p=payload['proposal'];source=base64.b64decode(payload['source']);assert k.sha(source)==b['source_sha']
(R/'synthetic.bundle').write_bytes(base64.b64decode(payload['bundle']))
repo=R/'synthetic-repo';k.git(R,'clone',str(R/'synthetic.bundle'),str(repo));assert k.git(repo,'rev-parse','HEAD')==b['base']
assert k.git(repo,'remote','remove','origin')==''
(R/'private/key.bin').write_bytes(secrets.token_bytes(32))
BUID,EUID,AUID=21001,21002,21003
cfg={'socket':'approval-'+secrets.token_hex(12),'principals':{str(EUID):{'role':'executor','abilities':['execute','read']}},'approvals':{}}
cfg['defer_close_to_governance']=bool(payload.get('defer_close'))
checks=[];processes=[]
def record(name,fn):
    try: checks.append({'name':name,'status':'PASS','detail':fn()})
    except Exception as e: checks.append({'name':name,'status':'FAIL','error':str(e),'type':type(e).__name__})
def drop(uid):
    def apply():os.setgroups([]);os.setgid(uid);os.setuid(uid)
    return apply
def own():
    for path in [R]+list(R.rglob('*')):
        os.chown(path,BUID,BUID);os.chmod(path,0o700 if path.is_dir() else 0o600)
CLIENT="""import socket,json,sys,os
s=socket.socket(socket.AF_UNIX);s.settimeout(30);s.connect('\\0'+sys.argv[1]);s.sendall(sys.stdin.buffer.read()+b'\\n');data=b''
while not data.endswith(b'\\n'):
 part=s.recv(65536)
 if not part:raise ConnectionError('interrupted')
 data+=part
print(json.dumps({'uid':os.getuid(),'reply':json.loads(data)}))
"""
def client(uid,request):
    out=subprocess.run([sys.executable,'-I','-c',CLIENT,cfg['socket']],input=k.canonical(request),capture_output=True,text=True,preexec_fn=drop(uid),timeout=40)
    if out.returncode:raise ConnectionError('broker interrupted')
    return json.loads(out.stdout)['reply']
def start():
    code='import sys;sys.path.insert(0,'+repr(str(R))+');import next_kernel_v3 as k;from pathlib import Path;k.Broker(Path('+repr(str(R))+'),Path('+repr(str(R/'private/config.json'))+')).serve()'
    proc=subprocess.Popen([sys.executable,'-I','-c',code],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,preexec_fn=drop(BUID));processes.append(proc)
    for _ in range(100):
        if proc.poll() is not None:raise RuntimeError(proc.stderr.read().decode())
        try:
            if client(EUID,{'op':'hello'})['ok']:return proc
        except (OSError,ConnectionError):pass
        time.sleep(.02)
    raise RuntimeError('broker not ready')
def stop(proc):
    if proc.poll() is None:proc.terminate();proc.wait(timeout=10)
def prepare(task,rid):
    wt=R/'worktrees'/task;k.git(repo,'worktree','add','-b','candidate/'+task,str(wt),b['base'])
    assert k.sha((wt/'history.py').read_bytes())==b['before_sha']
    review=R/'provenance'/(task+'.json');review.write_bytes(k.canonical({'verdict':'PASS','source_sha':b['source_sha'],'basis':'本交付独立审阅的简单合成源码；不是新模型评审','approval_receipt_sha':k.sha(k.canonical(payload['receipt']).encode())}).encode())
    requirement=R/'intake'/(task+'.json');requirement.write_bytes(k.canonical({'status':'awaiting_'+b['change']+'_approval','plan':{'kind':'DESTRUCTIVE' if b['change']=='destructive' else 'FIRST'},'proposal':p,'approval_receipt':payload['receipt']}).encode())
    envelope={'auth_id':'auth-'+rid,'task':task,'task_kind':'execution','request':rid,'uid':EUID,'scope':b['scope'],'base':b['base'],'worktree':str(wt),'branch':'candidate/'+task,'git_common':str(repo/'.git'),'expiry':b['expiry'],'actions':b['actions'],'action':'edit','path':'history.py','environment':'dev','approval':b['change']+'_approved','change':b['change'],'stage':3 if b['change']=='destructive' else 1,'source_sha':b['source_sha'],'before_sha':b['before_sha'],'review_path':str(review.relative_to(R)),'review_sha':k.sha(review.read_bytes()),'requirement_path':str(requirement.relative_to(R)),'requirement_sha':k.sha(requirement.read_bytes()),'authorizer':b['subject']+'; capability-holder-not-workbuddy-account','superseded':False}
    cfg['approvals'][envelope['auth_id']]=envelope
    return {'op':'execute',**{field:envelope[field] for field in ['task','request','base','worktree','action','path']},'source':base64.b64encode(source).decode(),'token':k.sign(R,envelope)}
req=prepare(b['task'],b['request_id'])
recoveries={}
if payload.get('synthetic_tests'):
    for fault in ['after_replace','after_acceptance']: recoveries[fault]=prepare('recovery-'+fault,b['request_id']+'-'+fault)
(R/'private/config.json').write_bytes(k.canonical(cfg).encode());own()
try:
    proc=start()
    def noauth():
        before=k.sha((R/'worktrees'/b['task']/'history.py').read_bytes())
        bad=copy.deepcopy(req);bad.pop('token');reply=client(EUID,bad);assert reply['code']=='INVALID_SIGNATURE'
        assert k.sha((R/'worktrees'/b['task']/'history.py').read_bytes())==before
        return reply
    record('kernel:no-token-denied',noauth)
    def forged():
        reply=client(EUID,{**req,'role':'human','actor':'user'});assert reply['code']=='SELF_REPORTED_ROLE';return reply
    record('kernel:self-reported-human-denied',forged)
    def attacker():
        reply=client(AUID,req);assert reply['code']=='UNTRUSTED_UID';return reply
    record('kernel:cross-uid-valid-token-denied',attacker)
    def early_uid():
        code="import socket,json,sys;s=socket.socket(socket.AF_UNIX);s.settimeout(2);s.connect('\\0'+sys.argv[1]);print(s.recv(65536).decode())"
        out=subprocess.run([sys.executable,'-I','-c',code,cfg['socket']],capture_output=True,text=True,preexec_fn=drop(AUID),timeout=5)
        assert out.returncode==0 and json.loads(out.stdout)['code']=='UNTRUSTED_UID',out.stderr
        assert client(EUID,{'op':'hello'})['ok']
        return {'untrusted_connection_rejected_before_request':True,'legal_client_available':True}
    record('kernel:uid-rejected-before-read-legal-control',early_uid)
    def execute():
        reply=client(EUID,req);expected='acceptance_pending_close' if payload.get('defer_close') else 'closed';assert reply['ok'] and reply['result']['status']==expected,reply
        count=len(k.chain(R));again=client(EUID,req);assert again['result']['cached'] and len(k.chain(R))==count
        return {'reply':reply,'repeat':again,'base':b['base'],'source_sha':b['source_sha']}
    record('kernel:approved-receipt-real-git-and-idempotency',execute)
    stop(proc)
    for fault,request in recoveries.items():
        def recover(fault=fault,request=request):
            proc=start()
            try:client(EUID,{**request,'fault':fault});raise AssertionError('not crashed')
            except ConnectionError:pass
            assert proc.wait(timeout=10)==87
            proc=start()
            try:
                reply=client(EUID,request);assert reply['ok'],reply
                kinds=[x['kind'] for x in k.chain(R) if x['request']==request['request']];assert kinds==(['intent','written','accepted'] if payload.get('defer_close') else ['intent','written','accepted','closed'])
                return {'crash_rc':87,'events':kinds}
            finally:stop(proc)
        record('kernel:real-crash-recovery:'+fault,recover)
    events=k.chain(R);task=b['task'];test=R/'tasks'/task/'test.json'
    evidence={'events':events,'task':task,'base':b['base'],'head':subprocess.check_output(['git','-c','safe.directory='+str(R/'worktrees'/task),'-C',str(R/'worktrees'/task),'rev-parse','HEAD']).decode().strip(),'artifact':(R/'worktrees'/task/'history.py').read_text(),'artifact_sha':k.sha((R/'worktrees'/task/'history.py').read_bytes()),'test':json.loads(test.read_bytes()),'test_sha':k.sha(test.read_bytes()),'test_raw':test.read_text(),'review_raw':(R/'provenance'/(task+'.json')).read_text(),'review_sha':k.sha((R/'provenance'/(task+'.json')).read_bytes()),'receipt_sha':k.sha(k.canonical(payload['receipt']).encode()),'repo_remotes':subprocess.check_output(['git','-c','safe.directory='+str(repo),'-C',str(repo),'remote','-v']).decode()}
    print(json.dumps({'ok':all(x['status']=='PASS' for x in checks),'checks':checks,'evidence':evidence,'identity':b['identity'],'approval_origin':'SYNTHETIC_TEST' if payload.get('synthetic_tests') else 'CAPABILITY_HOLDER_ACTION'},ensure_ascii=False))
finally:
    for proc in processes:stop(proc)
