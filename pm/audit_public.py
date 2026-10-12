"""只报告命中文件计数，不打印疑似秘密值；同时扫描完整新 Git 历史。"""
import argparse,ast,hashlib,importlib.util,json,re,subprocess,sys
from pathlib import Path
PATTERNS={
 'private_key':r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----',
 'token':r'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})',
 'credential_assignment':r'(?im)(?:api[_-]?key|password|secret|access[_-]?token|hmac[_-]?key)\s*[:=]\s*[\"\x27][^\"\x27\n]{8,}[\"\x27]',
 'ipv4':r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])',
 'internal_path':r'(?:[A-Za-z]:[/\\](?:Users|Project)|(?<![\w])/(?:home/[^/\s]+|root|data[0-9]+)/)',
 'email':r'[A-Za-z0-9_.+-]+@[A-Za-z0-9_.-]+\.[A-Za-z]{2,}',
}
def classify(entries):
    return {name:sum(bool(re.search(pattern,data.decode(errors='replace'))) for data in entries.values()) for name,pattern in PATTERNS.items()}
def main():
    p=argparse.ArgumentParser();p.add_argument('--root',default=str(Path(__file__).resolve().parent.parent));p.add_argument('--report',required=True);a=p.parse_args();root=Path(a.root).resolve()
    files={str(f.relative_to(root)).replace('\\','/'):f.read_bytes() for f in root.rglob('*') if f.is_file() and '.git' not in f.relative_to(root).parts}
    allowed=set(json.loads((root/'PUBLIC_FILES.json').read_bytes())['files']);assert set(files)==allowed,'PUBLIC_TREE_WHITELIST_MISMATCH'
    imports=[]
    for name,data in files.items():
        if not name.endswith('.py'):continue
        for node in ast.walk(ast.parse(data.decode(),filename=name)):
            modules=[v.name for v in node.names] if isinstance(node,ast.Import) else [node.module] if isinstance(node,ast.ImportFrom) and node.module else []
            for module in modules:
                first=module.split('.')[0]
                if not (root/'pm'/(first+'.py')).is_file() and importlib.util.find_spec(first) is None:imports.append(name)
    assert not imports,'PRIVATE_IMPORT_NOT_RESOLVED'
    history={};tracked=[]
    if (root/'.git').exists():
        def git(*args):return subprocess.check_output(['git','-C',str(root),*args])
        for commit in git('rev-list','--all').decode().splitlines():
            names=git('ls-tree','-r','--name-only',commit).decode().splitlines()
            historical_allowed=set(json.loads(git('show',commit+':PUBLIC_FILES.json'))['files'])
            assert set(names)==historical_allowed,'HISTORY_TREE_WHITELIST_MISMATCH'
            for name in names:history[commit+':'+name]=git('show',commit+':'+name)
            history[commit+':metadata']=git('show','-s','--format=fuller',commit)
        tracked=git('ls-files').decode().splitlines();assert set(tracked)==allowed,'INDEX_WHITELIST_MISMATCH'
    result={'public_file_count':len(files),'whitelist_match':True,'working_tree_counts':classify(files),'history_entry_count':len(history),'history_counts':classify(history),'private_imports':0,'hashes':{name:hashlib.sha256(data).hexdigest() for name,data in files.items()}}
    Path(a.report).write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='hashes'}))
if __name__=='__main__':main()
