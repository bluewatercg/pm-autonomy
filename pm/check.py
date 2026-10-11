"""在全新临时副本验收；测试状态和原始输出留在指定私有报告目录。"""
import argparse,ast,importlib.util,json,os,shutil,subprocess,sys,tempfile
from pathlib import Path
from initialize import initialize

def main():
    p=argparse.ArgumentParser();p.add_argument('--report-dir',required=True);a=p.parse_args()
    report=Path(a.report_dir).resolve();report.mkdir(mode=0o700,parents=True,exist_ok=False)
    runs=[]
    def run(name,args,cwd):
        result=subprocess.run(args,cwd=cwd,capture_output=True,text=True,timeout=600)
        (report/(name+'.stdout.log')).write_text(result.stdout);(report/(name+'.stderr.log')).write_text(result.stderr)
        runs.append({'name':name,'rc':result.returncode,'status':'PASS' if result.returncode==0 else 'FAIL'})
    with tempfile.TemporaryDirectory(prefix='pm-public-check-') as d:
        root=Path(d)/'runtime';initialize(root,fixture=True)
        for source in root.glob('*.py'):
            ast.parse(source.read_text(),filename=source.name)
        run('protocol',[sys.executable,'-m','unittest','test_approval','test_boundary','test_public_boundaries','-v'],root)
        runtime=Path(d)/'unconfigured-runtime';initialize(runtime)
        smoke="""import json,threading,http.client
import approval_api as a,remote_store
s=a.RemoteStore();server=a.make_server(s);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
try:
 c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5);c.request('GET','/api/card');r=c.getresponse();v=json.loads(r.read());assert r.status==200 and v['status']=='PENDING';c.close()
 sid,csrf=s.register({'capability':s.bootstrap_capability(),'challenge':s.challenge,'confirm_capability_holder':True});p=v['proposal'];d={'request_id':'unconfigured-demo-request','decision':'approve','version':p['version'],'proposal_hash':v['proposal_hash'],'scope':p['scope'],'base':p['base'],'expiry':p['expiry'],'actions':p['actions'],'note':''}
 try:s.decide(sid,csrf,d)
 except a.Denied as e:assert e.code=='BLOCKED_MODEL_NOT_CONFIGURED'
 else:raise AssertionError('未配置模型却批准')
 status=remote_store.call('status')['result'];assert status['decisions']==status['outbox']==0
 print(json.dumps({'http_card':200,'status':'PENDING','approval':'BLOCKED_MODEL_NOT_CONFIGURED','outbox':0,'secret_values_printed':False}))
finally:server.shutdown();server.server_close()
"""
        run('unconfigured-runtime-http',[sys.executable,'-c',smoke],runtime)
        run('architect-gates',[sys.executable,str(root/'test_roles_gate.py')],root)
        run('exception-recovery',[sys.executable,str(root/'test_exception_flow.py')],root)
        if shutil.which('node'):run('card-logic',['node',str(root/'test_card.js')],root)
        else:runs.append({'name':'card-logic','status':'BLOCKED','reason':'NODE_UNAVAILABLE'})
        image=os.environ.get('PM_CONTAINER_IMAGE','')
        available=False
        if image.startswith('sha256:') and shutil.which('docker'):
            available=subprocess.run(['docker','image','inspect',image],capture_output=True).returncode==0
        if available:
            run('docker-governed-flow',[sys.executable,str(root/'test_governed_flow.py')],root)
            regression=Path(d)/'docker-regression';initialize(regression,fixture=True)
            run('docker-regression',[sys.executable,str(regression/'run_validation.py')],regression)
            if (regression/'results.json').exists():shutil.copyfile(regression/'results.json',report/'results.json')
        else:runs.append({'name':'docker-governed-flow-and-regression','status':'BLOCKED','reason':'PINNED_IMAGE_UNAVAILABLE_NO_PULL'})
        for name in ['gate-test-results.json','exception-flow-results.json','governed-flow-results.json','results.json']:
            if (root/name).exists():shutil.copyfile(root/name,report/name)
    result={'activation':'NOT_IN_FORCE','reviewer':'SYNTHETIC_TEST_FIXTURE_ONLY','real_model_review':False,'runs':runs,'passed':sum(r['status']=='PASS' for r in runs),'failed':sum(r['status']=='FAIL' for r in runs),'blocked':sum(r['status']=='BLOCKED' for r in runs)}
    (report/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False));return bool(result['failed'])
if __name__=='__main__':raise SystemExit(main())
