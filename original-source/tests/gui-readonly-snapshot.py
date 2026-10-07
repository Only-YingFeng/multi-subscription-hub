"""Check that real GUI probe/generation preserves production configuration."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import portable
import manager as m


def snapshot():
    context = portable.detect()
    if not context.get('ok'):
        raise portable.PublicError('本机环境未通过，无法记录界面测试基线。')
    paths = [m.DATA / name for name in ('clash-verge.yaml', 'profiles.yaml', 'verge.yaml', 'config.yaml', 'dns_config.yaml')]
    paths += list((m.DATA / 'profiles').glob('*.js')) + list((m.DATA / 'profiles').glob('*.yaml'))
    paths = list(dict.fromkeys(path for path in paths if path.is_file()))
    selectors = {name: item.get('now') for name, item in m.proxy_snapshot().items() if item.get('type') == 'Selector'}
    return {'files': {str(path): m.digest(path.read_bytes()) for path in paths},
            'selectorsDigest': m.digest(json.dumps(selectors, sort_keys=True).encode()),
            'controlsDigest': m.digest(json.dumps(m.api('GET', '/configs')[1], sort_keys=True).encode()),
            'corePids': sorted(m.core_process_ids()), 'nodeCount': context['nodeCount']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--before', required=True)
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--report')
    args = parser.parse_args()
    current = snapshot()
    baseline = Path(args.before)
    if not args.verify:
        if baseline.exists():
            raise portable.PublicError('界面测试基线已存在，已保留原文件。')
        baseline.write_text(json.dumps(current, indent=2), encoding='utf8')
        print(json.dumps({'baselineSaved': True, 'productionFileCount': len(current['files']), 'nodeCount': current['nodeCount']}))
        return
    before = json.loads(baseline.read_text(encoding='utf8'))
    checks = {key + 'Unchanged': before[key] == current[key] for key in before}
    report = {'checks': checks, 'passed': sum(checks.values()), 'total': len(checks),
              'productionFileCount': len(current['files']), 'nodeCount': current['nodeCount'],
              'guiActions': ['probe-all-nodes', 'generate-backup'], 'productionApplyOrRollback': False}
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2), encoding='utf8')
    print(json.dumps(report))
    if not all(checks.values()):
        raise portable.PublicError('真实界面测试期间生产状态发生变化。')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'ok': False, 'issue': portable.safe_error(error)}, ensure_ascii=True))
        sys.exit(1)
