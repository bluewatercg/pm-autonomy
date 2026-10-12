"""独立新进程复算真实 Docker 导出证据，不执行导出的任意源码。"""
import argparse,json
from pathlib import Path
from roles_gate import sha,digest,canonical,GateDenied,load_roles

def verify(root,card,result):
    root=Path(root);load_roles(root/'roles.json')
    e=result['evidence'];receipt_sha=e['receipt_sha'];folder=root/'private/dispatch'
    matches=[]
    for path in folder.glob('*.json'):
        if path.name.endswith('.intent.json'):continue
        value=json.loads(path.read_bytes())
        if value.get('evidence',{}).get('receipt_sha')==receipt_sha:matches.append((path,value))
    if len(matches)!=1:raise GateDenied('ACCEPTANCE_DISPATCH_BINDING')
    path,stored=matches[0];supplied=dict(result);supplied.pop('cached',None)
    if supplied!=stored:raise GateDenied('ACCEPTANCE_RESULT_DRIFT')
    runtime=stored['runtime'];raw=(folder/(path.stem+'.stdout')).read_bytes();stderr=(folder/(path.stem+'.stderr')).read_bytes()
    if sha(raw)!=runtime['stdout_sha'] or sha(stderr)!=runtime['stderr_sha']:raise GateDenied('ACCEPTANCE_RUNTIME_RAW_DRIFT')
    exported=json.loads(raw.splitlines()[-1])
    if exported['evidence']!=e or not exported['ok'] or runtime['rc']!=0:raise GateDenied('ACCEPTANCE_RUNTIME_FAILED')
    from bridge import IMAGE
    if runtime['image']!=IMAGE or runtime['network']!='none' or not runtime['read_only'] or runtime['ports'] or runtime['privileged'] or any(m['Type']!='tmpfs' for m in runtime['mounts']):raise GateDenied('ACCEPTANCE_ISOLATION_INVALID')
    if e['task']!=card['task'] or e['head']!=card['base'] or e['base']!=card['base'] or e['repo_remotes']:raise GateDenied('ACCEPTANCE_GIT_BINDING')
    if sha(e['artifact'].encode())!=card['source_sha'] or e['artifact_sha']!=card['source_sha']:raise GateDenied('ACCEPTANCE_ARTIFACT_DRIFT')
    if sha(e['test_raw'].encode())!=e['test_sha'] or json.loads(e['test_raw'])!=e['test']:raise GateDenied('ACCEPTANCE_TEST_RAW_DRIFT')
    t=e['test']
    if t['rc']!=0 or t['source_sha']!=card['source_sha'] or t['actual_head']!=card['base']:raise GateDenied('ACCEPTANCE_TEST_BINDING')
    stage=3 if card['change']=='destructive' else 1
    if t['command']!=['python3','-I','fixed acceptance',stage] or json.loads(t['stdout'])!={'pass':True,'checks':['sort','copy_input','empty']+(['descending'] if stage==3 else [])}:raise GateDenied('ACCEPTANCE_FIXED_TEST_CONTRACT')
    if sha(e['review_raw'].encode())!=e['review_sha']:raise GateDenied('ACCEPTANCE_REVIEW_RAW_DRIFT')
    previous='0'*64
    for seq,event in enumerate(e['events'],1):
        body={k:v for k,v in event.items() if k!='hash'}
        if event['seq']!=seq or event['prev']!=previous or digest(body)!=event['hash']:raise GateDenied('ACCEPTANCE_EVENT_CHAIN')
        previous=event['hash']
    events=[v for v in e['events'] if v['request']==path.stem]
    expected=['intent','written','accepted'] if 'governance_policy' in card else ['intent','written','accepted','closed']
    if [v['kind'] for v in events]!=expected:raise GateDenied('ACCEPTANCE_EVENT_CONTRACT')
    intent,written,accepted=[v['payload'] for v in events[:3]]
    if intent['task']!=card['task'] or intent['base']!=card['base'] or intent['source_sha']!=card['source_sha'] or intent['uid']!=21002:raise GateDenied('ACCEPTANCE_INTENT_BINDING')
    if written['task']!=card['task'] or written['base']!=card['base'] or written['artifact_sha']!=card['source_sha'] or written['review_sha']!=e['review_sha']:raise GateDenied('ACCEPTANCE_WRITTEN_BINDING')
    if accepted!={'source_sha':card['source_sha'],'test_sha':e['test_sha'],'review_sha':e['review_sha']}:raise GateDenied('ACCEPTANCE_ACCEPTED_BINDING')
    return {'rc':0,'artifact':card['source_sha'],'base':card['base'],'test_sha':e['test_sha'],'receipt_sha':receipt_sha,'kernel_chain_sha':digest(e['events']),'kernel_chain_tail':previous,'dispatch_result_sha':digest(stored),'verifier_role':'verifier','verification':'INDEPENDENT_PROCESS_RECOMPUTATION'}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--card',required=True);p.add_argument('--result',required=True);a=p.parse_args()
    try:print(canonical({'ok':True,'acceptance':verify(a.root,json.loads(Path(a.card).read_bytes()),json.loads(Path(a.result).read_bytes()))}))
    except (GateDenied,ValueError,KeyError,TypeError) as e:print(canonical({'ok':False,'code':getattr(e,'code',str(e))}));raise SystemExit(2)
