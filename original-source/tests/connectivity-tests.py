"""Named-leaf connectivity contracts; all controller/network calls are mocked."""
from contextlib import contextmanager
import copy
import datetime as dt
import io
import json
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import MagicMock, patch
import urllib.error
from urllib.parse import parse_qs, unquote, urlsplit

from test_support import load_backend, load_portable


ROOT = TemporaryDirectory(prefix='czo-connectivity-tests-')
M = load_backend(Path(ROOT.name))
P = load_portable()
FINGERPRINT = ('synthetic-source', 'synthetic-config', 'synthetic-runtime', 'synthetic-script')


@contextmanager
def fixture(names=None, api_effect=None, snapshot_effect=None, fingerprint_effect=None):
    names = names or ['美国中转815', '日本中转811']
    runtime = {'mode': 'rule', 'mixed-port': 7890,
               'proxies': [{'name': name, 'type': 'socks5', 'server': '192.0.2.1', 'port': 9}
                           for name in names]}
    proxies = {name: {'name': name, 'type': 'Socks5',
                      'extra': {P.NODE_TEST_URL: {'alive': True}}} for name in names}
    proxies.update({'MAIN': {'type': 'Selector', 'all': names, 'now': names[0]},
                    'AUTO': {'type': 'URLTest', 'all': names, 'now': names[0]},
                    'DIRECT': {'type': 'DIRECT'}})
    context = {'ok': True, 'canGenerate': True, 'nodeCount': len(names), '_corePid': 999,
               '_fingerprint': FINGERPRINT, 'dataDirectory': str(Path(ROOT.name) / 'data'),
               'stateDirectory': str(Path(ROOT.name) / 'state'),
               '_core': str(Path(ROOT.name) / 'synthetic-core.exe'),
               '_verge': str(Path(ROOT.name) / 'synthetic-verge.exe')}
    calls, lock = [], threading.Lock()

    def api(method, path, **kwargs):
        with lock:
            calls.append((method, path, kwargs.get('timeout')))
        if method != 'GET':
            raise AssertionError('Connectivity must never mutate the controller')
        if path == '/configs':
            return 200, {'mode': 'rule', 'mixed-port': 7890}
        if api_effect:
            result = api_effect(method, path, kwargs)
            if result is not None:
                return result
        parsed = urlsplit(path)
        leaf_path = parsed.path.removeprefix('/proxies/')
        name = unquote(leaf_path.removesuffix('/delay') if parsed.query else leaf_path)
        if name not in names:
            raise AssertionError('Only literal synthetic leaf nodes can be queried')
        if parsed.query:
            return 200, {'delay': 120}
        return 200, copy.deepcopy(proxies[name])

    with patch.object(M, 'api', side_effect=api), \
         patch.object(M, 'read_yaml', return_value=runtime), \
         patch.object(M, 'core_process_ids', return_value={999}), \
         patch.object(M, 'listener_owners', return_value={7890: [('127.0.0.1:7890', 999)]}), \
         patch.object(M, 'proxy_snapshot', side_effect=snapshot_effect,
                      return_value=proxies), \
         patch.object(P, '_fingerprint', side_effect=fingerprint_effect,
                      return_value=FINGERPRINT), \
         patch.object(M, 'reload_file', side_effect=AssertionError('Reload is forbidden')), \
         patch.object(M, 'install', side_effect=AssertionError('Installation is forbidden')), \
         patch.object(M.urllib.request, 'urlopen', side_effect=AssertionError('Real networking is forbidden')):
        yield context, calls, proxies


class ConnectivityTests(unittest.TestCase):
    def test_literal_unicode_and_reserved_characters(self):
        name = '美国 中转/815?分组#甲%+😀'
        progress = []
        with fixture([name]) as (context, calls, _):
            result = P.check_nodes(context, progress.append)
        delay_call = next(row for row in calls if '/delay?' in row[1])
        path = delay_call[1]
        self.assertNotIn(name, path)
        self.assertIn('%2F', path)
        self.assertIn('%3F', path)
        self.assertIn('%23', path)
        self.assertIn('%25', path)
        self.assertEqual(unquote(urlsplit(path).path.split('/')[2]), name)
        self.assertEqual(parse_qs(urlsplit(path).query),
                         {'url': [P.NODE_TEST_URL], 'timeout': ['6000'], 'expected': ['204']})
        self.assertEqual(delay_call[2], 7.5)
        self.assertEqual(result['checks'], [{'nodeName': name, 'status': 'reachable', 'delayMs': 120}])
        self.assertEqual(progress, result['checks'])

    def test_order_and_public_snapshot(self):
        with fixture() as (context, _, _):
            result = P.check_nodes(context)
            self.assertEqual([row['nodeName'] for row in result['checks']], ['美国中转815', '日本中转811'])
            self.assertEqual(result['total'], 2)
            self.assertEqual(context['_nodeChecks'], result)
            result['checks'][0]['status'] = 'failed'
            self.assertEqual(context['_nodeChecks']['checks'][0]['status'], 'reachable')
            self.assertEqual(dt.datetime.fromisoformat(result['checkedAt'].replace('Z', '+00:00')).utcoffset(),
                             dt.timedelta(0))

    def test_leaf_only_once_and_no_put_or_fallback(self):
        with fixture() as (context, calls, _):
            P.check_nodes(context)
        tests = [path for method, path, _ in calls if '/delay?' in path]
        self.assertEqual(len(tests), 2)
        self.assertTrue(all(method == 'GET' for method, _, _ in calls))
        self.assertFalse(any('/group/' in path or 'MAIN' in path or 'AUTO' in path for _, path, _ in calls))
        self.assertEqual(len(set(tests)), 2)

    def test_zero_delay_success_is_reachable(self):
        with fixture(api_effect=lambda _, path, __: (200, {'delay': 0}) if '/delay?' in path else None) as (context, _, _):
            result = P.check_nodes(context)
        self.assertTrue(all(row['status'] == 'reachable' and row['delayMs'] == 0 for row in result['checks']))

    def test_bad_delay_payloads_fail(self):
        for value in (-1, 65536, True, 1.5, '120', None):
            with self.subTest(value=value), fixture(api_effect=lambda _, path, __: (200, {'delay': value})
                                                   if '/delay?' in path else None) as (context, _, _):
                result = P.check_nodes(context)
            self.assertTrue(all(row['status'] == 'failed' and row['delayMs'] is None for row in result['checks']))

    def test_http_gateway_timeout(self):
        def effect(_, path, __):
            if '/delay?' in path:
                raise urllib.error.HTTPError('https://synthetic-private.invalid/secret', 504,
                                             'SYNTHETIC_PRIVATE_BODY', {}, None)
        with fixture(api_effect=effect) as (context, _, _):
            result = P.check_nodes(context)
        self.assertTrue(all(row['status'] == 'timeout' for row in result['checks']))
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_pipe_gateway_timeout(self):
        def effect(_, path, __):
            if '/delay?' in path:
                raise RuntimeError('Local API failed: HTTP 504')
        with fixture(api_effect=effect) as (context, _, _):
            result = P.check_nodes(context)
        self.assertTrue(all(row['status'] == 'timeout' for row in result['checks']))

    def test_transport_timeout(self):
        for error in (TimeoutError('SYNTHETIC_PRIVATE_BODY'), socket.timeout('SYNTHETIC_PRIVATE_BODY'),
                      urllib.error.URLError(TimeoutError('SYNTHETIC_PRIVATE_BODY'))):
            def effect(_, path, __):
                if '/delay?' in path:
                    raise error
            with self.subTest(error=type(error).__name__), fixture(api_effect=effect) as (context, _, _):
                result = P.check_nodes(context)
            self.assertTrue(all(row['status'] == 'timeout' for row in result['checks']))
            self.assertNotIn('PRIVATE', json.dumps(result))

    def test_failed_leaf_remains_and_never_retries(self):
        def effect(_, path, __):
            if '/delay?' in path:
                raise OSError('SYNTHETIC_SECRET_UUID_PASSWORD')
        with fixture(api_effect=effect) as (context, calls, _):
            result = P.check_nodes(context)
        self.assertEqual(result['total'], 2)
        self.assertTrue(all(row['status'] == 'failed' for row in result['checks']))
        self.assertEqual(len([path for _, path, _ in calls if '/delay?' in path]), 2)
        self.assertNotIn('SECRET', json.dumps(result))

    def test_non204_response_does_not_count_as_reachable(self):
        def effect(_, path, __):
            if '/delay?' not in path and path.startswith('/proxies/'):
                return 200, {'type': 'Socks5', 'extra': {P.NODE_TEST_URL: {'alive': False}}}
        with fixture(api_effect=effect) as (context, _, _):
            result = P.check_nodes(context)
        self.assertTrue(all(row['status'] == 'failed' and row['delayMs'] is None for row in result['checks']))

    def test_missing_test_metadata_fails(self):
        def effect(_, path, __):
            if '/delay?' not in path and path.startswith('/proxies/'):
                return 200, {'type': 'Socks5', 'alive': True}
        with fixture(api_effect=effect) as (context, _, _):
            result = P.check_nodes(context)
        self.assertTrue(all(row['status'] == 'failed' for row in result['checks']))

    def test_groups_direct_and_chained_metadata_stop_before_testing(self):
        for kind, extra in (('Selector', {}), ('URLTest', {}), ('Fallback', {}), ('LoadBalance', {}),
                            ('Relay', {}), ('DIRECT', {}), ('Socks5', {'all': []}),
                            ('Socks5', {'dialer-proxy': 'MAIN'})):
            with self.subTest(kind=kind, extra=extra), fixture() as (context, calls, proxies):
                proxies['美国中转815'].update(type=kind, **extra)
                with self.assertRaises(P.PublicError):
                    P.check_nodes(context)
                self.assertFalse(any('/delay?' in path for _, path, _ in calls))

    def test_name_mismatch_stops_before_testing(self):
        with fixture() as (context, calls, proxies):
            proxies['美国中转815']['name'] = 'MAIN'
            with self.assertRaises(P.PublicError):
                P.check_nodes(context)
            self.assertEqual(calls, [])

    def test_stale_context_removes_previous_result(self):
        with fixture(fingerprint_effect=lambda: ('changed',)) as (context, calls, _):
            context['_nodeChecks'] = {'checks': [{'status': 'reachable'}]}
            with self.assertRaises(P.PublicError):
                P.check_nodes(context)
            self.assertNotIn('_nodeChecks', context)
            self.assertEqual(calls, [])

    def test_core_change_stops_before_testing(self):
        with fixture() as (context, calls, _), patch.object(M, 'core_process_ids', return_value={1000}):
            with self.assertRaises(P.PublicError):
                P.check_nodes(context)
            self.assertEqual(calls, [])

    def test_core_change_during_test_discards_results(self):
        with fixture() as (context, _, _), patch.object(M, 'core_process_ids', side_effect=({999}, {1000})):
            with self.assertRaises(P.PublicError):
                P.check_nodes(context)
            self.assertNotIn('_nodeChecks', context)

    def test_fingerprint_change_during_test_discards_results(self):
        count = 0
        def fingerprint():
            nonlocal count
            count += 1
            return FINGERPRINT if count <= 3 else ('changed',)
        with fixture(fingerprint_effect=fingerprint) as (context, _, _):
            with self.assertRaises(P.PublicError):
                P.check_nodes(context)
            self.assertNotIn('_nodeChecks', context)

    def test_selector_change_is_detected_without_restoring_or_put(self):
        with fixture() as (context, calls, proxies):
            changed = copy.deepcopy(proxies)
            changed['MAIN']['now'] = '日本中转811'
            with patch.object(M, 'proxy_snapshot', side_effect=(proxies, changed)):
                with self.assertRaises(P.PublicError):
                    P.check_nodes(context)
            self.assertNotIn('_nodeChecks', context)
            self.assertTrue(all(method == 'GET' for method, _, _ in calls))

    def test_live_setting_change_discards_results(self):
        with fixture() as (context, calls, _):
            normal_api = M.api.side_effect
            configs_read = 0
            def api(method, path, **kwargs):
                nonlocal configs_read
                if path == '/configs':
                    configs_read += 1
                    return 200, {'mode': 'rule' if configs_read == 1 else 'global', 'mixed-port': 7890}
                return normal_api(method, path, **kwargs)
            with patch.object(M, 'api', side_effect=api):
                with self.assertRaises(P.PublicError):
                    P.check_nodes(context)
            self.assertNotIn('_nodeChecks', context)
            self.assertTrue(all(method == 'GET' for method, _, _ in calls))

    def test_progress_mutation_cannot_change_saved_results(self):
        def progress(row):
            row['status'] = 'failed'
        with fixture() as (context, _, _):
            result = P.check_nodes(context, progress)
        self.assertTrue(all(row['status'] == 'reachable' for row in result['checks']))

    def test_concurrency_never_exceeds_four(self):
        active = peak = 0
        lock = threading.Lock()
        def effect(_, path, __):
            nonlocal active, peak
            if '/delay?' in path:
                with lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(.02)
                with lock:
                    active -= 1
                return 200, {'delay': 100}
        with fixture(['节点 ' + str(i) for i in range(11)], api_effect=effect) as (context, calls, _):
            result = P.check_nodes(context)
        self.assertEqual(result['total'], 11)
        self.assertGreater(peak, 1)
        self.assertLessEqual(peak, 4)
        self.assertEqual(len([path for _, path, _ in calls if '/delay?' in path]), 11)

    def test_api_http_optional_timeout_preserves_default(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"delay":0}'
        response.status = 200
        with patch.object(M.urllib.request, 'urlopen', return_value=response) as opener:
            self.assertEqual(M.api('GET', '/synthetic', config={'external-controller': '127.0.0.1:9090'}),
                             (200, {'delay': 0}))
            self.assertEqual(opener.call_args.kwargs['timeout'], 8)
            M.api('GET', '/synthetic', config={'external-controller': '127.0.0.1:9090'}, timeout=7.5)
            self.assertEqual(opener.call_args.kwargs['timeout'], 7.5)

    def test_api_pipe_timeout_is_classifiable_without_secret(self):
        class Stream(io.BytesIO):
            def fileno(self):
                return 3
        kernel = MagicMock()
        kernel.WaitNamedPipeW.return_value = True
        kernel.PeekNamedPipe.return_value = True
        with patch.object(M.ctypes.windll, 'kernel32', kernel), \
             patch.object(M.msvcrt, 'get_osfhandle', return_value=3), \
             patch('builtins.open', return_value=Stream()), \
             patch.object(M.time, 'monotonic', side_effect=(0, 1)):
            with self.assertRaises(TimeoutError):
                M.api('GET', '/synthetic', config={'external-controller-pipe': r'\\.\pipe\synthetic'}, timeout=.01)
        self.assertEqual(kernel.WaitNamedPipeW.call_args.args[1], 10)

    def test_invalid_api_timeout_rejected_before_io(self):
        with patch.object(M, 'read_yaml', side_effect=AssertionError('Must not read config')):
            for value in (0, -1, True, '6', float('inf'), float('nan')):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    M.api('GET', '/synthetic', timeout=value)


if __name__ == '__main__':
    try:
        unittest.main(verbosity=2)
    finally:
        ROOT.cleanup()
