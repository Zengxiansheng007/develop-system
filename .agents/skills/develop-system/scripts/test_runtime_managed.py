"""Public managed runtime protocol tests; professional returns are synthetic."""
import unittest
import test_required_helpers as helpers
import copy
import json
import shutil
import sys
import uuid
import os
from datetime import datetime, timedelta
from pathlib import Path
import test_worktree_checks as worktrees
from workflow_state import RunStore, SKILL_ROOT

sys.path.insert(0, str(SKILL_ROOT.parent / 'runtime-verify/scripts'))
from managed import execute


class RuntimeManagedTests(unittest.TestCase):
    setUp = helpers.RequiredHelperTests.setUp
    event = helpers.RequiredHelperTests.event
    def test_runtime_duty_is_durable_before_helper_dispatch(self):
        state = self.store.create('runtime task', [{'step_id': 'a', 'skill_id': 'implement',
            'runtime_contract': {'version': 1, 'criteria': {'1': ['business']}, 'scope_adjustments': []}}], ['business'])
        state = self.store.dispatch(state['revision'], 'implement', 'v1', 'a')
        parent = state['frames'][-1]
        self.assertEqual({d['skill_id'] for d in parent['completion_obligations']}, {'code-review', 'runtime-verify'})
        with self.assertRaises(ValueError):
            self.store.dispatch(state['revision'], 'code-review', 'v1', parent_frame_id=parent['frame_id'])
        before = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.receive(state['revision'], self.event(parent))
        self.assertEqual(before, self.store.path.read_bytes())


class RuntimeEvidenceTests(unittest.TestCase):
    setUp = worktrees.WorktreeCheckTests.setUp
    git_run = worktrees.WorktreeCheckTests.git_run
    authorization = worktrees.WorktreeCheckTests.authorization

    def prepare(self, expected='correct\n', adjustments=None, narrow_execution=False):
        node = shutil.which('node')
        if not node:
            self.skipTest('Real Node runtime required')
        (self.worktree / 'src/main.js').write_text("console.log('correct');\n", encoding='utf-8')
        self.store = RunStore(self.project, 'runtime')
        self.state = self.store.create('synthetic protocol qualification', [{'step_id': 'a', 'skill_id': 'implement',
            'runtime_contract': {'version': 1, 'criteria': {'1': ['business']}, 'scope_adjustments': adjustments or []}}], ['business'])
        authorization = self.authorization()
        if narrow_execution:
            authorization['execution_roots'] = [str(self.worktree / 'src')]
        self.state = self.store.dispatch(self.state['revision'], 'implement', 'v1', 'a', worktree=authorization)
        self.parent = self.state['frames'][-1]
        self.state = self.store.dispatch(self.state['revision'], 'runtime-verify', 'v1',
            parent_frame_id=self.parent['frame_id'], worktree=authorization)
        self.frame = self.state['frames'][-1]
        self.plan = {'schema_version': 1, 'task': 'synthetic protocol fixture', 'input_version': 'v1',
            'authorization_reference': 'fixture only', 'environment': 'local Node', 'root': str(self.worktree),
            'candidate': [{'path': 'src', 'role': 'source'}], 'dependencies': [],
            'redaction': {'secret_env': [], 'omit_output': False}, 'scope_adjustments': adjustments or [],
            'coverage': {'success': ['business'], 'abnormal': [], 'abnormal_not_applicable': 'protocol fixture'},
            'checks': [{'id': 'business', 'required': True, 'behavior': 'success', 'expectation_source': 'literal fixture',
                'entry': 'src/main.js', 'cwd': '.', 'argv': [node, 'src/main.js'], 'entry_arg': 1, 'timeout': 10,
                'expected_exit': 0, 'assertions': [{'kind': 'stdout_text', 'equals': expected}],
                'cleanup': {'remove_files': [], 'reason': 'no outputs'}}]}

    def run_runtime(self):
        return execute(self.store, self.frame['frame_id'], self.plan, self.root / uuid.uuid4().hex)

    def refresh_runtime_event(self, event, envelope):
        from workflow_state import file_evidence
        path = Path(event['runtime_evidence']['path'])
        path.write_text(json.dumps(envelope), encoding='utf-8')
        evidence = file_evidence(str(path))
        event['runtime_evidence'] = evidence
        event['artifacts'] = [evidence]
        event['acceptance_checks'][0]['evidence'] = [evidence]

    def test_unrelated_executed_receipt_cannot_complete_planned_entry(self):
        from record_check import run_record
        from workflow_state import file_evidence
        self.prepare()
        event = self.run_runtime()
        envelope = json.loads(Path(event['runtime_evidence']['path']).read_text(encoding='utf-8'))
        receipt = Path(envelope['records'][0]['path'])
        unrelated = self.root / 'unrelated.json'
        run_record([self.plan['checks'][0]['argv'][0], '-e', "console.log('correct')"], self.project, unrelated, 10)
        receipt.write_bytes(unrelated.read_bytes())
        envelope['records'][0] = dict(file_evidence(str(receipt)), name=envelope['records'][0]['name'])
        self.refresh_runtime_event(event, envelope)
        before = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.receive(self.state['revision'], event)
        self.assertEqual(before, self.store.path.read_bytes())
        with self.assertRaises(ValueError):
            self.store.complete(self.state['revision'], [{'id': '1', 'source_frame_id': self.frame['frame_id']}])
        self.assertEqual(before, self.store.path.read_bytes())

    def test_receipt_operation_dimensions_and_version_cannot_be_swapped(self):
        from workflow_state import file_evidence
        self.prepare()
        event = self.run_runtime()
        envelope_path = Path(event['runtime_evidence']['path'])
        original_envelope = json.loads(envelope_path.read_text(encoding='utf-8'))
        receipt = Path(original_envelope['records'][0]['path'])
        original_record = json.loads(receipt.read_text(encoding='utf-8'))
        result_path = Path(original_envelope['result']['path'])
        original_result = json.loads(result_path.read_text(encoding='utf-8'))
        before = self.store.path.read_bytes()
        for variant in ('command', 'execution_command', 'cwd', 'entry', 'time', 'missing-record', 'version'):
            with self.subTest(variant=variant):
                changed = copy.deepcopy(event)
                envelope = copy.deepcopy(original_envelope)
                record = copy.deepcopy(original_record)
                result = copy.deepcopy(original_result)
                if variant in ('command', 'execution_command'):
                    record[variant] = [record['command'][0], '-e', "console.log('correct')"]
                elif variant == 'cwd':
                    record['cwd'] = str(self.project)
                elif variant == 'entry':
                    result['plan']['checks'][0]['entry'] = 'src/value.py'
                elif variant == 'time':
                    for field in ('started_at', 'finished_at'):
                        record[field] = (datetime.fromisoformat(record[field]) - timedelta(days=1)).isoformat()
                elif variant == 'version':
                    envelope['version'] = 1
                receipt.write_text(json.dumps(record), encoding='utf-8')
                result_path.write_text(json.dumps(result), encoding='utf-8')
                envelope['result'] = file_evidence(str(result_path))
                envelope['records'] = [] if variant == 'missing-record' else [dict(file_evidence(str(receipt)), name=envelope['records'][0]['name'])]
                self.refresh_runtime_event(changed, envelope)
                with self.assertRaises(ValueError):
                    self.store.receive(self.state['revision'], changed)
                self.assertEqual(before, self.store.path.read_bytes())

    def test_masked_cli_argv_and_omitted_output_retain_operation_linkage(self):
        self.prepare()
        variable, value = 'DS_TEST_RUNTIME_SECRET', 'SYNTHETIC_RUNTIME_VALUE'
        previous = os.environ.get(variable)
        os.environ[variable] = value
        try:
            self.plan['redaction'] = {'secret_env': [variable], 'omit_output': True}
            self.plan['checks'][0]['argv'].append('password=' + value)
            event = self.run_runtime()
            envelope = json.loads(Path(event['runtime_evidence']['path']).read_text(encoding='utf-8'))
            result_text = Path(envelope['result']['path']).read_text(encoding='utf-8')
            receipt_text = Path(envelope['records'][0]['path']).read_text(encoding='utf-8')
            self.assertNotIn(value, result_text + receipt_text)
            self.assertEqual(json.loads(receipt_text)['stdout'], '[omitted]')
            self.finish_professionals(event)
            self.assertEqual(self.store.complete(self.state['revision'], [{'id': '1', 'source_frame_id': self.frame['frame_id']}])['status'], 'completed')
        finally:
            if previous is None:
                os.environ.pop(variable, None)
            else:
                os.environ[variable] = previous
    def test_target_execution_cannot_escape_original_bound_roots(self):
        self.prepare(narrow_execution=True)
        before = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.run_runtime()
        self.assertEqual(before, self.store.path.read_bytes())

    def finish_professionals(self, event):
        self.state = self.store.receive(self.state['revision'], event)
        self.state = self.store.dispatch(self.state['revision'], 'code-review', 'v1', parent_frame_id=self.parent['frame_id'])
        for frame in (self.state['frames'][-1], self.parent):
            result = {**event, 'event_id': uuid.uuid4().hex, 'frame_id': frame['frame_id'],
                'parent_frame_id': frame['parent_frame_id'], 'skill_id': frame['skill_id'], 'acceptance_checks': []}
            del result['runtime_evidence']
            self.state = self.store.receive(self.state['revision'], result)

    def test_public_completion_and_changed_candidate_gate(self):
        self.prepare()
        self.finish_professionals(self.run_runtime())
        refs = [{'id': '1', 'source_frame_id': self.frame['frame_id']}]
        original = (self.worktree / 'src/main.js').read_bytes()
        (self.worktree / 'src/main.js').write_text("console.log('changed');\n", encoding='utf-8')
        before = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.complete(self.state['revision'], refs)
        self.assertEqual(before, self.store.path.read_bytes())
        (self.worktree / 'src/main.js').write_bytes(original)
        self.state = self.store.complete(self.state['revision'], refs)
        self.assertEqual(self.state['status'], 'completed')

    def test_expected_nonzero_is_business_success_without_rewriting_raw_check(self):
        self.prepare('invalid\n')
        (self.worktree / 'src/main.js').write_text("console.log('invalid'); process.exit(1);\n", encoding='utf-8')
        self.plan['checks'][0]['expected_exit'] = 1
        event = self.run_runtime()
        envelope = json.loads(Path(event['runtime_evidence']['path']).read_text(encoding='utf-8'))
        record = json.loads(Path(envelope['records'][0]['path']).read_text(encoding='utf-8'))
        self.assertEqual(record['status'], 'failed')
        self.assertEqual(event['acceptance_checks'][0]['status'], 'passed')
        self.finish_professionals(event)
        self.assertEqual(self.store.complete(self.state['revision'], [{'id': '1', 'source_frame_id': self.frame['frame_id']}])['status'], 'completed')

    def test_explicit_scoped_decision_preserves_original_criterion_and_evidence(self):
        from workflow_state import file_evidence
        decision = self.root / 'synthetic-user-decision.md'
        decision.write_text('Synthetic protocol fixture: defer business only.', encoding='utf-8')
        adjustment = {'action': 'defer', 'check_ids': ['business'], 'reason': 'synthetic scope decision',
            'user_reference': 'synthetic fixture, not live user approval', 'decision_evidence': file_evidence(str(decision))}
        self.prepare(adjustments=[adjustment])
        event = self.run_runtime()
        self.finish_professionals(event)
        self.state = self.store.complete(self.state['revision'], [{'id': '1', 'source_frame_id': self.frame['frame_id']}])
        criterion = self.state['acceptance'][0]
        self.assertEqual(criterion['criterion'], 'business')
        self.assertEqual(criterion['status'], 'passed_with_adjustment')
        self.assertEqual(criterion['original_runtime_acceptance'], 'deferred')
        self.assertEqual(criterion['scope_adjustments'], [adjustment])

    def test_required_dependency_blocked_remains_unaccepted(self):
        self.prepare()
        self.plan['dependencies'] = [{'mode': 'real', 'required': True, 'available': False, 'evidence': 'synthetic absent dependency'}]
        event = self.run_runtime()
        self.assertTrue(event['blockers'])
        self.assertEqual(event['acceptance_checks'][0]['status'], 'pending')
        self.state = self.store.receive(self.state['revision'], event)
        with self.assertRaises(ValueError):
            self.store.complete(self.state['revision'], [{'id': '1', 'source_frame_id': self.frame['frame_id']}])

    def test_same_run_scoped_adjustment_retains_failed_history_and_original_plan(self):
        from workflow_state import file_evidence
        self.prepare('wrong\n')
        failure = self.run_runtime()
        self.state = self.store.receive(self.state['revision'], failure)
        original_plan = copy.deepcopy(self.state['plan'])
        original_frame = self.frame['frame_id']
        decision = self.root / 'later-decision.md'
        decision.write_text('Synthetic fixture later defers business.', encoding='utf-8')
        adjustment = {'action': 'defer', 'check_ids': ['business'], 'reason': 'synthetic later decision',
            'user_reference': 'synthetic fixture only', 'decision_evidence': file_evidence(str(decision))}
        self.state = self.store.adjust_runtime_scope(self.state['revision'], self.parent['frame_id'], adjustment)
        self.assertEqual(self.state['plan'], original_plan)
        self.assertEqual(self.state['events'][0]['event_id'], failure['event_id'])
        self.assertEqual(next(f for f in self.state['frames'] if f['frame_id'] == original_frame)['status'], 'superseded')
        self.state = self.store.dispatch(self.state['revision'], 'runtime-verify', 'v1',
            parent_frame_id=self.parent['frame_id'], worktree=self.authorization())
        self.frame = self.state['frames'][-1]
        self.plan['scope_adjustments'] = [adjustment]
        self.finish_professionals(self.run_runtime())
        self.state = self.store.complete(self.state['revision'], [{'id': '1', 'source_frame_id': self.frame['frame_id']}])
        self.assertEqual(self.state['acceptance'][0]['status'], 'passed_with_adjustment')
        self.assertEqual(self.state['runtime_scope_amendments'][0]['original_contract']['scope_adjustments'], [])

    def test_wrong_binding_and_missing_original_record_do_not_mutate_receipt(self):
        self.prepare()
        event = self.run_runtime()
        path = Path(event['runtime_evidence']['path'])
        envelope = json.loads(path.read_text(encoding='utf-8'))
        original = path.read_bytes()
        envelope['identity']['input_version'] = 'other'
        path.write_text(json.dumps(envelope), encoding='utf-8')
        from workflow_state import file_evidence
        event['runtime_evidence'] = file_evidence(str(path))
        event['artifacts'] = [event['runtime_evidence']]
        event['acceptance_checks'][0]['evidence'] = [event['runtime_evidence']]
        before = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.receive(self.state['revision'], event)
        self.assertEqual(before, self.store.path.read_bytes())
        path.write_bytes(original)
        Path(envelope['records'][0]['path']).unlink()
        event['runtime_evidence'] = file_evidence(str(path))
        event['artifacts'] = [event['runtime_evidence']]
        event['acceptance_checks'][0]['evidence'] = [event['runtime_evidence']]
        with self.assertRaises((ValueError, OSError)):
            self.store.receive(self.state['revision'], event)
        self.assertEqual(before, self.store.path.read_bytes())

    def test_actual_node_receipt_preserves_binding_and_blocks_dirty_candidate(self):
        self.prepare()
        event = self.run_runtime()
        self.assertEqual(event['acceptance_checks'][0]['status'], 'passed')
        self.state = self.store.receive(self.state['revision'], event)
        self.assertEqual(self.state['frames'][-1]['worktree_binding'], self.frame['worktree_binding'])
        (self.worktree / 'src/main.js').write_text("console.log('changed');\n", encoding='utf-8')
        before = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.dispatch(self.state['revision'], 'code-review', 'v1', parent_frame_id=self.parent['frame_id'])
        self.assertEqual(before, self.store.path.read_bytes())

    def test_exit_zero_wrong_business_cannot_pass_or_clear_parent(self):
        self.prepare('wrong\n')
        event = self.run_runtime()
        self.assertEqual(event['acceptance_checks'][0]['status'], 'pending')
        forged = copy.deepcopy(event)
        forged['acceptance_checks'][0].update(status='passed', completion_checked=True)
        before = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.receive(self.state['revision'], forged)
        self.assertEqual(before, self.store.path.read_bytes())
        self.state = self.store.receive(self.state['revision'], event)
        self.assertTrue(self.state['frames'][-1]['needs_assessment'])

    def test_missing_runtime_envelope_is_rejected_without_mutation(self):
        self.prepare()
        event = self.run_runtime()
        del event['runtime_evidence']
        before = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.receive(self.state['revision'], event)
        self.assertEqual(before, self.store.path.read_bytes())
