"""Public async plan/receipt contract; real services, no internal helper tests."""
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest

import test_runtime_http


class AsyncRuntimeTests(unittest.TestCase):
    run_plan = test_runtime_http.HTTPRuntimeTests.run_plan
    existing = test_runtime_http.HTTPRuntimeTests.existing
    send = test_runtime_http.HTTPRuntimeTests.send

    def plan(self, root, mode='good'):
        plan = test_runtime_http.HTTPRuntimeTests.plan(self, root, mode)
        shutil.copyfile(Path(__file__).parent / 'fixtures/runtime_async_service.py', root / 'service.py')
        plan['task'] = 'async sum 10 plus 32'
        plan['input_version'] = 't03-test'
        check = plan['checks'][0]
        check['service']['request_timeout'] = 3
        check['expectation_source'] = 'TASK: accepted job succeeds with total 42; failure/pending/wrong job cannot pass'
        check['requests'] = [{'method': 'POST', 'path': '/jobs', 'json': {'amounts': [10, 32]},
            'status': 202, 'retry': 'none', 'assertions': [{'path': ['state'], 'equals': 'pending'}],
            'poll': {'method': 'GET', 'path': '/jobs/{correlation}', 'retry': 'none', 'status': 200,
                     'correlation': {'accepted_path': ['id'], 'response_path': ['id']},
                     'state_path': ['state'], 'pending': ['pending'], 'success': ['succeeded'], 'failure': ['failed'],
                     'timeout': 4, 'max_attempts': 20, 'interval': 0.05,
                     'assertions': [{'path': ['total'], 'equals': 42}]}}]
        return plan

    def test_success_requires_correlated_terminal_business_result(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _, result, _ = self.run_plan(root, self.plan(root))
            self.assertEqual(result['runtime_status'], 'passed', result)
            poll = result['checks'][0]['requests'][0]['poll']
            self.assertEqual(poll['outcome'], 'succeeded')
            self.assertEqual(poll['observations'][-1]['assertions'][0]['actual'], 42)
            self.assertTrue(all(o['correlation']['status'] == 'passed' for o in poll['observations']))
            self.assertFalse((root / 'data.db').exists())

    def test_failed_stuck_wrong_job_and_wrong_total_never_resubmit_or_pass(self):
        for mode, outcome in (('failure', 'failed'), ('stuck', 'attempts_exhausted'),
                              ('wrong_job', 'correlation_mismatch'), ('wrong_total', 'business_failed'),
                              ('slow', 'transport_failed')):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                plan = self.plan(root, mode)
                check = plan['checks'][0]
                check['requests'][0]['poll'].update(max_attempts=2, interval=0.25)
                if mode == 'slow':
                    check['requests'][0]['poll']['timeout'] = 8
                check['cleanup']['external_owner'] = 'test harness owns isolated SQLite jobs and removes database after service shutdown'
                with self.existing(root, plan) as (process, origin):
                    started = time.monotonic()
                    _, result, _ = self.run_plan(root, plan)
                    self.assertLess(time.monotonic() - started, 20)
                    self.assertEqual(result['runtime_status'], 'unaccepted', result)
                    poll = result['checks'][0]['requests'][0]['poll']
                    self.assertEqual(poll['outcome'], outcome, result)
                    self.assertLessEqual(len(poll['observations']), 2)
                    self.assertEqual(self.send(origin, 'GET', '/jobs')['count'], 1)
                    self.assertIsNone(process.poll())

    def test_acceptance_without_terminal_contract_is_blocked_before_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            plan = self.plan(root)
            plan['checks'][0]['requests'][0].pop('poll')
            _, result, _ = self.run_plan(root, plan)
            self.assertEqual(result['runtime_status'], 'unaccepted')
            self.assertFalse(result['checks'])

    def test_deadline_preserves_last_pending_state_and_original_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            plan = self.plan(root, 'stuck')
            plan['checks'][0]['requests'][0]['poll'].update(timeout=3, interval=5)
            _, result, output = self.run_plan(root, plan, 'original')
            poll = result['checks'][0]['requests'][0]['poll']
            self.assertEqual(result['runtime_status'], 'unaccepted')
            self.assertEqual(poll['outcome'], 'deadline')
            self.assertEqual(poll['observations'][-1]['state']['actual'], 'pending')
            self.assertLess(poll['elapsed_seconds'], 4)
            original = (output / 'result.json').read_bytes()
            plan['checks'][0]['service']['argv'][-1] = 'good'
            plan['checks'][0]['requests'][0]['poll'].update(timeout=4, interval=0.05)
            _, fixed, _ = self.run_plan(root, plan, 'fixed')
            self.assertEqual(fixed['runtime_status'], 'passed')
            self.assertEqual((output / 'result.json').read_bytes(), original)

    def test_missing_id_or_unknown_write_never_resubmits(self):
        for mode in ('missing_id', 'write_timeout'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                plan = self.plan(root, mode)
                check = plan['checks'][0]
                check['cleanup']['external_owner'] = 'test harness owns new fixture jobs'
                with self.existing(root, plan) as (_, origin):
                    _, result, _ = self.run_plan(root, plan)
                    self.assertEqual(result['runtime_status'], 'unaccepted')
                    request = result['checks'][0]['requests'][0]
                    self.assertEqual(request['attempts'], 1)
                    self.assertEqual(request['poll']['observations'], [])
                    if mode == 'write_timeout':
                        self.assertEqual(request['outcome'], 'unknown')
                    self.assertEqual(self.send(origin, 'GET', '/jobs')['count'], 1)

    def test_poll_headers_and_sensitive_selections_are_redacted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            plan = self.plan(root)
            poll = plan['checks'][0]['requests'][0]['poll']
            marker = 'synthetic-poll-secret-marker'
            poll['headers_env'] = {'Authorization': 'ASYNC_TEST_SECRET'}
            poll['assertions'].append({'path': ['trace'], 'equals': marker})
            _, result, output = self.run_plan(root, plan, env={**os.environ, 'ASYNC_TEST_SECRET': marker})
            self.assertEqual(result['runtime_status'], 'passed', result)
            for path in output.glob('*.json'):
                self.assertNotIn(marker, path.read_text(encoding='utf-8'))

    def test_poll_cannot_acquire_cleanup_ownership_or_change_to_write(self):
        for invalid in ('capture', 'method', 'overlap', 'missing_budget'):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                plan = self.plan(root)
                request = plan['checks'][0]['requests'][0]
                if invalid == 'capture':
                    request.update(capture={'job': ['id']}, ownership_reason='correlation is not ownership')
                elif invalid == 'method':
                    request['poll']['method'] = 'POST'
                elif invalid == 'overlap':
                    request['poll']['success'].append('pending')
                else:
                    request['poll'].pop('max_attempts')
                _, result, _ = self.run_plan(root, plan)
                self.assertEqual(result['runtime_status'], 'unaccepted')
                self.assertFalse(result['checks'])

    def test_malformed_poll_is_blocked_with_evidence_before_any_launch(self):
        for malformed in (None, [], 'invalid', {'headers_env': None}, {'headers_env': []}):
            with self.subTest(poll=malformed), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                plan = self.plan(root)
                request = plan['checks'][0]['requests'][0]
                if isinstance(malformed, dict):
                    request['poll'].update(malformed)
                else:
                    request['poll'] = malformed
                _, result, _ = self.run_plan(root, plan)
                self.assertEqual(result['runtime_status'], 'unaccepted')
                self.assertFalse(result['checks'])
                self.assertTrue(result['blockers'])
                self.assertFalse((root / 'data.db').exists())

    def correlated_secret_plan(self, root, accepted_field, response_field, short=False):
        mode = accepted_field + '-' + response_field + ('-short' if short else '')
        plan = self.plan(root, mode)
        marker = '1' if short else 'synthetic-job-token-28'
        request = plan['checks'][0]['requests'][0]
        poll = request['poll']
        poll['correlation'] = {'accepted_path': [accepted_field], 'response_path': [response_field]}
        request['assertions'].extend([
            {'path': [accepted_field], 'equals': marker},
            {'path': [], 'equals': {accepted_field: marker, 'state': 'pending'}}])
        poll['assertions'].extend([
            {'path': [response_field], 'equals': marker},
            {'path': [], 'equals': {response_field: marker, 'state': 'succeeded', 'total': 42, 'error': None, 'trace': None}}])
        return plan, marker

    def test_sensitive_correlation_propagates_across_selectors_paths_and_assertions(self):
        for accepted_field, response_field in (('token', 'token'), ('token', 'id'), ('id', 'token')):
            with self.subTest(accepted=accepted_field, response=response_field), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                plan, marker = self.correlated_secret_plan(root, accepted_field, response_field)
                _, result, output = self.run_plan(root, plan)
                self.assertEqual(result['runtime_status'], 'passed', result)
                for artifact in output.glob('*.json'):
                    self.assertNotIn(marker, artifact.read_text(encoding='utf-8'))
                request = result['checks'][0]['requests'][0]
                self.assertEqual(request['poll']['accepted_correlation']['actual'], '[REDACTED]')
                for observed in request['poll']['observations']:
                    self.assertEqual(observed['path'], '/jobs/{correlation}')
                    self.assertEqual(observed['correlation']['actual'], '[REDACTED]')
                    self.assertEqual(observed['correlation']['equals'], '[REDACTED]')
                self.assertFalse((root / 'data.db').exists())

    def test_short_sensitive_correlation_does_not_rewrite_structural_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            plan, _ = self.correlated_secret_plan(root, 'token', 'id', short=True)
            _, result, _ = self.run_plan(root, plan)
            self.assertEqual(result['runtime_status'], 'passed', result)
            self.assertEqual(result['schema_version'], 1)
            self.assertEqual(result['plan']['input_version'], 't03-test')
            check = result['checks'][0]
            self.assertEqual(check['identity'][0]['actual']['instance_id'], 'fixture-1')
            self.assertEqual(check['id'], 'AC-write')
            request = check['requests'][0]
            self.assertEqual(request['attempts'], 1)
            poll = request['poll']
            self.assertEqual(poll['accepted_correlation']['actual'], '[REDACTED]')
            self.assertEqual(poll['observations'][-1]['correlation']['actual'], '[REDACTED]')
            self.assertEqual(poll['observations'][-1]['assertions'][0]['actual'], 42)
            self.assertEqual(poll['observations'][-1]['assertions'][1]['actual'], '[REDACTED]')
            self.assertEqual(poll['observations'][-1]['assertions'][2]['actual']['id'], '[REDACTED]')
            self.assertEqual(result['plan']['checks'][0]['requests'][0]['poll']['assertions'][1]['equals'], '[REDACTED]')

    def array_correlation_plan(self, root, side, ordinary=False):
        accepted_array = side in ('accepted', 'acceptance_assertion')
        mode = 'id-token-acceptedarray' if accepted_array else 'id-id-responsearray' if ordinary else 'token-id-responsearray'
        plan = self.plan(root, mode)
        request = plan['checks'][0]['requests'][0]
        poll = request['poll']
        accepted_path = ['rows', 0, 'id'] if accepted_array else ['id' if ordinary else 'token']
        response_path = ['token'] if accepted_array else ['rows', 0, 'id']
        poll['correlation'] = {'accepted_path': accepted_path, 'response_path': response_path}
        marker = 'synthetic-job-token-28'
        if accepted_array:
            request['assertions'].append({'path': ['rows'], 'equals': [{'id': marker}]})
        else:
            poll['assertions'].append({'path': ['rows'], 'equals': [{'id': marker}]})
        request['assertions'].append({'path': list(accepted_path), 'equals': marker})
        poll['assertions'].append({'path': list(response_path), 'equals': marker})
        return plan, marker

    def test_sensitive_negative_array_selectors_are_safely_blocked_before_launch(self):
        for location in ('accepted', 'response', 'acceptance_assertion', 'poll_assertion', 'state'):
            with self.subTest(location=location), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                plan, marker = self.array_correlation_plan(root, location)
                request = plan['checks'][0]['requests'][0]
                poll = request['poll']
                if location in ('accepted', 'response'):
                    poll['correlation'][location + '_path'][1] = -1
                elif location == 'acceptance_assertion':
                    request['assertions'][-1]['path'][1] = -1
                elif location == 'poll_assertion':
                    poll['assertions'][-1]['path'][1] = -1
                else:
                    poll['state_path'] = ['states', -1]
                _, result, output = self.run_plan(root, plan)
                for artifact in output.glob('*.json'):
                    self.assertNotIn(marker, artifact.read_text(encoding='utf-8'))
                self.assertEqual(result['runtime_status'], 'unaccepted')
                self.assertFalse(result['checks'])
                self.assertTrue(result['blockers'])
                self.assertFalse(list(output.glob('http-*.json')))
                self.assertFalse((root / 'data.db').exists())
                self.assertFalse((root / 'port.json').exists())

    def test_positive_sensitive_array_selectors_and_ordinary_negative_indices_still_work(self):
        for side, ordinary in (('accepted', False), ('response', False), ('response', True)):
            with self.subTest(side=side, ordinary=ordinary), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                plan, marker = self.array_correlation_plan(root, side, ordinary)
                if ordinary:
                    plan['checks'][0]['requests'][0]['poll']['correlation']['response_path'][1] = -1
                _, result, output = self.run_plan(root, plan)
                self.assertEqual(result['runtime_status'], 'passed', result)
                if not ordinary:
                    for artifact in output.glob('*.json'):
                        self.assertNotIn(marker, artifact.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
