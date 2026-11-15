"""Public plan/receipt tests using real HTTP and SQLite, not live skill qualification."""
import json
import os
from contextlib import contextmanager
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import Request, build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / '.agents/skills/runtime-verify/scripts/verify.py'


class HTTPRuntimeTests(unittest.TestCase):
    def plan(self, root, mode='good'):
        shutil.copyfile(Path(__file__).parent / 'fixtures/runtime_http_service.py', root / 'service.py')
        return {
            'schema_version': 1, 'task': 'persist alpha', 'input_version': 't02-test',
            'authorization_reference': 'isolated loopback fixture', 'root': str(root),
            'candidate': [{'path': 'service.py', 'role': 'source'}],
            'environment': 'installed Python, real loopback and SQLite; unrelated region fake',
            'dependencies': [
                {'name': 'SQLite', 'mode': 'real', 'required': True, 'available': True, 'evidence': 'real on-disk database and readback'},
                {'name': 'region', 'mode': 'simulated', 'required': False, 'available': True, 'evidence': 'constant test region; not region integration'}],
            'redaction': {'secret_env': [], 'omit_output': True},
            'checks': [{'kind': 'http', 'id': 'AC-write', 'required': True, 'behavior': 'success',
                'expectation_source': 'TASK: alpha is stored and available on subsequent GET',
                'service': {'mode': 'owned', 'entry': 'service.py', 'entry_arg': 2,
                    'argv': [sys.executable, '-B', 'service.py', 'data.db', 'port.json', 'fixture-1', mode],
                    'cwd': '.', 'origin': 'http://127.0.0.1', 'port_file': 'port.json',
                    'identity': {'path': '/identity', 'source': 'service.py', 'instance_id': 'fixture-1',
                                 'database': str(root / 'data.db'), 'provenance': 'immutable source hash captured at service startup'},
                    'ready': {'path': '/health', 'status': 200, 'json': {'status': 'ready'}},
                    'startup_timeout': 5, 'ready_timeout': 3, 'poll_interval': 0.05,
                    'request_timeout': 2, 'max_bytes': 16384},
                'requests': [
                    {'method': 'POST', 'path': '/items', 'json': {'name': 'alpha'}, 'retry': 'none',
                     'status': 200, 'assertions': [{'path': ['name'], 'equals': 'alpha'}]},
                    {'method': 'GET', 'path': '/items', 'retry': 'none', 'status': 200,
                     'assertions': [{'path': ['names'], 'equals': ['alpha']}, {'path': ['region'], 'equals': 'test'}]}],
                'cleanup': {'remove_files': ['data.db', 'port.json'], 'reason': 'fresh owned test files', 'timeout': 2}}],
            'coverage': {'success': ['AC-write'], 'abnormal': [], 'abnormal_not_applicable': 'fixture success path'},
            'scope_adjustments': []}

    def run_plan(self, root, plan, name='evidence', env=None):
        request = root / (name + '-plan.json')
        request.write_text(json.dumps(plan), encoding='utf-8')
        output = root / name
        process = subprocess.run([sys.executable, '-B', str(ENTRY), '--plan', str(request), '--output', str(output)],
                                 capture_output=True, text=True, timeout=30, env=env)
        self.assertTrue((output / 'result.json').exists(), process.stderr)
        return process, json.loads((output / 'result.json').read_text(encoding='utf-8')), output

    def test_real_write_readback_and_owned_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            process, result, _ = self.run_plan(root, self.plan(root))
            self.assertEqual(process.returncode, 0, result)
            self.assertEqual(result['runtime_status'], 'passed')
            check = result['checks'][0]
            self.assertEqual(check['requests'][1]['assertions'][0]['actual'], ['alpha'])
            self.assertEqual(check['cleanup']['status'], 'passed')
            self.assertFalse((root / 'data.db').exists())
            self.assertFalse((root / 'port.json').exists())

    def test_http_200_wrong_state_stays_failed_and_preserves_original_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            plan = self.plan(root, 'wrong')
            _, result, output = self.run_plan(root, plan, 'original')
            self.assertEqual(result['checks'][0]['requests'][0]['status'], 'passed')
            readback = result['checks'][0]['requests'][1]
            self.assertEqual(readback['http_status'], 200)
            self.assertEqual(readback['assertions'][0]['actual'], ['wrong'])
            self.assertEqual(result['runtime_status'], 'unaccepted')
            original = (output / 'result.json').read_bytes()
            plan['checks'][0]['service']['argv'][-1] = 'good'
            _, fixed, _ = self.run_plan(root, plan, 'fixed')
            self.assertEqual(fixed['runtime_status'], 'passed')
            self.assertEqual((output / 'result.json').read_bytes(), original)

    def test_start_identity_ready_dependency_and_cleanup_failures(self):
        for mode in ('exit', 'unready', 'identity', 'dependency', 'cleanup', 'foreign'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                plan = self.plan(root, mode)
                check = plan['checks'][0]
                if mode == 'unready':
                    check['service']['ready_timeout'] = 0.7
                elif mode == 'identity':
                    check['service']['identity']['instance_id'] = 'different-instance'
                elif mode == 'dependency':
                    plan['dependencies'][0]['available'] = False
                elif mode == 'cleanup':
                    check['cleanup']['remove_files'].append('residual')
                elif mode == 'foreign':
                    (root / 'data.db').write_text('foreign')
                _, result, _ = self.run_plan(root, plan)
                self.assertEqual(result['runtime_status'], 'unaccepted')
                if mode in ('dependency', 'foreign'):
                    self.assertFalse(result['checks'])
                    if mode == 'foreign':
                        self.assertEqual((root / 'data.db').read_text(), 'foreign')
                else:
                    self.assertEqual(result['checks'][0]['cleanup']['status'], 'failed' if mode == 'cleanup' else 'passed')
                    self.assertFalse((root / 'data.db').exists())
                    self.assertFalse((root / 'port.json').exists())

    def test_write_deadline_sends_once_and_records_unknown_outcome(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            plan = self.plan(root, 'delay')
            plan['checks'][0]['service']['request_timeout'] = 0.7
            _, result, _ = self.run_plan(root, plan)
            check = result['checks'][0]
            self.assertEqual(result['runtime_status'], 'unaccepted')
            self.assertEqual(check['requests'][0]['attempts'], 1)
            self.assertEqual(check['requests'][0]['outcome'], 'unknown')
            self.assertEqual(check['requests'][1]['assertions'][0]['actual'], ['alpha'])
            self.assertEqual(check['cleanup']['status'], 'passed')

    @contextmanager
    def existing(self, root, plan):
        service = plan['checks'][0]['service']
        process = subprocess.Popen(service['argv'], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 5
            while not (root / 'port.json').exists():
                self.assertIsNone(process.poll())
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.02)
            port = json.loads((root / 'port.json').read_text())['port']
            service['origin'] += ':' + str(port)
            service['mode'] = 'existing'
            service['identity']['pid'] = process.pid
            for name in ('entry', 'entry_arg', 'argv', 'cwd', 'port_file'):
                service.pop(name)
            plan['checks'][0]['cleanup']['remove_files'] = []
            yield process, service['origin']
        finally:
            process.terminate()
            process.wait(timeout=3)

    def send(self, origin, method, path, body=None):
        request = Request(origin + path, method=method, data=json.dumps(body).encode() if body else None,
                          headers={'Content-Type': 'application/json'})
        with build_opener(ProxyHandler({})).open(request, timeout=2) as response:
            return json.load(response)

    def test_existing_service_keeps_unrelated_row_and_cleans_only_captured_created_id(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            plan = self.plan(root)
            check = plan['checks'][0]
            check['requests'][0].update(capture={'created_id': ['id']}, ownership_reason='POST creates new task-owned row')
            check['requests'][1]['assertions'][0]['equals'] = ['untouched', 'alpha']
            check['cleanup']['requests'] = [{'method': 'DELETE', 'path': '/items/{created_id}', 'retry': 'none', 'status': 200,
                                            'assertions': [{'path': ['deleted'], 'equals': 2}]}]
            with self.existing(root, plan) as (process, origin):
                self.send(origin, 'POST', '/items', {'name': 'untouched'})
                _, result, _ = self.run_plan(root, plan)
                self.assertEqual(result['runtime_status'], 'passed', result)
                self.assertIsNone(process.poll())
                self.assertEqual(self.send(origin, 'GET', '/items')['names'], ['untouched'])
                self.assertTrue((root / 'data.db').exists())

    def test_stale_process_cannot_claim_current_disk_hash_and_remains_alive(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            plan = self.plan(root)
            plan['checks'][0]['cleanup']['external_owner'] = 'test harness owns all new fixture rows'
            with self.existing(root, plan) as (process, origin):
                with (root / 'service.py').open('a') as target:
                    target.write('\n# edited after process startup\n')
                _, result, _ = self.run_plan(root, plan)
                self.assertEqual(result['runtime_status'], 'unaccepted')
                self.assertEqual(result['checks'][0]['requests'], [])
                self.assertIsNone(process.poll())
                self.assertEqual(self.send(origin, 'GET', '/items')['names'], [])

    def test_redirect_and_slow_stream_do_not_follow_or_exceed_request_budget(self):
        for path in ('/redirect', '/slow'):
            with self.subTest(path=path), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                plan = self.plan(root)
                check = plan['checks'][0]
                check['requests'][1]['path'] = path
                check['service']['request_timeout'] = 0.7
                started = time.monotonic()
                _, result, _ = self.run_plan(root, plan)
                self.assertLess(time.monotonic() - started, 8)
                self.assertEqual(result['runtime_status'], 'unaccepted')
                request = result['checks'][0]['requests'][1]
                self.assertEqual(request['http_status'], 302 if path == '/redirect' else None)

    def test_secrets_in_headers_body_and_launch_output_never_persist(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            plan = self.plan(root)
            plan['checks'][0]['requests'][0]['headers_env'] = {'Authorization': 'HTTP_TEST_SECRET'}
            plan['checks'][0]['requests'][1]['assertions'].append({'path': ['nested'], 'equals': {'password': 'body-secret-marker'}})
            plan['checks'][0]['requests'][0]['json']['password'] = 'plan-secret-marker'
            with (root / 'service.py').open('a') as target:
                target.write('\n# synthetic safe fixture\n')
            _, result, output = self.run_plan(root, plan, env={**os.environ, 'HTTP_TEST_SECRET': 'header-secret-marker'})
            self.assertEqual(result['runtime_status'], 'passed', result)
            evidence = ''.join(p.read_text() for p in output.glob('*.json'))
            for secret in ('body-secret-marker', 'plan-secret-marker', 'session-test-marker', 'header-secret-marker'):
                self.assertNotIn(secret, evidence)


if __name__ == '__main__':
    unittest.main()
