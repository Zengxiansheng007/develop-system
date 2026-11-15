"""Public restoration seam; textual recovery is not model qualification."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import release_bundle

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'tools/verification/fixtures/implement-upstream.md'


class RestoreTests(unittest.TestCase):
    def fixture(self, root):
        skills = root / 'skills'
        release_bundle.stage_skills(ROOT, skills)
        target = skills / 'implement/SKILL.md'
        local = target.read_bytes() + b'\nLocal customization: retain issue-specific review notes.\n'
        target.write_bytes(local)
        candidate = root / 'candidate.md'
        candidate.write_bytes(BASE.read_bytes().replace(b'Use /tdd where possible,', b'Keep task evidence local.\n\nUse /tdd where possible,'))
        return skills, target, local, candidate

    def call(self, skills, mode, snapshot, *args):
        return subprocess.run([sys.executable, '-B', str(skills / 'implement-runtime/scripts/restore.py'),
                               mode, '--skills-root', str(skills), '--snapshot', str(snapshot), *args],
                              capture_output=True, text=True, timeout=30)

    def prepare(self, skills, snapshot, candidate):
        return self.call(skills, 'prepare', snapshot, '--base', str(BASE), '--candidate', str(candidate),
                         '--review', 'test fixture synthetic upstream addition; runtime qualification pending')

    def test_missing_new_reference_blocks_preparation_without_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            skills, target, local, candidate = self.fixture(root)
            candidate.write_bytes(candidate.read_bytes().replace(b'Keep task evidence local.', b'Read [missing policy](../missing/SKILL.md).'))
            proc = self.prepare(skills, root / 'snapshot', candidate)
            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn('reference', proc.stdout.lower())
            self.assertEqual(target.read_bytes(), local)
            self.assertFalse((root / 'snapshot').exists())

    def test_reference_style_links_fail_explicitly(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            skills, target, local, candidate = self.fixture(root)
            candidate.write_bytes(candidate.read_bytes().replace(b'Keep task evidence local.',
                                  b'Read [policy][p].\n\n[p]: ../missing/SKILL.md'))
            proc = self.prepare(skills, root / 'snapshot', candidate)
            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertIn('reference-style', proc.stdout)
            self.assertEqual(target.read_bytes(), local)

    def test_additional_referenced_file_is_bound_against_drift(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            skills, target, local, candidate = self.fixture(root)
            candidate.write_bytes(candidate.read_bytes().replace(b'Keep task evidence local.',
                                  b'Read [diagnosis](../diagnosing-bugs/SKILL.md).'))
            snapshot = root / 'snapshot'
            proc = self.prepare(skills, snapshot, candidate)
            self.assertEqual(proc.returncode, 0, proc.stdout)
            target.write_bytes(candidate.read_bytes())
            dependency = skills / 'diagnosing-bugs/SKILL.md'
            dependency.write_bytes(dependency.read_bytes() + b'\nChanged after review.\n')
            proc = self.call(skills, 'restore', snapshot)
            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertEqual(target.read_bytes(), candidate.read_bytes())

    def test_snapshot_through_directory_link_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            skills, target, local, candidate = self.fixture(root)
            outside = root / 'outside'
            outside.mkdir()
            link = root / 'linked'
            if os.name == 'nt':
                result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)],
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            else:
                link.symlink_to(outside, target_is_directory=True)
            proc = self.prepare(skills, link / 'snapshot', candidate)
            self.assertNotEqual(proc.returncode, 0, proc.stdout)
            self.assertFalse((outside / 'snapshot').exists())
            self.assertEqual(target.read_bytes(), local)

    def test_conflicting_upstream_preserves_local_and_no_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            skills, target, local, candidate = self.fixture(root)
            candidate.write_bytes(BASE.read_bytes().replace(b'Once done, use /code-review to review the work.',
                                  b'Announce completion immediately; skip final review.'))
            proc = self.prepare(skills, root / 'snapshot', candidate)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn('Incompatible', proc.stdout)
            self.assertEqual(target.read_bytes(), local)
            self.assertFalse((root / 'snapshot').exists())

    def test_duplicate_partial_or_modified_hooks_fail_closed(self):
        for mutation in ('duplicate', 'partial', 'modified'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                skills, target, local, candidate = self.fixture(root)
                if mutation == 'duplicate':
                    local += b'\n<!-- BEGIN LOCAL IMPLEMENT RUNTIME v1 -->\n'
                elif mutation == 'partial':
                    local = local.replace(b'<!-- END LOCAL IMPLEMENT RUNTIME v1 -->', b'')
                else:
                    local = local.replace(b'missing extension is a blocker.', b'missing extension is optional.')
                target.write_bytes(local)
                proc = self.prepare(skills, root / 'snapshot', candidate)
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(target.read_bytes(), local)

    def test_restore_rejects_changed_dependency_and_unplanned_target(self):
        for mutation in ('dependency', 'target', 'snapshot'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                skills, target, local, candidate = self.fixture(root)
                snapshot = root / 'snapshot'
                self.assertEqual(self.prepare(skills, snapshot, candidate).returncode, 0)
                target.write_bytes(candidate.read_bytes())
                if mutation == 'dependency':
                    (skills / 'runtime-verify/SKILL.md').unlink()
                elif mutation == 'target':
                    target.write_bytes(candidate.read_bytes() + b'\nUnplanned edit.\n')
                else:
                    (snapshot / 'merged.md').write_bytes(b'corrupted')
                before = target.read_bytes()
                proc = self.call(skills, 'restore', snapshot)
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(target.read_bytes(), before)

    def test_snapshot_inside_skills_and_wrong_base_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            skills, target, local, candidate = self.fixture(root)
            self.assertNotEqual(self.prepare(skills, skills / 'snapshot', candidate).returncode, 0)
            proc = self.call(skills, 'prepare', root / 'snapshot', '--base', str(candidate),
                             '--candidate', str(candidate), '--review', 'test fixture')
            self.assertNotEqual(proc.returncode, 0)
            self.assertEqual(target.read_bytes(), local)

    def test_exact_upstream_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            skills, target, local, candidate = self.fixture(root)
            candidate.write_bytes(BASE.read_bytes())
            snapshot = root / 'snapshot'
            self.assertEqual(self.prepare(skills, snapshot, candidate).returncode, 0)
            target.write_bytes(candidate.read_bytes())
            self.assertEqual(self.call(skills, 'restore', snapshot).returncode, 0)
            self.assertEqual(target.read_bytes(), local)

    def test_changed_upstream_and_local_customizations_survive_idempotently(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            skills, target, local, candidate = self.fixture(root)
            snapshot = root / 'snapshot'
            proc = self.prepare(skills, snapshot, candidate)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(target.read_bytes(), local)
            target.write_bytes(candidate.read_bytes())  # actual upstream overwrite
            proc = self.call(skills, 'restore', snapshot)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            restored = target.read_bytes()
            self.assertIn(b'Keep task evidence local.', restored)
            self.assertIn(b'Local customization: retain issue-specific review notes.', restored)
            self.assertIn('Codex执行与回流约定'.encode(), restored)
            self.assertEqual(restored.count(b'<!-- BEGIN LOCAL IMPLEMENT RUNTIME v1 -->'), 1)
            self.assertEqual(json.loads(proc.stdout)['runtime_status'], 'unqualified')
            proc = self.call(skills, 'restore', snapshot)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(target.read_bytes(), restored)
            self.assertEqual(json.loads(proc.stdout)['action'], 'unchanged')


if __name__ == '__main__':
    unittest.main()
