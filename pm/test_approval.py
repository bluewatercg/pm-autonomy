import hashlib,json,secrets,tempfile,time,unittest
from pathlib import Path
from approval_api import Store,Denied

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
class FakeRemote:
    def __init__(self,root):self.root=Path(root);self.calls=[]
    def baseline(self,card):self.calls.append(('baseline',card['version']));return {'base':card['base'],'before_sha':card['before_sha']}
    def sync(self,p):self.calls.append(('sync',p['version']));return {'ok':True}
    def dispatch(self,r):self.calls.append(('dispatch',r['body']['request_id']));return {'ok':True,'synthetic':True,'request_id':r['body']['request_id']}
class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.fake=FakeRemote(self.root);self.key=secrets.token_bytes(32)
        self.proposal={'schema':'approval-proposal/v1','task':'SYNTHETIC-APPROVAL-001','version':1,'goal':'升序返回记录副本','scope':['history.py'],'base':'base-abc','before_sha':'before-abc','source_sha':'source-abc','expiry':time.time()+3600,'actions':['edit'],'change':'normal','risk':['仅合成'],'acceptance':['固定验收'],'relation':'task52 successor'}
        self.store=Store(self.root,self.proposal,self.key,self.fake.baseline,self.fake.dispatch,self.fake.sync)
        self.token=self.store.bootstrap_capability(); self.sid,self.csrf=self.store.register({'capability':self.token,'challenge':self.store.challenge,'confirm_capability_holder':True})
    def decision(self,**kw):
        p=self.store.card()['proposal'];return self.store.decide(self.sid,self.csrf,{'request_id':kw.get('request_id','req-0001'),'decision':kw.get('decision','approve'),'version':p['version'],'proposal_hash':self.store.card()['proposal_hash'],'scope':p['scope'],'base':p['base'],'expiry':p['expiry'],'actions':p['actions'],'note':kw.get('note','')})
    def test_pending_is_initial_and_registration_requires_actual_confirmation(self):
        self.assertEqual(self.store.card()['status'],'PENDING')
        tok=self.store.bootstrap_capability()
        with self.assertRaises(Denied) as e:self.store.register({'capability':tok,'challenge':self.store.challenge,'confirm_capability_holder':False})
        self.assertEqual(e.exception.code,'REGISTRATION_CONFIRMATION_REQUIRED')
    def test_csrf_token_version_hash_scope_base_expiry_and_actions(self):
        with self.assertRaises(Denied) as e:self.store.decide(self.sid,'bad',{})
        self.assertEqual(e.exception.code,'CSRF_DENIED')
        good={'request_id':'req-0001','decision':'approve','version':99,'proposal_hash':'bad','scope':['history.py'],'base':'base-abc','expiry':self.proposal['expiry'],'actions':['edit'],'note':''}
        with self.assertRaises(Denied) as e:self.store.decide(self.sid,self.csrf,good)
        self.assertEqual(e.exception.code,'STALE_PROPOSAL')
        p=self.store.card()['proposal'];p['expiry']=time.time()-1
        with self.store.connect() as con:con.execute('UPDATE cards SET body=?',(json.dumps(p),))
        with self.assertRaises(Denied) as e:self.decision(request_id='req-expired')
        self.assertEqual(e.exception.code,'PROPOSAL_EXPIRED')
    def test_approve_idempotency_and_execute(self):
        first=self.decision();second=self.decision();self.assertFalse(first['cached']);self.assertTrue(second['cached']);self.assertEqual(self.fake.calls.count(('dispatch','req-0001')),0)
        result=self.store.execute(self.sid,self.csrf,'req-0001');self.assertTrue(result['ok']);again=self.store.execute(self.sid,self.csrf,'req-0001');self.assertTrue(again['cached']);self.assertEqual(self.fake.calls.count(('dispatch','req-0001')),1)
    def test_agent_cannot_decide_without_capability_and_change_invalidates_old(self):
        p=self.store.card()['proposal']
        with self.assertRaises(Denied) as e:self.store.decide('forged',self.csrf,{'request_id':'req-forge','decision':'approve','version':p['version'],'proposal_hash':digest(p),'scope':p['scope'],'base':p['base'],'expiry':p['expiry'],'actions':p['actions'],'note':''})
        self.assertEqual(e.exception.code,'SESSION_REQUIRED')
        changed=self.decision(request_id='req-change',decision='change',note='补充必须保持输入不变的验收')
        self.assertEqual(changed['status'],'SUCCESSOR_PENDING');self.assertEqual(self.fake.calls[-1],('sync',2));self.assertEqual(self.store.card()['proposal']['version'],2)
    def test_destructive_requires_separate_privilege(self):
        p=dict(self.proposal);p.update(version=1,change='destructive');other=FakeRemote(self.root);s=Store(self.root/'destructive',p,self.key,other.baseline,other.dispatch,other.sync);t=s.bootstrap_capability('normal');sid,csrf=s.register({'capability':t,'challenge':s.challenge,'confirm_capability_holder':True})
        with self.assertRaises(Denied) as e:s.decide(sid,csrf,{'request_id':'req-destructive','decision':'approve','version':1,'proposal_hash':s.card()['proposal_hash'],'scope':p['scope'],'base':p['base'],'expiry':p['expiry'],'actions':p['actions'],'note':''})
        self.assertEqual(e.exception.code,'DESTRUCTIVE_CAPABILITY_REQUIRED')
if __name__=='__main__':unittest.main(verbosity=2)
