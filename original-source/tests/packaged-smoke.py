"""Real EXE, read-only controller GETs and generation only; no install/reload."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import portable
import manager as m


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--exe', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    context = portable.detect()
    if not context['ok']:
        raise RuntimeError('Real environment probe did not pass')
    _, item, script, _ = m.current()
    paths = [m.DATA / name for name in ('clash-verge.yaml', 'profiles.yaml', 'verge.yaml', 'config.yaml', 'dns_config.yaml')]
    paths += [script, m.DATA / 'profiles' / item['file']]
    paths += list((m.DATA / 'profiles').glob('*.js')) + list((m.DATA / 'profiles').glob('*.yaml'))
    paths = list(dict.fromkeys(p for p in paths if p.is_file()))
    before = {str(p): m.digest(p.read_bytes()) for p in paths}
    selectors = {name: value.get('now') for name, value in m.proxy_snapshot().items()
                 if value.get('type') == 'Selector' and not name.startswith(m.NS)}
    pids = m.core_process_ids()
    env = dict(os.environ)
    system_root = os.environ['SystemRoot']
    env['PATH'] = os.pathsep.join([str(Path(system_root) / 'System32'), system_root])
    commands = [
        ['--self-test-json', str(output / 'runtime.json')],
        ['--detect-json', str(output / 'detect.json')],
        ['--generate', '--output', str(output / 'exports'), '--result-json', str(output / 'generated.json')],
    ]
    for command in commands:
        completed = subprocess.run([args.exe, *command], env=env, capture_output=True, timeout=90,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        if completed.returncode:
            raise RuntimeError('Packaged command returned failure')
    runtime = json.loads((output / 'runtime.json').read_text(encoding='utf8'))
    detect = json.loads((output / 'detect.json').read_text(encoding='utf8'))
    generated = json.loads((output / 'generated.json').read_text(encoding='utf8'))
    if not runtime.get('ok') or not detect.get('ok') or 'generation' not in generated:
        raise RuntimeError('Packaged diagnostic did not pass')
    generation = generated['generation']
    backup = json.loads(Path(generation['bakPath']).read_text(encoding='utf8'))
    profiles = [value for key, value in backup.items() if key.startswith('+')]
    after = {str(p): m.digest(p.read_bytes()) for p in paths}
    after_selectors = {name: value.get('now') for name, value in m.proxy_snapshot().items()
                       if value.get('type') == 'Selector' and not name.startswith(m.NS)}
    checks = {
        'packagedRuntimeSelfContained': runtime['bundled'] and runtime['resourcesReady'],
        'packagedIconResourcesReady': runtime.get('iconReady') is True,
        'nativeQtAndIconFontReady': runtime.get('qtUiReady') is True,
        'probePassed': detect['ok'], 'generationPassed': generation['active'] == context['nodeCount'],
        'productionFilesUnchanged': before == after,
        'coreNotRestarted': pids == m.core_process_ids(), 'mainSelectorsUnchanged': selectors == after_selectors,
        'existingMappingsRetained': sorted(e['port'] for e in generation['entries']) == list(range(20000, 20069)),
        'localSocksOnly': all(p['profileType'] == 'FixedProfile' and p['fallbackProxy']['scheme'] == 'socks5'
                              and p['fallbackProxy']['host'] == '127.0.0.1'
                              and set(p['fallbackProxy']) == {'host', 'scheme', 'port'} for p in profiles),
        'noUnneededSynchronization': not generation['needsApply'],
    }
    report = {'checks': checks, 'passed': sum(checks.values()), 'total': len(checks),
              'activeNodes': generation['active'], 'verifiedProductionFileCount': len(paths),
              'controllerWrites': 0, 'browserStorageAccess': False}
    (output / 'smoke-summary.json').write_text(json.dumps(report, indent=2), encoding='utf8')
    print(json.dumps(report))
    if not all(checks.values()):
        raise RuntimeError('One or more packaged checks failed')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'ok': False, 'errorType': type(error).__name__}))
        sys.exit(1)
