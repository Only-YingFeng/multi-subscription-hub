"""Explicit low-traffic node checks and generation; never apply/reload Clash."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import portable
import manager as m


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    context = portable.detect()
    if not context.get('ok'):
        raise portable.PublicError('本机环境探测未通过，未开始连通性测试。')
    _, subscription, script, _ = m.current()
    paths = [m.DATA / name for name in ('clash-verge.yaml', 'profiles.yaml', 'verge.yaml', 'config.yaml', 'dns_config.yaml')]
    paths += [script, m.DATA / 'profiles' / subscription['file']]
    paths += list((m.DATA / 'profiles').glob('*.js')) + list((m.DATA / 'profiles').glob('*.yaml'))
    paths = list(dict.fromkeys(path for path in paths if path.is_file()))
    before = {str(path): m.digest(path.read_bytes()) for path in paths}
    selectors = {name: value.get('now') for name, value in m.proxy_snapshot().items() if value.get('type') == 'Selector'}
    pids = m.core_process_ids()
    controls = m.api('GET', '/configs')[1]
    api_original = m.api
    calls = Counter()

    def readonly_api(method, path, *arguments, **keywords):
        if method != 'GET':
            raise portable.PublicError('测试拒绝了控制接口写入。')
        calls['GET'] += 1
        if '/delay?' in path:
            calls['namedNodeDelayTests'] += 1
        return api_original(method, path, *arguments, **keywords)

    progress = []
    try:
        m.api = readonly_api
        checked = portable.check_nodes(context, on_progress=progress.append)
        generated = portable.generate(context, output_directory=output / 'exports')
    finally:
        m.api = api_original
    after = {str(path): m.digest(path.read_bytes()) for path in paths}
    after_selectors = {name: value.get('now') for name, value in m.proxy_snapshot().items() if value.get('type') == 'Selector'}
    count = Counter(item['status'] for item in checked['checks'])
    checks = {
        'everyRealNodeTestedExactlyOnce': calls['namedNodeDelayTests'] == checked['total'] == context['nodeCount'],
        'everyProgressResultReceived': len(progress) == checked['total'],
        'allStatusesExplicit': all(item['status'] in ('reachable', 'timeout', 'failed') for item in checked['checks']),
        'allNodesRetainedInBackup': generated['active'] == checked['total'],
        'existingPortsRetained': sorted(item['port'] for item in generated['entries']) == list(range(20000, 20069)),
        'productionFilesUnchanged': before == after,
        'coreNotRestarted': pids == m.core_process_ids(),
        'selectorsUnchanged': selectors == after_selectors,
        'liveControlsUnchanged': controls == m.api('GET', '/configs')[1],
        'noUnneededWriteback': generated['needsApply'] is False,
        'persistentExtensionPathReturned': Path(generated['extensionPath']) == script,
    }
    report = {'checks': checks, 'passed': sum(checks.values()), 'total': len(checks),
              'activeNodes': checked['total'], 'reachable': count['reachable'], 'timeout': count['timeout'],
              'failed': count['failed'], 'checkedAt': checked['checkedAt'], 'controllerWrites': 0,
              'namedNodeDelayTests': calls['namedNodeDelayTests'], 'verifiedProductionFileCount': len(paths),
              'browserStorageAccess': False, 'bakPath': generated['bakPath'],
              'nodeChecks': checked['checks']}
    (output / 'real-node-check-summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({key: value for key, value in report.items() if key != 'nodeChecks'}, ensure_ascii=True))
    if not all(checks.values()):
        raise portable.PublicError('连通性测试或配置保留校验未通过。')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'ok': False, 'issue': portable.safe_error(error)}, ensure_ascii=True))
        sys.exit(1)
