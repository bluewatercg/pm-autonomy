"""回环标准库审批服务。身份仅代表能力持有人，绝非 平台 账户认证。"""
import argparse, base64, contextlib, hashlib, hmac, http.cookies, http.server, json, os, secrets, sqlite3, subprocess, sys, threading, time, webbrowser
from pathlib import Path

import roles_gate

R = Path(__file__).resolve().parent

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()
class Denied(Exception):
    def __init__(self, code, status=403): self.code, self.status = code, status

def signature(key, value):
    return hmac.new(key, canonical(value).encode(), 'sha256').hexdigest()

class Store:
    def __init__(self, root, proposal, key, baseline, dispatch, sync_version, gate=None, recover=None, task_closed=None):
        self.task_closed=task_closed
        self.recover=recover
        self.gate=gate
        self.root, self.key = Path(root), key
        self.baseline, self.dispatch, self.sync_version = baseline, dispatch, sync_version
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root/'approval.sqlite'
        self.lock = threading.RLock()
        self.sessions = {}
        self.capabilities = {}
        self.challenge = secrets.token_urlsafe(32)
        with self.connect() as con:
            con.executescript('CREATE TABLE IF NOT EXISTS cards(version INTEGER PRIMARY KEY,body TEXT NOT NULL,status TEXT NOT NULL); CREATE TABLE IF NOT EXISTS decisions(id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,version INTEGER NOT NULL,body TEXT NOT NULL); CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY,receipt TEXT NOT NULL,result TEXT,phase TEXT NOT NULL);')
            con.execute('CREATE TABLE IF NOT EXISTS failure_occurrences(id TEXT PRIMARY KEY,card TEXT NOT NULL,result TEXT NOT NULL,applied INTEGER NOT NULL)')
            from requirements import schema
            schema(con)
            if not con.execute('SELECT 1 FROM cards').fetchone():
                con.execute('INSERT INTO cards VALUES(?,?,?)',(proposal['version'],canonical(proposal),'PENDING'))
        os.chmod(self.db,0o600)
    @contextlib.contextmanager
    def connect(self):
        con = sqlite3.connect(self.db, timeout=20)
        con.execute('PRAGMA synchronous=FULL')
        try:
            with con:
                yield con
        finally:
            con.close()
    def card(self):
        with self.connect() as con:
            row=con.execute('SELECT body,status FROM cards ORDER BY version DESC LIMIT 1').fetchone()
        body=json.loads(row[0])
        return {'proposal':body,'proposal_hash':digest(body),'status':row[1],'identity':'能力持有人授权；不是 平台 账户认证或多用户实名身份','registration':'尚未确认' if not self.sessions else '本次服务内存在能力会话','server_time':time.time()}
    def check_gate(self,card,phase,result=None):
        if 'governance_policy' in card and self.gate is None:
            raise Denied('GOVERNANCE_GATE_NOT_CONNECTED',409)
        if self.gate:
            try: return self.gate(card,phase,result) if result is not None else self.gate(card,phase)
            except roles_gate.GateDenied as exc: raise Denied(exc.code,409)
    def replay_failures(self):
        with self.connect() as con: rows=con.execute('SELECT id,card,result FROM failure_occurrences WHERE applied=0 ORDER BY rowid').fetchall()
        for occurrence,card,result in rows:
            self.check_gate(json.loads(card),'failure',json.loads(result))
            with self.connect() as con:con.execute('UPDATE failure_occurrences SET applied=1 WHERE id=?',(occurrence,))
    def record_exception(self,card,result):
        occurrence=secrets.token_hex(16);result={**result,'occurrence':occurrence}
        with self.connect() as con:con.execute('INSERT INTO failure_occurrences VALUES(?,?,?,0)',(occurrence,canonical(card),canonical(result)))
        self.replay_failures()
    def bootstrap_capability(self, privilege='normal', ttl=600):
        if privilege not in ('discussion','normal','destructive'): raise ValueError('privilege')
        token=secrets.token_urlsafe(32)
        self.capabilities[hashlib.sha256(token.encode()).hexdigest()]={'privilege':privilege,'expiry':time.time()+ttl,'used':False}
        return token
    def register(self, data):
        if set(data)!={'capability','challenge','confirm_capability_holder'} or data['confirm_capability_holder'] is not True or data['challenge']!=self.challenge:
            raise Denied('REGISTRATION_CONFIRMATION_REQUIRED')
        caphash=hashlib.sha256(str(data['capability']).encode()).hexdigest()
        with self.lock:
            cap=self.capabilities.get(caphash)
            if not cap or cap['used'] or time.time()>=cap['expiry']: raise Denied('INVALID_OR_EXPIRED_BOOTSTRAP')
            sid, csrf=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
            self.sessions[hashlib.sha256(sid.encode()).hexdigest()]={'csrf':csrf,'privilege':cap['privilege'],'expiry':time.time()+1800,'subject':'capability-holder:'+hashlib.sha256(self.key).hexdigest()[:24]+':'+cap['privilege']+':'+hashlib.sha256(sid.encode()).hexdigest()}
            cap['used']=True
        return sid, csrf
    def session(self, sid, csrf=None):
        session=self.sessions.get(hashlib.sha256(str(sid).encode()).hexdigest())
        if not session or time.time()>=session['expiry']: raise Denied('SESSION_REQUIRED',401)
        if csrf is not None and not hmac.compare_digest(session['csrf'],str(csrf)): raise Denied('CSRF_DENIED')
        return session
    def decide(self, sid, csrf, data):
        session=self.session(sid,csrf)
        if session['privilege']=='discussion': raise Denied('APPROVAL_CAPABILITY_REQUIRED')
        self.replay_failures()
        fields={'request_id','decision','version','proposal_hash','scope','base','expiry','actions','note'}
        if set(data)!=fields or data['decision'] not in ('approve','change','reject'): raise Denied('INVALID_DECISION',400)
        if not isinstance(data['request_id'],str) or not 8<=len(data['request_id'])<=100 or not all(c.isalnum() or c=='-' for c in data['request_id']): raise Denied('INVALID_REQUEST_ID',400)
        if not isinstance(data['note'],str) or len(data['note'])>2000: raise Denied('INVALID_NOTE',400)
        fp=digest({'subject':session['subject'],'data':data})
        with self.lock, self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            previous=con.execute('SELECT fingerprint,body FROM decisions WHERE id=?',(data['request_id'],)).fetchone()
            if previous:
                if previous[0]!=fp: raise Denied('IDEMPOTENCY_CONFLICT',409)
                return {**json.loads(previous[1]),'cached':True}
            body,status=con.execute('SELECT body,status FROM cards ORDER BY version DESC LIMIT 1').fetchone()
            card=json.loads(body)
            if data['version']!=card['version'] or data['proposal_hash']!=digest(card): raise Denied('STALE_PROPOSAL',409)
            for field in ('scope','base','expiry','actions'):
                if data[field]!=card[field]: raise Denied('BINDING_'+field.upper(),409)
            if time.time()>=card['expiry']: raise Denied('PROPOSAL_EXPIRED',409)
            if card['change']=='destructive' and session['privilege']!='destructive': raise Denied('DESTRUCTIVE_CAPABILITY_REQUIRED')
            if status!='PENDING' and not (data['decision']=='change' and status in ('APPROVED_EXECUTION_PENDING','EXECUTION_BLOCKED')): raise Denied('ALREADY_DECIDED',409)
            if data['decision']=='approve':
                if card.get('revision_required'): raise Denied('PLAN_REVISION_REQUIRED',409)
                self.check_gate(card,'approve')
                actual=self.baseline(card)
                if actual['base']!=card['base'] or actual['before_sha']!=card['before_sha']: raise Denied('BASELINE_DRIFT',409)
                receipt={'request_id':data['request_id'],'task':card['task'],'version':card['version'],'proposal_hash':digest(card),'scope':card['scope'],'base':card['base'],'before_sha':card['before_sha'],'expiry':min(card['expiry'],time.time()+900),'actions':card['actions'],'source_sha':card['source_sha'],'change':card['change'],'privilege':session['privilege'],'subject':session['subject'],'identity':'capability-holder-not-workbuddy-account','decision':'approve','approval_time':time.time()}
                receipt={'body':receipt,'hmac':signature(self.key,receipt)}
                con.execute('INSERT INTO outbox VALUES(?,?,NULL,?)',(data['request_id'],canonical(receipt),'QUEUED'))
                new_status='APPROVED_EXECUTION_PENDING'
            elif data['decision']=='reject':
                new_status='REJECTED'
            else:
                if not data['note'].strip(): raise Denied('CHANGE_NOTE_REQUIRED',400)
                successor={**card,'version':card['version']+1,'parent_proposal_hash':digest(card),'change_note':data['note'],'revision_required':True,'expiry':time.time()+86400}
                # 先使远端旧版本失效；若本地事务中断，旧卡只能拒绝，不能偷用旧授权。
                self.sync_version(successor)
                con.execute('UPDATE cards SET status=? WHERE version=?',('SUPERSEDED',card['version']))
                con.execute('UPDATE outbox SET phase=? WHERE phase!=?',('REVOKED','CLOSED'))
                con.execute('INSERT INTO cards VALUES(?,?,?)',(successor['version'],canonical(successor),'PENDING'))
                new_status='SUCCESSOR_PENDING'
            if data['decision']!='change': con.execute('UPDATE cards SET status=? WHERE version=?',(new_status,card['version']))
            result={'request_id':data['request_id'],'decision':data['decision'],'status':new_status,'version':card['version'],'proposal_hash':digest(card),'identity':'capability-holder-not-workbuddy-account','cached':False}
            con.execute('INSERT INTO decisions VALUES(?,?,?,?)',(data['request_id'],fp,card['version'],canonical(result)))
            return result
    def requirements(self, sid, requirement_id=None):
        from requirements import read
        return read(self, sid, requirement_id)
    def requirement_write(self, sid, csrf, data):
        from requirements import mutate
        return mutate(self, sid, csrf, data)
    def execute(self, sid, csrf, rid):
        session=self.session(sid,csrf)
        if session['privilege']=='discussion': raise Denied('APPROVAL_CAPABILITY_REQUIRED')
        self.replay_failures()
        with self.lock:
            with self.connect() as con:
                row=con.execute('SELECT receipt,result,phase FROM outbox WHERE id=?',(rid,)).fetchone()
                if not row: raise Denied('APPROVAL_REQUIRED')
                receipt=json.loads(row[0]); b=receipt['body']; card=self.card()
                if session['subject']!=b['subject']: raise Denied('SESSION_SUBJECT_MISMATCH')
                if b['version']!=card['proposal']['version'] or b['proposal_hash']!=card['proposal_hash'] or row[2]=='REVOKED': raise Denied('SUPERSEDED_AUTHORIZATION',409)
                if row[2]=='CLOSED': return {**json.loads(row[1]),'cached':True}
                completed_result=json.loads(row[1]) if row[1] else None
                if completed_result is None and self.recover:
                    try: completed_result=self.recover(receipt)
                    except ValueError as exc:
                        self.record_exception(card['proposal'],{'ok':False,'code':'RECOVERY_'+str(exc),'request_id':rid,'version':b['version'],'failure_origin':'RECOVERY_EXCEPTION'})
                        raise Denied(str(exc),409)
                if completed_result is None:
                    if time.time()>=b['expiry']: raise Denied('AUTHORIZATION_EXPIRED',409)
                    self.check_gate(card['proposal'],'execute')
                    con.execute('UPDATE outbox SET phase=? WHERE id=?',('DISPATCHING',rid))
                else:
                    con.execute('UPDATE outbox SET result=?,phase=? WHERE id=?',(canonical(completed_result),'RESULT_READY',rid))
            try: result=completed_result if completed_result is not None else self.dispatch(receipt)
            except Exception as exc:
                with self.connect() as con: con.execute('UPDATE outbox SET phase=? WHERE id=?',('INTERRUPTED_RETRY_SAME_REQUEST',rid))
                self.record_exception(card['proposal'],{'ok':False,'code':'DISPATCH_'+type(exc).__name__,'request_id':rid,'version':b['version'],'failure_origin':'DISPATCH_EXCEPTION'})
                raise Denied('DISPATCH_INTERRUPTED_RETRY_SAME_REQUEST',503)
            with self.connect() as con:
                con.execute('UPDATE outbox SET result=?,phase=? WHERE id=?',(canonical(result),'RESULT_READY',rid))
            if not result.get('ok'):
                self.check_gate(card['proposal'],'failure',result)
            if result.get('ok'):
                try: self.check_gate(card['proposal'],'close',result)
                except Denied:
                    with self.connect() as con:
                        con.execute('UPDATE outbox SET result=?,phase=? WHERE id=?',(canonical(result),'CLOSE_REVIEW_BLOCKED',rid))
                        con.execute('UPDATE cards SET status=? WHERE version=?',('EXECUTION_BLOCKED',b['version']))
                    raise
            with self.connect() as con:
                phase='CLOSED' if result.get('ok') else 'BLOCKED'
                con.execute('UPDATE outbox SET result=?,phase=? WHERE id=?',(canonical(result),phase,rid))
                con.execute('UPDATE cards SET status=? WHERE version=?',('COMPLETED_SYNTHETIC' if result.get('ok') else 'EXECUTION_BLOCKED',b['version']))
            return result

def make_server(store, port=0):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def send(self,status,value,cookie=None,html=False):
            raw=value.encode() if html else canonical(value).encode()
            self.send_response(status)
            self.send_header('Content-Type','text/html; charset=utf-8' if html else 'application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(raw)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if cookie: self.send_header('Set-Cookie','approval_session='+cookie+'; HttpOnly; SameSite=Strict; Path=/; Max-Age=1800')
            self.end_headers(); self.wfile.write(raw)
        def trusted(self, mutation=False):
            expected='127.0.0.1:'+str(self.server.server_port)
            if self.headers.get('Host')!=expected: raise Denied('HOST_DENIED')
            if mutation and self.headers.get('Origin')!='http://'+expected: raise Denied('ORIGIN_DENIED')
            if mutation and self.headers.get('Sec-Fetch-Site') not in (None,'same-origin','none'): raise Denied('FETCH_SITE_DENIED')
        def sid(self):
            c=http.cookies.SimpleCookie(); c.load(self.headers.get('Cookie',''))
            return c['approval_session'].value if 'approval_session' in c else ''
        def do_GET(self):
            try:
                self.trusted()
                if self.path=='/': return self.send(200,(R/'approval-card.html').read_text(encoding='utf-8'),html=True)
                if self.path=='/card.js':
                    raw=(R/'card.js').read_bytes(); self.send_response(200); self.send_header('Content-Type','application/javascript; charset=utf-8'); self.send_header('Content-Length',str(len(raw))); self.send_header('Cache-Control','no-store'); self.end_headers(); return self.wfile.write(raw)
                if self.path=='/requirements': return self.send(200,(R/'requirements.html').read_text(encoding='utf-8'),html=True)
                if self.path=='/requirements.js':
                    raw=(R/'requirements.js').read_bytes(); self.send_response(200); self.send_header('Content-Type','application/javascript; charset=utf-8'); self.send_header('Content-Length',str(len(raw))); self.send_header('Cache-Control','no-store'); self.end_headers(); return self.wfile.write(raw)
                if self.path=='/api/requirements': return self.send(200,store.requirements(self.sid()))
                if self.path.startswith('/api/requirements/'):
                    return self.send(200,store.requirements(self.sid(),self.path.split('/')[-1]))
                if self.path=='/api/card': return self.send(200,{**store.card(),'challenge':store.challenge})
                if self.path=='/api/session': return self.send(200,store.session(self.sid()))
                raise Denied('NOT_FOUND',404)
            except Denied as e: self.send(e.status,{'ok':False,'code':e.code})
            except Exception: self.send(503,{'ok':False,'code':'DEPENDENCY_UNAVAILABLE_NO_APPROVAL_CONFIRMED'})
        def do_POST(self):
            try:
                self.trusted(True)
                if self.headers.get('Content-Type')!='application/json': raise Denied('JSON_REQUIRED',415)
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=16384: raise Denied('REQUEST_SIZE',413)
                data=json.loads(self.rfile.read(size))
                if not isinstance(data,dict): raise Denied('JSON_OBJECT_REQUIRED',400)
                if self.path=='/api/register':
                    sid,csrf=store.register(data); return self.send(200,{'ok':True,'csrf':csrf,'identity':'capability-holder-not-workbuddy-account'},cookie=sid)
                if self.path=='/api/requirements': return self.send(200,store.requirement_write(self.sid(),self.headers.get('X-CSRF-Token',''),data))
                if self.path=='/api/decision': return self.send(200,store.decide(self.sid(),self.headers.get('X-CSRF-Token',''),data))
                if self.path=='/api/execute':
                    if set(data)!={'request_id'}: raise Denied('INVALID_REQUEST',400)
                    return self.send(200,store.execute(self.sid(),self.headers.get('X-CSRF-Token',''),data['request_id']))
                raise Denied('NOT_FOUND',404)
            except Denied as e: self.send(e.status,{'ok':False,'code':e.code})
            except (ValueError,TypeError,KeyError): self.send(400,{'ok':False,'code':'INVALID_REQUEST'})
            except Exception: self.send(503,{'ok':False,'code':'DEPENDENCY_UNAVAILABLE_NO_APPROVAL_CONFIRMED'})
    return http.server.ThreadingHTTPServer(('127.0.0.1',port),Handler)

class RemoteStore:
    """本地调用；持久状态、能力登记、签名密钥在运行目录 private。"""
    def invoke(self, op, value=None):
        from remote_store import call
        out=call(op,value)
        if out.get('ok') is False: raise Denied(out['code'],out.get('status',403))
        return out['result']
    @property
    def challenge(self): return self.invoke('challenge')
    def card(self): return self.invoke('card')
    def bootstrap_capability(self,privilege='normal'): return self.invoke('bootstrap',privilege)
    def register(self,data): return self.invoke('register',data)
    def session(self,sid): return self.invoke('session',sid)
    def decide(self,sid,csrf,data): return self.invoke('decide',[sid,csrf,data])
    def execute(self,sid,csrf,rid): return self.invoke('execute',[sid,csrf,rid])
    def requirements(self,sid,requirement_id=None): return self.invoke('requirements',[sid,requirement_id])
    def requirement_write(self,sid,csrf,data): return self.invoke('requirement_write',[sid,csrf,data])

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=0);p.add_argument('--open-browser',action='store_true');privileges=p.add_mutually_exclusive_group();privileges.add_argument('--destructive-session',action='store_true');privileges.add_argument('--discussion-session',action='store_true');args=p.parse_args()
    store=RemoteStore()
    server=make_server(store,args.port)
    token=store.bootstrap_capability('destructive' if args.destructive_session else 'discussion' if args.discussion_session else 'normal')
    url='http://127.0.0.1:'+str(server.server_port)+'/#bootstrap='+token
    print('仅回环临时入口；能力持有人测试边界，并非真实账户认证。请亲自注册和审批。',flush=True)
    print(url,flush=True)
    if args.open_browser:webbrowser.open(url)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
