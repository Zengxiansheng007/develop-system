import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from release_bundle import build, inventory, verify

class ReleaseTests(unittest.TestCase):
    def fixture(self, root):
        source = root / '.agents/skills/example'
        source.mkdir(parents=True)
        (source / 'SKILL.md').write_text('example', encoding='utf-8')
        (source / 'LICENSE').write_text('MIT', encoding='utf-8')
        (root / 'LICENSE').write_text('MIT', encoding='utf-8')
        (root / 'CHANGELOG.md').write_text('test', encoding='utf-8')
        (root / 'release-manifest.json').write_text(json.dumps({'schema_version': 1, 'skills': ['example']}))
        return source

    def test_reproducible_allowlisted_archive_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.fixture(root)
            (root / '.local').mkdir()
            (root / '.local/private.txt').write_text('must not ship')
            a = build(root, root / 'one', '0.1.0', 'a' * 40)
            b = build(root, root / 'two', '0.1.0', 'a' * 40)
            self.assertEqual(hashlib.sha256(a.read_bytes()).digest(), hashlib.sha256(b.read_bytes()).digest())
            self.assertNotIn('.local/private.txt', verify(a)['files'])
            bad = root / 'bad.zip'
            with zipfile.ZipFile(a) as original, zipfile.ZipFile(bad, 'w') as changed:
                for name in original.namelist():
                    changed.writestr(name, b'changed' if name.endswith('SKILL.md') else original.read(name))
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                verify(bad)

    def test_missing_license_and_secret_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = self.fixture(root)
            (source / 'LICENSE').unlink()
            with self.assertRaises(ValueError):
                inventory(root)
            (source / 'LICENSE').write_text('MIT')
            (source / 'bad.txt').write_text('gh' + 'p_' + 'x' * 30)
            with self.assertRaisesRegex(ValueError, 'Credential-like'):
                inventory(root)

    def test_manifest_cannot_escape_skill_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.fixture(root)
            (root / 'release-manifest.json').write_text(json.dumps({'schema_version': 1, 'skills': ['../private']}))
            with self.assertRaises(ValueError):
                inventory(root)
