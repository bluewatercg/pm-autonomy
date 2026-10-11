"""仅 TEST- 合成夹具的新进程 reviewer；不是模型调用。"""
import argparse,json,os
from pathlib import Path
from roles_gate import write_new
p=argparse.ArgumentParser();p.add_argument('--request',required=True);p.add_argument('--output',required=True);args=p.parse_args()
v=json.loads(Path(args.request).read_bytes())
assert v['binding']['task'].startswith('TEST-')
write_new(args.output,{'binding':v['binding'],'verdict':v['verdict'],'pid':os.getpid(),'role':'architect','producer':'SYNTHETIC_TEST_SUBPROCESS','model':None,'review_is_acceptance':False,'rationale':'合成协议夹具，不是模型质量验证'})
