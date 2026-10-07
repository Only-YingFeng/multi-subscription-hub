"""Transactional recovery using temporary files and fully mocked core operations."""
import copy
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml
from test_support import backend_paths, load_backend, report


def run_case(m, root, operation, failure):
    work = root / (operation + '-' + failure)
    with backend_paths(m, work) as (data, private, share):
        originals = private / 'original-scripts'
        originals.mkdir()
        script = data / 'profiles' / 'synthetic.js'
        original = b'function main(config, profileName) { return config; }\r\n'
        enhanced_script = b'// CZO MANAGED V1\r\n// synthetic wrapper\r\n'
        entry = {'id': '1' * 64, 'nodeName': 'synthetic-node', 'active': True, 'port': 13001}
        base = {'mode': 'rule', 'proxies': [],
                'proxy-groups': [{'name': 'MAIN', 'type': 'select', 'proxies': ['DIRECT']}],
                'rules': ['MATCH,DIRECT'], 'tun': {'enable': False}, 'dns': {'enable': False}}
        enhanced = copy.deepcopy(base)
        enhanced['listeners'] = [{'name': m.NS + 'in.' + '1' * 20, 'type': 'socks', 'listen': '127.0.0.1', 'port': 13001}]
        enhanced['proxy-groups'].append({'name': m.NS + 'bind.' + '1' * 20, 'type': 'select', 'proxies': ['REJECT']})
        enhanced['sub-rules'] = {m.NS + 'dead': ['MATCH,REJECT']}
        initial = enhanced if operation == 'rollback' else base
        script.write_bytes(enhanced_script if operation == 'rollback' else original)
        runtime = data / 'clash-verge.yaml'
        runtime.write_text(yaml.safe_dump(initial, sort_keys=False), encoding='utf8')
        original_path = originals / 'synthetic.js'
        original_path.write_bytes(original)
        state = {'scripts': {str(script): {'hash': m.digest(enhanced_script), 'original': str(original_path)}}} if operation == 'rollback' else {'scripts': {}}
        m.write_json(private / 'installed.json', state)
        m.write_json(private / 'registry.json', {'entries': [entry], 'policies': {}})
        core = {'config': copy.deepcopy(initial), 'reloads': 0}
        failures = {'reload': False, 'ledger': False}
        real_write = m.write_json

        def fake_reload(path, controller_config):
            core['reloads'] += 1
            if failure == 'reload' and not failures['reload']:
                failures['reload'] = True
                raise RuntimeError('Synthetic reload failure')
            if failure == 'recovery-core' and core['reloads'] > 1:
                raise RuntimeError('Synthetic recovery reload failure')
            core['config'] = m.read_yaml(Path(path))
            return 204, None

        def fake_validate(config, stage):
            path = stage / 'candidate.yaml'
            path.write_text(yaml.safe_dump(config, sort_keys=False), encoding='utf8')
            return path

        def failing_write(path, value):
            if Path(path) == share / '验证摘要.json' and failure == 'summary':
                raise PermissionError('SYNTHETIC_SUMMARY_PRIVATE_DETAIL')
            if Path(path) == private / 'installed.json' and failure in ('ledger', 'recovery-core') and not failures['ledger']:
                failures['ledger'] = True
                raise OSError('Synthetic ledger failure')
            return real_write(path, value)

        def owners():
            if failure == 'occupied-port':
                return {13001: [('127.0.0.1:13001', 888)]}
            return {13001: [('127.0.0.1:13001', 999)]} if 'listeners' in core['config'] else {}

        def fake_api(method, path, **kwargs):
            if method == 'GET' and path == '/configs':
                return 200, {'mode': 'direct' if failure == 'mode-change' else 'rule'}
            raise AssertionError('Unexpected mock core API access')

        selector_calls = {'count': 0}

        def proxies(config):
            selector_calls['count'] += 1
            changed = failure == 'selector-change' and selector_calls['count'] > 1
            return {'MAIN': {'type': 'Selector', 'now': 'REJECT' if changed else 'DIRECT'}}

        prepared = None
        if operation == 'install':
            stage = private / 'staging'
            stage.mkdir()
            (stage / 'installed-extension.js').write_bytes(enhanced_script)
            m.write_json(stage / 'registry.json', {'entries': [entry], 'policies': {}})
            candidate = stage / 'candidate.yaml'
            candidate.write_text(yaml.safe_dump(enhanced, sort_keys=False), encoding='utf8')
            prepared = {'stage': str(stage), 'script': str(script), 'source': 'synthetic-source',
                        'scriptBeforeHash': m.digest(script.read_bytes()),
                        'runtimeBeforeHash': m.digest(runtime.read_bytes()),
                        'config': str(candidate), 'changes': {}, 'liveControl': {'mode': 'rule'}}
        if failure == 'external-edit':
            script.write_bytes(b'USER_EDIT_MUST_REMAIN')
        if failure == 'runtime-edit':
            runtime.write_text(yaml.safe_dump({**initial, 'mixed-port': 7890}, sort_keys=False), encoding='utf8')
        snapshot = {'script': script.read_bytes(), 'runtime': runtime.read_bytes(),
                    'state': copy.deepcopy(state), 'registry': (private / 'registry.json').read_bytes()}
        with patch.object(m, 'reload_file', side_effect=fake_reload), \
             patch.object(m, 'core_validate', side_effect=fake_validate), \
             patch.object(m, 'write_json', side_effect=failing_write), \
             patch.object(m, 'api', side_effect=fake_api), \
             patch.object(m, 'current', return_value=({}, {}, script, 'synthetic-source')), \
             patch.object(m, 'core_process_ids', return_value={999}), \
             patch.object(m, 'listener_owners', side_effect=owners), \
             patch.object(m, 'proxy_snapshot', side_effect=proxies), \
             patch.object(m.time, 'sleep', return_value=None):
            raised = False
            result = None
            try:
                if operation == 'rollback':
                    result = m.rollback()
                else:
                    result = m.install(prepared)
            except (RuntimeError, OSError):
                raised = True
        ledger = json.loads((private / 'installed.json').read_text(encoding='utf8'))
        if failure in ('none', 'summary'):
            assert not raised
            expected = base if operation == 'rollback' else enhanced
            assert script.read_bytes() == (original if operation == 'rollback' else enhanced_script)
            assert m.read_yaml(runtime) == expected and core['config'] == expected
            assert ledger['scripts'] == {} if operation == 'rollback' else str(script) in ledger['scripts']
            recovery = json.loads((private / 'recovery.json').read_text(encoding='utf8'))
            assert recovery['phase'] == ('rolled-back' if operation == 'rollback' else 'complete')
            if failure == 'summary':
                assert result['status'] == 'installed-and-live' and result['listeners'] == 1
                assert result['summaryWarning'] == '已写回并生效，但验证摘要导出失败。'
                assert 'SYNTHETIC_SUMMARY_PRIVATE_DETAIL' not in json.dumps(result)
                assert core['reloads'] == 1 and not (share / '验证摘要.json').exists()
                assert (Path(result['backup']) / 'manifest.json').is_file()
            else:
                assert 'summaryWarning' not in result
        elif failure == 'recovery-core':
            assert raised and core['reloads'] >= 2
            recovery = json.loads((private / 'recovery.json').read_text(encoding='utf8'))
            assert recovery['phase'] == 'recovery-required' and recovery['failedStages']
            assert Path(recovery['backup']).is_dir()
            assert (Path(recovery['backup']) / 'manifest.json').is_file()
            assert script.read_bytes() == snapshot['script']
        else:
            assert raised
            assert script.read_bytes() == snapshot['script']
            assert runtime.read_bytes() == snapshot['runtime']
            assert ledger == snapshot['state'] and core['config'] == initial
            if failure in ('external-edit', 'runtime-edit', 'occupied-port', 'mode-change'):
                assert core['reloads'] == 0
                assert not (private / 'recovery.json').exists()
            else:
                assert core['reloads'] >= 2
                assert json.loads((private / 'recovery.json').read_text(encoding='utf8'))['phase'] == 'restored'
        assert (private / 'registry.json').read_bytes() == snapshot['registry']
        return operation + ':' + failure


def main():
    with TemporaryDirectory(prefix='czo-transaction-tests-') as temporary:
        root = Path(temporary)
        m = load_backend(root)
        results = [run_case(m, root, operation, failure) for operation, failure in [
            ('rollback', 'none'), ('rollback', 'reload'), ('rollback', 'ledger'),
            ('rollback', 'external-edit'), ('rollback', 'recovery-core'),
            ('install', 'none'), ('install', 'reload'), ('install', 'ledger'),
            ('install', 'external-edit'), ('install', 'runtime-edit'),
            ('install', 'occupied-port'), ('install', 'mode-change'),
            ('install', 'selector-change'), ('install', 'recovery-core'),
            ('install', 'summary')]]
        with patch.dict(os.environ, {'SystemRoot': str(root / 'synthetic-system-root'),
                                     'PSModulePath': 'SYNTHETIC_POWERSHELL_7_MODULES'}), \
             patch.object(m.subprocess, 'run', return_value=None) as subprocess_run:
            expected = str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/Modules')
            m.run(['powershell.exe', '-NoProfile', '-Command', 'SYNTHETIC_NO_EXECUTION'])
            assert subprocess_run.call_args.kwargs['env']['PSModulePath'] == expected
            assert os.environ['PSModulePath'] == 'SYNTHETIC_POWERSHELL_7_MODULES'
            explicit = {'PSModulePath': 'SYNTHETIC_CALLER_MODULES', 'SYNTHETIC_KEEP': 'value'}
            m.run(['powershell.exe', '-NoProfile'], env=explicit)
            child = subprocess_run.call_args.kwargs['env']
            assert child['PSModulePath'] == expected and child['SYNTHETIC_KEEP'] == 'value'
            assert explicit == {'PSModulePath': 'SYNTHETIC_CALLER_MODULES', 'SYNTHETIC_KEEP': 'value'}
        results.append('PowerShell child environment overrides module path without changing global or caller environments')
    report('manager-transaction', results)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('TRANSACTION_TEST_FAILED:' + type(error).__name__)
        sys.exit(1)
