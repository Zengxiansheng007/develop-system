"""Exercise the qualification entry using tiny public Python test modules."""
import json
import importlib.util
from pathlib import Path
import sys
import subprocess
import os
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # Select the repository-owned qualification entry.


class IssueSyncQualificationTests(unittest.TestCase):
    def tearDown(self):
        for name in list(sys.modules):
            if name.startswith('test_qualification_'):
                sys.modules.pop(name)  # Temporary fixture modules must not survive into another independent scenario.

    def test_explicit_scripts_execute_and_record_the_actual_test_source(self):
        from qualify_issue_sync import qualify
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scripts = root / 'chosen'
            scripts.mkdir()
            (scripts / 'test_qualification_fixture.py').write_text(
                'import unittest\nclass Example(unittest.TestCase):\n def test_literal(self): self.assertEqual(2 + 2, 4)\n', encoding='utf-8')  # This fixture tests tool execution, not Spec14 acceptance.
            report = root / 'report.json'
            result = qualify(scripts, 'test_qualification_fixture', report)
            self.assertEqual(result['status'], 'passed')
            self.assertEqual(result['tests'], [{'id': 'test_qualification_fixture.Example.test_literal', 'status': 'passed'}])
            self.assertEqual(result['loaded']['test_qualification_fixture']['path'], str((scripts / 'test_qualification_fixture.py').resolve()))
            self.assertEqual(json.loads(report.read_text(encoding='utf-8'))['tests'], result['tests'])

    def test_foreign_cached_module_is_rejected_before_any_selected_test_runs(self):
        from qualify_issue_sync import qualify
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            chosen = root / 'chosen'
            chosen.mkdir()
            name = 'test_qualification_foreign'
            foreign = root / (name + '.py')
            text = 'import unittest\nclass Example(unittest.TestCase):\n def test_literal(self): self.assertTrue(True)\n'
            foreign.write_text(text, encoding='utf-8')
            (chosen / foreign.name).write_text(text, encoding='utf-8')
            spec = importlib.util.spec_from_file_location(name, foreign)
            value = importlib.util.module_from_spec(spec)
            sys.modules[name] = value
            spec.loader.exec_module(value)  # An actual foreign import reproduces accidental source/package mixing.
            with self.assertRaisesRegex(ValueError, 'Foreign loaded module'):
                qualify(chosen, name, root / 'report.json')
            self.assertFalse((root / 'report.json').exists())

    def test_failures_errors_subtests_and_platform_skips_keep_their_real_outcomes(self):
        from qualify_issue_sync import qualify
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'test_qualification_outcomes.py').write_text('''import unittest
class Example(unittest.TestCase):
 def test_failure(self): self.fail('fixture failure')
 def test_error(self): raise RuntimeError('fixture error')
 @unittest.skip('platform-specific fixture')
 def test_skip(self): pass
 def test_subtest(self):
  with self.subTest(case='literal'): self.assertEqual(1, 2)
 @unittest.expectedFailure
 def test_expected(self): self.fail('expected fixture failure')
 @unittest.expectedFailure
 def test_unexpected(self): self.assertTrue(True)
''', encoding='utf-8')  # These literal unittest outcomes independently specify the reporting contract.
            result = qualify(root, 'test_qualification_outcomes', root / 'report.json')
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(sorted(value['status'] for value in result['tests']),
                             ['error', 'expected_failure', 'failed', 'failed', 'skipped', 'unexpected_success'])
            self.assertEqual(result['tests_run'], 6)

    def test_existing_report_is_rejected_before_running_any_tests(self):
        from qualify_issue_sync import qualify
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / 'executed.txt'
            (root / 'test_qualification_reserved.py').write_text(
                'from pathlib import Path\nPath(' + repr(str(marker)) + ").write_text('executed')\n" +
                'import unittest\nclass Example(unittest.TestCase):\n def test_literal(self): self.assertTrue(True)\n', encoding='utf-8')
            report = root / 'report.json'
            report.write_text('preserved original', encoding='utf-8')
            with self.assertRaises(FileExistsError):
                qualify(root, 'test_qualification_reserved', report)
            self.assertEqual(report.read_text(encoding='utf-8'), 'preserved original')
            self.assertFalse(marker.exists())  # A rejected output path must not rerun potentially expensive behavior.

    def test_missing_or_empty_module_cannot_produce_a_passing_qualification(self):
        from qualify_issue_sync import qualify
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'test_qualification_empty.py').write_text('# No tests here.\n', encoding='utf-8')
            for name in ('test_qualification_missing', 'test_qualification_empty'):
                with self.assertRaisesRegex(ValueError, 'Missing selected module|No tests'):
                    qualify(root, name, root / (name + '.json'))
                self.assertFalse((root / (name + '.json')).exists())

    def test_cli_executes_the_selected_module_and_returns_its_report(self):
        import qualify_issue_sync
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            name = 'test_qualification_cli'
            (root / (name + '.py')).write_text('import unittest\nclass Example(unittest.TestCase):\n def test_literal(self): self.assertEqual(3, 3)\n', encoding='utf-8')
            report = root / 'report.json'
            completed = subprocess.run([sys.executable, '-B', qualify_issue_sync.__file__, '--scripts', str(root),
                '--module', name, '--report', str(report)], capture_output=True, text=True, encoding='utf-8', timeout=30)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertEqual(json.loads(report.read_text(encoding='utf-8'))['tests'],
                             [{'id': name + '.Example.test_literal', 'status': 'passed'}])

    def test_cli_stages_the_real_manifest_without_replacing_an_existing_output(self):
        import qualify_issue_sync
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'new-package'
            command = [sys.executable, '-B', qualify_issue_sync.__file__, '--stage-package', str(target)]
            completed = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=60)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            core = target / 'develop-system/scripts/issue_sync.py'
            source = Path(__file__).resolve().parents[2] / '.agents/skills/develop-system/scripts/issue_sync.py'
            self.assertEqual(core.read_bytes(), source.read_bytes())
            original = core.read_bytes()
            repeated = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=60)
            self.assertNotEqual(repeated.returncode, 0)
            self.assertEqual(core.read_bytes(), original)  # Existing generated evidence is not silently replaced.

    def test_environment_identity_is_explicit_without_copying_environment_values(self):
        from qualify_issue_sync import qualify
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'test_qualification_environment.py').write_text(
                'import unittest\nclass Example(unittest.TestCase):\n def test_literal(self): self.assertTrue(True)\n', encoding='utf-8')
            previous = os.environ.get('QUALIFICATION_PRIVATE_FIXTURE')
            os.environ['QUALIFICATION_PRIVATE_FIXTURE'] = 'private-fixture-do-not-export'
            try:
                report = root / 'report.json'
                result = qualify(root, 'test_qualification_environment', report)
                self.assertEqual(result['environment']['platform'], sys.platform)
                self.assertEqual(result['environment']['python_version'].split('.')[0], '3')
                self.assertEqual(Path(result['environment']['executable']), Path(sys.executable).resolve())
                self.assertNotIn('private-fixture-do-not-export', report.read_text(encoding='utf-8'))
                self.assertNotIn('QUALIFICATION_PRIVATE_FIXTURE', report.read_text(encoding='utf-8'))
            finally:
                if previous is None:
                    os.environ.pop('QUALIFICATION_PRIVATE_FIXTURE')
                else:
                    os.environ['QUALIFICATION_PRIVATE_FIXTURE'] = previous  # Restore the caller's temporary test environment.


if __name__ == '__main__':
    unittest.main()  # Preserve a direct source-bound TDD entry.
