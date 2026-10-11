"""私有 SSH 桥：验签、活动提案/基线绑定、唯一 Docker 派发及不确定结果阻断。"""
import base64,fcntl,hashlib,hmac,json,os,subprocess,time
from pathlib import Path
R=Path(__file__).resolve().parent
IMAGE=os.environ.get('PM_CONTAINER_IMAGE', '')
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)
def sha(data):return hashlib.sha256(data).hexdigest()
def sign(key,value):return hmac.new(key,canonical(value).encode(),'sha256').hexdigest()
def save(path,value):
    data=canonical(value).encode();temp=path.with_suffix('.pending')
    with temp.open('wb') as f:os.chmod(temp,0o600);f.write(data);f.flush();os.fsync(f.fileno())
    os.replace(temp,path)
    fd=os.open(path.parent,os.O_DIRECTORY);os.fsync(fd);os.close(fd)
def read_proposal(root):
    current=root/'private/active-proposal.json'
    return json.loads((current if current.exists() else root/'pending-proposal.json').read_bytes())
def validate_receipt(root,receipt,key,recover=False):
    if set(receipt)!={'body','hmac'} or not isinstance(receipt['body'],dict) or not hmac.compare_digest(sign(key,receipt['body']),str(receipt['hmac'])):raise ValueError('INVALID_APPROVAL_SIGNATURE')
    b=receipt['body'];p=read_proposal(root)
    fields={'request_id','task','version','proposal_hash','scope','base','before_sha','expiry','actions','source_sha','change','privilege','subject','identity','decision','approval_time'}
    if set(b)!=fields or b['decision']!='approve' or b['identity']!='capability-holder-not-workbuddy-account':raise ValueError('APPROVAL_CONTRACT')
    rid=b['request_id']
    if not isinstance(rid,str) or not 8<=len(rid)<=100 or not all(c.isalnum() or c=='-' for c in rid):raise ValueError('REQUEST_ID')
    if b['version']!=p['version'] or b['proposal_hash']!=sha(canonical(p).encode()):raise ValueError('SUPERSEDED_AUTHORIZATION')
    for field in ['task','scope','base','before_sha','actions','source_sha','change']:
        if b[field]!=p[field]:raise ValueError('BINDING_'+field.upper())
    if p.get('revision_required'):raise ValueError('PLAN_REVISION_REQUIRED')
    if b['scope']!=['history.py'] or b['actions']!=['edit'] or b['change'] not in ('normal','destructive'):raise ValueError('ACTION_SCOPE_DENIED')
    if b['privilege'] not in ('normal','destructive') or (b['change']=='destructive' and b['privilege']!='destructive'):raise ValueError('DESTRUCTIVE_CAPABILITY_REQUIRED')
    if (not recover and time.time()>=b['expiry']) or not b['approval_time']<b['expiry']<=p['expiry'] or b['expiry']>b['approval_time']+901 or b['approval_time']>time.time()+5:raise ValueError('EXPIRED_OR_INVALID_APPROVAL')
    expected='capability-holder:'+sha(key)[:24]+':'+b['privilege']
    if not isinstance(b['subject'],str) or not b['subject'].startswith(expected+':') or len(b['subject'].split(':')[-1])!=64 or any(c not in '0123456789abcdef' for c in b['subject'].split(':')[-1]):raise ValueError('SUBJECT_MISMATCH')
    if not recover:
        actual=baseline(root)
        if actual['base']!=b['base'] or actual['before_sha']!=b['before_sha']:raise ValueError('BASELINE_DRIFT')
    return p,b

def baseline(root):
    repo=root/'synthetic-repo'
    head=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD']).decode().strip()
    status=subprocess.check_output(['git','-C',str(repo),'status','--porcelain']).decode()
    if status:raise ValueError('BASELINE_DIRTY')
    return {'base':head,'before_sha':sha((repo/'history.py').read_bytes())}
def recover_result(root,receipt,key):
    _,b=validate_receipt(root,receipt,key,recover=True)
    target=root/'private/dispatch';rid=b['request_id']
    intent=target/(rid+'.intent.json');result=target/(rid+'.json')
    if not intent.exists(): return None
    if json.loads(intent.read_bytes())['fingerprint']!=sha(canonical(receipt).encode()): raise ValueError('IDEMPOTENCY_CONFLICT')
    if not result.exists(): raise ValueError('DISPATCH_RESULT_UNKNOWN_MANUAL_REVIEW_NO_RESEND')
    return json.loads(result.read_bytes())

def dispatch(root,receipt,key,synthetic_tests=False):
    p,b=validate_receipt(root,receipt,key)
    if 'governance_policy' in p:
        from roles_gate import Governance
        Governance(root).check(p,'execute')
    if sha((root/'candidate-history.py').read_bytes())!=b['source_sha']:raise ValueError('SOURCE_DIGEST_MISMATCH')
    if sha((root/'next_kernel_v3.py').read_bytes())!=p['source']['kernel_sha']:raise ValueError('KERNEL_DIGEST_MISMATCH')
    if 'governance_policy' in p and sha((root/'governed_kernel.py').read_bytes())!=p['source']['governed_kernel_sha']:raise ValueError('GOVERNED_KERNEL_DIGEST_MISMATCH')
    if not IMAGE.startswith('sha256:') or len(IMAGE)!=71 or any(c not in '0123456789abcdef' for c in IMAGE[7:]):raise ValueError('BLOCKED_PINNED_CONTAINER_IMAGE_NOT_CONFIGURED')
    target=root/'private/dispatch';target.mkdir(mode=0o700,exist_ok=True)
    rid=b['request_id'];fingerprint=sha(canonical(receipt).encode());result_path=target/(rid+'.json');intent=target/(rid+'.intent.json')
    if intent.exists():
        if json.loads(intent.read_bytes())['fingerprint']!=fingerprint:raise ValueError('IDEMPOTENCY_CONFLICT')
        if result_path.exists():return {**json.loads(result_path.read_bytes()),'cached':True}
        raise ValueError('DISPATCH_RESULT_UNKNOWN_MANUAL_REVIEW_NO_RESEND')
    kernel_path=root/('governed_kernel.py' if 'governance_policy' in p else 'secured_kernel.py')
    if sha(kernel_path.read_bytes())!=p['source']['governed_kernel_sha' if 'governance_policy' in p else 'secured_kernel_sha']:raise ValueError('EXECUTION_KERNEL_DRIFT')
    payload={'receipt':receipt,'proposal':p,'source':base64.b64encode((root/'candidate-history.py').read_bytes()).decode(),'kernel':base64.b64encode(kernel_path.read_bytes()).decode(),'bundle':base64.b64encode((root/'synthetic.bundle').read_bytes()).decode(),'synthetic_tests':synthetic_tests,'defer_close': 'governance_policy' in p,'adapter':base64.b64encode((root/'model_adapter.py').read_bytes()).decode()}
    command='import base64;exec(base64.b64decode('+repr(base64.b64encode((root/'container_task.py').read_bytes()).decode())+'))'
    name='approval-entry-'+sha((str(root)+rid).encode())[:20]
    args=['docker','run','-i','--pull=never','--name',name,'--network=none','--read-only','--cap-drop=ALL','--cap-add=CHOWN','--cap-add=SETUID','--cap-add=SETGID','--cap-add=DAC_OVERRIDE','--cap-add=FOWNER','--cap-add=KILL','--security-opt=no-new-privileges','--pids-limit=128','--memory=256m','--user=0:0','--tmpfs','/outputs:rw,noexec,nosuid,size=128m','--tmpfs','/data/credentials:rw,noexec,nosuid,size=1m','--tmpfs','/data/model:rw,noexec,nosuid,size=1m','--tmpfs','/data/state:rw,noexec,nosuid,size=1m','--entrypoint=/usr/local/bin/python3',IMAGE,'-I','-c',command]
    save(intent,{'fingerprint':fingerprint,'container':name,'utc':time.time(),'state':'INTENT_NO_AUTOMATIC_RESEND'})
    out=subprocess.run(args,input=canonical(payload).encode(),capture_output=True,timeout=300)
    with (target/(rid+'.stdout')).open('xb') as f:f.write(out.stdout)
    with (target/(rid+'.stderr')).open('xb') as f:f.write(out.stderr)
    inspected=json.loads(subprocess.check_output(['docker','inspect',name]))[0]
    host=inspected['HostConfig']
    metadata={'container':name,'image':inspected['Image'],'network':host['NetworkMode'],'read_only':host['ReadonlyRootfs'],'ports':host['PortBindings'],'privileged':host['Privileged'],'mounts':inspected['Mounts'],'cap_drop':host['CapDrop'],'orchestrator_caps':host['CapAdd'],'security_opt':host['SecurityOpt'],'rc':out.returncode,'stdout_sha':sha(out.stdout),'stderr_sha':sha(out.stderr)}
    try:result=json.loads(out.stdout)
    except ValueError:result={'ok':False,'code':'DOCKER_NO_VALID_RESULT','stderr':out.stderr.decode(errors='replace')[-600:]}
    isolated=metadata['image']==IMAGE and metadata['network']=='none' and metadata['read_only'] and not metadata['ports'] and not metadata['privileged'] and all(m['Type']=='tmpfs' for m in metadata['mounts'])
    result['runtime']=metadata;result['ok']=bool(result.get('ok') and isolated and out.returncode==0)
    save(result_path,result)
    return result

def handle(request):
    key=(R/'private/bridge-key.bin').read_bytes()
    try:
        if set(request)!={'op','value','hmac'}:raise ValueError('BRIDGE_CONTRACT')
        body={k:v for k,v in request.items() if k!='hmac'}
        if not hmac.compare_digest(sign(key,body),str(request['hmac'])):raise ValueError('INVALID_BRIDGE_SIGNATURE')
        with (R/'private/bridge.lock').open('a+b') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            if request['op']=='baseline':return baseline(R)
            if request['op']=='execute':return dispatch(R,request['value'],key)
            if request['op']=='successor':
                old=read_proposal(R);new=request['value']
                if new['version']!=old['version']+1 or new['parent_proposal_hash']!=sha(canonical(old).encode()):raise ValueError('SUCCESSOR_VERSION')
                if any(new[f]!=old[f] for f in old if f not in ['version','expiry','parent_proposal_hash','change_note','revision_required']):raise ValueError('SUCCESSOR_SCOPE_ESCALATION')
                save(R/'private/active-proposal.json',new)
                return {'ok':True,'version':new['version'],'old_authorizations':'INVALIDATED'}
            raise ValueError('UNKNOWN_OP')
    except (ValueError,KeyError,TypeError) as e:return {'ok':False,'code':str(e)}
