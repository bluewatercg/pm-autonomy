"""公开接线的阻塞和恢复验证；无真实模型或用户批准。"""
import json,os,shutil,subprocess,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import bridge,approval_api,model_adapter,remote_store
from roles_gate import load_roles,GateDenied

class PublicBoundaryTests(unittest.TestCase):
    def test_empty_model_configuration_blocks(self):
        root=Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);roles=json.loads((root/'roles.json').read_bytes())
            for actor in roles['roles'].values():actor['model']=''
            roles['model_policy']['verified_models']=[]
            (p/'roles.json').write_text(json.dumps(roles));(p/'model-id-observation.json').write_text('{"models":[]}')
            with self.assertRaises(GateDenied) as e:load_roles(p/'roles.json')
            self.assertEqual(e.exception.code,'BLOCKED_MODEL_NOT_CONFIGURED')
    def test_fixture_configuration_cannot_be_used_without_marker(self):
        root=Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            for name in ['roles.json','model-id-observation.json']:(p/name).write_bytes((root/name).read_bytes())
            with self.assertRaises(GateDenied) as e:load_roles(p/'roles.json')
            self.assertEqual(e.exception.code,'FIXTURE_MODEL_NOT_RUNTIME')
    def test_adapter_missing_configuration_blocks(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaisesRegex(model_adapter.Blocked,'BLOCKED_MODEL_ADAPTER_NOT_CONFIGURED'):
                model_adapter.command(Path('.'),Path('.'),'','high','prompt')
    def test_adapter_keeps_no_tools_and_separate_arguments(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for n in ['adapter','config','deny-tools.mjs']:(root/n).touch()
            with patch.dict(os.environ,{'PM_MODEL_EXECUTABLE':str(root/'adapter'),'PM_MODEL_CONFIG':str(root/'config')},clear=True):
                args,env=model_adapter.command(root,root,'external/model','high','untrusted; $(value)')
            self.assertIn('--no-tools',args);self.assertIn('--extension',args)
            self.assertEqual(args[-1],'untrusted; $(value)');self.assertNotIn('bash',args)
    def test_no_model_intake_blocks_before_call(self):
        import next_kernel_v3 as k
        with patch.object(k,'MODEL',''):
            with self.assertRaisesRegex(k.Denied,'BLOCKED_MODEL_NOT_CONFIGURED'):k.model_call(Path('.'),'request-1','prompt')
    def test_actual_successor_process_crash_recovers(self):
        source=Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'private').mkdir()
            for name in ['roles.json','model-id-observation.json','FIXTURE_ONLY','pending-proposal.json']:
                shutil.copyfile(source/name,root/name)
            (root/'private/bridge-key.bin').write_bytes(os.urandom(32))
            card=json.loads((root/'pending-proposal.json').read_bytes());card['control_sources']={};card['task']='TEST-PROCESS-CRASH';(root/'pending-proposal.json').write_text(json.dumps(card))
            s=remote_store.load_store(root)
            successor={**card,'version':2,'parent_proposal_hash':approval_api.digest(card),'revision_required':True,'change_note':'fixture successor'}
            code="import sys,os,json;sys.path.insert(0,%r);from roles_gate import Governance;p=json.loads(sys.stdin.read());Governance(%r).register(p,p['governance_policy']);os._exit(87)"%(str(source),str(root))
            process=subprocess.run([__import__('sys').executable,'-I','-c',code],input=json.dumps(successor),capture_output=True,text=True,timeout=10)
            self.assertEqual(process.returncode,87)
            recovered=remote_store.load_store(root);self.assertEqual(recovered.card()['proposal']['version'],2)
            self.assertEqual(recovered.card()['status'],'PENDING');self.assertTrue(recovered.card()['proposal']['revision_required'])
    def test_unknown_dispatch_blocks_without_resend(self):
        root=Path(__file__).resolve().parent;p=bridge.read_proposal(root);key=(root/'private/bridge-key.bin').read_bytes()
        s=approval_api.Store(root/'private/public-boundary-approval',p,key,lambda card:bridge.baseline(root),lambda receipt:None,lambda new:None,gate=lambda card,phase:None)
        sid,csrf=s.register({'capability':s.bootstrap_capability(),'challenge':s.challenge,'confirm_capability_holder':True})
        from test_approval import authorize_l2
        authorize_l2(s,sid,csrf)
        card=s.card();d={'request_id':'test-unknown-boundary','decision':'approve','version':p['version'],'proposal_hash':card['proposal_hash'],'scope':p['scope'],'base':p['base'],'expiry':p['expiry'],'actions':p['actions'],'note':''}
        s.decide(sid,csrf,d)
        with s.connect() as con:r=json.loads(con.execute('SELECT receipt FROM outbox').fetchone()[0])
        folder=root/'private/dispatch';folder.mkdir(exist_ok=True)
        bridge.save(folder/(d['request_id']+'.intent.json'),{'fingerprint':bridge.sha(approval_api.canonical(r).encode())})
        with self.assertRaisesRegex(ValueError,'DISPATCH_RESULT_UNKNOWN_MANUAL_REVIEW_NO_RESEND'):bridge.recover_result(root,r,key)
        self.assertFalse((folder/(d['request_id']+'.json')).exists())

if __name__=='__main__':unittest.main(verbosity=2)
