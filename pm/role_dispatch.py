"""按绑定角色配置派发独立只读模型进程；写执行只能经过审批 broker。"""
import argparse,json,secrets,subprocess,sys
from pathlib import Path
from roles_gate import load_roles,canonical
import next_kernel_v3 as kernel

def main():
    p=argparse.ArgumentParser();p.add_argument('--role',choices=['pm','explorer','researcher','worker','architect','verifier'],required=True);p.add_argument('--prompt-file',required=True);a=p.parse_args()
    root=Path(__file__).resolve().parent
    try:
        actor=load_roles(root/'roles.json')['roles'][a.role]
        if a.role=='architect':raise ValueError('BOUND_ARCHITECT_USE_REVIEW_TASK')
        prompt=Path(a.prompt_file).read_text()
        request='role-'+secrets.token_hex(12);kernel.MODEL=actor['resolved_model'];kernel.MODEL_EFFORT=actor['effort']
        value=kernel.model_call(root,request,'角色职责：'+a.role+'；只能输出建议，不得批准、写文件、执行工具或宣称验收。以下为待处理材料：\n'+prompt,role=a.role)
        print(canonical({'request':request,'role':a.role,'response':value,'authority':'ADVISORY_ONLY','activation':'NOT_IN_FORCE'}));return 0
    except Exception as exc:
        print(canonical({'ok':False,'code':getattr(exc,'code',str(exc))}));return 2
if __name__=='__main__':raise SystemExit(main())
