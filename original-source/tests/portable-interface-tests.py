"""Portable API contracts with synthetic files and no native program/API access."""
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml
from test_support import backend_paths, load_backend, load_portable, report


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob('*') if path.is_file()}


@contextmanager
def fixture(m, p, work, failure=None, missing_state=False):
    with backend_paths(m, work) as (data, private, share):
        program = work / 'program'
        program.mkdir()
        verge, core = program / 'clash-verge.exe', program / 'verge-mihomo.exe'
        verge.write_bytes(b'SYNTHETIC_PROGRAM')
        core.write_bytes(b'SYNTHETIC_CORE')
        script = data / 'profiles' / 'synthetic.js'
        script.write_text(('// CZO MANAGED V1\n' if failure == 'missing-ledger' else '') +
                          'function main(config, profileName) { return config; }', encoding='utf8')
        profiles = {'current': 'synthetic-source', 'items': [
            {'uid': 'synthetic-source', 'type': 'remote', 'url': 'https://example.invalid/synthetic', 'option': {'script': 'synthetic-script'}},
            {'uid': 'synthetic-script', 'type': 'script', 'file': 'synthetic.js'}]}
        config = {'mode': 'rule', 'mixed-port': 7890,
                  'proxies': [{'name': 'synthetic-A', 'type': 'socks5', 'server': '192.0.2.10', 'port': 9}],
                  'proxy-groups': [{'name': 'MAIN', 'type': 'select', 'proxies': ['synthetic-A']}],
                  'rules': ['DOMAIN,domestic.example,DIRECT', 'MATCH,MAIN']}
        (data / 'profiles.yaml').write_text(yaml.safe_dump(profiles), encoding='utf8')
        (data / 'clash-verge.yaml').write_text(yaml.safe_dump(config), encoding='utf8')
        if failure == 'pending-recovery':
            m.write_json(private / 'recovery.json', {'phase': 'recovery-required', 'failedStages': ['core']})
        state = work / 'missing-state' if missing_state else private
        processes = [{'name': 'clash-verge', 'pid': 111, 'path': str(verge)},
                     {'name': 'verge-mihomo', 'pid': 999, 'path': str(core)}]
        core_version = '9.9.9' if failure == 'core-version' else '1.19.32'
        verge_version = '9.9.9' if failure == 'clash-version' else '2.5.7'
        api_calls = []

        def api(method, path, **kwargs):
            api_calls.append((method, path))
            assert method == 'GET', 'Detection cannot write to a controller'
            if path == '/configs':
                return 200, {'mode': 'rule', 'mixed-port': 7890}
            if path == '/version':
                return 200, {'version': core_version}
            raise AssertionError('Unexpected mock API request')

        all_names = ['synthetic-A', 'DIRECT'] if failure == 'mixed-actions' else ['synthetic-A']
        proxies = {'synthetic-A': {'type': 'Socks5'},
                   'MAIN': {'type': 'Selector', 'all': all_names, 'now': 'synthetic-A'}}
        with patch.object(p, '_processes', return_value=processes), \
             patch.object(p, '_ps', return_value=verge_version), \
             patch.object(m, 'run', return_value=subprocess.CompletedProcess([], 0, ('Mihomo Meta v' + core_version + ' synthetic').encode(), b'')), \
             patch.object(m, 'api', side_effect=api), \
             patch.object(m, 'listener_owners', return_value={7890: [('127.0.0.1:7890', 999)]}), \
             patch.object(m, 'proxy_snapshot', return_value=proxies), \
             patch.object(m, 'install', side_effect=AssertionError('Implicit apply is forbidden')), \
             patch.object(m, 'rollback', side_effect=AssertionError('Implicit rollback is forbidden')), \
             patch.object(m, 'reload_file', side_effect=AssertionError('Native reload is forbidden')):
            yield {'data': data, 'private': private, 'state': state, 'share': share,
                   'script': script, 'program': program, 'api_calls': api_calls,
                   'detect': lambda: p.detect(str(data), str(program), str(state))}


def expect_stopped(action):
    try:
        action()
    except RuntimeError:
        return
    raise AssertionError('Operation should have stopped')


def security_cases(m, p, root):
    results = []
    with fixture(m, p, root / 'private-paths') as f:
        foreign = root / 'private-paths' / 'foreign-files'
        foreign.mkdir()
        (foreign / 'unrelated-user-file.txt').write_bytes(b'SYNTHETIC_USER_FILE')
        user_root = root / 'private-paths' / 'synthetic-user-root'
        forbidden = (foreign, f['data'], f['data'] / 'nested-state',
                     Path(f['private'].anchor), user_root)
        before = snapshot(root)
        with patch.object(Path, 'home', return_value=user_root), \
             patch.object(Path, 'mkdir', side_effect=AssertionError('Rejected state directories cannot be created')), \
             patch.object(p, '_ps', side_effect=AssertionError('Rejected state directories cannot change ACLs')):
            for directory in forbidden:
                with patch.object(m, 'PRIVATE', directory):
                    expect_stopped(p._ensure_private)
        assert snapshot(root) == before
        results.append('unsafe private paths and unrelated user files stop before mkdir or ACL changes')

    with fixture(m, p, root / 'output-paths') as f:
        context = f['detect']()
        before = snapshot(root)
        api_count = len(f['api_calls'])
        with patch.object(Path, 'mkdir', side_effect=AssertionError('Rejected outputs cannot be created')), \
             patch.object(p, '_ensure_private', side_effect=AssertionError('Rejected outputs cannot change state')), \
             patch.object(m, 'prepare', side_effect=AssertionError('Rejected outputs cannot prepare')):
            for output in (f['private'], f['private'] / 'nested-output', f['private'].parent):
                expect_stopped(lambda output=output: p.generate(context, str(output)))
        assert snapshot(root) == before and len(f['api_calls']) == api_count
        results.append('equal or containing output and private paths stop without writes or new API calls')

    with fixture(m, p, root / 'template-fields') as f:
        from zeroomega import build_backup
        context = f['detect']()
        template = build_backup([{'id': 'a' * 64, 'profileName': 'synthetic-A', 'port': 20000, 'active': True}])
        variants = []
        for mutate in (
            lambda value: value['+synthetic-A']['fallbackProxy'].update(host='192.0.2.1'),
            lambda value: value['+synthetic-A']['fallbackProxy'].update(scheme='http'),
            lambda value: value.update({'SYNTHETIC_UNKNOWN_OPTION': True}),
            lambda value: value['+synthetic-A'].update({'SYNTHETIC_UNKNOWN_FIELD': True}),
            lambda value: value['+synthetic-A']['bypassList'][0].update(pattern='*.synthetic-private.invalid'),
        ):
            variant = copy.deepcopy(template)
            mutate(variant)
            path = root / 'template-fields' / ('invalid-' + str(len(variants)) + '.bak')
            m.write_json(path, variant)
            variants.append(path)
        before = snapshot(root)
        with patch.object(Path, 'mkdir', side_effect=AssertionError('Rejected templates cannot create output')), \
             patch.object(p, '_ensure_private', side_effect=AssertionError('Rejected templates cannot change state')), \
             patch.object(m, 'prepare', side_effect=AssertionError('Rejected templates cannot prepare')):
            for path in variants:
                expect_stopped(lambda path=path: p.generate(context, str(root / 'rejected-template-output'), str(path)))
        assert snapshot(root) == before
        results.append('nonlocal proxy unknown fields and private bypass templates are rejected before writes')

    with fixture(m, p, root / 'generated-template') as f:
        context = f['detect']()
        output = root / 'generated-template-output'
        real_run = subprocess.run

        def offline_run(command, **kwargs):
            if command == [str(m.CORE), '-v']:
                return subprocess.CompletedProcess(command, 0, b'Mihomo Meta v1.19.32 synthetic', b'')
            if Path(command[0]).name.lower() == 'powershell.exe':
                return subprocess.CompletedProcess(command, 0, b'2.5.7\r\n', b'')
            assert command[0] == str(m.NODE), 'Only local JavaScript may execute'
            kwargs.setdefault('creationflags', subprocess.CREATE_NO_WINDOW)
            return real_run(command, **kwargs)

        def validate(config, stage):
            path = stage / 'candidate.yaml'
            path.write_text(yaml.safe_dump(config), encoding='utf8')
            return path

        before = snapshot(f['data'])
        with patch.object(p, '_ensure_private', return_value=None), \
             patch.object(m, 'run', side_effect=offline_run), \
             patch.object(m, 'core_validate', side_effect=validate), \
             patch.object(m, 'excluded_ports', return_value=set()), \
             patch.object(m, 'free_port', return_value=True):
            first = p.generate(context, str(output))
            backup_path = Path(first['bakPath'])
            first_backup = backup_path.read_bytes()
            p._template_check(str(backup_path))
            second = p.generate(context, str(output), str(backup_path))
        assert second['entries'] == first['entries'] and backup_path.read_bytes() == first_backup
        assert snapshot(f['data']) == before and first['needsApply'] and second['needsApply']
        results.append('generated backup is accepted as template and repeated generation keeps names and ports')
    return results


def main():
    results = []
    with TemporaryDirectory(prefix='czo-portable-tests-') as temporary:
        root = Path(temporary)
        m, p = load_backend(root), None
        p = load_portable()

        with fixture(m, p, root / 'read-only', missing_state=True) as f:
            before = snapshot(root)
            with patch.object(m, 'write_json', side_effect=AssertionError('Detection cannot write JSON')), \
                 patch.object(m, 'private_backup', side_effect=AssertionError('Detection cannot back up')), \
                 patch.object(m, 'prepare', side_effect=AssertionError('Detection cannot prepare')):
                context = f['detect']()
            assert context['ok'] and context['canGenerate'] and context['canApply']
            assert snapshot(root) == before and not f['state'].exists()
            assert f['api_calls'] and all(method == 'GET' for method, _ in f['api_calls'])
            results.append('detect reads only and cannot create a missing state directory')
            public = p.public_summary(context)
            assert not any(key.startswith('_') for key in public)
            assert 'example.invalid' not in json.dumps(public) and 'synthetic-source' not in json.dumps(public)
            results.append('public detect summary excludes internal source and fingerprint fields')

        with fixture(m, p, root / 'generate') as f:
            context = f['detect']()
            output = root / 'generate-output'
            before = snapshot(f['data'])
            order = []
            real_backup = m.private_backup

            def backup(paths):
                order.append('backup')
                return real_backup(paths)

            prepared = {'changes': {'new': 1, 'active': 1, 'inactive': 0}}

            def prepare():
                order.append('prepare')
                assert m.RESOURCES != m.HERE and m.HERE == output.resolve()
                m.write_json(output / '验证摘要.json', {'status': 'prepared-not-applied'})
                m.write_json(output / 'node-port-map.json', [{'nodeName': 'synthetic-A', 'port': 20000, 'active': True}])
                return copy.deepcopy(prepared)

            with patch.object(p, '_ensure_private', return_value=None), \
                 patch.object(m, 'private_backup', side_effect=backup), \
                 patch.object(m, 'prepare', side_effect=prepare):
                generated = p.generate(context, str(output))
            assert order == ['backup', 'prepare']
            assert generated['needsApply'] and context['_prepared'] == prepared
            assert snapshot(f['data']) == before
            assert Path(generated['backupPath']).joinpath('manifest.json').is_file()
            results.append('generate backs up before staging and never applies or changes subscription files')

            with patch.object(p, '_ensure_private', return_value=None), \
                 patch.object(m, 'install', return_value={'status': 'synthetic-applied'}) as install:
                applied = p.apply(context)
                install.assert_called_once_with(prepared)
            assert applied['status'] == 'synthetic-applied' and not context['_generated']['needsApply']
            results.append('apply dispatches only after an explicit call with a prepared generation')
            with patch.object(p, '_ensure_private', return_value=None), \
                 patch.object(m, 'rollback', return_value={'status': 'synthetic-rolled-back'}) as rollback:
                rolled = p.rollback(context)
                rollback.assert_called_once_with()
            assert rolled['status'] == 'synthetic-rolled-back' and not context['canApply'] and not context['canGenerate']
            results.append('rollback dispatches explicitly and requires a fresh detection afterward')

        with fixture(m, p, root / 'stale') as f:
            context = f['detect']()
            f['script'].write_text('// CHANGED AFTER DETECT\nfunction main(config, profileName) { return config; }', encoding='utf8')
            output = root / 'stale-output'
            before = snapshot(root)
            with patch.object(p, '_ensure_private', side_effect=AssertionError('Stale detection must fail before private writes')), \
                 patch.object(m, 'prepare', side_effect=AssertionError('Stale detection cannot prepare')):
                expect_stopped(lambda: p.generate(context, str(output)))
                expect_stopped(lambda: p.apply(context))
                expect_stopped(lambda: p.rollback(context))
            assert snapshot(root) == before and not output.exists()
            results.append('stale detection blocks generation apply and rollback before writes')

        with fixture(m, p, root / 'locked') as f:
            context = f['detect']()
            output = root / 'locked-output'
            with patch.object(p, '_ensure_private', return_value=None), \
                 patch.object(m, 'private_backup', side_effect=AssertionError('Lock conflict must stop before backups')), \
                 patch.object(m, 'prepare', side_effect=AssertionError('Lock conflict cannot prepare')):
                # Both handles refer only to a synthetic temporary lock file.
                with p._private_lock():
                    expect_stopped(lambda: p.generate(context, str(output)))
            assert not output.exists()
            results.append('another file handle holding the operation lock prevents generation')

        with fixture(m, p, root / 'mixed-actions', failure='mixed-actions') as f:
            before = snapshot(root)
            context = f['detect']()
            assert context['ok'] and context['needsPolicyReview']
            assert context['groupPolicies'] == [{'name': 'MAIN', 'choices': ['DIRECT', 'PROXY'], 'currentAction': 'PROXY'}]
            output = root / 'mixed-output'
            with patch.object(p, '_ensure_private', side_effect=AssertionError('Unreviewed policies cannot write')), \
                 patch.object(m, 'prepare', side_effect=AssertionError('Unreviewed policies cannot prepare')):
                expect_stopped(lambda: p.generate(context, str(output)))
                expect_stopped(lambda: p.apply(context))
            assert snapshot(root) == before and not output.exists()
            results.append('mixed action groups require explicit review before generation')
            for choices in ({}, {'MAIN': 'REJECT'}, {'MAIN': 'DIRECT', 'EXTRA': 'PROXY'}):
                expect_stopped(lambda choices=choices: p.review_policies(context, choices))
                assert context['needsPolicyReview'] and '_reviewedPolicy' not in context
            results.append('missing invalid and additional policy choices are rejected')
            p.review_policies(context, {'MAIN': 'DIRECT'})
            assert not context['needsPolicyReview'] and context['_reviewedPolicy'] == {'MAIN': 'DIRECT'}

            def reviewed_prepare():
                assert m.POLICY_OVERRIDE == {'MAIN': 'DIRECT'}
                m.write_json(output / '验证摘要.json', {'status': 'prepared-not-applied'})
                m.write_json(output / 'node-port-map.json', [])
                return {'changes': {'new': 1, 'active': 1, 'inactive': 0}}

            with patch.object(p, '_ensure_private', return_value=None), \
                 patch.object(m, 'prepare', side_effect=reviewed_prepare):
                generated = p.generate(context, str(output))
            assert generated['needsApply']
            results.append('reviewed policies reach generation without implicit apply')

        with fixture(m, p, root / 'cached-policies', failure='mixed-actions') as f:
            _, _, _, source = m.current()
            m.write_json(f['private'] / 'registry.json', {'entries': [], 'policies': {source: {'MAIN': 'DIRECT'}}})
            before = snapshot(root)
            context = f['detect']()
            assert context['ok'] and not context['needsPolicyReview'] and context['groupPolicies'] == []
            assert context['_policyBase'] == {'MAIN': 'DIRECT'} and snapshot(root) == before
            results.append('confirmed policy cache for the same source avoids repeated review')

        for failure in ('clash-version', 'core-version', 'missing-ledger', 'pending-recovery'):
            with fixture(m, p, root / failure, failure=failure) as f:
                before = snapshot(root)
                context = f['detect']()
                assert not context['ok'] and not context['canGenerate'] and not context['canApply'] and context['issues']
                assert snapshot(root) == before
                results.append(failure + ' stops detection without writes')
        private_sentinel = 'SYNTHETIC_PRIVATE_DIAGNOSTIC_VALUE'
        for error in (RuntimeError(private_sentinel), ValueError(private_sentinel)):
            assert private_sentinel not in p.safe_error(error)
        results.append('arbitrary diagnostic exception text cannot reach public error output')
        results.extend(security_cases(m, p, root))
    report('portable-interface', results)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('PORTABLE_TEST_FAILED:' + type(error).__name__)
        sys.exit(1)
