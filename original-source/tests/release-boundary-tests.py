"""Release privacy boundaries, with temporary synthetic tampering only."""
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import unittest

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from release_layout import SOURCE_FILES, licensed_files


class ReleaseBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix='czo-release-boundary-')
        self.root = Path(self.temporary.name)
        shutil.copytree(SOURCE / 'licenses-ui', self.root / 'licenses-ui')
        shutil.copy2(SOURCE / 'ui-license-manifest.json', self.root / 'ui-license-manifest.json')

    def tearDown(self):
        self.temporary.cleanup()

    def test_reviewed_inputs_are_accepted(self):
        self.assertEqual(len(licensed_files(self.root)), 117)

    def test_unexpected_private_named_file_is_rejected(self):
        (self.root / 'licenses-ui' / 'subscription.bak').write_text('synthetic test data', encoding='utf8')
        with self.assertRaises(RuntimeError):
            licensed_files(self.root)

    def test_missing_license_is_rejected(self):
        (self.root / 'licenses-ui' / 'NOTICE.md').unlink()
        with self.assertRaises(RuntimeError):
            licensed_files(self.root)

    def test_private_data_in_known_filename_is_rejected(self):
        (self.root / 'licenses-ui' / 'NOTICE.md').write_text('synthetic test data', encoding='utf8')
        with self.assertRaises(RuntimeError):
            licensed_files(self.root)

    def test_whitelist_manifest_tampering_is_rejected(self):
        manifest = self.root / 'ui-license-manifest.json'
        value = json.loads(manifest.read_text(encoding='utf8'))
        value['files']['subscription.bak'] = 'synthetic'
        manifest.write_text(json.dumps(value), encoding='utf8')
        with self.assertRaises(RuntimeError):
            licensed_files(self.root)

    def test_public_source_list_excludes_user_state_and_runtime(self):
        self.assertIn('qt_view.py', SOURCE_FILES)
        self.assertIn('requirements-build.txt', SOURCE_FILES)
        self.assertFalse(any(Path(name).suffix in ('.bak', '.yaml', '.yml') for name in SOURCE_FILES))
        self.assertFalse(any(part in name for name in SOURCE_FILES for part in ('.venv', 'registry.json', 'installed.json')))


if __name__ == '__main__':
    unittest.main(verbosity=2)
