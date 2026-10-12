"""远端审批持久化；SSH 连接持有人可铸造能力，仅测试边界。"""
import fcntl,json,os,secrets,time,subprocess,sys,sqlite3
from pathlib import Path
from approval_api import Store,Denied,canonical,digest
import bridge
R=Path(__file__).resolve().parent

def load_store(root=R):
    root=Path(root);private=root/'private'
    p=bridge.read_proposal(root)
    db=private/'approval/approval.sqlite'
    if db.exists():
        with sqlite3.connect(db) as con:
            row=con.execute('SELECT body FROM cards ORDER BY version DESC LIMIT 1').fetchone()
        if row:
            durable=json.loads(row[0])
            if durable['version']>p['version']:
                # 提案文件是 SQLite 卡片的可恢复投影，事务提交前不写它。
                bridge.save(private/'active-proposal.json',durable);p=durable
    key=(private/'bridge-key.bin').read_bytes()
    def sync(new):
        old=bridge.read_proposal(root)
        if new['version']!=old['version']+1 or new['parent_proposal_hash']!=digest(old):raise Denied('REMOTE_VERSION_CONFLICT',409)
        if 'governance_policy' in new:
            governance.register(new,new['governance_policy'])
        bridge.save(private/'active-proposal.json',new)
    from roles_gate import Governance
    governance=Governance(root)
    registered=[e['body']['card'] for e in governance.export_events() if e['kind']=='register' and e['body']['task']==p['task']]
    if registered and registered[-1]['version']>p['version']:
        successor=registered[-1]
        if successor['version']!=p['version']+1 or successor.get('parent_proposal_hash')!=digest(p):raise Denied('GOVERNANCE_SUCCESSOR_RECOVERY_CONFLICT',409)
        bridge.save(private/'active-proposal.json',successor);p=successor
    if 'governance_policy' in p:
        try:
            governance.snapshot(p)
        except Exception as exc:
            if getattr(exc, 'code', '') == 'GOVERNANCE_TASK_UNREGISTERED':
                try: governance.register(p, p['governance_policy'])
                except Exception as blocked:
                    if getattr(blocked,'code','')!='BLOCKED_MODEL_NOT_CONFIGURED': raise
            elif getattr(exc, 'code', '') != 'BLOCKED_MODEL_NOT_CONFIGURED':
                raise
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
    active=bridge.read_proposal(root)
    if active['version']>store.card()['proposal']['version']:
        with store.connect() as con:
            con.execute("UPDATE cards SET status='SUPERSEDED'")
            con.execute("UPDATE outbox SET phase='REVOKED' WHERE phase!='CLOSED'")
            con.execute('INSERT INTO cards VALUES(?,?,?)',(active['version'],canonical(active),'PENDING'))
    return store

def call(op,value=None):
    with (R/'private/approval.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        store=load_store(R)
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
