"""Exercise real preparation against synthetic subscriptions and mock core APIs."""
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml
from test_support import backend_paths, load_backend, report


def main():
    results = []
    with TemporaryDirectory(prefix='czo-mapping-tests-') as temporary:
        root = Path(temporary)
        m = load_backend(root)
        with backend_paths(m, root) as (data, private, share):
            script = data / 'profiles' / 'script.js'
            script.write_text('function main(config, profileName) { return config; }', encoding='utf8')
            source = {'uid': 'synthetic-subscription-A'}
            names = ['synthetic-A', 'synthetic-B']
            endpoint = {'server': '192.0.2.10', 'port': 9}

            def setup(extra=None):
                profiles = {'current': source['uid'], 'items': [
                    {'uid': source['uid'], 'type': 'remote', 'url': 'https://example.invalid/synthetic', 'option': {'script': 'synthetic-script'}},
                    {'uid': 'synthetic-script', 'file': 'script.js', 'type': 'script'}]}
                (data / 'profiles.yaml').write_text(yaml.safe_dump(profiles), encoding='utf8')
                config = {'mode': 'rule', 'proxies': [{'name': n, 'type': 'socks5', **endpoint} for n in names],
                          'proxy-groups': [{'name': 'MAIN', 'type': 'select', 'proxies': list(names)}],
                          'rules': ['DOMAIN,localhost,DIRECT', 'MATCH,MAIN']}
                config.update(extra or {})
                (data / 'clash-verge.yaml').write_text(yaml.safe_dump(config), encoding='utf8')

            calls = []

            def api(method, path, **kwargs):
                calls.append((method, path))
                assert method == 'GET', 'Preparation may only inspect the mock API'
                if path == '/configs':
                    return 200, {'mode': 'rule'}
                if path == '/proxies':
                    values = {n: {'type': 'Socks5'} for n in names}
                    values['MAIN'] = {'type': 'Selector', 'now': names[0]}
                    return 200, {'proxies': values}
                raise AssertionError('Unexpected mock API call')

            real_run = subprocess.run

            def run(command, **kwargs):
                if command == [str(m.CORE), '-v']:
                    return subprocess.CompletedProcess(command, 0, b'Mihomo Meta v1.19.32 synthetic', b'')
                if Path(command[0]).name.lower() == 'powershell.exe':
                    return subprocess.CompletedProcess(command, 0, b'2.5.7\r\n', b'')
                assert command[0] == str(m.NODE), 'Only local JavaScript may execute'
                return real_run(command, **kwargs)

            def validate(config, stage):
                path = stage / 'candidate.yaml'
                path.write_text(yaml.safe_dump(config), encoding='utf8')
                return path

            with patch.object(m, 'api', side_effect=api), \
                 patch.object(m, 'excluded_ports', return_value=set()), \
                 patch.object(m, 'free_port', return_value=True), \
                 patch.object(m, 'core_validate', side_effect=validate), \
                 patch.object(m.subprocess, 'run', side_effect=run), \
                 patch.object(m, 'reload_file', side_effect=AssertionError('Preparation cannot reload')):
                def generate():
                    setup()
                    before = {(data / name): (data / name).read_bytes() for name in ('profiles.yaml', 'clash-verge.yaml')}
                    before[script] = script.read_bytes()
                    prepared = m.prepare()
                    assert all(path.read_bytes() == content for path, content in before.items())
                    registry = json.loads((private / 'registry.json').read_text(encoding='utf8'))['entries']
                    return prepared, registry

                initial_prepared, initial = generate()
                original = {e['nodeName']: e['port'] for e in initial}
                initial_config = m.read_yaml(Path(initial_prepared['config']))
                assert all(next(x for x in initial_config['listeners'] if x['port'] == e['port'])['rule'] == m.NS + 'rules' for e in initial)
                names[:] = ['synthetic-B', 'synthetic-A']
                _, reordered = generate()
                assert {e['nodeName']: e['port'] for e in reordered} == original
                results.append('reordering preserves ports and leaves installation unchanged')

                names[:] = ['synthetic-B', 'synthetic-C', 'synthetic-A']
                _, added = generate()
                assert all(next(e for e in added if e['nodeName'] == name)['port'] == port for name, port in original.items())
                assert next(e for e in added if e['nodeName'] == 'synthetic-C')['port'] not in original.values()
                results.append('new node cannot recycle a reserved identity port')

                names[:] = ['synthetic-C', 'synthetic-B']
                prepared, removed = generate()
                a = next(e for e in removed if e['nodeName'] == 'synthetic-A')
                assert not a['active'] and a['port'] == original['synthetic-A']
                backup = json.loads((share / m.BAK).read_text(encoding='utf8'))
                assert backup['+synthetic-A']['fallbackProxy']['port'] == original['synthetic-A']
                candidate = m.read_yaml(Path(prepared['config']))
                assert next(x for x in candidate['listeners'] if x['port'] == a['port'])['rule'] == m.NS + 'dead'
                results.append('removed node keeps its old profile and reject listener')

                names[:] = ['synthetic-C', 'synthetic-B-renamed']
                _, renamed = generate()
                assert not next(e for e in renamed if e['nodeName'] == 'synthetic-B')['active']
                assert next(e for e in renamed if e['nodeName'] == 'synthetic-B-renamed')['port'] not in {e['port'] for e in removed}
                results.append('renamed node retires old identity without recycling')

                prior_ports = {e['port'] for e in renamed}
                source['uid'] = 'synthetic-subscription-B'
                names[:] = ['synthetic-C']
                _, switched = generate()
                active = [e for e in switched if e['active']]
                assert len(active) == 1 and active[0]['port'] not in prior_ports
                assert all(not e['active'] for e in switched if e['port'] in prior_ports)
                results.append('same name in another subscription cannot inherit a port')

                first_backup = (share / m.BAK).read_bytes()
                _, repeated = generate()
                assert first_backup == (share / m.BAK).read_bytes() and switched == repeated
                results.append('repeated generation is deterministic')

                stable_port = active[0]['port']
                endpoint.update(server='192.0.2.20', port=10)
                _, changed = generate()
                assert next(e for e in changed if e['active'])['port'] == stable_port
                assert next(e for e in changed if e['active'])['endpointTag'] != active[0]['endpointTag']
                results.append('endpoint update changes its tag but preserves the identity port')

                for extra, reserved in [({}, {stable_port}), ({'mixed-port': stable_port}, set())]:
                    setup(extra)
                    before = (private / 'registry.json').read_bytes()
                    with patch.object(m, 'excluded_ports', return_value=reserved):
                        try:
                            m.prepare()
                        except RuntimeError:
                            pass
                        else:
                            raise AssertionError('Conflicting port must block generation')
                    assert (private / 'registry.json').read_bytes() == before
                results.append('reserved and unmanaged port conflicts block without remapping')
                assert all(method == 'GET' for method, _ in calls)
    report('mapping-update', results)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('MAPPING_TEST_FAILED:' + type(error).__name__)
        sys.exit(1)
