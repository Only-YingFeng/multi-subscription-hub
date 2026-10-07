"""Synthetic fixtures only: all backend destinations are temporary directories."""
from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import shutil
import sys
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parent.parent
sys.dont_write_bytecode = True


def load_backend(root):
    """Import without using the user's APPDATA/LOCALAPPDATA defaults."""
    sys.path.insert(0, str(APP_DIR))
    with patch.dict(os.environ, {
        'APPDATA': str(root / 'appdata'),
        'LOCALAPPDATA': str(root / 'localappdata'),
    }):
        spec = importlib.util.spec_from_file_location('manager', APP_DIR / 'manager.py')
        backend = importlib.util.module_from_spec(spec)
        sys.modules['manager'] = backend
        spec.loader.exec_module(backend)
    return backend


def load_portable():
    spec = importlib.util.spec_from_file_location('portable', APP_DIR / 'portable.py')
    portable = importlib.util.module_from_spec(spec)
    sys.modules['portable'] = portable
    spec.loader.exec_module(portable)
    return portable


@contextmanager
def backend_paths(backend, root):
    data, private, share, resources = [root / name for name in ('data', 'private', 'share', 'resources')]
    for directory in (data / 'profiles', private, share, resources):
        directory.mkdir(parents=True, exist_ok=True)
    for name in ('enhancement.js', 'js-runner.js'):
        shutil.copy2(APP_DIR / name, resources / name)
        # This also makes the fixture usable with the pre-portable manager.
        shutil.copy2(APP_DIR / name, share / name)
    node = shutil.which('node')
    if not node:
        raise RuntimeError('Node is required for the offline transform tests')
    with patch.multiple(backend, create=True, DATA=data, PRIVATE=private, HERE=share,
                        RESOURCES=resources, CORE=root / 'synthetic-core.exe',
                        VERGE=root / 'synthetic-verge.exe', NODE=Path(node),
                        LOCAL_PATHS=share / 'local-paths.json', TEMPLATE=share / 'no-template.bak',
                        POLICY_OVERRIDE=None), \
         patch.object(backend.urllib.request, 'urlopen', side_effect=AssertionError('Network calls are forbidden')), \
         patch.object(backend.socket, 'create_connection', side_effect=AssertionError('Socket connections are forbidden')):
        yield data, private, share


def report(suite, results):
    import json
    print(json.dumps({'suite': suite, 'passed': len(results), 'total': len(results),
                      'realApiCalls': 0, 'temporaryDataOnly': True}))
