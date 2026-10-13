import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[2] / '.agents/skills/develop-system/scripts'
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class RecordedCheckTests(unittest.TestCase):
    def test_invalid_working_directory_still_records_failed_execution(self):
        from record_check import run_record, validate_record
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'invalid-cwd.json'
            record = run_record([sys.executable, '-c', "print('must not run')"], root / 'missing', output)
            self.assertEqual(validate_record(record), 'failed')
            self.assertIsNone(record['exit_code'])
            self.assertTrue(record['error'])
            self.assertEqual(json.loads(output.read_text(encoding='utf-8'))['stdout'], '')

    def test_scheduler_rejects_other_project_record(self):
        from record_check import run_record
        fixture = self.fixture()
        state = fixture.dispatch(fixture.create())
        with tempfile.TemporaryDirectory() as other:
            record = fixture.root / 'foreign.json'
            run_record([sys.executable, '-c', "print('ok')"], other, record)
            before = fixture.store.path.read_bytes()
            with self.assertRaises(ValueError):
                fixture.store.receive(state['revision'], fixture.event(state, checks=[{'status': 'passed', 'record': str(record)}]))
            self.assertEqual(before, fixture.store.path.read_bytes())

    def fixture(self):
        from test_workflow_state import DurableWorkflowTests
        fixture = DurableWorkflowTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_scheduler_rejects_premature_success_without_mutation(self):
        fixture = self.fixture()
        state = fixture.dispatch(fixture.create())
        before = fixture.store.path.read_bytes()
        event = fixture.event(state, checks=[{'status': 'passed', 'command': 'unexecuted'}])
        with self.assertRaises(ValueError):
            fixture.store.receive(state['revision'], event)
        self.assertEqual(before, fixture.store.path.read_bytes())

    def test_scheduler_rejects_forged_success_and_failed_acceptance(self):
        from record_check import run_record
        fixture = self.fixture()
        state = fixture.dispatch(fixture.create())
        record = fixture.root / 'failed.json'
        run_record([sys.executable, '-c', 'raise SystemExit(1)'], fixture.root, record)
        before = fixture.store.path.read_bytes()
        for changes in ({'status': 'passed', 'record': str(record)},
                        {'status': 'failed', 'record': str(record)}):
            with self.assertRaises(ValueError):
                fixture.store.receive(state['revision'], fixture.event(state, checks=[changes]))
        data = json.loads(record.read_text(encoding='utf-8'))
        data['status'] = 'passed'
        record.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaises(ValueError):
            fixture.store.receive(state['revision'], fixture.event(state, checks=[{'status': 'passed', 'record': str(record)}]))
        self.assertEqual(before, fixture.store.path.read_bytes())

    def test_changed_check_record_invalidates_dependent_work(self):
        from record_check import run_record
        fixture = self.fixture()
        state = fixture.dispatch(fixture.create())
        record = fixture.root / 'passed.json'
        run_record([sys.executable, '-c', "print('ok')"], fixture.root, record)
        event = fixture.event(state, checks=[{'status': 'passed', 'record': str(record)}])
        state = fixture.store.receive(state['revision'], event)
        duplicate = fixture.store.receive(state['revision'], event)
        self.assertEqual(state['revision'], duplicate['revision'])
        state = fixture.receive(fixture.dispatch(state, 'to-spec', 'spec-step'))
        record.write_text(record.read_text(encoding='utf-8') + ' ', encoding='utf-8')
        with self.assertRaises(ValueError):
            fixture.complete(state)
        state = fixture.store.resume(state['revision'])
        self.assertEqual([step['status'] for step in state['plan']], ['pending', 'pending'])

    def test_timeout_launch_error_and_existing_record_never_pass(self):
        from record_check import run_record, validate_record
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, command, timeout in [
                ('timeout', [sys.executable, '-c', 'import time; time.sleep(5)'], 0.05),
                ('missing', [str(root / 'missing-executable')], 1)]:
                record = run_record(command, root, root / (name + '.json'), timeout)
                self.assertEqual(validate_record(record), 'failed')
                self.assertIsNone(record['exit_code'])
            output = root / 'once.json'
            run_record([sys.executable, '-c', "print('first')"], root, output)
            original = output.read_bytes()
            with self.assertRaises(FileExistsError):
                run_record([sys.executable, '-c', 'raise SystemExit(1)'], root, output)
            self.assertEqual(original, output.read_bytes())

    def test_copy_check_detects_missing_changed_and_extra_files(self):
        from checks import compare_copies
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / 'source', root / 'target'
            source.mkdir(); target.mkdir()
            (source / 'SKILL.md').write_text('canonical', encoding='utf-8')
            with self.assertRaises(ValueError):
                compare_copies(source, target)
            (target / 'SKILL.md').write_text('wrong', encoding='utf-8')
            with self.assertRaises(ValueError):
                compare_copies(source, target)
            (target / 'SKILL.md').write_text('canonical', encoding='utf-8')
            (target / 'extra.md').write_text('extra', encoding='utf-8')
            with self.assertRaises(ValueError):
                compare_copies(source, target)
            (target / 'extra.md').unlink()
            compare_copies(source, target)

    def test_failed_command_is_recorded_and_propagates_exit_code(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'result.json'
            completed = subprocess.run([sys.executable, '-B', str(SCRIPTS / 'record_check.py'),
                '--output', str(output), '--', sys.executable, '-c',
                "import sys; print('实际失败'); print('missing dependency', file=sys.stderr); sys.exit(7)"],
                capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(completed.returncode, 7, completed.stderr)
            record = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(record['status'], 'failed')
            self.assertEqual(record['exit_code'], 7)
            self.assertIn('实际失败', record['stdout'])
            self.assertIn('missing dependency', record['stderr'])
            self.assertTrue(record['command'])
            self.assertTrue(record['finished_at'])


if __name__ == '__main__':
    unittest.main()
