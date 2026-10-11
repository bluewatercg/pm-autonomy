"""独立 architect 进程，真实 OMP 无工具调用；失败或超时留下阻塞证据。"""
import argparse,json,os,shlex,subprocess,time
from pathlib import Path
import next_kernel_v3 as kernel
from roles_gate import load_roles,sha,canonical,write_new,review_prompt

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--request',required=True);args=p.parse_args()
    root=Path(args.root).resolve(); request=json.loads(Path(args.request).read_bytes())
    if not (root/'private').is_dir(): raise ValueError('RUNTIME_NOT_INITIALIZED')
    rid=request['request'];assert rid.replace('-','').isalnum()
    folder=root/'private/reviews'/rid;folder.mkdir(mode=0o700,parents=True)
    roles=load_roles(root/'roles.json');actor=roles['roles']['architect']
    prompt=review_prompt(request['binding'],request['material'])
    write_new(folder/'invocation.json',{'request':rid,'role':'architect','model':actor['resolved_model'],'effort':actor['effort'],'pid':os.getpid(),'prompt_sha':sha(prompt.encode()),'provider_attempts':1,'roles_sha':sha((root/'roles.json').read_bytes()),'start':time.time()})
    with (folder/'prompt.txt').open('xb') as f:os.chmod(f.name,0o600);f.write(prompt.encode())
    from model_adapter import command
    argv,env=command(root,folder,actor['resolved_model'],actor['effort'],'@'+str(folder/'prompt.txt'))
    with (folder/'raw.ndjson').open('xb') as out,(folder/'stderr.log').open('xb') as err:
        os.chmod(out.name,0o600);os.chmod(err.name,0o600)
        try: proc=subprocess.run(argv,stdout=out,stderr=err,env=env,timeout=270);rc=proc.returncode
        except subprocess.TimeoutExpired: rc=124
        out.flush();os.fsync(out.fileno());err.flush();os.fsync(err.fileno())
    write_new(folder/'exit.json',{'rc':rc,'end':time.time()})
    result={'binding':request['binding'],'verdict':'TIMEOUT' if rc==124 else 'FAIL','findings':[],'rationale':'模型失败，禁止自动批准','pid':os.getpid(),'role':'architect','model':actor['resolved_model'],'effort':actor['effort'],'producer':'REAL_MODEL_SUBPROCESS','review_is_acceptance':False,'raw_sha':sha((folder/'raw.ndjson').read_bytes()),'invocation_sha':sha((folder/'invocation.json').read_bytes()),'exit_sha':sha((folder/'exit.json').read_bytes())}
    if rc==0:
        try:
            response=kernel.parse_response((folder/'raw.ndjson').read_bytes())
            if set(response)!={'verdict','findings','rationale'} or response['verdict'] not in ('PASS','FAIL') or not isinstance(response['findings'],list) or any(not isinstance(v,str) for v in response['findings']) or not isinstance(response['rationale'],str):raise ValueError('MODEL_REVIEW_SCHEMA')
            result.update(response)
            write_new(folder/'response.json',response)
            result['response_sha']=sha((folder/'response.json').read_bytes())
        except Exception as exc: result['rationale']='MODEL_REVIEW_SCHEMA:'+type(exc).__name__
    path=root/'reviews'/(rid+'.json');write_new(path,result)
    print(canonical({'path':str(path.relative_to(root)),'sha':sha(path.read_bytes()),'verdict':result['verdict'],'pid':os.getpid(),'model':result['model'],'rc':rc}))
    return 0 if result['verdict']=='PASS' else 2
if __name__=='__main__': raise SystemExit(main())
