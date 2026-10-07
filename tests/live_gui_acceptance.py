"""Opt-in native Windows GUI acceptance against the already-running hub."""
from pathlib import Path
import json
import sys
import time
import faulthandler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from backend import Backend
from controller import Controller
from view import Window, configure_application


def main():
    installation = Path(sys.argv[1]).resolve()
    diagnostic = (installation / 'private/logs/gui-stack.private.txt').open('w', encoding='utf8')
    faulthandler.dump_traceback_later(30, repeat=True, file=diagnostic)
    backend = Backend(installation / 'private')
    status = backend.status()
    print('status verified', flush=True)
    assert status.get('ok') and status.get('running') and status.get('allListenersPrivate')
    app = QApplication([])
    configure_application(app)
    window = Window('2.0.0')
    controller = Controller(window, backend, output_dir=installation / 'output')
    window.controller = controller
    window.show()
    print('native window shown', flush=True)
    window.raise_()
    window.activateWindow()
    def settle():
        deadline = time.monotonic() + 70
        while time.monotonic() < deadline:
            QTest.qWait(100)
            time.sleep(0.02)  # Let Python worker I/O run between Qt test waits.
            if controller.worker is None:
                QTest.qWait(200)
                if controller.worker is None:
                    return
        raise AssertionError('GUI worker timeout')
    settle()
    print('discovery settled', flush=True)
    assert len(window.get_selected_sources()) == 2
    assert window.table.rowCount() == 177
    # Populate the completed real network snapshot without repeating traffic.
    snapshot = json.loads((backend.root / 'logs/connectivity.private.json').read_text(encoding='utf8'))
    controller._result('test', snapshot)
    assert window.export_button.isEnabled()
    QTest.mouseClick(window.export_button, Qt.MouseButton.LeftButton)
    settle()
    print('export settled', flush=True)
    assert window.bak_edit.text().endswith('.bak')
    assert Path(window.bak_edit.toolTip()).is_file()
    captures = ROOT / 'design-qa-assets'
    captures.mkdir(exist_ok=True)
    checks = {'nativeWindows': True, 'subscriptions': 2, 'nodes': 177,
              'guiExport': True, 'testedSnapshot': True, 'sizes': []}
    for width, height in ((1200, 800), (1080, 720), (1000, 640)):
        window.resize(width, height)
        QTest.qWait(350)
        screenshot = window.screen().grabWindow(int(window.winId()))
        assert not screenshot.isNull()
        screenshot.save(str(captures / f'hub-live-exported-{width}x{height}.png'))
        assert window.table.viewport().width() > 600
        assert window.export_button.isVisible() and window.export_button.geometry().height() >= 30
        checks['sizes'].append([width, height])
    selected = window.get_selected_sources()
    window.set_selected_sources(selected[:1])
    QTest.qWait(300)
    assert not window.export_button.isEnabled()
    assert window.prepare_button.isEnabled()
    window.screen().grabWindow(int(window.winId())).save(str(captures / 'hub-live-pending-1080x720.png'))
    window.set_selected_sources(selected)
    QTest.qWait(200)
    assert window.export_button.isEnabled()
    checks['pendingSelectionGuard'] = True
    window.search_edit.setText(snapshot['rows'][0]['name'][:5])
    QTest.qWait(200)
    assert any(window.table.isRowHidden(i) for i in range(window.table.rowCount()))
    checks['search'] = True
    window.search_edit.clear()
    backend._path('logs/gui-acceptance.private.json').write_text(json.dumps(checks, indent=2), encoding='utf8')
    print(json.dumps(checks), flush=True)
    controller.timer.stop()
    controller.tray.hide()
    app.quit()
    faulthandler.cancel_dump_traceback_later()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
