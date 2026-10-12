"""只初始化全新独立运行目录，不接触既有项目或自动批准。"""
import argparse,json,os,secrets,shutil,subprocess,time
from pathlib import Path
import bridge

SOURCE=Path(__file__).resolve().parent
PROJECT=SOURCE.parent

def initialize(destination, fixture=False):
    root=Path(destination).resolve()
    if root.exists():raise ValueError('DESTINATION_EXISTS_NO_OVERWRITE')
    root.mkdir(mode=0o700)
    for path in SOURCE.iterdir():
        if path.is_file() and path.suffix in ('.py','.js','.mjs','.html'):
            shutil.copyfile(path,root/path.name)
    private=root/'private';private.mkdir(mode=0o700)
    key=private/'bridge-key.bin';key.write_bytes(secrets.token_bytes(32));key.chmod(0o600)
    shutil.copyfile(PROJECT/'examples/bootstrap-policy.json',private/'bootstrap-policy.json')
    roles=json.loads((PROJECT/'examples/roles.json').read_bytes())
    observation={'models':[],'fixture':fixture,'basis':[]}
    if fixture:
        (root/'FIXTURE_ONLY').write_text('SYNTHETIC_TEST_BOUNDARY_ONLY; never a real review or approval\n')
        for actor in roles['roles'].values():actor['model']='fixture/model'
        roles['model_policy']['verified_models']=['fixture/model']
        observation['models']=['fixture/model']
    (root/'roles.json').write_text(json.dumps(roles,ensure_ascii=False,indent=2))
    (root/'model-id-observation.json').write_text(json.dumps(observation))
    repo=root/'synthetic-repo';repo.mkdir()
    def git(*args):
        env=dict(os.environ,GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL='/dev/null')
        return subprocess.check_output(['git','-C',str(repo),*args],env=env,stderr=subprocess.PIPE).decode().strip()
    git('init','-b','synthetic-main');git('config','user.name','Synthetic fixture');git('config','user.email','fixture@localhost')
    (repo/'history.py').write_text('def select_records(records):\n    raise NotImplementedError\n')
    git('add','history.py');git('commit','-m','Synthetic baseline')
    base=git('rev-parse','HEAD');git('bundle','create',str(root/'synthetic.bundle'),'--all')
    source='def select_records(records):\n    return [dict(row) for row in sorted(records, key=lambda row: row["id"])]\n'
    (root/'candidate-history.py').write_text(source)
    controls=['roles_gate.py','approval_api.py','remote_store.py','bridge.py','governed_kernel.py','container_task.py','reviewer_process.py','verify_acceptance.py','secured_kernel.py','model_adapter.py','requirements.py']
    proposal={'schema':'approval-proposal/v1','task':'TEST-PENDING-DEMO' if fixture else 'PENDING-DEMO','version':1,'goal':'合成 history.py 按 id 升序返回副本','source':{'request':'独立合成演练，初始化不是批准','relation':'fresh synthetic baseline -> pending candidate','predecessor':'fresh synthetic baseline','kernel_sha':bridge.sha((root/'next_kernel_v3.py').read_bytes()),'secured_kernel_sha':bridge.sha((root/'secured_kernel.py').read_bytes()),'governed_kernel_sha':bridge.sha((root/'governed_kernel.py').read_bytes())},'scope':['history.py'],'base':base,'before_sha':bridge.sha((repo/'history.py').read_bytes()),'source_sha':bridge.sha(source.encode()),'source_code':source,'expiry':time.time()+86400,'actions':['edit'],'change':'normal','environment':'dev / isolated synthetic Docker','worktree':'/outputs/candidate-next-approval/worktrees/PENDING-DEMO','branch':'candidate/PENDING-DEMO','acceptance':['固定排序、副本、空输入测试','真实 Git base、产物摘要和独立 verifier','架构审查、批准、执行、验收、结案分别检查'],'risks':['能力持有人不是实名或人类身份','同UID/root/Docker管理员属于可信管理边界','容器tmpfs退出后不持久；未知结果禁止重派'],'rollback':'不合并，不部署；保留失败证据','forbidden':['业务目录','生产','网络','宿主挂载'],'remaining':['配置真实模型并登记观测','独立架构审查','能力持有人批准','执行及独立验收','结案审查'],'governance_policy':{'large_plan':True,'long_task':True,'repeat_threshold':2},'control_sources':{n:bridge.sha((root/n).read_bytes()) for n in controls}}
    (root/'pending-proposal.json').write_text(json.dumps(proposal,ensure_ascii=False,indent=2))
    return {'status':'PENDING','activation':'NOT_IN_FORCE','fixture':fixture,'model':'BLOCKED' if not fixture else 'SYNTHETIC_FIXTURE_ONLY'}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--destination',required=True);p.add_argument('--fixture',action='store_true');a=p.parse_args()
    print(json.dumps(initialize(a.destination,a.fixture),ensure_ascii=False))
