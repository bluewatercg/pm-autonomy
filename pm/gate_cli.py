"""新进程 gate 入口，默认只检查已登记的独立候选任务。"""
import argparse,json,sys
from pathlib import Path
from roles_gate import Governance,GateDenied,canonical
p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--card',required=True);p.add_argument('--phase',choices=['approve','execute','close'],required=True);args=p.parse_args()
try:
 root=Path(args.root).resolve();card=json.loads(Path(args.card).read_bytes())
 result=Governance(root).check(card,args.phase)
 print(canonical({'ok':True,'result':result,'process':__import__('os').getpid()}))
except GateDenied as exc:
 print(canonical({'ok':False,'code':exc.code,'process':__import__('os').getpid()}));sys.exit(2)
