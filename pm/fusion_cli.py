"""本地负责人融合控制与恢复；读取私有能力文件，不向输出泄露能力。"""
import argparse
import json
from pathlib import Path
from approval_api import Denied, canonical
import remote_store

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--root',required=True)
    p.add_argument('--session-file',required=True)
    p.add_argument('operation',choices=['status','pause','configure','recover'])
    p.add_argument('--spec')
    p.add_argument('--request-id')
    a=p.parse_args();remote_store.R=Path(a.root).resolve()
    try:
        auth=json.loads(Path(a.session_file).read_bytes())
        if a.operation=='configure' and not a.spec or a.operation=='recover' and not a.request_id:raise ValueError('缺少参数')
        if a.operation=='status': result=remote_store.call('fusion_status',[auth['sid']])
        elif a.operation=='pause': result=remote_store.call('fusion_control',[auth['sid'],auth['csrf'],{'operation':'pause'}])
        elif a.operation=='configure':
            data=json.loads(Path(a.spec).read_bytes());data['operation']='configure'
            result=remote_store.call('fusion_control',[auth['sid'],auth['csrf'],data])
        else:
            result=remote_store.call('recover',[auth['sid'],auth['csrf'],a.request_id])
        print(canonical(result));return 0 if result.get('ok') else 2
    except (Denied,ValueError,KeyError,TypeError,OSError) as exc:
        print(canonical({'ok':False,'code':getattr(exc,'code','FUSION_CLI_INPUT_INVALID')}));return 2

if __name__=='__main__':raise SystemExit(main())
