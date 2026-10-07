"""Qt orchestration; workers never touch widgets or write to a controller implicitly."""
from __future__ import annotations

import copy
import queue
import sys
import threading
from typing import Any, Callable

from PySide6.QtCore import QTimer

import portable


_ACTIONS = {'PROXY', 'DIRECT', 'REJECT', 'REJECT-DROP', 'PASS', 'COMPATIBLE'}
_NODE_STATUSES = {'reachable', 'timeout', 'failed'}
_SUMMARY_WARNINGS = {'已写回并生效，但验证摘要导出失败。'}


def _safe_error(error: Exception) -> str:
    """Use only the backend's fixed public messages; never display a traceback."""
    try:
        message = portable.safe_error(error)
        if isinstance(message, str) and message:
            return message
    except Exception:
        pass
    return '操作未完成（' + type(error).__name__ + '）。请重新探测。'


class Controller:
    def __init__(self, window):
        self.window = window
        self._context: dict[str, Any] | None = None
        self._generation: dict[str, Any] | None = None
        self._checks: dict[str, Any] = {}
        self._revision = 0
        self._busy = False
        self._progress_names: set[str] = set()
        self._events: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._timer = QTimer(window)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._poll_events)
        window.detectRequested.connect(self.detect)
        window.generateRequested.connect(self.generate)
        window.applyRequested.connect(self.apply)
        window.rollbackRequested.connect(self.rollback)
        window.pathsChanged.connect(self.paths_changed)
        self._timer.start()
        self._refresh_permissions()

    def _paths(self) -> dict[str, str]:
        values = self.window.path_values()
        return {name: values.get(name, '').strip() for name in ('output', 'data', 'clash', 'state', 'template')}

    def _refresh_permissions(self) -> None:
        context = self._context or {}
        self.window.set_permissions(
            can_generate=not self._busy and bool(context.get('ok') and context.get('canGenerate')),
            can_apply=not self._busy and bool(context.get('ok') and context.get('canApply')
                                           and self._generation and self._generation.get('needsApply')),
            installed=not self._busy and bool(context.get('ok') and context.get('hasInstallation')),
            output_available=not self._busy and bool(self._paths()['output']))

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.window.set_busy(busy)
        self._refresh_permissions()

    def _clear_context(self) -> None:
        self._context = None
        self._generation = None
        self._checks = {}
        self._progress_names.clear()
        self.window.clear_results()

    def paths_changed(self) -> None:
        self._revision += 1
        self._clear_context()
        self.window.tell('需要重新探测', '路径已变化，请重新探测后生成备份。')
        self._refresh_permissions()

    def _dispatch(self, action: str, operation: Callable[[], dict]) -> None:
        revision = self._revision
        self._set_busy(True)

        def work() -> None:
            try:
                self._events.put((action, revision, operation(), None))
            except Exception as error:
                self._events.put((action, revision, None, _safe_error(error)))

        self._thread = threading.Thread(target=work, name='czo-qt-task', daemon=True)
        self._thread.start()

    def _poll_events(self) -> None:
        try:
            while True:
                action, revision, result, error = self._events.get_nowait()
                if action == 'node_progress':
                    if revision == self._revision and self._busy:
                        self._finish_progress(result)
                    continue
                self._set_busy(False)
                if revision != self._revision:
                    self.window.tell('需要重新探测', '路径已变化，旧操作结果已清除。')
                    self._refresh_permissions()
                    continue
                if error:
                    self._operation_failed(error)
                    continue
                if not isinstance(result, dict):
                    self._operation_failed('返回结果格式异常，请重新探测。')
                    continue
                try:
                    finish = {'detect': self._finish_detect, 'generate': self._finish_generate,
                              'apply': self._finish_apply, 'rollback': self._finish_rollback}[action]
                    finish(result)
                except Exception as failure:
                    self._operation_failed(_safe_error(failure))
                self._refresh_permissions()
        except queue.Empty:
            pass

    def _operation_failed(self, message: str) -> None:
        self._clear_context()
        self.window.tell('操作未完成', '请处理提示后重新探测。')
        self._refresh_permissions()
        self.window.error_dialog(message)

    def detect(self) -> None:
        if self._busy:
            return
        paths = self._paths()
        selected = {key + '_directory': paths[name] for name, key in
                    (('data', 'data'), ('clash', 'clash'), ('state', 'state')) if paths[name]}
        self._revision += 1
        revision = self._revision
        self._clear_context()
        self.window.tell('正在探测本机环境…', '检查完成后，将逐条测试具体节点的连通性。')

        def probe() -> dict:
            context = portable.detect(**selected)
            if not isinstance(context, dict):
                raise portable.PublicError('环境探测结果格式异常，请重新探测。')
            if context.get('ok'):
                def progress(check: dict) -> None:
                    # Only the public node result crosses to the UI thread.
                    if isinstance(check, dict):
                        safe = {key: check.get(key) for key in ('nodeName', 'status', 'delayMs')}
                        self._events.put(('node_progress', revision,
                                          {'check': safe, 'total': context.get('nodeCount', 0)}, None))
                context['_nodeChecks'] = portable.check_nodes(context, on_progress=progress)
            return context

        self._dispatch('detect', probe)

    def _finish_progress(self, result) -> None:
        if not isinstance(result, dict):
            return
        check, total = result.get('check'), result.get('total')
        if (not isinstance(check, dict) or not isinstance(check.get('nodeName'), str)
            or check.get('status') not in _NODE_STATUSES
            or isinstance(total, bool) or not isinstance(total, int) or total < 0):
            return
        self._progress_names.add(check['nodeName'])
        done = min(len(self._progress_names), total)
        self.window.show_progress(done, total)
        self.window.tell('正在测试节点 ' + str(done) + ' / ' + str(total) + '…',
                         '检测结果是当前时刻的连通性快照。')

    def _finish_detect(self, result: dict) -> None:
        if not result.get('ok'):
            self._clear_context()
            self.window.tell('探测未通过', '请处理探测提示后重试。')
            issues = result.get('issues')
            public_issues = [issue.strip() for issue in issues
                             if isinstance(issue, str) and issue.strip()] if isinstance(issues, list) else []
            self.window.error_dialog('\n'.join(public_issues) or '未找到支持的本机环境，请确认 Clash 已运行后重试。')
            return
        checks = result.get('_nodeChecks')
        if (not isinstance(checks, dict) or not isinstance(checks.get('checks'), list)
            or checks.get('total') != result.get('nodeCount')
            or len(checks['checks']) != checks.get('total')
            or any(not isinstance(row, dict) or not isinstance(row.get('nodeName'), str)
                   or row.get('status') not in _NODE_STATUSES for row in checks['checks'])):
            raise portable.PublicError('节点测试结果格式异常，请重新探测。')
        self._context = result
        self._checks = copy.deepcopy(checks)
        self.window.render_detect(result)
        self.window.tell('探测完成', '测试失败的节点也会保留。点击“生成备份”继续。')

    def generate(self) -> None:
        context = self._context
        if self._busy or not context or not context.get('ok') or not context.get('canGenerate'):
            return
        paths = self._paths()
        if not paths['output']:
            self.window.tell('请选择输出目录', '选择备份文件的保存位置后再生成。')
            self.window.error_dialog('请先选择备份输出目录。')
            return
        choices = None
        if context.get('needsPolicyReview'):
            rows = context.get('groupPolicies')
            if (not isinstance(rows, list) or not rows or any(
                not isinstance(row, dict) or not isinstance(row.get('name'), str)
                or not isinstance(row.get('choices'), list) or not row['choices']
                or any(choice not in _ACTIONS for choice in row['choices']) for row in rows)):
                self._operation_failed('分流动作信息不完整，请重新探测。')
                return
            try:
                choices = self.window.review_policies(rows)
            except Exception as error:
                self._operation_failed(_safe_error(error))
                return
            if choices is None:
                self.window.tell('已取消生成', '分流动作尚未确认，需要时可再次生成。')
                return
            if not isinstance(choices, dict):
                self._operation_failed('分流动作选择格式异常，请重新探测。')
                return
        self._generation = None
        self._checks = copy.deepcopy(context.get('_nodeChecks', {}))
        self.window.tell('正在生成备份…', '正在备份并校验配置，此步骤不会写回 Clash。')

        def work() -> dict:
            if choices is not None:
                portable.review_policies(context, choices)
            return portable.generate(context, output_directory=paths['output'],
                                     template_path=paths['template'] or None)

        self._dispatch('generate', work)

    def _finish_generate(self, result: dict) -> None:
        if (not isinstance(result.get('bakPath'), str) or not result['bakPath']
            or not isinstance(result.get('mapPath'), str) or not result['mapPath']
            or not isinstance(result.get('entries'), list)
            or not isinstance(result.get('needsApply'), bool)):
            raise portable.PublicError('备份生成结果格式异常，请重新探测。')
        self._generation = result
        self.window.render_generate(result, self._checks)
        if result.get('needsApply'):
            self.window.tell('备份已生成', '点击“写回 Clash 扩展配置”，使本地端口生效。')
        else:
            self.window.tell('备份已生成', '配置已生效，无需重复写回。请在 ZeroOmega 中恢复备份。')

    def apply(self) -> None:
        context = self._context
        if (self._busy or not context or not context.get('ok') or not context.get('canApply')
            or not self._generation or not self._generation.get('needsApply')):
            return
        try:
            confirmed = self.window.confirm_apply()
        except Exception as error:
            self._operation_failed(_safe_error(error))
            return
        if not confirmed:
            return
        self.window.tell('正在写回 Clash 扩展配置…', '正在检查持久扩展和本地入口的生效状态。')
        self._dispatch('apply', lambda: portable.apply(context))

    def _finish_apply(self, result: dict) -> None:
        if result.get('status') != 'installed-and-live':
            raise portable.PublicError('写回生效状态未通过校验，请重新探测。')
        if self._context:
            self._context['hasInstallation'] = True
        if self._generation:
            self._generation['needsApply'] = False
        warning = result.get('summaryWarning')
        known_warning = isinstance(warning, str) and warning in _SUMMARY_WARNINGS
        public = dict(result)
        if not known_warning:
            public.pop('summaryWarning', None)
        self.window.render_apply(public)
        self.window.tell('写回完成', warning if known_warning else
                         '已写回并生效。请在 ZeroOmega 中恢复生成的备份。')

    def rollback(self) -> None:
        context = self._context
        if self._busy or not context or not context.get('ok') or not context.get('hasInstallation'):
            return
        try:
            confirmed = self.window.confirm_rollback()
        except Exception as error:
            self._operation_failed(_safe_error(error))
            return
        if not confirmed:
            return
        self.window.tell('正在回滚本工具…', '正在恢复并校验原配置，请稍候。')
        self._dispatch('rollback', lambda: portable.rollback(context))

    def _finish_rollback(self, result: dict) -> None:
        if result.get('status') != 'rolled-back':
            raise portable.PublicError('回滚完成状态未通过校验，请重新探测。')
        self._clear_context()
        self.window.render_rollback()
        self.window.tell('回滚完成', '已有备份仍保留。再次使用前，请重新探测。')


def main() -> int:
    from PySide6.QtWidgets import QApplication
    from qt_view import Window

    application = QApplication.instance() or QApplication(sys.argv)
    window = Window(version=portable.VERSION)
    window.setWindowTitle('Clash 节点备份助手 ' + portable.VERSION)
    window._controller = Controller(window)
    window.show()
    return application.exec()


if __name__ == '__main__':
    main()
