"""Public reuse behavior; protocol fixtures are not actual implement acceptance."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import test_runtime_verify as fixtures
import test_runtime_http as http_fixtures

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.agents/skills/runtime-verify/scripts'))
from verify import verify


class ReuseTests(unittest.TestCase):
    fixture = fixtures.RuntimeReceiptTests.fixture

    def test_noncanonical_root_requalifies_and_reuses_original_execution(self):
        with tempfile.TemporaryDirectory() as folder:  # Exercise the public plan with a real path alias.
            root = Path(folder).resolve()  # Keep the receipt reader independent of the supplied spelling.
            plan = self.plan(root)  # Use the existing real Node entry and probe.
            plan['root'] = str(root / '..' / root.name)  # Portable equivalent of the CI short-name discrepancy.
            first = verify(plan, root / 'first')  # Requalification must stay inside the canonical candidate.
            self.assertEqual(first['runtime_status'], 'passed', first['blockers'])  # Diagnose the original blocker.
            second = verify(plan, root / 'second', previous=root / 'first/result.json')  # Do not replay the business write.
            self.assertEqual(second['checks'][0]['execution_status'], 'reused')  # Retain the first execution identity.
            self.assertEqual(second['checks'][0]['original']['result_id'], first['result_id'])  # Check direct provenance.
            from reuse import validate_reuse  # Exercise the same validation used when adopting managed evidence.
            validate_reuse(second['checks'][0], plan, plan['checks'][0], second['candidate'])  # The fresh probe must remain admissible.
            plan['checks'][0]['requalification']['cwd'] = '..'  # A genuine escape is still outside the canonical root.
            blocked = verify(plan, root / 'blocked', previous=root / 'first/result.json')  # Preserve original success separately.
            self.assertEqual(blocked['checks'][0]['execution_status'], 'not_run')  # Do not run a probe outside scope.
            self.assertIn('Candidate range escapes worktree', blocked['blockers'])  # Keep the authorization guard intact.

    def plan(self, root):
        plan = self.fixture(root)
        for name, role in [('test.txt', 'test'), ('config.txt', 'config'), ('dependency.txt', 'dependency')]:
            (root / name).write_text('one')
            plan['candidate'].append({'path': name, 'role': role})
        plan['checks'][0]['candidate_paths'] = ['export.cjs', 'test.txt', 'config.txt', 'dependency.txt']
        plan['checks'][0]['requalification'] = {
            'argv': [plan['checks'][0]['argv'][0], '-e', "console.log('ready')"],
            'cwd': '.', 'timeout': 10, 'stdout': 'ready\n', 'scope': 'Node execution'}
        return plan

    def test_related_roles_execute_again_and_originals_survive(self):
        for name in ('export.cjs', 'test.txt', 'config.txt', 'dependency.txt'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                plan = self.plan(root)
                first = verify(plan, root / 'first')
                path = root / name
                path.write_text(path.read_text() + ('\n// change' if name.endswith('.cjs') else '\ntwo'))
                second = verify(plan, root / 'second', previous=root / 'first/result.json')
                self.assertEqual(second['checks'][0]['execution_status'], 'executed')
                self.assertEqual(second['runtime_status'], 'passed')
                self.assertNotEqual(first['result_id'], second['result_id'])

    def test_environment_change_blocks_without_laundering_old_success(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            plan = self.plan(root)
            probe = root / 'probe.cjs'
            probe.write_text("console.log('ready')")
            plan['checks'][0]['requalification']['argv'][1:] = [str(probe)]
            verify(plan, root / 'first')
            probe.write_text("console.log('not ready')")
            second = verify(plan, root / 'second', previous=root / 'first/result.json')
            self.assertEqual(second['runtime_status'], 'unaccepted')
            self.assertEqual(second['checks'][0]['execution_status'], 'not_run')

    def test_expectations_and_failed_originals_cannot_reuse(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            plan = self.plan(root)
            verify(plan, root / 'first')
            plan['checks'][0]['assertions'][0]['equals'] = 'different'
            failed = verify(plan, root / 'second', previous=root / 'first/result.json')
            self.assertEqual(failed['checks'][0]['execution_status'], 'executed')
            self.assertEqual(failed['runtime_status'], 'unaccepted')
            third = verify(plan, root / 'third', previous=root / 'second/result.json')
            self.assertEqual(third['checks'][0]['execution_status'], 'executed')

    def test_repeated_reuse_points_directly_to_first_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            plan = self.plan(root)
            first = verify(plan, root / 'first')
            verify(plan, root / 'second', previous=root / 'first/result.json')
            third = verify(plan, root / 'third', previous=root / 'second/result.json')
            self.assertEqual(third['checks'][0]['execution_status'], 'reused')
            self.assertEqual(third['checks'][0]['original']['result_id'], first['result_id'])

    def test_receipt_tampering_blocks_reuse(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            plan = self.plan(root)
            first = verify(plan, root / 'first')
            (root / 'first' / first['checks'][0]['record']).write_text('{}')
            second = verify(plan, root / 'second', previous=root / 'first/result.json')
            self.assertEqual(second['runtime_status'], 'unaccepted')

    def test_mixed_checks_preserve_unaffected_original_and_real_missing_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            plan = self.plan(root)
            other = copy.deepcopy(plan['checks'][0])
            (root / 'other.py').write_text("print('status=ready')")
            plan['candidate'].append({'path': 'other.py', 'role': 'source'})
            other.update(id='other', entry='other.py', argv=[sys.executable, 'other.py'],
                         candidate_paths=['other.py'], assertions=[{'kind': 'stdout_text', 'equals': 'status=ready\n'}],
                         cleanup={'remove_files': [], 'reason': 'stdout only'},
                         requalification={'argv': [sys.executable, '-c', "print('ready')"], 'cwd': '.',
                                          'timeout': 10, 'stdout': 'ready\n', 'scope': 'Python process execution'})
            plan['checks'].append(other)
            plan['coverage']['success'].append('other')
            first = verify(plan, root / 'first')
            (root / 'config.txt').write_text('changed')
            mixed = verify(plan, root / 'mixed', previous=root / 'first/result.json')
            self.assertEqual([c['execution_status'] for c in mixed['checks']], ['executed', 'reused'])
            again = verify(plan, root / 'again', previous=root / 'mixed/result.json')
            self.assertEqual([c['execution_status'] for c in again['checks']], ['reused', 'reused'])
            self.assertEqual(again['checks'][1]['original']['result_id'], first['result_id'])
            plan['checks'][0]['requalification']['argv'][0] = str(root / 'missing.exe')
            blocked = verify(plan, root / 'blocked', previous=root / 'again/result.json')
            self.assertEqual(blocked['runtime_status'], 'unaccepted')
            self.assertEqual(blocked['checks'][1]['execution_status'], 'reused')

    def test_original_execution_is_reused_after_unrelated_change(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            plan = self.fixture(root)
            plan['checks'][0]['candidate_paths'] = ['export.cjs']
            plan['checks'][0]['requalification'] = {
                'argv': [plan['checks'][0]['argv'][0], '-e', "console.log('ready')"],
                'cwd': '.', 'timeout': 10, 'stdout': 'ready\n',
                'scope': 'installed Node can execute; local filesystem remains available'}
            first = verify(plan, root / 'first')
            original = (root / 'first/result.json').read_bytes()
            (root / 'unrelated.txt').write_text('unrelated')
            second = verify(plan, root / 'second', previous=root / 'first/result.json')
            self.assertEqual(second['runtime_status'], 'passed')
            self.assertEqual(second['checks'][0]['execution_status'], 'reused')
            self.assertEqual(second['checks'][0]['original']['result_id'], first['result_id'])
            self.assertFalse(list((root / 'second').glob('command-*.json')))
            self.assertEqual((root / 'first/result.json').read_bytes(), original)

    def test_fresh_passed_check_survives_unrelated_initial_environment_blocker(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            plan = self.plan(root)
            blocked = copy.deepcopy(plan['checks'][0])
            blocked['id'] = 'blocked'
            (root / 'blocked.cjs').write_text("console.log('unavailable')")
            plan['candidate'].append({'path': 'blocked.cjs', 'role': 'source'})
            blocked.update(entry='blocked.cjs', argv=[blocked['argv'][0], 'blocked.cjs'], candidate_paths=['blocked.cjs'])
            blocked['requalification']['argv'][0] = str(root / 'unavailable-node.exe')
            plan['checks'].insert(0, blocked)
            plan['coverage']['success'].insert(0, 'blocked')
            first = verify(plan, root / 'first')
            self.assertEqual(first['runtime_status'], 'unaccepted')
            self.assertEqual(first['checks'][1]['execution_status'], 'executed')
            second = verify(plan, root / 'second', previous=root / 'first/result.json')
            self.assertEqual(second['runtime_status'], 'unaccepted')
            self.assertEqual(second['checks'][1]['execution_status'], 'reused')
            self.assertEqual(second['checks'][1]['original']['result_id'], first['result_id'])

    def test_own_candidate_drift_during_execution_still_prevents_reuse(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            plan = self.plan(root)
            path = root / 'export.cjs'
            path.write_text(path.read_text() + "require('node:fs').appendFileSync('export.cjs', '\\n// changed');")
            first = verify(plan, root / 'first')
            self.assertEqual(first['checks'][0]['status'], 'passed')
            self.assertEqual(first['runtime_status'], 'unaccepted')
            second = verify(plan, root / 'second', previous=root / 'first/result.json')
            self.assertEqual(second['checks'][0]['execution_status'], 'executed')


class HTTPReuseTests(unittest.TestCase):
    plan = http_fixtures.HTTPRuntimeTests.plan
    existing = http_fixtures.HTTPRuntimeTests.existing
    send = http_fixtures.HTTPRuntimeTests.send

    def test_noncanonical_fixture_root_declares_actual_database_identity(self):
        with tempfile.TemporaryDirectory() as folder:  # Run a real existing service with an aliased fixture root.
            root = Path(folder).resolve()  # The service advertises the canonical absolute database identity.
            plan = self.plan(root / '..' / root.name)  # Preserve the caller's noncanonical candidate spelling.
            check = plan['checks'][0]  # Reuse the public HTTP contract without weakening identity comparison.
            check['requalification'] = {'scope': 'same live process and canonical database identity'}  # Observe before reuse.
            check['cleanup']['external_owner'] = 'fixture owns created row'  # Existing service cleanup remains external.
            with self.existing(root, plan) as (_, origin):  # Real HTTP/SQLite; no mocked identity response.
                first = verify(plan, root / 'first')  # The declared identity must match the launched fixture.
                self.assertEqual(first['runtime_status'], 'passed', first['blockers'])  # Catch the original mismatch.
                second = verify(plan, root / 'second', previous=root / 'first/result.json')  # Requalify the original evidence.
                self.assertEqual(second['checks'][0]['execution_status'], 'reused')  # No second business execution.
                self.assertEqual(self.send(origin, 'GET', '/items')['names'], ['alpha'])  # Verify no duplicate write.

    def test_requalification_omits_unselected_payloads_on_success_and_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            plan = self.plan(root)
            check = plan['checks'][0]
            check['requalification'] = {'scope': 'actual readiness and immutable identity'}
            check['cleanup']['external_owner'] = 'fixture owns created row'
            with self.existing(root, plan) as (process, origin):
                first = verify(plan, root / 'first')
                original = (root / 'first/result.json').read_bytes()
                self.assertEqual(first['runtime_status'], 'passed', (first['blockers'], (root / 'first/environment-0.json').read_text()))
                for variant in ('reuse', 'wrong-identity', 'wrong-readiness'):
                    with self.subTest(variant=variant):
                        current = copy.deepcopy(plan)
                        if variant == 'wrong-identity':
                            current['checks'][0]['service']['identity']['instance_id'] = 'wrong-instance'
                        elif variant == 'wrong-readiness':
                            current['checks'][0]['service']['ready']['path'] = '/private-health'
                        result = verify(current, root / variant, previous=root / 'first/result.json')
                        if variant == 'reuse':
                            self.assertEqual(result['runtime_status'], 'passed', result['blockers'])
                            self.assertEqual(result['checks'][0]['execution_status'], 'reused')
                            self.assertEqual(result['checks'][0]['original']['result_id'], first['result_id'])
                        else:
                            self.assertEqual(result['runtime_status'], 'unaccepted')
                            self.assertEqual(result['checks'][0]['execution_status'], 'not_run')
                        persisted = ''.join(p.read_text(encoding='utf-8') for p in (root / variant).glob('*.json'))
                        for marker in ('unselected-identity-marker', 'synthetic-session-marker', 'unselected-readiness-marker'):
                            self.assertNotIn(marker, persisted)
                persisted = ''.join(p.read_text(encoding='utf-8') for p in (root / 'first').glob('*.json'))
                self.assertNotIn('unselected-identity-marker', persisted)
                self.assertNotIn('synthetic-session-marker', persisted)
                self.assertEqual((root / 'first/result.json').read_bytes(), original)
                self.assertEqual(self.send(origin, 'GET', '/items')['names'], ['alpha'])
                self.assertIsNone(process.poll())

    def test_existing_service_requalifies_identity_without_replaying_business_write(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            plan = self.plan(root)
            check = plan['checks'][0]
            check['requalification'] = {'scope': 'same running process, startup source, database and ready response'}
            check['cleanup']['external_owner'] = 'fixture owns original created row'
            with self.existing(root, plan) as (process, origin):
                first = verify(plan, root / 'first')
                self.assertEqual(first['runtime_status'], 'passed', first['blockers'])
                second = verify(plan, root / 'second', previous=root / 'first/result.json')
                self.assertEqual(second['runtime_status'], 'passed', second['blockers'])
                self.assertEqual(second['checks'][0]['execution_status'], 'reused')
                self.assertEqual(self.send(origin, 'GET', '/items')['names'], ['alpha'])
                self.assertIsNone(process.poll())
                plan['checks'][0]['service']['identity']['instance_id'] = 'wrong-instance'
                failed = verify(plan, root / 'failed', previous=root / 'second/result.json')
                self.assertEqual(failed['runtime_status'], 'unaccepted')


if __name__ == '__main__':
    unittest.main()
