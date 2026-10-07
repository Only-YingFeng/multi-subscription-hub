"""Independent multi-subscription fork of Clash 节点备份助手."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import re
import sys

VERSION = '2.0.0'


def paths():
    resource = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    installation = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
    return resource, installation


def main(argv=None):
    parser = argparse.ArgumentParser(description='多订阅后台助手')
    parser.add_argument('--private-root', type=Path)
    parser.add_argument('--clash-dir', type=Path)
    parser.add_argument('--core', type=Path)
    parser.add_argument('--bridge-root', type=Path)
    parser.add_argument('--sources', default='')
    parser.add_argument('--result-json', type=Path)
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--discover', action='store_true')
    parser.add_argument('--start', action='store_true')
    parser.add_argument('--stop', action='store_true')
    parser.add_argument('--export', type=Path)
    args = parser.parse_args(argv)
    resource, installation = paths()
    root = args.private_root or installation / 'private'
    preferences = {}
    try:
        preferences = json.loads((root / 'ui-settings.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        pass
    from backend import Backend
    backend = Backend(root=root, clash_dir=args.clash_dir or preferences.get('clashData'),
        core_path=args.core or preferences.get('corePath'), bridge_root=args.bridge_root or preferences.get('bridgeRoot'))
    if any((args.status, args.discover, args.start, args.stop, args.export)):
        try:
            if args.discover:
                result = backend.discover()
            elif args.stop:
                result = backend.stop()
            elif args.start:
                if args.sources:
                    result = backend.prepare([s for s in args.sources.split(',') if s])
                    if result.get('ok') is False:
                        error = RuntimeError()
                        error.code = result.get('code', 'LOCAL_OPERATION_FAILED')
                        raise error
                    if result.get('policyIssues') or result.get('prepared') is False:
                        raise RuntimeError('POLICY_REQUIRED')
                result = backend.start()
            elif args.export:
                result = backend.export_bak(args.export)
            else:
                result = backend.status()
            result = {'ok': True, **result}
            exit_code = 0 if result.get('ok') else 1
        except Exception as error:
            code = str(getattr(error, 'code', type(error).__name__))
            code = code if re.fullmatch(r'[A-Za-z0-9_]{1,90}', code) else type(error).__name__
            result, exit_code = {'ok': False, 'error': code}, 1
        if args.result_json:
            args.result_json.parent.mkdir(parents=True, exist_ok=True)
            args.result_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        elif sys.stdout:
            print(json.dumps(result, ensure_ascii=True))
        return exit_code
    # Some host environments have incompatible ICU/PDF DLLs on PATH. Qt uses its
    # own packaged/runtime DLLs; keep unrelated native tool directories out.
    windows = Path(os.environ.get('SystemRoot', r'C:\Windows'))
    os.environ['PATH'] = os.pathsep.join([str(Path(sys.executable).parent), str(Path(sys.base_prefix)), str(windows/'System32'), str(windows)])
    if os.name == 'nt':
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Codex.MultiSubscriptionHub.2')
    from PySide6.QtGui import QFont, QIcon
    from PySide6.QtWidgets import QApplication
    from view import Window, configure_application
    from controller import Controller
    qt = QApplication.instance() or QApplication(sys.argv[:1])
    qt.setApplicationName('多订阅后台助手')
    qt.setApplicationVersion(VERSION)
    qt.setQuitOnLastWindowClosed(False)
    qt.setFont(QFont('Microsoft YaHei UI', 10))
    configure_application(qt)
    icon = QIcon(str(resource / 'assets' / 'app.ico'))
    qt.setWindowIcon(icon)
    window = Window(VERSION)
    window.setWindowIcon(icon)
    window.setWindowTitle('多订阅后台助手 ' + VERSION)
    controller = Controller(window, backend, output_dir=installation/'output')
    window.controller = controller
    window.show()
    return qt.exec()


if __name__ == '__main__':
    raise SystemExit(main())
