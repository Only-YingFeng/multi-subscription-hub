"""Qt orchestration for the independent fork. No main-Clash mutations."""
from __future__ import annotations

from pathlib import Path
import re
from typing import Callable

from PySide6.QtCore import QObject, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QMessageBox, QSystemTrayIcon


class Worker(QThread):
    result = Signal(str, object)
    failed = Signal(str, str)
    progress = Signal(object)

    def __init__(self, operation: str, function: Callable, parent=None):
        super().__init__(parent)
        self.operation, self.function = operation, function

    def run(self):
        try:
            result = self.function(self.progress.emit)
            responses = [result]
            if isinstance(result, dict):
                responses += [result.get('discovery'), result.get('status')]
            for response in responses:
                if isinstance(response, dict) and response.get('ok') is False:
                    error = RuntimeError()
                    error.code = response.get('code', response.get('error', 'LOCAL_OPERATION_FAILED'))
                    raise error
            self.result.emit(self.operation, result)
        except Exception as error:
            code = getattr(error, 'code', type(error).__name__)
            code = str(code) if re.fullmatch(r'[A-Za-z0-9_]{1,90}', str(code)) else type(error).__name__
            self.failed.emit(self.operation, code)


class Controller(QObject):
    def __init__(self, view, backend, *, output_dir: Path):
        super().__init__(view)
        self.view, self.backend = view, backend
        self.sources, self.rows = [], []
        self.last_status = {'running': False}
        self.running_sources = []
        self.worker = None
        self._pending = None
        self._close_when_done = False
        self.policy_override = None
        self.output_dir = Path(output_dir)
        if hasattr(view, 'output_edit'):
            view.output_edit.setText(str(self.output_dir))
        elif hasattr(view, 'set_output_dir'):
            view.set_output_dir(str(self.output_dir))
        view.refreshRequested.connect(self.refresh)
        view.startRequested.connect(self.start)
        view.stopRequested.connect(self.request_stop)
        view.testRequested.connect(self.test)
        view.exportRequested.connect(self.export)
        view.outputBrowseRequested.connect(self.browse_output)
        view.copyPathRequested.connect(self.copy_path)
        view.openOutputRequested.connect(self.open_output)
        view.hideRequested.connect(self.hide)
        view.settingsRequested.connect(self.settings)
        view.prepareRequested.connect(lambda _ids: self.start())
        if hasattr(view, 'selectionChanged'):
            view.selectionChanged.connect(self.selection_changed)
        if hasattr(view, 'closeRequested'):
            view.closeRequested.connect(self.close)
        self.tray = QSystemTrayIcon(view.windowIcon(), self)
        self.tray.setToolTip('多订阅后台助手 · 浏览器独立线路')
        menu = QMenu()
        for title, function in [('打开主界面', self.show), ('停止浏览器后台', self.request_stop),
                                ('退出界面，后台继续运行', self.quit_interface)]:
            action = QAction(title, menu)
            action.triggered.connect(function)
            menu.addAction(action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show() if reason in (
            QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        self.timer = QTimer(self)
        self.timer.setInterval(15000)
        self.timer.timeout.connect(self.poll)
        self.timer.start()
        QTimer.singleShot(0, self.refresh)

    def _run(self, operation, function, message='', *, visible=True):
        if self.worker is not None:
            if operation != 'poll':
                self.view.set_message('正在处理上一项操作，请稍候。')
            return
        if visible:
            self.view.set_busy(True, message)
        self.worker = Worker(operation, function, self)
        self.worker.result.connect(self._result)
        self.worker.failed.connect(self._failed)
        self.worker.progress.connect(self._progress)
        self.worker.finished.connect(self._finished)
        self.worker.start()

    def _finished(self):
        worker, self.worker = self.worker, None
        worker.deleteLater()
        self.view.set_busy(False)
        self._update_status()
        pending, self._pending = self._pending, None
        if pending:
            QTimer.singleShot(0, pending)
        elif self._close_when_done:
            QApplication.instance().quit()

    def refresh(self):
        self._run('discover', lambda _p: {'discovery': self.backend.discover(), 'status': self.backend.status()},
                  '读取 Clash 已保存的订阅…')

    def poll(self):
        if not self.worker:
            self._run('poll', lambda _p: self.backend.status(), visible=False)

    def _normalize_rows(self, rows):
        source_names = {s['id']: s['name'] for s in self.sources}
        normalized = []
        for index, row in enumerate(rows or []):
            source_id = row.get('sourceId', row.get('source_id', ''))
            status = row.get('status', row.get('result', '未测试'))
            if 'ok' in row:
                status = '可用' if row['ok'] else '测试失败'
            status = {'ok': '可用', 'available': '可用', 'failed': '测试失败', 'timeout': '超时',
                      'untested': '未测试', 'inactive': '已失效'}.get(str(status), str(status))
            normalized.append({
                'id': row.get('id', row.get('nodeId', str(index))),
                'name': row.get('displayName', row.get('profileName', row.get('nodeName', row.get('name', '')))),
                'source': row.get('sourceName', row.get('source', source_names.get(source_id, ''))),
                'sourceId': source_id, 'port': row.get('port'), 'status': status,
                'delayMs': row.get('ms', row.get('latency', row.get('latencyMs', row.get('delayMs')))),
            })
        return normalized

    def _update_status(self):
        selected = self.view.get_selected_sources()
        running = bool(self.last_status.get('running'))
        changed = bool(self.last_status.get('pendingChanges') or
                       (running and sorted(selected) != sorted(self.running_sources)))
        healthy = bool(running and self.last_status.get('allListenersPrivate', False))
        status = dict(self.last_status)
        status['error'] = bool(self.last_status.get('error') or (running and not healthy))
        if running and not healthy:
            status['hint'] = '入口状态尚未通过核验，检测和导出已暂停'
        status.update(sourceCount=len(selected), nodeCount=len(self.rows),
                      canStart=bool(selected) and (not running or changed),
                      canTest=healthy and not changed,
                      canExport=healthy and not changed,
                      prepared=running, pendingChanges=changed)
        self.view.set_backend_status(status)

    def selection_changed(self, selected):
        if self.worker:
            return
        if not self.last_status.get('running') and hasattr(self.backend, 'preview'):
            self._run('preview', lambda _p: self.backend.preview(selected), visible=False)
        else:
            self._update_status()

    def start(self):
        selected = self.view.get_selected_sources()
        if not selected:
            self.view.set_message('先勾选至少一个可以读取的订阅。', error=True)
            return
        if self.last_status.get('running'):
            reply = QMessageBox.question(self.view, '应用订阅选择',
                '将更新本软件的浏览器后台，可能短暂中断使用这些入口的连接。\n电脑的 Clash 和系统代理保持原样。继续吗？',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
        self._run('prepare', lambda _p: self.backend.prepare(selected, policy_override=self.policy_override), '校验订阅与固定入口…')

    def _start_prepared(self):
        self._run('start', lambda _p: self.backend.start(), '启动独立浏览器后台…')

    def request_stop(self):
        if not self.last_status.get('running'):
            self.view.set_message('浏览器后台当前未运行。')
            return
        reply = QMessageBox.question(self.view, '停止浏览器后台',
            '停止后，使用本软件入口的浏览器代理请求会失败。\n电脑的 Clash 和系统代理保持原样。确认停止吗？',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self._run('stop', lambda _p: self.backend.stop(), '停止本软件后台…')

    def test(self):
        self._run('test', lambda progress: self.backend.test_nodes(progress=progress), '检测选定节点，不切换线路…')

    def export(self, output_dir=''):
        output_dir = output_dir or self.view.get_output_dir()
        if not output_dir:
            self.view.set_message('请选择输出目录。', error=True)
            return
        self._run('export', lambda _p: self.backend.export_bak(Path(output_dir)), '生成统一 ZeroOmega 备份…')

    def _result(self, operation, result):
        if operation == 'discover':
            discovery = result['discovery']
            self.sources = discovery.get('sources', [])
            selected = self.view.get_selected_sources() or result['status'].get('sourceIds', [])
            for source in self.sources:
                source['isBridge'] = bool(source.get('isFeiniao'))
                source['selected'] = source['id'] in selected if selected else bool(
                    source.get('available') and (source.get('current') or source.get('isFeiniao')))
            self.view.set_sources(self.sources)
            self.last_status = result['status']
            self.running_sources = self.last_status.get('sourceIds', self.last_status.get('sources', []))
            self.rows = self._normalize_rows(discovery.get('nodes', discovery.get('entries', [])))
            self.view.set_nodes(self.rows)
            self.view.set_message('已读取本地订阅。勾选后启动后台；订阅下载更新仍在 Clash 中完成。')
            if not self.rows and hasattr(self.backend, 'preview'):
                selected_ids = self.view.get_selected_sources()
                self._pending = lambda: self._run('preview', lambda _p: self.backend.preview(selected_ids), visible=False)
        elif operation == 'preview':
            rows = result if isinstance(result, list) else result.get('rows', result.get('entries', result.get('nodes', [])))
            self.rows = self._normalize_rows(rows)
            self.view.set_nodes(self.rows)
        elif operation == 'prepare':
            if result.get('policyIssues'):
                self._pending = lambda: self._ask_policy(result['policyIssues'])
                self.view.set_message('分流动作存在混合选项，请确认后继续。')
            elif result.get('prepared', True):
                self.rows = self._normalize_rows(result.get('entries', result.get('nodes', [])))
                self.view.set_nodes(self.rows)
                self._pending = self._start_prepared
            else:
                self.view.set_message('配置尚未通过校验，请查看分流设置。', error=True)
        elif operation == 'start':
            self.last_status = result
            if result.get('running') and result.get('allListenersPrivate', False):
                self.running_sources = result.get('sourceIds', self.view.get_selected_sources())
                self.view.set_message('浏览器后台已运行。检测节点后可生成并导入统一 .bak。电脑 Clash 保持原样。')
            else:
                self.last_status.update(running=False, error=True, allListenersPrivate=False)
                self.running_sources = []
                self.view.set_message('后台尚未确认正常运行，暂不能检测或导出。', error=True)
        elif operation == 'stop':
            self.last_status = result
            self.last_status['running'] = False
            self.view.set_message('浏览器后台已停止；固定端口登记保留，电脑 Clash 保持原样。')
        elif operation == 'poll':
            self.last_status = result
            if not result.get('running'):
                self.running_sources = []
        elif operation == 'test':
            rows = result if isinstance(result, list) else result.get('rows', result.get('nodes', result.get('entries', result.get('results', []))))
            self.rows = self._normalize_rows(rows)
            self.view.set_nodes(self.rows)
            passed = sum(x['status'] == '可用' for x in self.rows)
            self.view.set_message(f'本次检测完成：{passed}/{len(self.rows)} 可用。失败节点仍保留，选中后不会自动换线路。')
        elif operation == 'export':
            files = result.get('files', {})
            bak = files.get('bak', result.get('bak', result.get('bakPath', '')))
            mapping = files.get('mapping', result.get('map', result.get('mapPath', '')))
            self.view.set_export_paths(str(bak), str(mapping))
            self.view.set_message('统一备份已生成。请在各浏览器 ZeroOmega 中用“从备份文件恢复”导入。')
        self._update_status()

    def _progress(self, row):
        if not isinstance(row, dict):
            return
        normalized = self._normalize_rows([row])[0]
        found = next((i for i, value in enumerate(self.rows) if value['id'] == normalized['id']
                      or (value['port'] and value['port'] == normalized['port'])), None)
        if found is not None:
            self.rows[found].update(normalized)
            self.view.set_nodes(self.rows)

    def _failed(self, operation, code):
        descriptions = {
            'PORT_UNAVAILABLE': '固定端口被占用或被 Windows 保留，已停止应用。',
            'PORT_OCCUPIED': '固定端口被占用，已停止应用。',
            'POLICY_REQUIRED': '需要明确确认混合代理组的分流动作。',
            'CORE_START_FAILED': '后台启动失败；原 Clash 未修改。',
            'CORE_NOT_FOUND': '未找到标准 Mihomo 核心，请在设置中选择 Clash 的核心文件。',
            'CORE_FILE_UNREADABLE': '核心文件暂时无法读取，请检查文件与权限。',
            'CORE_COPY_FAILED': '独立核心复制失败，原核心文件保持完整。',
            'CORE_COPY_CHANGED': '核心复制期间内容发生变化，请稍后重新应用。',
            'CORE_UPDATE_REQUIRES_EXPLICIT_ACTION': 'Clash 核心版本已变化。请保留现有私有目录，按使用说明单独处理核心升级。',
            'NOT_APPLIED': '最新选择尚未应用或后台已停止，暂不能检测或导出。',
            'PROCESS_IDENTITY_MISMATCH': '后台进程身份不符，已停止操作以避免误停其他程序。',
        }
        self.view.set_message(descriptions.get(code, f'操作未完成（{code}）。原 Clash 未修改。'), error=True)
        self._pending = None
        if operation in {'poll', 'discover', 'start', 'stop'}:
            self.last_status.update(running=False, error=True, allListenersPrivate=False)
            self.running_sources = []
        self._update_status()

    def _ask_policy(self, issues):
        from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QVBoxLayout
        dialog = QDialog(self.view)
        dialog.setWindowTitle('确认浏览器分流动作')
        dialog.setMinimumWidth(620)
        layout = QVBoxLayout(dialog)
        label = QLabel('这些组同时包含不同动作。请选择浏览器入口命中它们时的处理方式；不会改变电脑原代理组。')
        label.setWordWrap(True)
        layout.addWidget(label)
        form = QFormLayout()
        combos = {}
        for issue in issues:
            target = issue.get('target', issue.get('name', ''))
            combo = QComboBox()
            combo.addItem('请选择…', None)
            for text, value in [('浏览器所选节点', 'PROXY'), ('直连', 'DIRECT'), ('拒绝', 'REJECT')]:
                combo.addItem(text, value)
            form.addRow(target, combo)
            combos[target] = combo
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('确认并继续')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(lambda: dialog.accept() if all(c.currentData() for c in combos.values()) else None)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            overrides = {name: combo.currentData() for name, combo in combos.items()}
            selected = self.view.get_selected_sources()
            self._run('prepare', lambda _p: self.backend.prepare(selected, policy_override=overrides), '校验已确认的分流动作…')

    def browse_output(self):
        directory = QFileDialog.getExistingDirectory(self.view, '选择输出目录', self.view.get_output_dir())
        if directory:
            if hasattr(self.view, 'set_output_dir'):
                self.view.set_output_dir(directory)
            elif hasattr(self.view, 'output_edit'):
                self.view.output_edit.setText(directory)

    def copy_path(self, path):
        if path:
            QApplication.clipboard().setText(str(path))
            self.view.set_message('文件完整路径已复制。')

    def open_output(self):
        directory = Path(self.view.get_output_dir())
        if directory.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))
        else:
            self.view.set_message('输出目录尚不存在，请先生成备份。')

    def settings(self):
        if self.worker:
            return
        values = self.view.show_settings({'privateRoot': str(self.backend.root),
            'clashData': str(self.backend.clash_dir), 'bridgeRoot': str(self.backend.bridge_root),
            'corePath': str(self.backend.core_path or '')})
        if not values:
            return
        # Relocating a live backend would orphan its process ownership record.
        if Path(values.get('privateRoot') or self.backend.root).resolve() != self.backend.root:
            self.view.set_message('私有目录由当前程序位置确定。需要迁移时，先停止后台，再移动完整软件目录。', error=True)
            return
        if self.last_status.get('running'):
            self.view.set_message('先停止本软件后台，再修改核心或数据目录。', error=True)
            return
        try:
            from backend import Backend
            import bridge_lifecycle as life
            replacement = Backend(self.backend.root,
                clash_dir=values.get('clashData') or None,
                core_path=values.get('corePath') or None,
                bridge_root=values.get('bridgeRoot') or None)
            replacement._ensure_root()
            life.atomic_json(replacement._path('ui-settings.json'), {
                'clashData': str(replacement.clash_dir),
                'corePath': str(replacement.core_path or ''),
                'bridgeRoot': str(replacement.bridge_root)})
            self.backend = replacement
            self.policy_override = values.get('policies') or None
            self.refresh()
        except Exception:
            self.view.set_message('设置未保存，请检查目录和权限。', error=True)

    def hide(self):
        if self.tray.isVisible():
            self.view.hide()
            self.tray.showMessage('多订阅后台助手', '后台继续运行。点击托盘图标可打开界面。',
                                  QSystemTrayIcon.MessageIcon.Information, 3000)
        else:
            self.view.showMinimized()

    def show(self):
        self.view.showNormal()
        self.view.raise_()
        self.view.activateWindow()

    def close(self):
        if self.last_status.get('running') or self.worker:
            self.hide()
        else:
            self.quit_interface()

    def quit_interface(self):
        if self.worker:
            self.view.set_message('正在完成后台操作，结束后将退出界面。')
            self._close_when_done = True
        else:
            QApplication.instance().quit()
