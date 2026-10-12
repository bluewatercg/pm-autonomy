"""需求事件与只读投影共用审批 SQLite；转换只登记待审批卡，不生成授权。"""
import copy
import json
import re
import time
from datetime import datetime

from approval_api import Denied, canonical, digest

STATES = {'discussing': '讨论中', 'awaiting_decision': '待决策', 'deferred': '暂缓',
          'converted': '已转提案', 'rejected': '已拒绝', 'superseded': '已被取代'}
TERMINAL = {'converted', 'rejected', 'superseded'}

def text(value, limit=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise Denied('REQUIREMENT_TEXT_INVALID', 400)
    return value

def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', value):
        raise Denied('REQUIREMENT_ID_INVALID', 400)
    return value

def source(value):
    if not isinstance(value, dict) or set(value) != {'ref', 'time', 'uncertain'}:
        raise Denied('REQUIREMENT_SOURCE_INVALID', 400)
    text(value['ref'], 500)
    text(value['time'], 80)
    try:
        parsed = datetime.fromisoformat(value['time'].replace('Z', '+00:00'))
        if parsed.tzinfo is None: raise ValueError()
    except ValueError:
        raise Denied('REQUIREMENT_SOURCE_TIME_INVALID', 400)
    if type(value['uncertain']) is not bool:
        raise Denied('REQUIREMENT_SOURCE_UNCERTAIN_INVALID', 400)

def questions(value):
    if not isinstance(value, list) or len(value) > 30:
        raise Denied('REQUIREMENT_QUESTIONS_INVALID', 400)
    for item in value: text(item, 500)

def priority(value):
    if value is not None and value not in ('low', 'medium', 'high'):
        raise Denied('REQUIREMENT_PRIORITY_INVALID', 400)

def owner(value):
    if value is not None: text(value, 100)

def schema(con):
    con.execute('CREATE TABLE IF NOT EXISTS requirement_events(seq INTEGER PRIMARY KEY,kind TEXT NOT NULL,body TEXT NOT NULL,prev TEXT NOT NULL,hash TEXT NOT NULL)')
    con.execute('CREATE TABLE IF NOT EXISTS requirement_requests(id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,result TEXT NOT NULL)')

def fold(con):
    state = {}; histories = {}; prev = '0' * 64
    for index, (seq, kind, raw, old, hashed) in enumerate(con.execute('SELECT * FROM requirement_events ORDER BY seq'), 1):
        body = json.loads(raw)
        if seq != index or old != prev or hashed != digest({'seq': seq, 'kind': kind, 'body': body, 'prev': old}):
            raise Denied('REQUIREMENT_CHAIN_BROKEN', 409)
        rid = identifier(body['requirement_id'])
        if kind not in ('add', 'note', 'update', 'state', 'defer', 'convert'):
            raise Denied('REQUIREMENT_EVENT_UNKNOWN', 409)
        before = state.get(rid)
        after = body['value']
        if (kind == 'add') != (before is None) or after['version'] != (before['version'] + 1 if before else 1):
            raise Denied('REQUIREMENT_REPLAY_VERSION', 409)
        if before and (after['source'] != before['source'] or after['goal'] != before['goal']):
            raise Denied('REQUIREMENT_REPLAY_SOURCE', 409)
        if after['status'] not in STATES:
            raise Denied('REQUIREMENT_REPLAY_STATE', 409)
        state[rid] = after
        histories.setdefault(rid, []).append({'seq': seq, 'kind': kind, 'body': body, 'prev': old, 'hash': hashed})
        prev = hashed
    return state, histories, prev

def registry(store, con):
    rows = con.execute('SELECT body,status FROM cards ORDER BY version').fetchall()
    tasks = {}
    for raw, status in rows:
        card = json.loads(raw)
        tasks[card['task']] = {'version': card['version'], 'status': status, 'complete': False}
        if status == 'COMPLETED_SYNTHETIC' and store.task_closed is not None:
            receipts = con.execute("SELECT receipt,result FROM outbox WHERE phase='CLOSED'").fetchall()
            matched = any(json.loads(receipt)['body']['proposal_hash'] == digest(card) and result and json.loads(result).get('ok') is True for receipt, result in receipts)
            tasks[card['task']]['complete'] = bool(matched and store.task_closed(card))
    return tasks

def view(value, history, tasks):
    deps = value['after_task']
    blocked = [tid for tid in deps if not tasks.get(tid, {}).get('complete')]
    eligible = value['status'] not in TERMINAL
    return {**value, 'status_label': STATES[value['status']], 'blocked_after_task': blocked,
            'ready_for_decision': eligible and not blocked,
            'readiness': ('等待依赖验收闭合' if blocked else '可供决策') if eligible else STATES[value['status']],
            'proposal_is_approval': False, 'automatic_execution': False, 'reminders': False,
            'events': history}

def read(store, sid, requirement_id=None):
    store.session(sid)
    with store.connect() as con:
        con.execute('BEGIN')
        state, histories, _ = fold(con)
        tasks = registry(store, con)
        if requirement_id is not None:
            identifier(requirement_id)
            if requirement_id not in state: raise Denied('REQUIREMENT_NOT_FOUND', 404)
            return view(state[requirement_id], histories[requirement_id], tasks)
        return {'requirements': [view(state[rid], histories[rid], tasks) for rid in sorted(state)],
                'tasks': tasks, 'activation': 'NOT_IN_FORCE', 'reminders': False}

def mutate(store, sid, csrf, data):
    session = store.session(sid, csrf)
    if session['privilege'] not in ('discussion', 'normal', 'destructive'):
        raise Denied('REQUIREMENT_WRITE_CAPABILITY_REQUIRED')
    if not isinstance(data, dict): raise Denied('REQUIREMENT_OBJECT_REQUIRED', 400)
    common = {'request_id', 'operation', 'requirement_id', 'expected_version'}
    fields = {'add': {'source', 'goal', 'owner', 'priority', 'open_questions'},
              'note': {'note'}, 'update': {'owner', 'priority', 'open_questions', 'after_task'},
              'state': {'status', 'reason', 'superseded_by'}, 'defer': {'reason', 'after_task'},
              'convert': {'proposal_version', 'proposal_hash', 'scope'}}
    op = data.get('operation')
    if not isinstance(op, str) or op not in fields or set(data) != common | fields[op]:
        raise Denied('REQUIREMENT_PAYLOAD_INVALID', 400)
    reqid = identifier(data['request_id']); rid = identifier(data['requirement_id'])
    expected = data['expected_version']
    if type(expected) is not int or expected < 0: raise Denied('REQUIREMENT_VERSION_INVALID', 400)
    fp = digest({'actor': session['subject'], 'data': data})
    with store.lock, store.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        state, histories, prev = fold(con)
        cached = con.execute('SELECT fingerprint,result FROM requirement_requests WHERE id=?', (reqid,)).fetchone()
        if cached:
            if cached[0] != fp: raise Denied('IDEMPOTENCY_CONFLICT', 409)
            return {**json.loads(cached[1]), 'cached': True}
        before = state.get(rid)
        if expected != (before['version'] if before else 0): raise Denied('REQUIREMENT_VERSION_CONFLICT', 409)
        if op == 'add':
            if before: raise Denied('REQUIREMENT_ALREADY_EXISTS', 409)
            source(data['source']); text(data['goal']); owner(data['owner']); priority(data['priority']); questions(data['open_questions'])
            value = {'requirement_id': rid, 'version': 1, 'source': data['source'], 'goal': data['goal'],
                     'status': 'discussing', 'owner': data['owner'], 'priority': data['priority'],
                     'open_questions': data['open_questions'], 'notes': [], 'decision_reason': None,
                     'after_task': [], 'superseded_by': None, 'proposal': None, 'task_id': None}
        else:
            if not before: raise Denied('REQUIREMENT_NOT_FOUND', 404)
            if before['status'] in TERMINAL: raise Denied('REQUIREMENT_TERMINAL', 409)
            value = copy.deepcopy(before); value['version'] += 1
            if op == 'note':
                value['notes'].append({'text': text(data['note']), 'actor': session['subject'], 'time': time.time()})
            if op == 'update':
                owner(data['owner']); priority(data['priority']); questions(data['open_questions'])
                for name in ('owner', 'priority', 'open_questions'): value[name] = data[name]
            if op in ('update', 'defer'):
                deps = data['after_task']
                if not isinstance(deps, list) or len(deps) > 30 or any(not isinstance(x, str) for x in deps) or len(set(deps)) != len(deps):
                    raise Denied('REQUIREMENT_DEPENDENCIES_INVALID', 400)
                known = registry(store, con)
                if any(tid not in known for tid in deps): raise Denied('REQUIREMENT_UNKNOWN_TASK', 409)
                value['after_task'] = deps
            if op == 'defer':
                value['decision_reason'] = text(data['reason']); value['status'] = 'deferred'
            if op == 'state':
                target = data['status']
                if not isinstance(target, str) or target not in ('discussing', 'awaiting_decision', 'rejected', 'superseded'):
                    raise Denied('REQUIREMENT_STATE_INVALID', 400)
                reason = data['reason']
                if target in ('rejected', 'superseded'): text(reason)
                elif reason is not None: text(reason)
                replacement = data['superseded_by']
                if target == 'superseded':
                    identifier(replacement)
                    if replacement not in state: raise Denied('REQUIREMENT_REPLACEMENT_UNKNOWN', 409)
                    seen = {rid}; cursor = replacement
                    while cursor:
                        if cursor in seen: raise Denied('REQUIREMENT_SUPERSESSION_CYCLE', 409)
                        seen.add(cursor); cursor = state[cursor]['superseded_by']
                elif replacement is not None: raise Denied('REQUIREMENT_REPLACEMENT_INVALID', 400)
                value.update(status=target, decision_reason=reason, superseded_by=replacement)
            if op == 'convert':
                if session['privilege'] == 'discussion': raise Denied('PROPOSAL_CAPABILITY_REQUIRED')
                current_raw, status = con.execute('SELECT body,status FROM cards ORDER BY version DESC LIMIT 1').fetchone()
                current = json.loads(current_raw)
                if type(data['proposal_version']) is not int or data['proposal_version'] != current['version'] or data['proposal_hash'] != digest(current):
                    raise Denied('STALE_PROPOSAL', 409)
                if data['scope'] != ['history.py'] or current['scope'] != data['scope'] or current['actions'] != ['edit'] or current['change'] != 'normal' or current.get('revision_required'):
                    raise Denied('REQUIREMENT_SYNTHETIC_SCOPE_ONLY', 409)
                known = registry(store, con)
                if status not in ('PENDING', 'REJECTED', 'SUPERSEDED') and not known[current['task']]['complete']:
                    raise Denied('REQUIREMENT_ACTIVE_TASK_BLOCKED', 409)
                ready = view(value, [], registry(store, con))
                if not ready['ready_for_decision']: raise Denied('REQUIREMENT_DEPENDENCIES_BLOCKED', 409)
                # 用户确定的是关联的当前合成合同，不把自由文本解释成已实现业务功能。
                proposal = {**current, 'version': current['version'] + 1, 'parent_proposal_hash': digest(current),
                            'expiry': time.time() + 86400, 'requirement_context': value['goal'],
                            'source_requirement': {'requirement_id': rid, 'version': value['version'],
                                                   'source': value['source'], 'actor': session['subject']}}
                task_id = ('TEST-REQ-' if current['task'].startswith('TEST-') else 'REQ-') + rid
                if task_id in known: raise Denied('REQUIREMENT_TASK_EXISTS', 409)
                proposal.update(task=task_id, worktree='/outputs/candidate-next-approval/worktrees/' + task_id, branch='candidate/' + task_id)
                proposal['source'] = {**current.get('source', {}), 'requirement_id': rid, 'requirement_version': value['version']}
                if status != 'COMPLETED_SYNTHETIC':
                    con.execute("UPDATE cards SET status='SUPERSEDED' WHERE version=?", (current['version'],))
                con.execute("UPDATE outbox SET phase='REVOKED' WHERE phase!='CLOSED'")
                con.execute('INSERT INTO cards VALUES(?,?,?)', (proposal['version'], canonical(proposal), 'PENDING'))
                value.update(status='converted', task_id=proposal['task'], proposal={'task_id': proposal['task'], 'version': proposal['version'], 'proposal_hash': digest(proposal)})
        body = {'requirement_id': rid, 'actor': session['subject'], 'request_id': reqid, 'time': time.time(), 'value': value}
        seq = con.execute('SELECT count(*) FROM requirement_events').fetchone()[0] + 1
        hashed = digest({'seq': seq, 'kind': op, 'body': body, 'prev': prev})
        con.execute('INSERT INTO requirement_events VALUES(?,?,?,?,?)', (seq, op, canonical(body), prev, hashed))
        result = {'requirement_id': rid, 'version': value['version'], 'status': value['status'], 'proposal': value['proposal'], 'cached': False}
        con.execute('INSERT INTO requirement_requests VALUES(?,?,?)', (reqid, fp, canonical(result)))
        return result
