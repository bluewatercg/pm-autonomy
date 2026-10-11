"""请求独立真实 architect；审查不会批准任务。"""
import argparse,json,secrets,subprocess,sys,time
from pathlib import Path
from roles_gate import Governance,review_binding,review_material,write_new,TRIGGERS
from remote_store import load_store
import bridge

def main():
    p=argparse.ArgumentParser();p.add_argument('--trigger',choices=TRIGGERS,required=True);a=p.parse_args()
    root=Path(__file__).resolve().parent
    try:
        load_store(root);card=bridge.read_proposal(root);g=Governance(root);state=g.snapshot(card)
        rid='review-'+secrets.token_hex(12);request=root/'private'/(rid+'.json')
        write_new(request,{'request':rid,'binding':review_binding(state,a.trigger),'material':review_material(root,card,state)})
        process=subprocess.run([sys.executable,str(root/'reviewer_process.py'),'--root',str(root),'--request',str(request)],capture_output=True,text=True,timeout=300)
        if process.stdout:
            result=json.loads(process.stdout);g.record_review(card,a.trigger,result['path'],'REAL_MODEL_SUBPROCESS',time.time()+3600)
            print(json.dumps(result));return process.returncode
        print(json.dumps({'ok':False,'code':'BLOCKED_MODEL_REVIEW_FAILED','process_rc':process.returncode}));return 2
    except Exception as exc:
        print(json.dumps({'ok':False,'code':getattr(exc,'code',str(exc))}));return 2
if __name__=='__main__':raise SystemExit(main())
