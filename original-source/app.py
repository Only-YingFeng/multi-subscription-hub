"""GUI entry and credential-safe command line diagnostics for packaged builds."""
import argparse
import ctypes
import json
import os
from pathlib import Path
import platform
import sys

# Python 3.13's Windows WMI probe can hang on some PCs. The documented platform
# fallback uses the native Windows version/architecture instead; process discovery
# is handled explicitly and never uses WMI.
platform._wmi = None

import manager
import portable


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--detect-json', metavar='FILE')
    parser.add_argument('--generate', action='store_true')
    parser.add_argument('--output')
    parser.add_argument('--result-json', metavar='FILE')
    parser.add_argument('--data-directory')
    parser.add_argument('--clash-directory')
    parser.add_argument('--state-directory')
    parser.add_argument('--template')
    parser.add_argument('--self-test-json', metavar='FILE')
    args = parser.parse_args()
    if not (args.detect_json or args.generate or args.self_test_json):
        if os.name == 'nt':
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Codex.ClashZeroOmega')
        import qt_controller
        return qt_controller.main()
    result = None
    try:
        if args.self_test_json:
            from PySide6.QtWidgets import QApplication
            from qt_view import Window
            from qt_controller import Controller
            import qtawesome
            ui_application = QApplication.instance() or QApplication([])
            ui_window = Window(version=portable.VERSION)
            ui_controller = Controller(ui_window)
            ui_ready = not ui_window.windowIcon().isNull() and not qtawesome.icon('mdi6.folder-outline').isNull()
            ui_metrics = {'logicalWidth': ui_window.width(), 'logicalHeight': ui_window.height(),
                          'devicePixelRatio': ui_window.devicePixelRatioF(),
                          'logicalDpi': ui_window.logicalDpiX()}
            ui_controller._timer.stop()
            ui_window.close()
            proc = manager.run([str(manager.NODE), '--version'], capture_output=True, timeout=15)
            icon_ready = all((manager.RESOURCES / 'assets' / f).is_file()
                             for f in ('app.ico', 'app-icon-64.png', 'app-icon-32.png'))
            resources_ready = ui_ready and icon_ready and all((manager.RESOURCES / f).is_file() for f in ('enhancement.js', 'js-runner.js'))
            result = {'ok': proc.returncode == 0 and resources_ready, 'appVersion': portable.VERSION,
                      'bundled': bool(getattr(sys, 'frozen', False)),
                      'nodeVersion': proc.stdout.decode(errors='replace').strip(),
                      'resourcesReady': resources_ready, 'iconReady': icon_ready, 'qtUiReady': ui_ready,
                      'uiMetrics': ui_metrics}
        else:
            context = portable.detect(args.data_directory, args.clash_directory, args.state_directory)
            result = portable.public_summary(context)
            if args.generate:
                if not args.output:
                    raise RuntimeError('生成时必须指定输出目录。')
                result = {'environment': result, 'generation': portable.generate(context, args.output, args.template)}
    except Exception as error:
        result = {'ok': False, 'issues': [portable.safe_error(error)]}
    target = args.self_test_json or args.result_json or args.detect_json
    if target:
        Path(target).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    elif sys.stdout:
        sys.stdout.reconfigure(encoding='utf8')
        print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get('ok') or 'generation' in result else 1


if __name__ == '__main__':
    sys.exit(main())
