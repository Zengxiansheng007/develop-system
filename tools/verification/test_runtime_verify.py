"""Public independent runtime receipt contract; not live skill acceptance."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / '.agents/skills/runtime-verify/scripts/verify.py'


class RuntimeReceiptTests(unittest.TestCase):
    def fixture(self, root):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node required for the non-Python entry qualification')
        (root / 'export.cjs').write_text(
            "require('node:fs').writeFileSync('result.txt', 'total=42\\n');", encoding='utf-8')
        return {
            'schema_version': 1, 'task': 'export report', 'input_version': 'fixture-v1',
            'authorization_reference': 'local fixture test', 'root': str(root),
            'candidate': [{'path': 'export.cjs', 'role': 'source'}],
            'environment': 'isolated temporary directory, installed Node',
            'dependencies': [{'name': 'Node/filesystem', 'mode': 'real', 'required': True,
                              'available': True, 'evidence': 'installed executable and writable fixture'}],
            'redaction': {'secret_env': [], 'omit_output': True},
            'checks': [{'id': 'AC-export', 'required': True, 'behavior': 'success',
                        'expectation_source': 'fixture requirement: report total is 42',
                        'entry': 'export.cjs', 'entry_arg': 1,
                        'argv': [node, 'export.cjs'], 'cwd': '.', 'timeout': 10,
                        'expected_exit': 0,
                        'assertions': [{'kind': 'file_text', 'path': 'result.txt', 'equals': 'total=42\n'}],
                        'cleanup': {'remove_files': ['result.txt'], 'reason': 'owned fixture output'}}],
            'coverage': {'success': ['AC-export'], 'abnormal': [],
                         'abnormal_not_applicable': 'first slice only; no external inputs'},
            'scope_adjustments': []}

    def run_plan(self, root, plan, name='receipt', env=None):
        request = root / (name + '-plan.json')
        output = root / name
        request.write_text(json.dumps(plan), encoding='utf-8')
        proc = subprocess.run([sys.executable, '-B', str(ENTRY), '--plan', str(request),
                               '--output', str(output)], capture_output=True, text=True, env=env)
        self.assertTrue((output / 'result.json').is_file(), proc.stderr)
        return proc, json.loads((output / 'result.json').read_text(encoding='utf-8')), output

    def test_real_non_python_file_business_result_and_owned_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            proc, result, output = self.run_plan(root, self.fixture(root))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(result['runtime_status'], 'passed')
            self.assertIsNone(result['candidate']['git_head'])
            self.assertEqual(result['checks'][0]['assertions'][0]['status'], 'passed')
            self.assertEqual(result['checks'][0]['cleanup']['status'], 'passed')
            self.assertFalse((root / 'result.txt').exists())
            record = json.loads((output / result['checks'][0]['record']).read_text(encoding='utf-8'))
            self.assertEqual(record['context'], 'independent')
            self.assertEqual(record['exit_code'], 0)

    def test_incomplete_expectation_or_wrong_entry_context_never_executes(self):
        for variant in ('expectation', 'entry', 'cwd', 'empty', 'dependency'):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                plan = self.fixture(root)
                if variant == 'expectation':
                    plan['checks'][0]['expectation_source'] = ''
                elif variant == 'entry':
                    plan['checks'][0]['argv'][1] = 'other.cjs'
                elif variant == 'cwd':
                    (root / 'other').mkdir()
                    plan['checks'][0]['cwd'] = 'other'
                elif variant == 'empty':
                    plan['checks'] = []
                else:
                    plan['dependencies'][0]['available'] = False
                proc, result, output = self.run_plan(root, plan)
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(result['runtime_status'], 'unaccepted')
                self.assertTrue(result['blockers'])
                self.assertFalse(list(output.glob('command-*.json')))
                self.assertFalse((root / 'result.txt').exists())

    def test_exit_zero_wrong_content_fails_without_persisting_sensitive_surfaces(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            plan = self.fixture(root)
            secret = 'fixture-private-value-7139'
            plan['task'] = 'export ' + secret
            plan['checks'][0]['argv'].append(secret)
            plan['redaction'] = {'secret_env': ['RUNTIME_TEST_SECRET'], 'omit_output': True}
            (root / 'export.cjs').write_text(
                "console.log(process.env.RUNTIME_TEST_SECRET);require('node:fs').writeFileSync('result.txt', 'wrong');")
            proc, result, output = self.run_plan(root, plan, env={**os.environ, 'RUNTIME_TEST_SECRET': secret})
            self.assertNotEqual(proc.returncode, 0)
            self.assertEqual(result['checks'][0]['status'], 'failed')
            self.assertEqual(result['checks'][0]['execution_status'], 'executed')
            self.assertEqual(result['checks'][0]['exit_code'], 0)
            for file in output.rglob('*'):
                if file.is_file():
                    self.assertNotIn(secret, file.read_text(encoding='utf-8'))
            self.assertNotIn(secret, proc.stdout + proc.stderr)

    def test_candidate_drift_and_cleanup_failure_preserve_unaccepted_evidence(self):
        for variant in ('drift', 'cleanup', 'timeout'):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                plan = self.fixture(root)
                suffix = {'drift': "require('node:fs').appendFileSync('export.cjs', '//changed');",
                          'cleanup': "require('node:fs').unlinkSync('result.txt');require('node:fs').mkdirSync('result.txt');",
                          'timeout': 'setInterval(() => {}, 1000);'}[variant]
                with (root / 'export.cjs').open('a') as target:
                    target.write(suffix)
                if variant == 'timeout':
                    plan['checks'][0]['timeout'] = 0.2
                proc, result, output = self.run_plan(root, plan)
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(result['runtime_status'], 'unaccepted')
                self.assertEqual(len(result['checks']), 1)
                if variant == 'cleanup':
                    self.assertEqual(result['checks'][0]['cleanup']['status'], 'failed')
                    self.assertTrue((root / 'result.txt').is_dir())
                elif variant == 'drift':
                    self.assertTrue(result['blockers'])
                else:
                    self.assertEqual(result['checks'][0]['error'], 'timeout')

    def test_explicit_deferral_preserves_original_requirement_and_history(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            plan = self.fixture(root)
            (root / 'export.cjs').write_text("require('node:fs').writeFileSync('result.txt', 'wrong');")
            _, original, output = self.run_plan(root, plan, 'original')
            before = (output / 'result.json').read_bytes()
            self.assertEqual(original['runtime_status'], 'unaccepted')
            plan['scope_adjustments'] = [{'check_ids': ['AC-export'], 'action': 'defer',
                                         'reason': 'user defers CLI acceptance',
                                         'user_reference': 'fixture explicit decision 2026-11-15'}]
            proc, adjusted, _ = self.run_plan(root, plan, 'adjusted')
            self.assertEqual(proc.returncode, 0)
            self.assertEqual(adjusted['runtime_status'], 'passed_with_adjustment')
            self.assertEqual(adjusted['checks'][0]['status'], 'not_run')
            self.assertTrue(adjusted['checks'][0]['required'])
            self.assertEqual((output / 'result.json').read_bytes(), before)

    def test_abnormal_entry_proves_stderr_and_no_output_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            plan = self.fixture(root)
            (root / 'export.cjs').write_text(
                "if(process.argv.includes('--invalid')) {console.error('invalid invoice');process.exit(2);}" +
                "require('node:fs').writeFileSync('result.txt', 'total=42\\n');")
            plan['checks'].append({**plan['checks'][0], 'id': 'AC-invalid', 'behavior': 'abnormal',
                'argv': [plan['checks'][0]['argv'][0], 'export.cjs', '--invalid'], 'expected_exit': 2,
                'expectation_source': 'invalid invoice reports error and writes no output',
                'assertions': [{'kind': 'stderr_text', 'equals': 'invalid invoice\n'},
                               {'kind': 'file_absent', 'path': 'result.txt'}]})
            plan['coverage']['abnormal'] = ['AC-invalid']
            proc, result, output = self.run_plan(root, plan)
            self.assertEqual(proc.returncode, 0, result)
            self.assertEqual(result['checks'][1]['status'], 'passed')
            record = json.loads((output / result['checks'][1]['record']).read_text(encoding='utf-8'))
            self.assertEqual(record['status'], 'failed')
            self.assertEqual(result['checks'][1]['assertions'][1]['actual_exists'], False)
            self.assertEqual(len(result['checks'][0]['assertions'][0]['actual_sha256']), 64)

    def test_inline_code_cannot_use_an_unused_filename_as_entry_proof(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            plan = self.fixture(root)
            plan['checks'][0]['argv'] = [plan['checks'][0]['argv'][0], '--eval',
                "require('node:fs').writeFileSync('result.txt', 'total=42\\n');", 'export.cjs']
            plan['checks'][0]['entry_arg'] = 3
            proc, result, output = self.run_plan(root, plan)
            self.assertNotEqual(proc.returncode, 0)
            self.assertEqual(result['runtime_status'], 'unaccepted')
            self.assertFalse(list(output.glob('command-*.json')))

    def test_second_check_cannot_reuse_or_cleanup_first_retained_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            plan = self.fixture(root)
            (root / 'export.cjs').write_text(
                "if(!process.argv.includes('--noop')) require('node:fs').writeFileSync('result.txt', 'total=42\\n');")
            first = plan['checks'][0]
            second = {**first, 'id': 'AC-second', 'argv': [*first['argv'], '--noop']}
            first['cleanup'] = {'remove_files': [], 'reason': 'retained first deliverable'}
            plan['checks'].append(second)
            plan['coverage']['success'].append('AC-second')
            proc, result, output = self.run_plan(root, plan)
            self.assertNotEqual(proc.returncode, 0)
            self.assertEqual(result['checks'][1]['status'], 'blocked')
            self.assertEqual(result['checks'][1]['execution_status'], 'not_run')
            self.assertFalse((output / 'command-1.json').exists())
            self.assertEqual((root / 'result.txt').read_text(), 'total=42\n')


if __name__ == '__main__':
    unittest.main()
