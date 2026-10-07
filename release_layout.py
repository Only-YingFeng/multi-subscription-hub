"""Fixed public release inputs; no user configuration may enter a release."""
import hashlib
import json
from pathlib import Path

# Filled from reviewed, version-pinned license materials before a release build.
UI_LICENSE_MANIFEST_SHA256 = 'ab102c5b38563694ba14f140cd086bcb0a286e3da6ba37582909bd81bd00bd24'

SOURCE_FILES = (
    'app.py', 'view.py', 'controller.py', 'qt_view.py', 'backend.py', 'bridge_lifecycle.py', 'clash_api.py', 'zeroomega.py',
    'build.py', 'release_layout.py', 'ui-license-manifest.json', 'requirements-build.txt',
    'README.md', 'FORK_ORIGIN.md', 'qt_view_tests.py',
    'tests/README.md', 'tests/test_backend_contract.py', 'tests/test_controller.py', 'tests/live_gui_acceptance.py',
    'tests/live_routing_acceptance.py', 'tests/live_deny_acceptance.py',
    'assets/app.ico', 'assets/app-icon-64.png', 'assets/app-icon-32.png',
    'assets/app-icon-source.png', 'assets/export-icon.py',
)

# The retained pre-fork archive is a fixed list, not a recursive copy of the
# workspace. Runtime data, logs, build output and private credentials stay out.
ORIGINAL_SOURCE_FILES = (
    'app.py', 'gui.py', 'qt_view.py', 'qt_controller.py', 'manager.py', 'portable.py',
    'zeroomega.py', 'build.py', 'release_layout.py', 'build_feiniao_bridge.py',
    'feiniao_bridge.py', 'install-ui-runtime.py', 'enhancement.js', 'js-runner.js',
    'ui-license-manifest.json', 'requirements-build.txt', 'README.md', 'Node_LICENSE.txt',
    'assets/app.ico', 'assets/app-icon-64.png', 'assets/app-icon-32.png',
    'assets/app-icon-source.png', 'assets/export-icon.py',
    'tests/test_support.py', 'tests/test_feiniao_bridge.py', 'tests/run-tests.py',
    'tests/release-boundary-tests.py', 'tests/release-artifacts.py', 'tests/regression-tests.js',
    'tests/real-node-checks.py', 'tests/real-core-transaction.py', 'tests/README.md',
    'tests/qt-view-tests.py', 'tests/qt-controller-tests.py', 'tests/portable-interface-tests.py',
    'tests/packaged-smoke.py', 'tests/mapping-update-tests.py', 'tests/manager-transaction-tests.py',
    'tests/gui-tests.py', 'tests/gui-readonly-snapshot.py', 'tests/connectivity-tests.py',
)


def public_source_files(source, *, include_original=True):
    """Return checked public upgrade inputs and the reviewed license materials."""
    source = Path(source).resolve()
    result = []
    planned = list(SOURCE_FILES)
    if include_original:
        planned.extend('original-source/' + name for name in ORIGINAL_SOURCE_FILES)
    for name in planned:
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or ':' in str(relative):
            raise RuntimeError('Invalid public source path')
        current = source
        for part in relative.parts:
            current /= part
            if current.is_symlink() or current.is_junction():
                raise RuntimeError('Links are not permitted in public source inputs')
        if not current.is_file() or not current.resolve().is_relative_to(source):
            raise RuntimeError('Missing or unsafe public source input')
        result.append(current)
    result.extend(licensed_files(source))
    if include_original:
        result.extend(licensed_files(source / 'original-source'))
    if len(set(result)) != len(result):
        raise RuntimeError('Duplicate public source input')
    return result


def licensed_files(source):
    source = Path(source).resolve()
    manifest = source / 'ui-license-manifest.json'
    if hashlib.sha256(manifest.read_bytes()).hexdigest() != UI_LICENSE_MANIFEST_SHA256:
        raise RuntimeError('UI license manifest differs from the reviewed release baseline')
    expected = json.loads(manifest.read_text(encoding='utf8'))['files']
    root = source / 'licenses-ui'
    if root.is_symlink() or root.is_junction():
        raise RuntimeError('Links are not permitted for the release license directory')
    actual = set()
    for path in root.rglob('*'):
        if path.is_symlink() or path.is_junction():
            raise RuntimeError('Links are not permitted in the release license directory')
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
    if actual != set(expected):
        raise RuntimeError('UI license directory contains unexpected or missing files')
    result = []
    for name, digest in expected.items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise RuntimeError('Invalid release license path')
        path = root / relative
        if not path.resolve().is_relative_to(root.resolve()):
            raise RuntimeError('Release license path leaves its source directory')
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError('Release license file differs from its reviewed digest')
        result.append(path)
    return result
