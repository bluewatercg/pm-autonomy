"""只操作显式运行目录的需求池；会话文件由持有人私下提供，不接受自报角色。"""
import argparse
import json
import sys
from pathlib import Path
from approval_api import Denied

def main():
    parser = argparse.ArgumentParser(description='需求池：read/list/add/note/update/state/defer/convert/archive；不自动审批、执行或提醒')
    parser.add_argument('--root', required=True, help='已初始化的私有运行目录')
    parser.add_argument('--session-file', required=True, help='私有 JSON 文件：sid 与 csrf；不要提交')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list', help='查询全部需求及可信依赖')
    p = sub.add_parser('read', help='读取单条需求和完整审计历史'); p.add_argument('requirement_id')
    for name in ('add', 'note', 'update', 'state', 'defer', 'convert'):
        p = sub.add_parser(name, help='提交完整 JSON 载荷，需 request_id 与 expected_version')
        p.add_argument('--payload', required=True, help='私有 JSON 载荷文件')
    sub.add_parser('archive', help='重建每需求独立 JSON 投影，仅写 runtime/private/requirement-archives')
    args = parser.parse_args()
    root = Path(args.root).resolve()
    if not (root/'private/bridge-key.bin').is_file(): parser.error('需要已初始化的运行目录')
    sys.path.insert(0, str(root))
    import remote_store
    remote_store.R = root
    session = json.loads(Path(args.session_file).read_bytes())
    if set(session) != {'sid', 'csrf'}: raise Denied('SESSION_FILE_INVALID', 400)
    if args.command in ('read', 'list', 'archive'):
        result = remote_store.call('requirements', [session['sid'], getattr(args, 'requirement_id', None)])
        if args.command == 'archive' and result['ok']:
            folder = root/'private/requirement-archives'
            if folder.is_symlink(): raise Denied('ARCHIVE_PATH_DENIED')
            folder.mkdir(mode=0o700, exist_ok=True)
            import bridge
            for item in result['result']['requirements']:
                target = folder/(item['requirement_id'] + '.json')
                if target.is_symlink(): raise Denied('ARCHIVE_PATH_DENIED')
                bridge.save(target, item)
            result = {'ok': True, 'result': {'archives': len(result['result']['requirements']), 'projection_only': True}}
    else:
        data = json.loads(Path(args.payload).read_bytes())
        if not isinstance(data, dict) or data.get('operation') != args.command:
            raise Denied('CLI_OPERATION_MISMATCH', 400)
        result = remote_store.call('requirement_write', [session['sid'], session['csrf'], data])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ok'] else 2

if __name__ == '__main__':
    try: raise SystemExit(main())
    except (Denied, ValueError, OSError) as exc:
        print(json.dumps({'ok': False, 'code': getattr(exc, 'code', 'CLI_INPUT_INVALID')}, ensure_ascii=False))
        raise SystemExit(2)
