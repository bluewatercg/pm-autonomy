"""融合授权与启动/写入 fence 共用审批 SQLite；不接管外部项目。"""
import contextlib
import json
import sqlite3
import time
from pathlib import Path

PROJECT = 'synthetic-history'
ENVIRONMENT = 'dev / isolated synthetic Docker'
INFLIGHT = ('RESERVED', 'RUNNING', 'UNKNOWN')

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)

def denied(code):
    from approval_api import Denied
    raise Denied(code, 409)

def event(con, kind, value):
    con.execute('INSERT INTO fusion_events(kind,body) VALUES(?,?)', (kind, canonical({**value, 'time': time.time()})))

def integrity(con):
    """只检查持久结构与初始事件，不重建历史，也不宣称事件链重放。"""
    tables={name for name, in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'fusion_state','fusion_events','fusion_runs'} <= tables: denied('FUSION_HISTORY_LOST')
    try:
        row=con.execute('SELECT body FROM fusion_state WHERE id=1').fetchone()
        initial=con.execute('SELECT seq,kind,body FROM fusion_events ORDER BY seq LIMIT 1').fetchone()
        con.execute('SELECT id,body,state FROM fusion_runs LIMIT 0')
        if not row or not initial or initial[:2]!=(1,'default'): denied('FUSION_HISTORY_LOST')
        current=json.loads(row[0]);first=json.loads(initial[2])
        expected={'level':'L1','epoch':1,'paused':False,'project':PROJECT,'environment':ENVIRONMENT,
                  'observation_scope':['history.py'],'control_scope':[],'actors':[],'tasks':[],'owner':None}
        if not isinstance(first,dict) or set(first)!=set(expected)|{'time'} or {k:first.get(k) for k in expected}!=expected or not isinstance(first.get('time'),(int,float)): denied('FUSION_HISTORY_LOST')
        if not isinstance(current,dict) or not expected.keys()<=current.keys() or type(current['epoch']) is not int or current['epoch']<1: denied('FUSION_HISTORY_LOST')
    except (sqlite3.DatabaseError,ValueError,TypeError,KeyError):
        denied('FUSION_HISTORY_LOST')

def schema(con):
    tables={name for name, in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    present={'fusion_state','fusion_events','fusion_runs'} & tables
    if present:
        integrity(con)
    elif con.execute('SELECT 1 FROM decisions LIMIT 1').fetchone() or con.execute('SELECT 1 FROM outbox LIMIT 1').fetchone():
        denied('FUSION_LEGACY_HISTORY_REQUIRES_REVIEW')
    con.execute('CREATE TABLE IF NOT EXISTS fusion_state(id INTEGER PRIMARY KEY CHECK(id=1),body TEXT NOT NULL)')
    con.execute('CREATE TABLE IF NOT EXISTS fusion_events(seq INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT NOT NULL,body TEXT NOT NULL)')
    con.execute('CREATE TABLE IF NOT EXISTS fusion_runs(id TEXT PRIMARY KEY,body TEXT NOT NULL,state TEXT NOT NULL)')
    if not con.execute('SELECT 1 FROM fusion_state').fetchone():
        value = {'level': 'L1', 'epoch': 1, 'paused': False, 'project': PROJECT,
                 'environment': ENVIRONMENT, 'observation_scope': ['history.py'],
                 'control_scope': [], 'actors': [], 'tasks': [], 'owner': None}
        con.execute('INSERT INTO fusion_state VALUES(1,?)', (canonical(value),))
        event(con, 'default', value)

def state(con):
    integrity(con)
    row = con.execute('SELECT body FROM fusion_state WHERE id=1').fetchone()
    if not row: denied('FUSION_STATE_MISSING')
    return json.loads(row[0])

def snapshot(con):
    value = state(con)
    runs = [{'request_id': rid, **json.loads(raw), 'state': status}
            for rid, raw, status in con.execute('SELECT id,body,state FROM fusion_runs ORDER BY rowid')]
    active = [row for row in runs if row['state'] in INFLIGHT]
    unconfirmed = any(row['state'] in ('RUNNING','UNKNOWN') for row in active)
    stopped = value['paused'] or value['level'] != 'L2'
    return {**value, 'inflight': active, 'runs': runs,
            'dispatch_stopped': stopped, 'write_quiescent': not unconfirmed,
            'handoff_required': unconfirmed, 'clean_pause': stopped and not active,
            'downgrade_status': 'HANDOFF_REQUIRED' if unconfirmed else 'SETTLED',
            'adapter': 'SYNTHETIC_HISTORY_ONLY', 'L3': 'BLOCKED_FLOW_ADAPTER_UNAVAILABLE',
            'production_authority': False, 'activation': 'NOT_IN_FORCE'}

def collaboration(con):
    if state(con)['level'] == 'L0': denied('FUSION_L0_READ_ONLY')

def binding(con, card, actor):
    value = state(con)
    if value['paused']: denied('FUSION_PAUSED')
    if value['level'] != 'L2': denied('FUSION_EXECUTION_LEVEL_REQUIRED')
    if card.get('project') != value['project']: denied('FUSION_PROJECT_DENIED')
    if card.get('environment') != value['environment']: denied('FUSION_ENVIRONMENT_DENIED')
    if card['scope'] != ['history.py'] or not set(card['scope']) <= set(value['control_scope']): denied('FUSION_SCOPE_DENIED')
    if actor not in value['actors']: denied('FUSION_ACTOR_DENIED')
    if card['task'] not in value['tasks']: denied('FUSION_TASK_DENIED')
    return {'epoch': value['epoch'], 'project': value['project'], 'environment': value['environment'],
            'scope': card['scope'], 'actor': actor, 'task': card['task']}

def verify(con, body, require_run=False):
    auth = body.get('fusion')
    if not isinstance(auth, dict): denied('FUSION_AUTHORIZATION_REQUIRED')
    if auth.get('epoch') != state(con)['epoch']: denied('FUSION_EPOCH_STALE')
    expected = binding(con, {'project': auth.get('project'), 'environment': auth.get('environment'),
                            'scope': body['scope'], 'task': body['task']}, body['subject'])
    if auth != expected: denied('FUSION_BINDING_DENIED')
    if require_run:
        row = con.execute('SELECT body,state FROM fusion_runs WHERE id=?', (body['request_id'],)).fetchone()
        if not row or json.loads(row[0]) != auth or row[1] != 'RUNNING': denied('FUSION_RUN_NOT_RUNNING')
    return auth

def invalidate(con):
    con.execute("UPDATE outbox SET phase='REVOKED' WHERE phase='QUEUED'")
    # RESERVED 尚未启动；RUNNING/UNKNOWN 不能冒充已取消。
    con.execute("UPDATE outbox SET phase='REVOKED' WHERE id IN (SELECT id FROM fusion_runs WHERE state='RESERVED')")
    con.execute("UPDATE fusion_runs SET state='REVOKED' WHERE state='RESERVED'")
    con.execute("UPDATE cards SET status='PENDING' WHERE status='APPROVED_EXECUTION_PENDING' AND NOT EXISTS (SELECT 1 FROM fusion_runs WHERE state IN ('RUNNING','UNKNOWN'))")

def change(con, session, data):
    if session['privilege'] not in ('normal', 'destructive'): denied('FUSION_OWNER_CAPABILITY_REQUIRED')
    value = state(con)
    owner=':'.join(session['subject'].split(':')[:2])
    if value['owner'] is not None and value['owner'] != owner: denied('FUSION_OWNER_REQUIRED')
    op = data.get('operation')
    if op == 'pause':
        if set(data) != {'operation'}: denied('FUSION_REQUEST_INVALID')
        new = {**value, 'paused': True}
    elif op == 'configure':
        fields = {'operation', 'expected_epoch', 'level', 'project', 'environment', 'observation_scope', 'control_scope', 'tasks', 'actors'}
        if set(data) != fields or type(data['expected_epoch']) is not int or data['expected_epoch'] != value['epoch']: denied('FUSION_CONFIG_CONFLICT')
        level = data['level']
        if level == 'L3': denied('BLOCKED_FLOW_ADAPTER_UNAVAILABLE')
        if level not in ('L0', 'L1', 'L2'): denied('FUSION_LEVEL_INVALID')
        if data['project'] != PROJECT: denied('BLOCKED_PROJECT_ADAPTER_UNAVAILABLE')
        if data['environment'] != ENVIRONMENT: denied('FUSION_ENVIRONMENT_DENIED')
        if data['observation_scope'] != ['history.py']: denied('FUSION_OBSERVATION_SCOPE_DENIED')
        if data['control_scope'] not in ([], ['history.py']) or (level != 'L2' and data['control_scope']): denied('FUSION_SCOPE_DENIED')
        tasks = data['tasks']
        if not isinstance(tasks, list) or any(not isinstance(t, str) for t in tasks) or len(tasks) != len(set(tasks)): denied('FUSION_TASK_DENIED')
        known = {json.loads(raw)['task'] for raw, in con.execute('SELECT body FROM cards')}
        if not set(tasks) <= known or (level == 'L2' and (not tasks or not data['control_scope'])) or (level != 'L2' and tasks): denied('FUSION_TASK_DENIED')
        if any(status in ('RUNNING','UNKNOWN') for status, in con.execute('SELECT state FROM fusion_runs')) and level == 'L2':
            denied('FUSION_PAUSE_AND_HANDOFF_REQUIRED')
        actors=data['actors']
        if not isinstance(actors,list) or any(not isinstance(a,str) or not a.startswith(owner+':') or len(a.split(':'))!=4 or a.split(':')[2] not in ('normal','destructive') or len(a.split(':')[-1])!=64 for a in actors) or len(actors)!=len(set(actors)) or (level=='L2' and not actors) or (level!='L2' and actors): denied('FUSION_ACTOR_DENIED')
        new = {**value, **{k: data[k] for k in ('level','project','environment','observation_scope','control_scope','tasks','actors')}, 'paused': False}
    else: denied('FUSION_OPERATION_INVALID')
    new.update(epoch=value['epoch'] + 1, owner=value['owner'] or owner)
    invalidate(con)
    con.execute('UPDATE fusion_state SET body=? WHERE id=1', (canonical(new),))
    event(con, op, {'actor': session['subject'], 'before': value, 'after': new})
    return snapshot(con)

@contextlib.contextmanager
def transaction(db):
    # 缺库/表一律拒绝，不隐式重建已授权运行时。
    con = sqlite3.connect(Path(db).resolve().as_uri() + '?mode=rw', uri=True, timeout=20)
    con.execute('PRAGMA synchronous=FULL')
    try:
        con.execute('BEGIN IMMEDIATE')
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()

@contextlib.contextmanager
def start(db, body):
    with transaction(db) as con:
        verify(con, body)
        row = con.execute('SELECT state FROM fusion_runs WHERE id=?', (body['request_id'],)).fetchone()
        if not row or row[0] != 'RESERVED': denied('DISPATCH_RESULT_UNKNOWN_MANUAL_REVIEW_NO_RESEND')
        # 调用者在事务内只启动进程；Popen 与 pause 确定性串行。
        con.execute("UPDATE fusion_runs SET state='RUNNING' WHERE id=?", (body['request_id'],))
        event(con, 'start', {'request_id': body['request_id'], 'fusion': body['fusion']})
        yield

@contextlib.contextmanager
def broker_write(cfg, body):
    """容器 broker 的父进程转发在线事务；没有父端许可不能写入。"""
    import sys
    bound = cfg.get('fusion')
    if not bound:
        raise ValueError('FUSION_BROKER_AUTHORIZATION_REQUIRED')
    if body.get('fusion') != bound or body.get('task')!=bound['task'] or body.get('scope')!=bound['scope'] or body.get('environment')!='dev' or not body.get('authorizer','').startswith(bound['actor']+';'): raise ValueError('FUSION_BROKER_BINDING_DENIED')
    for phase in ('begin',):
        print(canonical({'fusion_fence': phase, 'request_id': cfg['fusion_request']}), flush=True)
        if json.loads(sys.stdin.readline()).get('permit') is not True: raise ValueError('FUSION_BROKER_WRITE_DENIED')
    try:
        yield
    finally:
        print(canonical({'fusion_fence': 'end', 'request_id': cfg['fusion_request']}), flush=True)
        if json.loads(sys.stdin.readline()).get('permit') is not True: raise ValueError('FUSION_BROKER_WRITE_DENIED')
