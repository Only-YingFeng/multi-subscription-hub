"""Fixed public release inputs; no user configuration may enter a release."""
import hashlib
import json
from pathlib import Path

# Filled from reviewed, version-pinned license materials before a release build.
UI_LICENSE_MANIFEST_SHA256 = 'ab102c5b38563694ba14f140cd086bcb0a286e3da6ba37582909bd81bd00bd24'

SOURCE_FILES = (
    'app.py', 'qt_view.py', 'qt_controller.py', 'manager.py', 'portable.py', 'zeroomega.py',
    'build.py', 'release_layout.py', 'ui-license-manifest.json', 'requirements-build.txt',
    'README.md', 'Node_LICENSE.txt', 'enhancement.js', 'js-runner.js',
    'assets/app.ico', 'assets/app-icon-64.png', 'assets/app-icon-32.png',
    'assets/app-icon-source.png', 'assets/export-icon.py',
)


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
