"""远端审批持久化；SSH 连接持有人可铸造能力，仅测试边界。"""
import fcntl,json,os,secrets,time,subprocess,sys,sqlite3
from pathlib import Path
from approval_api import Store,Denied,canonical,digest
import bridge
R=Path(__file__).resolve().parent

def load_store(root=R):
    root=Path(root);private=root/'private'
    projected=bridge.read_proposal(root)
    db=private/'approval/approval.sqlite'
    cards=[]; evidence=[]
    if db.exists():
        with sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True) as con:
            for version,raw,status in con.execute('SELECT * FROM cards ORDER BY version'):
                card=json.loads(raw)
                if type(card.get('version')) is not int or card['version']!=version: raise Denied('REMOTE_VERSION_CONFLICT',409)
                cards.append((card,status))
            evidence=[json.loads(raw)['body']['proposal_hash'] for raw, in con.execute('SELECT receipt FROM outbox')]
            dirty=bool(con.execute('SELECT 1 FROM decisions LIMIT 1').fetchone() or con.execute('SELECT 1 FROM failure_occurrences LIMIT 1').fetchone() or evidence or any(status!='PENDING' for _,status in cards))
    else: dirty=False
    def successor(new,old):
        if type(new.get('version')) is not int or new['version']!=old['version']+1 or new.get('parent_proposal_hash')!=digest(old):
            raise Denied('REMOTE_VERSION_CONFLICT',409)
    for index,(card,status) in enumerate(cards):
        if index:
            parent,parent_status=cards[index-1];successor(card,parent)
            if card['task']==parent['task'] and parent_status=='COMPLETED_SYNTHETIC': raise Denied('COMPLETED_TASK_SUCCESSOR_DENIED',409)
    durable=cards[-1][0] if cards else projected
    if projected['version']<=durable['version']:
        matched=next((card for card,_ in cards if card['version']==projected['version']),durable if not cards else None)
        if matched is None or digest(matched)!=digest(projected): raise Denied('REMOTE_PROJECTION_DRIFT',409)
        p=durable
    else:
        successor(projected,durable);p=projected
    from roles_gate import Governance,GateDenied,load_roles
    if not (private/'governance.sqlite').exists() and dirty:
        raise Denied('GOVERNANCE_HISTORY_LOST',409)
    governance=Governance(root)
    events=governance.export_events()
    if not events and dirty: raise Denied('GOVERNANCE_HISTORY_LOST',409)
    registrations=[e['body']['card'] for e in events if e['kind']=='register']
    registered=[card for card in registrations if card['task']==p['task']]
    if registered and registered[-1]['version']>p['version']:
        new=registered[-1];successor(new,p);p=new
    if p['version']>durable['version']:
        successor(p,durable)
        if p['task']==durable['task'] and cards and cards[-1][1]=='COMPLETED_SYNTHETIC':
            raise Denied('COMPLETED_TASK_SUCCESSOR_DENIED',409)
    # 先完成全部只读校验，再允许登记、SQLite 恢复及投影写入。
    planned=[]
    latest={card['task']:card for card,_ in cards}
    latest[p['task']]=p
    for card in latest.values():
        if 'governance_policy' not in card: continue
        for name,hashed in card.get('control_sources',{}).items():
            if Path(name).name!=name or bridge.sha((root/name).read_bytes())!=hashed: raise Denied('CONTROL_SOURCE_DRIFT',409)
        prior=[item for item in registrations if item['task']==card['task']]
        if prior and digest(prior[-1])==digest(card):
            try: governance.snapshot(card)
            except GateDenied as exc:
                if exc.code!='BLOCKED_MODEL_NOT_CONFIGURED': raise
        else:
            if prior:
                successor(card,prior[-1])
                if card['governance_policy']!=prior[-1]['governance_policy']: raise Denied('GOVERNANCE_BINDING_DRIFT',409)
            elif digest(card) in evidence or any(old['task']==card['task'] and status!='PENDING' for old,status in cards) or any(old['task']==card['task'] and old['version']<card['version'] for old,_ in cards):
                raise Denied('GOVERNANCE_HISTORY_LOST',409)
            try: load_roles(root/'roles.json')
            except GateDenied as exc:
                if exc.code!='BLOCKED_MODEL_NOT_CONFIGURED': raise
            planned.append(card)
    for card in planned:
        try: governance.register(card,card['governance_policy'])
        except GateDenied as exc:
            if exc.code!='BLOCKED_MODEL_NOT_CONFIGURED': raise
    if events and not governance.checkpoint.exists(): governance.save_checkpoint()
    key=(private/'bridge-key.bin').read_bytes()
    def sync(new):
        # 调用者已提交 SQLite；统一使用 load_store 恢复治理和文件投影。
        recovered=load_store(root)
        if recovered.card()['proposal_hash']!=digest(new): raise Denied('REMOTE_VERSION_CONFLICT',409)
    def gate(card,phase,result=None):
        if 'governance_policy' not in card:
            return {'legacy_regression_only':True}
        if phase=='failure':
            failed=[x['name'] for x in result.get('checks',[]) if x.get('status')=='FAIL']
            return governance.failure(card,result.get('code') or '|'.join(failed) or 'EXECUTION_FAILED',{'result_sha':digest(result)})
        if phase=='close':
            from roles_gate import GateDenied
            folder=private/'verification';folder.mkdir(exist_ok=True)
            tag=digest(result);cp=folder/(tag+'-card.json');rp=folder/(tag+'-result.json')
            bridge.save(cp,card);bridge.save(rp,result)
            proc=subprocess.run([sys.executable,str(R/'verify_acceptance.py'),'--root',str(root),'--card',str(cp),'--result',str(rp)],capture_output=True,text=True,timeout=30)
            value=json.loads(proc.stdout)
            if proc.returncode or not value['ok']:raise GateDenied(value.get('code','ACCEPTANCE_VERIFIER_FAILED'))
            acceptance=value['acceptance']
            if governance.snapshot(card)['acceptance'] is None:
                governance.accept(card,acceptance)
            governance.check(card,'close')
            if not governance.snapshot(card)['closed']:
                governance.close(card)
            return {'closed':True,'review_is_acceptance':False}
        return governance.check(card,phase)
    def task_closed(card):
        try:
            state=governance.snapshot(card)
            return bool(state['closed'] and state['acceptance'])
        except Exception:
            return False
    store=Store(private/'approval',p,key,lambda card:bridge.baseline(root),lambda receipt:bridge.dispatch(root,receipt,key),sync,gate=gate,recover=lambda receipt:bridge.recover_result(root,receipt,key),task_closed=task_closed)
    state=private/'sessions.json'
    if state.exists():
        v=json.loads(state.read_bytes());store.sessions=v['sessions'];store.capabilities=v['capabilities'];store.challenge=v['challenge']
    if p['version']>store.card()['proposal']['version']:
        with store.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            raw,status=con.execute('SELECT body,status FROM cards ORDER BY version DESC LIMIT 1').fetchone()
            parent=json.loads(raw);successor(p,parent)
            if status!='COMPLETED_SYNTHETIC':
                con.execute("UPDATE cards SET status='SUPERSEDED' WHERE version=? AND body=? AND status!='COMPLETED_SYNTHETIC'",(parent['version'],raw))
            for rid,receipt in con.execute("SELECT id,receipt FROM outbox WHERE phase!='CLOSED'").fetchall():
                body=json.loads(receipt)['body']
                if body.get('proposal_hash')==digest(parent) and body.get('version')==parent['version']:
                    con.execute("UPDATE outbox SET phase='REVOKED' WHERE id=? AND phase!='CLOSED'",(rid,))
            con.execute('INSERT INTO cards VALUES(?,?,?)',(p['version'],canonical(p),'PENDING'))
    if digest(projected)!=digest(p): bridge.save(private/'active-proposal.json',p)
    return store

def call(op,value=None):
    # 负责人停止只依赖本地能力与融合库，不等待执行锁、模型或 architect。
    if op in ('fusion_status','fusion_control'):
        try:
            from fusion import transaction,snapshot,change
            sessions=json.loads((R/'private/sessions.json').read_bytes())['sessions']
            sid=value[0];csrf=value[1] if op=='fusion_control' else None
            import hashlib,hmac
            session=sessions.get(hashlib.sha256(str(sid).encode()).hexdigest())
            if not session or time.time()>=session['expiry']:raise Denied('SESSION_REQUIRED',401)
            if csrf is not None and not hmac.compare_digest(session['csrf'],str(csrf)):raise Denied('CSRF_DENIED')
            with transaction(R/'private/approval/approval.sqlite') as con:
                result=change(con,session,value[2]) if op=='fusion_control' else snapshot(con)
            return {'ok':True,'result':result}
        except Denied as exc:return {'ok':False,'code':exc.code,'status':exc.status}
    with (R/'private/approval.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        from roles_gate import GateDenied
        try: store=load_store(R)
        except Denied as exc: return {'ok':False,'code':exc.code,'status':exc.status}
        except GateDenied as exc: return {'ok':False,'code':exc.code,'status':409}
        try:
            if op=='card':result=store.card()
            elif op=='challenge':result=store.challenge
            elif op=='bootstrap':
                policy=json.loads((R/'private/bootstrap-policy.json').read_bytes())
                if value not in policy['allowed_privileges']: raise Denied('BOOTSTRAP_PRIVILEGE_NOT_CONFIGURED')
                result=store.bootstrap_capability(value)
            elif op=='register':result=store.register(value)
            elif op=='session':result=store.session(value)
            elif op=='decide':result=store.decide(*value)
            elif op=='execute':result=store.execute(*value)
            elif op=='recover':result=store.execute(*value,recovery_only=True)
            elif op=='requirements':result=store.requirements(*value)
            elif op=='requirement_write':result=store.requirement_write(*value)
            elif op=='status':
                with store.connect() as con:
                    result={'status':store.card()['status'],'version':store.card()['proposal']['version'],'proposal_hash':store.card()['proposal_hash'],'decisions':con.execute('SELECT count(*) FROM decisions').fetchone()[0],'outbox':con.execute('SELECT count(*) FROM outbox').fetchone()[0],'sessions':len(store.sessions),'identity':'能力会话仅测试边界；不是账户认证','activation':'NOT_IN_FORCE'}
            else:raise Denied('UNKNOWN_OPERATION',400)
            return {'ok':True,'result':result}
        except Denied as exc:return {'ok':False,'code':exc.code,'status':exc.status}
        finally:
            bridge.save(R/'private/sessions.json',{'sessions':store.sessions,'capabilities':store.capabilities,'challenge':store.challenge})
