"""隔离候选：可信 UID 写入口、真实 Git 绑定、持久化幂等状态及真实 OMP 调用。"""
import ast, base64, fcntl, hashlib, hmac, json, os, shlex, socket, sqlite3, struct, subprocess, sys, time
from pathlib import Path
MODEL=os.environ.get('PM_MODEL', '')
TEST='''import json,sys
from pathlib import Path
ns={}; exec(compile(Path(sys.argv[1]).read_bytes(),sys.argv[1],'exec'),ns)
f=ns['select_records']; stage=int(sys.argv[2])
a=[{'id':3,'name':'BETA'},{'id':1,'name':'Alpha'},{'id':2,'name':'alphabet'}]; old=[x.copy() for x in a]
assert [r['id'] for r in f(a)]==([3,2,1] if stage==3 else [1,2,3])
r=f(a); r[0]['name']='changed'; assert a==old
assert f([])==[]
if stage==2:
 assert [r['id'] for r in f(a,'ALP')]==[1,2]
 assert len(f(a,None))==3 and len(f(a,''))==3 and f(a,'missing')==[]
print(json.dumps({'pass':True,'checks':['sort','copy_input','empty']+(['casefold','none_empty','no_match'] if stage==2 else (['descending'] if stage==3 else []))}))
'''
def canonical(x): return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False)
def sha(b): return hashlib.sha256(b).hexdigest()
def syncdir(p):
 fd=os.open(str(p),os.O_RDONLY|os.O_DIRECTORY)
 try: os.fsync(fd)
 finally: os.close(fd)
def atomic(p,b,mode=0o600):
 p=Path(p); p.parent.mkdir(parents=True,exist_ok=True)
 fd,tmp=__import__('tempfile').mkstemp(prefix='.pending-',dir=p.parent)
 try:
  os.fchmod(fd,mode)
  with os.fdopen(fd,'wb') as f: f.write(b); f.flush(); os.fsync(f.fileno())
  os.replace(tmp,p); syncdir(p.parent)
 finally:
  if os.path.exists(tmp): os.unlink(tmp)
def save(p,x): atomic(p,canonical(x).encode())
def immutable(p,b):
 p=Path(p); p.parent.mkdir(parents=True,exist_ok=True)
 if p.exists():
  if p.read_bytes()!=b: raise Denied('EVIDENCE_CONFLICT')
  return
 with p.open('xb') as f: os.chmod(p,0o600); f.write(b); f.flush(); os.fsync(f.fileno())
 syncdir(p.parent)
def git(p,*args):
 env=dict(os.environ,GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL='/dev/null')
 for key in ['GIT_DIR','GIT_WORK_TREE','GIT_INDEX_FILE','GIT_CONFIG_COUNT']: env.pop(key,None)
 r=subprocess.run(['git','-C',str(p),*args],capture_output=True,text=True,env=env,timeout=20)
 if r.returncode: raise Denied('GIT_FAILURE')
 return r.stdout.strip()
def crash(point,fault):
 if point==fault: os._exit(87)
class Denied(Exception): pass

def open_db(root):
 con=sqlite3.connect(root/'state.sqlite',timeout=20)
 con.execute('PRAGMA journal_mode=WAL'); con.execute('PRAGMA synchronous=FULL')
 con.executescript('CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,task TEXT NOT NULL,phase TEXT NOT NULL); CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY,request TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,prev TEXT NOT NULL,hash TEXT NOT NULL,UNIQUE(request,kind));')
 return con

def event(con,rid,kind,payload):
 row=con.execute('SELECT payload FROM events WHERE request=? AND kind=?',(rid,kind)).fetchone()
 text=canonical(payload)
 if row:
  if row[0]!=text: raise Denied('EVENT_CONFLICT')
  return
 tail=con.execute('SELECT seq,hash FROM events ORDER BY seq DESC LIMIT 1').fetchone()
 seq=tail[0]+1 if tail else 1; prev=tail[1] if tail else '0'*64
 content={'seq':seq,'request':rid,'kind':kind,'payload':payload,'prev':prev}
 con.execute('INSERT INTO events VALUES(?,?,?,?,?,?)',(seq,rid,kind,text,prev,sha(canonical(content).encode())))

def chain(root):
 with open_db(root) as con: rows=con.execute('SELECT * FROM events ORDER BY seq').fetchall()
 prev='0'*64
 for i,(seq,rid,kind,text,old,h) in enumerate(rows,1):
  assert seq==i and old==prev
  assert h==sha(canonical({'seq':seq,'request':rid,'kind':kind,'payload':json.loads(text),'prev':old}).encode())
  prev=h
 return [{'seq':s,'request':r,'kind':k,'payload':json.loads(p),'prev':v,'hash':h} for s,r,k,p,v,h in rows]

class Broker:
 def __init__(self,root,config):
  self.root=Path(root); self.config=Path(config)
  self.raw=self.config.read_bytes(); self.cfg=json.loads(self.raw)
  assert self.config.stat().st_mode & 0o077==0
  self.key=(self.root/'private/key.bin').read_bytes()
  assert len(self.key)==32 and (self.root/'private/key.bin').stat().st_mode & 0o077==0
  self.config_sha=sha(self.raw)
  with open_db(self.root): pass
 def check(self,uid,req):
  if 'role' in req or 'actor' in req: raise Denied('SELF_REPORTED_ROLE')
  principal=self.cfg['principals'].get(str(uid))
  if not principal: raise Denied('UNTRUSTED_UID')
  if req.get('op')=='hello': return principal
  if req.get('op')!='execute' or 'execute' not in principal['abilities']: raise Denied('ROLE_ACTION_DENIED')
  token=req.get('token',{})
  body=token.get('body'); signature=token.get('hmac','')
  if not isinstance(body,dict) or not hmac.compare_digest(hmac.new(self.key,canonical(body).encode(),'sha256').hexdigest(),signature): raise Denied('INVALID_SIGNATURE')
  envelope=self.cfg['approvals'].get(body.get('auth_id'))
  if envelope!=body: raise Denied('UNREGISTERED_AUTHORIZATION')
  if body['uid']!=uid: raise Denied('AUTH_UID_MISMATCH')
  if body.get('superseded'): raise Denied('SUPERSEDED_AUTHORIZATION')
  if time.time()>=body['expiry']: raise Denied('EXPIRED_AUTHORIZATION')
  proposal_path=self.root/body['requirement_path']
  if not proposal_path.is_file() or sha(proposal_path.read_bytes())!=body['requirement_sha']: raise Denied('REQUIREMENT_BINDING')
  proposal=json.loads(proposal_path.read_bytes())
  if proposal['status']=='paused_ambiguity' or proposal['plan']['kind']=='AMBIGUOUS': raise Denied('AMBIGUITY_PAUSED')
  if proposal['plan']['kind']=='DESTRUCTIVE' and body['change']!='destructive': raise Denied('DESTRUCTIVE_MISCLASSIFIED')
  if body['approval'] not in ('normal_approved','destructive_approved'): raise Denied('APPROVAL_REQUIRED')
  if body['change']=='destructive' and body['approval']!='destructive_approved': raise Denied('DESTRUCTIVE_APPROVAL_REQUIRED')
  for field in ['task','base','worktree','action','path','request']:
   if req.get(field)!=body[field]: raise Denied('BINDING_'+field.upper())
  if body['environment']!='dev' or body['scope']!=['history.py'] or body['path']!='history.py': raise Denied('SCOPE_DENIED')
  if req['action'] not in body['actions']: raise Denied('ACTION_DENIED')
  if body['task_kind']!='execution': raise Denied('DELIBERATION_WRITE_DENIED')
  data=base64.b64decode(req.get('source',''),validate=True)
  if sha(data)!=body['source_sha']: raise Denied('SOURCE_DIGEST_MISMATCH')
  wt=Path(body['worktree'])
  if wt.is_symlink() or wt.parent.resolve()!=self.root/'worktrees' or not wt.is_dir(): raise Denied('WORKTREE_BOUNDARY')
  if git(wt,'rev-parse','--show-toplevel')!=str(wt): raise Denied('WORKTREE_TOPLEVEL')
  if Path(git(wt,'rev-parse','--git-common-dir')).resolve()!=Path(body['git_common']):
   # rev-parse 在某些 Git 版本返回相对目录。
   common=git(wt,'rev-parse','--path-format=absolute','--git-common-dir')
   if common!=body['git_common']: raise Denied('GIT_COMMON_MISMATCH')
  if git(wt,'rev-parse','HEAD')!=body['base']: raise Denied('BASELINE_DRIFT')
  if git(wt,'symbolic-ref','--short','HEAD')!=body['branch']: raise Denied('BRANCH_MISMATCH')
  if git(wt,'status','--porcelain','--untracked-files=all').splitlines() not in ([],[' M history.py']):
   # strip 去除首行前导空格，因此用路径集合再次检查。
   lines=git(wt,'status','--porcelain','--untracked-files=all').splitlines()
   allowed={'history.py','.write-'+body['request']}
   if any(line[3:] not in allowed and line[2:] not in allowed for line in lines): raise Denied('WORKTREE_DIRTY_OUTSIDE_SCOPE')
  target=wt/'history.py'
  if target.is_symlink(): raise Denied('SYMLINK_DENIED')
  review=self.root/body['review_path']
  if not review.is_file() or sha(review.read_bytes())!=body['review_sha']: raise Denied('REVIEW_EVIDENCE_MISMATCH')
  value=json.loads(review.read_bytes())
  if value['verdict']!='PASS' or value['source_sha']!=body['source_sha']: raise Denied('REVIEW_NOT_ACCEPTED')
  return body,data,wt
 def execute(self,uid,req):
  checked=self.check(uid,req)
  if req.get('op')=='hello': return {'uid':uid,'role':checked['role'],'abilities':checked['abilities'],'authentication':'SO_PEERCRED OS UID only'}
  body,data,wt=checked; rid=body['request']; fault=req.get('fault'); target=wt/'history.py'
  fingerprint=sha(canonical({k:v for k,v in req.items() if k!='fault'}).encode())
  with open_db(self.root) as con:
   row=con.execute('SELECT fingerprint,phase FROM requests WHERE id=?',(rid,)).fetchone()
   if row and row[0]!=fingerprint: raise Denied('IDEMPOTENCY_CONFLICT')
   if not row:
    if sha(target.read_bytes())!=body['before_sha']: raise Denied('ARTIFACT_BASELINE_DRIFT')
    con.execute('INSERT INTO requests VALUES(?,?,?,?)',(rid,fingerprint,body['task'],'intent'))
    event(con,rid,'intent',{'task':body['task'],'base':body['base'],'source_sha':body['source_sha'],'uid':uid,'config_sha':self.config_sha})
    con.commit()
   crash('after_intent',fault)
   phase=con.execute('SELECT phase FROM requests WHERE id=?',(rid,)).fetchone()[0]
   if phase=='closed':
    if sha(target.read_bytes())!=body['source_sha']: raise Denied('CLOSED_ARTIFACT_DRIFT')
    return {'task':body['task'],'status':'closed','cached':True,'source_sha':body['source_sha']}
   if phase=='intent':
    actual=sha(target.read_bytes())
    if actual not in (body['before_sha'],body['source_sha']): raise Denied('ARTIFACT_BASELINE_DRIFT')
    if actual!=body['source_sha']:
     # 单目录 fd 和 O_NOFOLLOW 防止目标符号链接；同 UID 恶意竞态不属于沙箱保证。
     self.check(uid,req)
     fd=os.open(str(wt),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
     temp='.write-'+rid
     try:
      out=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600,dir_fd=fd)
      with os.fdopen(out,'wb') as f: f.write(data); f.flush(); os.fsync(f.fileno())
      crash('before_replace',fault)
      os.replace(temp,'history.py',src_dir_fd=fd,dst_dir_fd=fd); os.fsync(fd)
     finally: os.close(fd)
    crash('after_replace',fault)
    evidence={'task':body['task'],'base':git(wt,'rev-parse','HEAD'),'branch':body['branch'],'artifact_sha':sha(target.read_bytes()),'config_sha':self.config_sha,'review_sha':body['review_sha']}
    immutable(self.root/'tasks'/body['task']/'artifact-evidence.json',canonical(evidence).encode())
    crash('before_event',fault)
    event(con,rid,'written',evidence); con.execute('UPDATE requests SET phase=? WHERE id=?',('written',rid)); con.commit()
    crash('after_event',fault)
   phase=con.execute('SELECT phase FROM requests WHERE id=?',(rid,)).fetchone()[0]
   if phase=='written':
    crash('before_acceptance',fault)
    if sha(target.read_bytes())!=body['source_sha']: raise Denied('ACCEPTANCE_ARTIFACT_DRIFT')
    path=self.root/'tasks'/body['task']/'test.json'
    if path.exists(): result=json.loads(path.read_bytes())
    else:
     p=subprocess.run([sys.executable,'-I','-c',TEST,str(target),str(body['stage'])],capture_output=True,text=True,timeout=8)
     result={'rc':p.returncode,'stdout':p.stdout,'stderr':p.stderr,'source_sha':sha(target.read_bytes()),'actual_head':git(wt,'rev-parse','HEAD'),'command':['python3','-I','fixed acceptance',body['stage']],'time':time.time()}
     immutable(path,canonical(result).encode())
    crash('after_test_evidence',fault)
    if result['source_sha']!=body['source_sha'] or result['actual_head']!=body['base']: raise Denied('TEST_EVIDENCE_MISMATCH')
    if result['rc']!=0: raise Denied('ACCEPTANCE_TEST_FAILED')
    self.check(uid,req)
    event(con,rid,'accepted',{'source_sha':body['source_sha'],'test_sha':sha(path.read_bytes()),'review_sha':body['review_sha']})
    con.execute('UPDATE requests SET phase=? WHERE id=?',('accepted',rid)); con.commit()
    crash('after_acceptance',fault)
   if self.cfg.get('defer_close_to_governance'):
    return {'task':body['task'],'status':'acceptance_pending_close','cached':bool(row and row[1]=='accepted'),'source_sha':body['source_sha'],'review_is_acceptance':False}
   crash('before_close',fault)
   if con.execute('SELECT phase FROM requests WHERE id=?',(rid,)).fetchone()[0]!='accepted': raise Denied('EARLY_CLOSE_DENIED')
   self.check(uid,req)
   if sha(target.read_bytes())!=body['source_sha']: raise Denied('CLOSE_ARTIFACT_DRIFT')
   event(con,rid,'closed',{'task':body['task'],'source_sha':body['source_sha']})
   con.execute('UPDATE requests SET phase=? WHERE id=?',('closed',rid)); con.commit()
   crash('after_close',fault)
   return {'task':body['task'],'status':'closed','cached':False,'source_sha':body['source_sha']}
 def serve(self):
  address='\0'+self.cfg['socket']
  with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as server:
   server.bind(address); server.listen(32)
   save(self.root/'private/ready.json',{'pid':os.getpid(),'config_sha':self.config_sha})
   while True:
    conn,_=server.accept()
    with conn:
     uid=struct.unpack('3i',conn.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[1]
     if str(uid) not in self.cfg['principals']:
      try:conn.sendall(canonical({'ok':False,'code':'UNTRUSTED_UID'}).encode()+b'\n')
      except OSError:pass
      continue
     conn.settimeout(3)
     data=b'';deadline=time.monotonic()+3
     try:
      while not data.endswith(b'\n') and len(data)<262144:
       remaining=deadline-time.monotonic()
       if remaining<=0:raise TimeoutError()
       conn.settimeout(remaining)
       part=conn.recv(65536)
       if not part: break
       data+=part
     except (TimeoutError,OSError):
      continue
     try:
      if len(data)>=262144: raise Denied('REQUEST_TOO_LARGE')
      req=json.loads(data)
      with (self.root/'write.lock').open('a+b') as lock:
       fcntl.flock(lock,fcntl.LOCK_EX)
       answer={'ok':True,'result':self.execute(uid,req)}
     except Denied as exc: answer={'ok':False,'code':str(exc)}
     except Exception as exc: answer={'ok':False,'code':'INVALID_REQUEST','error_type':type(exc).__name__}
     try:conn.sendall(canonical(answer).encode()+b'\n')
     except OSError:pass

def rpc(address,req):
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as c:
  c.settimeout(45); c.connect('\0'+address); c.sendall(canonical(req).encode()+b'\n'); data=b''
  while not data.endswith(b'\n'):
   part=c.recv(65536)
   if not part: raise ConnectionError('BROKER_INTERRUPTED')
   data+=part
  return json.loads(data)

def sign(root,body): return {'body':body,'hmac':hmac.new((Path(root)/'private/key.bin').read_bytes(),canonical(body).encode(),'sha256').hexdigest()}

def parse_response(raw):
 texts=[]
 for line in raw.decode(errors='replace').splitlines():
  try: e=json.loads(line)
  except ValueError: continue
  messages=[e.get('message',{})] if e.get('type')=='message_end' else e.get('messages',[]) if e.get('type')=='agent_end' else []
  for m in messages:
   if m.get('role')=='assistant':
    content=m.get('content',[])
    text=content if isinstance(content,str) else '\n'.join(c.get('text','') for c in content if c.get('type')=='text')
    if text.strip(): texts.append(text.strip())
 if not texts: raise Denied('NO_MODEL_RESPONSE')
 text=texts[-1]
 if text.startswith('```'): text=text.split('\n',1)[1].rsplit('```',1)[0].strip()
 data=json.loads(text)
 if not isinstance(data,dict): raise Denied('MODEL_SCHEMA')
 return data

def model_call(root,request,prompt,fault=None,role='decomposer'):
 if not MODEL: raise Denied('BLOCKED_MODEL_NOT_CONFIGURED')
 if not request or not request.replace('-', '').isalnum(): raise Denied('REQUEST_ID')
 root=Path(root); folder=root/'private/models'/request; folder.mkdir(parents=True,exist_ok=True); os.chmod(folder,0o700)
 prompt_sha=sha(prompt.encode())
 with (root/'model.lock').open('a+b') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  invocation=folder/'invocation.json'; response=folder/'response.json'; exitfile=folder/'exit.json'
  if invocation.exists():
   inv=json.loads(invocation.read_bytes())
   if inv['prompt_sha']!=prompt_sha or inv['model']!=MODEL: raise Denied('MODEL_IDEMPOTENCY_CONFLICT')
   if response.exists() and exitfile.exists() and json.loads(exitfile.read_bytes())['rc']==0:
    data=json.loads(response.read_bytes())
    if data!=parse_response((folder/'raw.ndjson').read_bytes()): raise Denied('MODEL_EVIDENCE_MISMATCH')
    save(folder/'state.json',{'status':'completed','prompt_sha':prompt_sha,'response_sha':sha(response.read_bytes())})
    return data
   save(folder/'state.json',{'status':'blocked_unknown','reason':'durable invocation without complete response and successful exit; do not resend','prompt_sha':prompt_sha})
   raise Denied('blocked_unknown')
  immutable(folder/'prompt.txt',prompt.encode())
  immutable(invocation,canonical({'request':request,'role':role,'model':MODEL,'prompt_sha':prompt_sha,'start':time.time(),'tools':'disabled and deny-tools extension','provider_attempts':1}).encode())
  save(folder/'state.json',{'status':'calling','prompt_sha':prompt_sha})
  crash('before_model_launch',fault)
  from model_adapter import command
  args,env=command(root,folder,MODEL,'high','@'+str(folder/'prompt.txt'))
  with (folder/'raw.ndjson').open('xb') as out,(folder/'stderr.log').open('xb') as err:
   os.chmod(folder/'raw.ndjson',0o600); os.chmod(folder/'stderr.log',0o600)
   try: p=subprocess.run(args,stdout=out,stderr=err,env=env,timeout=270); rc=p.returncode
   except subprocess.TimeoutExpired: rc=124
   out.flush(); os.fsync(out.fileno()); err.flush(); os.fsync(err.fileno())
  crash('after_model_return',fault)
  immutable(exitfile,canonical({'rc':rc,'end':time.time()}).encode())
  if rc: save(folder/'state.json',{'status':'blocked_unknown','reason':'provider failure; manual investigation required'}); raise Denied('MODEL_FAILED')
  data=parse_response((folder/'raw.ndjson').read_bytes()); immutable(response,canonical(data).encode())
  crash('after_model_response',fault)
  save(folder/'state.json',{'status':'completed','prompt_sha':prompt_sha,'response_sha':sha(response.read_bytes())})
  return data

def intake(root,rid,requirement,context='',fault=None):
 prompt='''You decompose natural-language software requirements for an isolated pure Python select_records demo. Do not use tools. Return ONLY JSON with exactly these keys: kind (FIRST, OPTIMIZATION, AMBIGUOUS or DESTRUCTIVE), goal (string), tasks (array of objects with id, goal, scope (array of paths), acceptance (array of strings), depends_on (array of IDs)), questions (array of strings), replaces (array of strings), rationale (string). Missing behavior or unclear criteria => AMBIGUOUS, nonempty questions, tasks=[]; do not guess. Removing or reversing an accepted contract => DESTRUCTIVE and nonempty replaces. Classification contract: FIRST means an initial implementation with NO existing accepted behavior. OPTIMIZATION means a non-destructive iteration of existing accepted behavior, including additive functionality (not only performance tuning). When accepted context exists and its behavior is preserved, use OPTIMIZATION, not FIRST. AMBIGUOUS and DESTRUCTIVE take priority over these lifecycle kinds. Otherwise propose bounded tasks scoped exclusively to history.py. Existing accepted context:\n'''+context+'\nUser requirement:\n'+requirement
 data=model_call(root,rid,prompt,fault)
 required={'kind','goal','tasks','questions','replaces','rationale'}
 if set(data)!=required or data['kind'] not in ['FIRST','OPTIMIZATION','AMBIGUOUS','DESTRUCTIVE']: raise Denied('INTAKE_SCHEMA')
 if not all(isinstance(data[k],str) and data[k] for k in ['goal','rationale']): raise Denied('INTAKE_SCHEMA')
 for key in ['questions','replaces']:
  if not isinstance(data[key],list) or any(not isinstance(v,str) or not v for v in data[key]): raise Denied('INTAKE_SCHEMA')
 if not isinstance(data['tasks'],list): raise Denied('INTAKE_SCHEMA')
 ids=set()
 for task in data['tasks']:
  if set(task)!={'id','goal','scope','acceptance','depends_on'}: raise Denied('INTAKE_TASK_SCHEMA')
  if not isinstance(task['id'],str) or task['id'] in ids: raise Denied('INTAKE_DUPLICATE_TASK')
  ids.add(task['id'])
  if task['scope']!=['history.py'] or not isinstance(task['goal'],str) or not task['goal']: raise Denied('INTAKE_SCOPE')
  if not isinstance(task['acceptance'],list) or not task['acceptance'] or any(not isinstance(v,str) or not v for v in task['acceptance']): raise Denied('INTAKE_ACCEPTANCE')
  if not isinstance(task['depends_on'],list) or any(not isinstance(v,str) for v in task['depends_on']): raise Denied('INTAKE_DEPENDENCY')
 if data['kind']=='AMBIGUOUS':
  if not data['questions'] or data['tasks']: raise Denied('AMBIGUITY_NOT_PAUSED')
  status='paused_ambiguity'
 elif data['kind']=='DESTRUCTIVE':
  if not data['replaces'] or not data['tasks'] or data['questions']: raise Denied('DESTRUCTIVE_CONTRACT')
  status='awaiting_destructive_approval'
 else:
  if not data['tasks'] or data['questions'] or data['replaces']: raise Denied('NORMAL_CONTRACT')
  status='awaiting_normal_approval'
 if data['kind']=='FIRST' and context.strip(): raise Denied('FIRST_WITH_ACCEPTED_BASELINE')
 if data['kind']=='OPTIMIZATION' and not context.strip(): raise Denied('OPTIMIZATION_WITHOUT_BASELINE')
 exported={'request':rid,'source':'user-authorized isolated natural-language case','requirement':requirement,'accepted_context':context,'plan':data,'status':status,'model':MODEL,'raw_response_sha':sha((Path(root)/'private/models'/rid/'raw.ndjson').read_bytes()),'response_sha':sha((Path(root)/'private/models'/rid/'response.json').read_bytes()),'prompt_sha':sha(prompt.encode()),'remaining':['approval and execution'] if status!='paused_ambiguity' else ['human clarification']}
 immutable(Path(root)/'intake'/rid/'proposal.json',canonical(exported).encode())
 return exported

if __name__=='__main__':
 import argparse
 p=argparse.ArgumentParser(); p.add_argument('mode',choices=['broker','intake','model']); p.add_argument('--root',required=True); p.add_argument('--config'); p.add_argument('--spec'); p.add_argument('--fault'); args=p.parse_args()
 root=Path(args.root).resolve()
 if not (root/'private').is_dir(): raise Denied('RUNTIME_NOT_INITIALIZED')
 if args.mode=='broker': Broker(root,args.config).serve()
 else:
  spec=json.loads(Path(args.spec).read_bytes())
  try:
   value=intake(root,spec['request'],spec['requirement'],spec.get('context',''),args.fault) if args.mode=='intake' else model_call(root,spec['request'],spec['prompt'],args.fault)
   print(canonical({'ok':True,'request':spec['request'],'response_sha':sha(canonical(value).encode())}))
  except Denied as exc: print(canonical({'ok':False,'code':str(exc)})); sys.exit(2)
