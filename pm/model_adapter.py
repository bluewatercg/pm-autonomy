"""外部 OMP adapter；配置缺失明确阻塞，不加载任何内置 provider 或代理。"""
import os
from pathlib import Path

class Blocked(ValueError):
    pass

def command(root, folder, model, effort, prompt):
    executable=os.environ.get('PM_MODEL_EXECUTABLE','')
    config=os.environ.get('PM_MODEL_CONFIG','')
    if not model or not executable or not config:
        raise Blocked('BLOCKED_MODEL_ADAPTER_NOT_CONFIGURED')
    if not Path(executable).is_file() or not Path(config).is_file():
        raise Blocked('BLOCKED_MODEL_ADAPTER_PATH_UNAVAILABLE')
    extension=Path(root)/'deny-tools.mjs'
    if not extension.is_file():
        raise Blocked('BLOCKED_TOOL_DENY_EXTENSION_MISSING')
    argv=[executable,'--config',config,'--model',model,'--thinking',effort,'--cwd',str(folder),'--no-session','--no-extensions','--extension',str(extension),'--no-skills','--no-rules','--no-lsp','--no-title','--no-prewalk','--print','--mode','json','--max-time','240','--no-tools',prompt]
    env=dict(os.environ)
    for name in ['OMP_PROFILE','PI_PROFILE','PI_CODING_AGENT_DIR']:
        env.pop(name,None)
    if os.environ.get('PM_MODEL_AGENT_DIR'):
        env['PI_CODING_AGENT_DIR']=os.environ['PM_MODEL_AGENT_DIR']
    return argv,env
