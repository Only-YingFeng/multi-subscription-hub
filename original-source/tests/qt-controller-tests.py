"""Qt controller regressions with fake widgets/backend; production API calls: zero."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys
import threading
import time
import types
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QCoreApplication, QObject, Signal


APP = QCoreApplication.instance() or QCoreApplication([])
SOURCE = Path(__file__).resolve().parent.parent / 'qt_controller.py'


class PublicError(RuntimeError):
    pass


def context():
    return {'ok': True, 'issues': [], 'canGenerate': True, 'canApply': True,
            'hasInstallation': False, 'nodeCount': 2, 'outputSuggested': 'C:/synthetic/exports',
            'clashVersion': '2.5.7', 'coreVersion': '1.19.32', 'mode': 'rule'}


def checks(status='reachable'):
    return {'checks': [{'nodeName': name, 'status': status,
                        'delayMs': 120 if status == 'reachable' else None}
                       for name in ('美国中转815', '日本中转811')],
            'checkedAt': '2026-10-06T01:00:00Z', 'total': 2}


def generation(needs_apply=True):
    return {'bakPath': 'C:/synthetic/exports/ZeroOmega.bak',
            'mapPath': 'C:/synthetic/exports/map.md', 'extensionPath': 'C:/synthetic/profiles/script.js',
            'entries': [{'nodeName': name, 'port': 20000 + index, 'active': True}
                        for index, name in enumerate(('美国中转815', '日本中转811'))],
            'needsApply': needs_apply, 'new': 2, 'active': 2, 'inactive': 0}


class FakeWindow(QObject):
    detectRequested = Signal()
    generateRequested = Signal()
    applyRequested = Signal()
    rollbackRequested = Signal()
    pathsChanged = Signal()

    def __init__(self):
        super().__init__()
        self.values = {name: '' for name in ('output', 'data', 'clash', 'state', 'template')}
        self.calls = []
        self.main_thread = threading.get_ident()
        self.busy = False
        self.paths_enabled = True
        self.close_enabled = True
        self.permissions = {}
        self.apply_yes = False
        self.rollback_yes = False
        self.choices = None

    def record(self, name, *args):
        if threading.get_ident() != self.main_thread:
            raise AssertionError('A worker touched the view')
        self.calls.append((name, *copy.deepcopy(args)))

    def path_values(self):
        self.record('path_values')
        return dict(self.values)

    def set_path_values(self, values):
        self.record('set_path_values', values)
        self.values.update(values)

    def set_busy(self, busy):
        self.record('set_busy', busy)
        self.busy = busy
        self.paths_enabled = self.close_enabled = not busy

    def set_permissions(self, **permissions):
        self.record('set_permissions', permissions)
        self.permissions = permissions

    def clear_results(self):
        self.record('clear_results')

    def render_detect(self, result):
        self.record('render_detect', result)
        if result.get('ok') and not self.values['output']:
            self.set_path_values({'output': result.get('outputSuggested', '')})

    def render_generate(self, result, checked):
        self.record('render_generate', result, checked)

    def render_apply(self, result):
        self.record('render_apply', result)

    def render_rollback(self):
        self.record('render_rollback')

    def show_progress(self, done, total):
        self.record('show_progress', done, total)

    def tell(self, headline, hint=''):
        self.record('tell', headline, hint)

    def error_dialog(self, message):
        self.record('error_dialog', message)

    def confirm_apply(self):
        self.record('confirm_apply')
        return self.apply_yes

    def confirm_rollback(self):
        self.record('confirm_rollback')
        return self.rollback_yes

    def review_policies(self, rows):
        self.record('review_policies', rows)
        return self.choices


class QtControllerTests(unittest.TestCase):
    def setUp(self):
        self.backend = types.ModuleType('portable')
        self.backend.PublicError = PublicError
        self.backend.VERSION = '1.0.2'
        self.backend.safe_error = MagicMock(side_effect=lambda error: str(error) if isinstance(error, PublicError)
                                            else '操作未完成（' + type(error).__name__ + '）。请重新探测。')
        self.backend.detect = MagicMock(side_effect=lambda **_: context())
        def test_nodes(_, on_progress):
            checked = checks()
            for row in checked['checks']:
                on_progress(row)
            return checked
        self.backend.check_nodes = MagicMock(side_effect=test_nodes)
        self.backend.generate = MagicMock(side_effect=lambda *_, **__: generation())
        self.backend.review_policies = MagicMock()
        self.backend.apply = MagicMock(return_value={'status': 'installed-and-live', 'listeners': 2})
        self.backend.rollback = MagicMock(return_value={'status': 'rolled-back', 'registryRetained': True})
        spec = importlib.util.spec_from_file_location('synthetic_qt_controller', SOURCE)
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'portable': self.backend}):
            spec.loader.exec_module(self.module)
        self.window = FakeWindow()
        self.controller = self.module.Controller(self.window)
        self.gates = []

    def tearDown(self):
        for gate in self.gates:
            gate.set()
        if self.controller._thread:
            self.controller._thread.join(timeout=2)
        self.controller._timer.stop()
        self.window.deleteLater()
        QCoreApplication.processEvents()

    def idle(self):
        deadline = time.monotonic() + 3
        while self.controller._busy:
            self.controller._poll_events()
            QCoreApplication.processEvents()
            if time.monotonic() > deadline:
                self.fail('Synthetic worker did not complete')
            time.sleep(.001)
        self.controller._poll_events()

    def probe(self):
        self.window.detectRequested.emit()
        self.idle()

    def prepare(self, needs_apply=True):
        self.probe()
        self.backend.generate.side_effect = lambda *_, **__: generation(needs_apply)
        self.window.generateRequested.emit()
        self.idle()

    def calls(self, name):
        return [row for row in self.window.calls if row[0] == name]

    def assert_no_secret(self):
        self.assertNotIn('SYNTHETIC_SECRET', json.dumps(self.window.calls, ensure_ascii=False))

    def test_initial_state_has_no_implicit_operation(self):
        self.assertEqual(self.window.permissions, {'can_generate': False, 'can_apply': False,
                                                  'installed': False, 'output_available': False})
        self.backend.detect.assert_not_called()
        self.backend.generate.assert_not_called()
        self.backend.apply.assert_not_called()

    def test_detect_async_progress_autofill_and_no_implicit_generate(self):
        self.probe()
        self.assertEqual([row[1:] for row in self.calls('show_progress')], [(1, 2), (2, 2)])
        self.assertEqual(self.window.values['output'], 'C:/synthetic/exports')
        self.assertTrue(self.window.permissions['can_generate'])
        self.assertFalse(self.window.permissions['can_apply'])
        self.assertFalse(self.window.busy)
        self.backend.generate.assert_not_called()
        self.backend.apply.assert_not_called()

    def test_paths_trim_and_omit_blanks(self):
        self.window.values.update(data=' C:/synthetic/data ', state=' C:/synthetic/state ', clash=' ')
        self.probe()
        self.backend.detect.assert_called_once_with(data_directory='C:/synthetic/data',
                                                   state_directory='C:/synthetic/state')

    def test_failed_detect_never_tests_nodes(self):
        self.backend.detect.side_effect = None
        self.backend.detect.return_value = {'ok': False, 'issues': ['本机环境未通过']}
        self.probe()
        self.backend.check_nodes.assert_not_called()
        self.assertIsNone(self.controller._context)
        self.assertFalse(self.window.permissions['can_generate'])
        self.assertEqual(self.calls('render_detect'), [])
        self.assertEqual(self.calls('error_dialog')[-1][1], '本机环境未通过')

    def test_failed_detect_displays_only_public_string_issues(self):
        self.backend.detect.side_effect = None
        self.backend.detect.return_value = {'ok': False, 'issues': [
            ' 当前仅支持已验证版本。 ', None, {'secret': 'SYNTHETIC_SECRET'},
            RuntimeError('SYNTHETIC_SECRET'), '', '请启用订阅后重试。']}
        self.probe()
        self.assertEqual(self.calls('error_dialog')[-1][1], '当前仅支持已验证版本。\n请启用订阅后重试。')
        self.assertEqual(self.calls('render_detect'), [])
        self.assertFalse(self.window.permissions['can_generate'])
        self.assert_no_secret()

    def test_failed_detect_without_public_issues_has_safe_reason(self):
        self.backend.detect.side_effect = None
        self.backend.detect.return_value = {'ok': False, 'issues': {'secret': 'SYNTHETIC_SECRET'}}
        self.probe()
        self.assertEqual(self.calls('error_dialog')[-1][1], '未找到支持的本机环境，请确认 Clash 已运行后重试。')
        self.assertEqual(self.calls('render_detect'), [])
        self.assert_no_secret()

    def test_detect_exception_clears_old_results_and_hides_exception_body(self):
        self.prepare()
        self.backend.detect.side_effect = RuntimeError('SYNTHETIC_SECRET_URL_PASSWORD')
        self.probe()
        self.assertIsNone(self.controller._context)
        self.assertIsNone(self.controller._generation)
        self.assertEqual(self.controller._checks, {})
        self.assertIn('RuntimeError', self.calls('error_dialog')[-1][1])
        self.assert_no_secret()

    def test_node_test_exception_stops_generation(self):
        self.backend.check_nodes.side_effect = OSError('SYNTHETIC_SECRET')
        self.probe()
        self.window.generateRequested.emit()
        self.backend.generate.assert_not_called()
        self.assertIsNone(self.controller._context)
        self.assert_no_secret()

    def test_duplicate_progress_does_not_inflate_count(self):
        def node_checks(_, on_progress):
            snapshot = checks()
            on_progress(snapshot['checks'][0])
            on_progress(snapshot['checks'][0])
            on_progress(snapshot['checks'][1])
            return snapshot
        self.backend.check_nodes.side_effect = node_checks
        self.probe()
        self.assertEqual([row[1:] for row in self.calls('show_progress')], [(1, 2), (1, 2), (2, 2)])

    def test_busy_locks_paths_close_and_ignores_repeated_operations(self):
        gate, entered = threading.Event(), threading.Event()
        self.gates.append(gate)
        def detect(**_):
            entered.set()
            gate.wait(2)
            return context()
        self.backend.detect.side_effect = detect
        self.window.detectRequested.emit()
        self.assertTrue(entered.wait(1))
        self.assertTrue(self.window.busy)
        self.assertFalse(self.window.paths_enabled)
        self.assertFalse(self.window.close_enabled)
        for signal in (self.window.detectRequested, self.window.generateRequested,
                       self.window.applyRequested, self.window.rollbackRequested):
            signal.emit()
        self.assertEqual(self.backend.detect.call_count, 1)
        self.backend.generate.assert_not_called()
        self.backend.apply.assert_not_called()
        self.backend.rollback.assert_not_called()
        gate.set()
        self.idle()

    def test_path_change_discards_stale_completion_and_progress(self):
        gate, entered = threading.Event(), threading.Event()
        self.gates.append(gate)
        def detect(**_):
            entered.set()
            gate.wait(2)
            return context()
        self.backend.detect.side_effect = detect
        self.window.detectRequested.emit()
        self.assertTrue(entered.wait(1))
        self.window.values['data'] = 'C:/synthetic/new-data'
        self.window.pathsChanged.emit()
        gate.set()
        self.idle()
        self.assertIsNone(self.controller._context)
        self.assertEqual(self.calls('render_detect'), [])
        self.assertEqual(self.calls('show_progress'), [])
        self.assertFalse(self.window.permissions['can_generate'])

    def test_invalid_node_snapshot_does_not_enable_generate(self):
        self.backend.check_nodes.side_effect = None
        self.backend.check_nodes.return_value = {'checks': [], 'total': 2}
        self.probe()
        self.assertFalse(self.window.permissions['can_generate'])
        self.assertIsNone(self.controller._context)
        self.assertTrue(self.calls('error_dialog'))

    def test_all_failed_nodes_are_preserved_in_generation(self):
        self.backend.check_nodes.side_effect = lambda _, on_progress: checks('failed')
        self.prepare()
        rendered = self.calls('render_generate')[-1]
        self.assertEqual(len(rendered[1]['entries']), 2)
        self.assertEqual(len(rendered[2]['checks']), 2)
        self.assertTrue(all(row['status'] == 'failed' for row in rendered[2]['checks']))
        self.assertTrue(self.window.permissions['can_apply'])

    def test_output_and_template_are_forwarded(self):
        self.window.values.update(output=' C:/synthetic/other ', template=' C:/synthetic/template.bak ')
        self.prepare()
        self.assertEqual(self.backend.generate.call_args.kwargs,
                         {'output_directory': 'C:/synthetic/other', 'template_path': 'C:/synthetic/template.bak'})

    def test_empty_output_stops_before_backend(self):
        self.probe()
        self.window.values['output'] = ''
        self.window.generateRequested.emit()
        self.backend.generate.assert_not_called()
        self.assertTrue(self.calls('error_dialog'))
        self.assertIsNotNone(self.controller._context)

    def test_generate_failure_clears_context_and_sanitizes_error(self):
        self.probe()
        self.backend.generate.side_effect = ValueError('SYNTHETIC_SECRET')
        self.window.generateRequested.emit()
        self.idle()
        self.assertIsNone(self.controller._context)
        self.assertIsNone(self.controller._generation)
        self.assertFalse(self.window.permissions['can_apply'])
        self.assert_no_secret()

    def test_malformed_generation_does_not_show_success(self):
        for result in (None, {}, {'bakPath': 'synthetic'}):
            with self.subTest(result=result):
                self.probe()
                self.backend.generate.side_effect = None
                self.backend.generate.return_value = result
                self.window.generateRequested.emit()
                self.idle()
                self.assertIsNone(self.controller._generation)
                self.assertFalse(self.window.permissions['can_apply'])
        self.assertEqual(self.calls('render_generate'), [])

    def test_already_live_generation_disables_apply(self):
        self.prepare(False)
        self.window.apply_yes = True
        self.window.applyRequested.emit()
        self.assertFalse(self.window.permissions['can_apply'])
        self.backend.apply.assert_not_called()
        self.assertEqual(self.calls('confirm_apply'), [])

    def test_apply_confirmation_no_never_mutates(self):
        self.prepare()
        self.window.applyRequested.emit()
        self.backend.apply.assert_not_called()
        self.assertTrue(self.controller._generation['needsApply'])
        self.assertTrue(self.window.permissions['can_apply'])

    def test_apply_verified_success_marks_installation(self):
        self.prepare()
        self.window.apply_yes = True
        self.window.applyRequested.emit()
        self.idle()
        self.backend.apply.assert_called_once_with(self.controller._context)
        self.assertFalse(self.controller._generation['needsApply'])
        self.assertTrue(self.controller._context['hasInstallation'])
        self.assertFalse(self.window.permissions['can_apply'])
        self.assertTrue(self.window.permissions['installed'])
        self.assertEqual(self.calls('render_apply')[-1][1]['status'], 'installed-and-live')

    def test_invalid_apply_result_never_claims_live(self):
        self.prepare()
        self.backend.apply.return_value = {'status': 'prepared'}
        self.window.apply_yes = True
        self.window.applyRequested.emit()
        self.idle()
        self.assertEqual(self.calls('render_apply'), [])
        self.assertIsNone(self.controller._context)
        self.assertFalse(self.window.permissions['installed'])

    def test_apply_permission_is_required(self):
        self.prepare()
        self.controller._context['canApply'] = False
        self.window.apply_yes = True
        self.window.applyRequested.emit()
        self.backend.apply.assert_not_called()
        self.assertEqual(self.calls('confirm_apply'), [])

    def test_only_fixed_summary_warning_is_displayed(self):
        self.prepare()
        self.backend.apply.return_value = {'status': 'installed-and-live', 'summaryWarning': 'SYNTHETIC_SECRET'}
        self.window.apply_yes = True
        self.window.applyRequested.emit()
        self.idle()
        self.assertNotIn('summaryWarning', self.calls('render_apply')[-1][1])
        self.assert_no_secret()

    def test_known_summary_warning_does_not_falsify_live_status(self):
        self.prepare()
        warning = '已写回并生效，但验证摘要导出失败。'
        self.backend.apply.return_value = {'status': 'installed-and-live', 'summaryWarning': warning}
        self.window.apply_yes = True
        self.window.applyRequested.emit()
        self.idle()
        self.assertEqual(self.calls('tell')[-1][2], warning)
        self.assertTrue(self.controller._context['hasInstallation'])

    def test_policy_cancel_does_not_review_or_generate(self):
        self.probe()
        self.controller._context.update(needsPolicyReview=True,
                                        groupPolicies=[{'name': '其他流量', 'choices': ['DIRECT', 'PROXY']}])
        self.window.generateRequested.emit()
        self.backend.review_policies.assert_not_called()
        self.backend.generate.assert_not_called()
        self.assertTrue(self.controller._context['needsPolicyReview'])

    def test_policy_review_runs_before_generate(self):
        self.probe()
        self.controller._context.update(needsPolicyReview=True,
                                        groupPolicies=[{'name': '其他流量', 'choices': ['DIRECT', 'PROXY']}])
        self.window.choices = {'其他流量': 'PROXY'}
        order = []
        self.backend.review_policies.side_effect = lambda *_: order.append('review')
        self.backend.generate.side_effect = lambda *_, **__: order.append('generate') or generation()
        self.window.generateRequested.emit()
        self.idle()
        self.assertEqual(order, ['review', 'generate'])
        self.assertEqual(self.backend.review_policies.call_args.args[1], {'其他流量': 'PROXY'})

    def test_policy_backend_error_stops_generate(self):
        self.probe()
        self.controller._context.update(needsPolicyReview=True,
                                        groupPolicies=[{'name': '其他流量', 'choices': ['DIRECT', 'PROXY']}])
        self.window.choices = {'其他流量': 'PROXY'}
        self.backend.review_policies.side_effect = RuntimeError('SYNTHETIC_SECRET')
        self.window.generateRequested.emit()
        self.idle()
        self.backend.generate.assert_not_called()
        self.assertIsNone(self.controller._context)
        self.assert_no_secret()

    def test_policy_dialog_error_stops_generate(self):
        self.probe()
        self.controller._context.update(needsPolicyReview=True,
                                        groupPolicies=[{'name': '其他流量', 'choices': ['DIRECT', 'PROXY']}])
        self.window.review_policies = MagicMock(side_effect=OSError('SYNTHETIC_SECRET'))
        self.window.generateRequested.emit()
        self.backend.generate.assert_not_called()
        self.assertIsNone(self.controller._context)
        self.assert_no_secret()

    def test_invalid_policy_rows_stop_without_dialog(self):
        self.probe()
        self.controller._context.update(needsPolicyReview=True, groupPolicies=[{'name': '其他流量', 'choices': ['UNKNOWN']}])
        self.window.generateRequested.emit()
        self.assertEqual(self.calls('review_policies'), [])
        self.backend.generate.assert_not_called()
        self.assertIsNone(self.controller._context)

    def test_rollback_no_requires_confirmation(self):
        self.probe()
        self.controller._context['hasInstallation'] = True
        self.window.rollbackRequested.emit()
        self.backend.rollback.assert_not_called()
        self.assertIsNotNone(self.controller._context)

    def test_rollback_success_requires_reprobe(self):
        self.prepare()
        self.controller._context['hasInstallation'] = True
        self.window.rollback_yes = True
        self.window.rollbackRequested.emit()
        self.idle()
        self.assertEqual(len(self.calls('render_rollback')), 1)
        self.assertIsNone(self.controller._context)
        self.assertIsNone(self.controller._generation)
        self.assertFalse(self.window.permissions['can_generate'])
        self.assertFalse(self.window.permissions['can_apply'])
        self.assertFalse(self.window.permissions['installed'])

    def test_invalid_rollback_status_does_not_show_complete(self):
        self.probe()
        self.controller._context['hasInstallation'] = True
        self.window.rollback_yes = True
        self.backend.rollback.return_value = {'status': 'restored'}
        self.window.rollbackRequested.emit()
        self.idle()
        self.assertEqual(self.calls('render_rollback'), [])
        self.assertIsNone(self.controller._context)

    def test_safe_error_fallback_never_echoes_exception(self):
        self.backend.safe_error.side_effect = RuntimeError('SYNTHETIC_SECRET')
        self.assertEqual(self.module._safe_error(ValueError('SYNTHETIC_SECRET')),
                         '操作未完成（ValueError）。请重新探测。')


if __name__ == '__main__':
    unittest.main(verbosity=2)
