"""Local reproducible packaging; never bundles subscription or user state files."""
import argparse
import ast
import os
from pathlib import Path
import platform
import shutil
import sys
import urllib.request

sys.stdout.reconfigure(encoding='utf8')
sys.stderr.reconfigure(encoding='utf8')
platform._wmi = None
import PyInstaller.__main__
import PyInstaller
import yaml
from release_layout import licensed_files


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--node', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    assets = source / 'assets'
    if not all((assets / name).is_file() for name in ('app.ico', 'app-icon-64.png', 'app-icon-32.png')):
        raise RuntimeError('Application icon assets are missing')
    output = Path(args.output).resolve()
    build = output.parent / ('build-' + output.name)
    output.mkdir(parents=True, exist_ok=True)
    bootstrap = build / 'bootstrap'
    bootstrap.mkdir(parents=True, exist_ok=True)
    (bootstrap / 'sitecustomize.py').write_text('import platform\nplatform._wmi = None\n', encoding='utf8')
    os.environ['PYTHONPATH'] = str(bootstrap) + os.pathsep + os.environ.get('PYTHONPATH', '')
    # Qt expects Windows' ICU ABI. Unrelated image/PDF tools on the caller's PATH
    # can supply an incompatible icuuc.dll; they must never enter this EXE.
    windows = Path(os.environ['SystemRoot'])
    os.environ['PATH'] = os.pathsep.join([str(Path(sys.executable).parent), str(Path(sys.base_prefix)),
                                        str(windows / 'System32'), str(windows)])
    licenses = output / '第三方许可'
    licenses.mkdir(exist_ok=True)
    node_license = source / 'Node_LICENSE.txt'
    if not node_license.exists():
        with urllib.request.urlopen('https://raw.githubusercontent.com/nodejs/node/v25.2.1/LICENSE', timeout=30) as response:
            node_license.write_bytes(response.read())
    shutil.copy2(node_license, licenses / 'Node_LICENSE.txt')
    shutil.copy2(Path(sys.base_prefix) / 'LICENSE.txt', licenses / 'Python_LICENSE.txt')
    import importlib.metadata
    for package, filename in (('PyInstaller', 'COPYING.txt'), ('PyYAML', 'LICENSE')):
        dist = importlib.metadata.distribution(package)
        license_path = next(Path(dist.locate_file(f)) for f in dist.files if str(f).endswith('/licenses/' + filename))
        shutil.copy2(license_path, licenses / (package + '_LICENSE.txt'))
    ui_licenses = source / 'licenses-ui'
    for original in licensed_files(source):
        target = licenses / 'UI' / original.relative_to(ui_licenses)
        if target.exists() and target.read_bytes() != original.read_bytes():
            raise RuntimeError('Existing release license material differs; use a new release directory')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, target)
    expected_ui = {original.relative_to(ui_licenses) for original in licensed_files(source)}
    if {p.relative_to(licenses / 'UI') for p in (licenses / 'UI').rglob('*') if p.is_file()} != expected_ui:
        raise RuntimeError('Unexpected UI license material exists in the release directory')
    PyInstaller.__main__.run([
        '--onefile', '--windowed', '--noupx', '--name', 'ClashZeroOmega',
        '--icon', str(assets / 'app.ico'),
        '--distpath', str(output), '--workpath', str(build / 'pyinstaller'),
        '--specpath', str(build), '--paths', str(source),
        '--add-binary', str(Path(args.node).resolve()) + ';.',
        '--add-data', str(source / 'enhancement.js') + ';.',
        '--add-data', str(source / 'js-runner.js') + ';.',
        '--add-data', str(assets / 'app.ico') + ';assets',
        '--add-data', str(assets / 'app-icon-64.png') + ';assets',
        '--add-data', str(assets / 'app-icon-32.png') + ';assets',
        '--add-data', str(assets / 'app-icon-source.png') + ';assets',
        '--add-data', str(licenses) + ';licenses',
        '--collect-data', 'qtawesome',
        '--exclude-module', 'sitecustomize',
        '--exclude-module', 'tkinter', '--exclude-module', 'PySide6.QtQuick',
        '--exclude-module', 'PySide6.QtQml', '--exclude-module', 'PySide6.QtQuickWidgets',
        '--exclude-module', 'PySide6.QtQuickControls2', '--exclude-module', 'PySide6.QtDesigner',
        '--exclude-module', 'PySide6.QtUiTools', '--exclude-module', 'PySide6.QtHelp',
        '--exclude-module', 'PySide6.QtSql', '--exclude-module', 'PySide6.QtTest',
        '--exclude-module', 'numpy', '--exclude-module', 'matplotlib',
        '--exclude-module', 'pandas', '--exclude-module', 'IPython',
        '--exclude-module', 'pytest', '--exclude-module', 'scipy',
        str(source / 'app.py'),
    ])
    analysis = ast.literal_eval((build / 'pyinstaller' / 'ClashZeroOmega' / 'Analysis-00.toc').read_text(encoding='utf8'))
    binaries = [entry for section in analysis if isinstance(section, list)
                for entry in section if isinstance(entry, tuple) and len(entry) == 3 and entry[2] == 'BINARY']
    allowed = [Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve(), windows.resolve()]
    node = Path(args.node).resolve()
    for destination, origin, _kind in binaries:
        origin = Path(origin).resolve()
        if origin != node and not any(origin.is_relative_to(root) for root in allowed):
            raise RuntimeError('Unexpected third-party native dependency entered the release')
        if Path(destination).name.lower() == 'icuuc.dll' and not origin.is_relative_to(windows.resolve()):
            raise RuntimeError('A non-Windows ICU library entered the release')


if __name__ == '__main__':
    main()
