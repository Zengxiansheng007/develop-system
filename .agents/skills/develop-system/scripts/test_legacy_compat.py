"""Public legacy continuation; professional facts and authorization are explicitly synthetic."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import subprocess
import unittest
import test_run_migration as fixtures
from workflow_state import RunStore, SKILL_ROOT, file_evidence, ready_steps


class LegacyCompatibilityTests(unittest.TestCase):
    setUp = fixtures.RunMigrationTests.setUp
    save = fixtures.RunMigrationTests.save
    old = fixtures.RunMigrationTests.old
    preview = fixtures.RunMigrationTests.preview
    old_fields = fixtures.RunMigrationTests.old_fields
    finish_legacy = fixtures.RunMigrationTests.finish_legacy

    def start_legacy(self, inputs=()):
        options = [arg for path in inputs for arg in ('--input', str(path))]
        self.state = self.old('dispatch', '--revision', str(self.state['revision']),
            '--skill-id', 'codebase-design', '--step-id', 'a', '--input-version', 'synthetic-old-v1', *options)
        return self.state['frames'][-1]

    def compatible_preview(self):
        adoption = self.preview()['adoption']
        adoption['compatibility'] = {'contract': 'state-v3',
            'frame_ids': [f['frame_id'] for f in self.state['frames']],
            'basis': file_evidence(str(self.basis))}
        return self.store.preview_migration(adoption=adoption)

    def test_adoption_preserves_in_flight_identity_contract_and_unknown_obligations(self):
        frame = self.start_legacy()
        before = self.store.path.read_bytes()
        result = self.store.apply_migration(self.compatible_preview())
        state = result['state']
        self.assertEqual(state['frames'], self.state['frames'])
        self.assertEqual(state['events'], self.state['events'])
        self.assertEqual(state['plan'], self.state['plan'])
        self.assertEqual(state['frames'][0]['status'], 'running')
        self.assertNotIn('protocol_version', state['frames'][0])
        self.assertNotIn('completion_obligations', state['frames'][0])
        self.assertEqual(Path(result['recovery']['original_state']['path']).read_bytes(), before)
        self.assertEqual(state['migration']['compatibility']['frames'][frame['frame_id']], frame)
        self.assertEqual(ready_steps(state), [])

    def old_result(self, frame, event_id='synthetic-old-return'):
        artifact = self.project / (event_id + '.md')
        artifact.write_text('Synthetic professional result, not model execution.', encoding='utf-8')
        return {**{key: frame[key] for key in ('frame_id', 'parent_frame_id', 'skill_id', 'input_version', 'mode')},
            'event_id': event_id, 'run_id': self.store.run_id, 'execution': 'sequential',
            'status': 'completed', 'return_to': 'develop-system', 'completion_checked': True,
            'artifacts': [str(artifact)], 'checks': [], 'findings': [], 'blockers': [],
            'acceptance_checks': [], 'resolved_findings': []}

    def test_old_return_is_retained_pending_once_without_inventing_new_qualification(self):
        frame = self.start_legacy()
        applied = self.store.apply_migration(self.compatible_preview())
        result = self.old_result(frame)
        state = self.store.receive(applied['state']['revision'], result)
        receipt = state['migration']['compatibility']['receipts'][-1]
        self.assertEqual(receipt['payload'], result)
        self.assertEqual(receipt['status'], 'pending')
        self.assertEqual(state['frames'][0]['status'], 'running')
        self.assertNotIn('completion_obligations', state['frames'][0])
        self.assertEqual(state['events'], applied['state']['events'])
        before = self.store.path.read_bytes()
        self.assertEqual(self.store.receive(state['revision'], result), state)
        self.assertEqual(self.store.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.store.receive(state['revision'], {**result, 'findings': ['conflicting duplicate']})
        self.assertEqual(self.store.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.store.rollback_migration(state['revision'], applied['receipt'])
        with self.assertRaises(ValueError):
            self.store.complete(state['revision'], [])

    def professional_verification(self, frame, checks=(), required_calls=(), root=False,
            identity_status='verified', obligations_known=True, pause=False, parent=None, system_holds=False):
        state = self.store.read()
        if root:
            step = 'verify-' + frame['frame_id']
            state = self.store.extend_plan(state['revision'], [{'step_id': step, 'skill_id': 'code-review'}],
                'Synthetic in-scope professional verification of adopted history')
            state = self.store.dispatch(state['revision'], 'code-review', 'synthetic-qualification-v1', step_id=step)
        else:
            state = self.store.dispatch(state['revision'], 'code-review', 'synthetic-qualification-v1',
                parent_frame_id=parent or frame['frame_id'])
        reviewer = state['frames'][-1]
        report = self.project / (reviewer['frame_id'] + '.md')
        report.write_text('Synthetic professional applicability/identity assessment of original frame and sources.', encoding='utf-8')
        evidence = [file_evidence(str(report))]
        conclusion = {'frame_id': frame['frame_id'], 'input_version': frame['input_version'],
            'source_sha256': state['migration']['source']['sha256'],
            'identity': {'status': identity_status, 'evidence': evidence},
            'obligations': {'required_calls': list(required_calls) if obligations_known else None, 'evidence': evidence},
            'checks': [{**check, 'evidence': evidence} for check in checks]}
        event = self.old_result(reviewer, reviewer['frame_id'] + '-return')
        event.update(protocol_version=1, artifacts=[str(report)], legacy_verifications=[conclusion])
        if system_holds:
            event['system_conclusions'] = [{'restriction_id': 'synthetic-system-' + str(i), 'action': 'set',
                'scope': 'global', 'failed_preconditions': ['synthetic-prerequisite-' + str(i)],
                'evidence': [str(report)]} for i in range(2)]
        if pause:
            state = self.store.control(state['revision'], 'paused', 'Synthetic user pause before reviewer return')
        state = self.store.receive(state['revision'], event)
        return reviewer, state

    def test_professional_qualification_completes_old_work_without_rewriting_its_contract(self):
        frame = self.start_legacy()
        applied = self.store.apply_migration(self.compatible_preview())
        result = self.old_result(frame)
        state = self.store.receive(applied['state']['revision'], result)
        reviewer, state = self.professional_verification(frame)
        state = self.store.qualify_legacy(state['revision'], frame['frame_id'], reviewer['frame_id'])
        self.assertEqual(state['frames'][0]['status'], 'completed')
        self.assertNotIn('completion_obligations', state['frames'][0])
        self.assertNotIn('protocol_version', state['frames'][0])
        receipt = state['migration']['compatibility']['receipts'][0]
        self.assertEqual(receipt['payload'], result)
        self.assertEqual(receipt['status'], 'qualified')
        self.assertEqual(len(state['migration']['compatibility']['qualifications']), 1)
        self.assertEqual([s['step_id'] for s in ready_steps(state)], ['b'])
        state = self.store.dispatch(state['revision'], 'domain-modeling', 'synthetic-new-v1', step_id='b')
        new = state['frames'][-1]
        before = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.receive(state['revision'], self.old_result(new, 'synthetic-new-spoof'))
        self.assertEqual(self.store.path.read_bytes(), before)

    def real_old_check(self, fail=False):
        source = self.project / 'value.py'
        source.write_text('VALUE = 18\n' if fail else 'VALUE = 17\n', encoding='utf-8')
        entry = self.project / 'original-check.py'
        observation = self.project / 'original-loading.json'
        counter = self.project / 'executions.txt'
        entry.write_text("import hashlib,json,pathlib,value\n"
            "p=pathlib.Path(value.__file__).resolve()\n"
            "data={'files':[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}]}\n"
            "pathlib.Path('original-loading.json').write_text(json.dumps(data),encoding='utf-8')\n"
            "c=pathlib.Path('executions.txt');c.write_text(str(int(c.read_text())+1) if c.exists() else '1')\n"
            "assert value.VALUE == 17\nprint(json.dumps(data))\n", encoding='utf-8')
        frozen = Path(self.save('original-frozen', {'files': [file_evidence(str(source)), file_evidence(str(entry))]}))
        frame = self.start_legacy([frozen])
        record_path = self.project / 'original-v1-check.json'
        command = [sys.executable, '-X', 'utf8', '-B', str(self.legacy / 'scripts/record_check.py'),
            '--output', str(record_path), '--cwd', str(self.project), '--',
            sys.executable, '-X', 'utf8', '-B', str(entry)]
        executed = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(executed.returncode, 1 if fail else 0, executed.stdout + executed.stderr)
        record = json.loads(record_path.read_text(encoding='utf-8'))
        self.assertEqual(record['schema_version'], 1)
        result = self.old_result(frame)
        result['checks'] = [{'record': str(record_path), 'status': record['status']}]
        result['artifacts'].append(str(observation))
        statement = {'record': file_evidence(str(record_path)), 'record_id': record['record_id'],
            'decision': 'reuse', 'frozen': file_evidence(str(frozen)), 'loaded': file_evidence(str(observation)),
            'candidate': json.loads(frozen.read_text(encoding='utf-8'))['files']}
        return frame, result, statement, record_path, counter

    def test_actual_v1_execution_can_be_qualified_without_rerun_or_record_upgrade(self):
        frame, result, statement, record_path, counter = self.real_old_check()
        original = record_path.read_bytes()
        state = self.store.apply_migration(self.compatible_preview())['state']
        state = self.store.receive(state['revision'], result)
        reviewer, state = self.professional_verification(frame, [statement])
        state = self.store.qualify_legacy(state['revision'], frame['frame_id'], reviewer['frame_id'])
        self.assertEqual(state['frames'][0]['status'], 'completed')
        self.assertEqual(record_path.read_bytes(), original)
        self.assertEqual(counter.read_text(), '1')
        self.assertEqual(state['frames'][0]['checks'][0]['record'], file_evidence(str(record_path)))
        # Currentness consumes qualification, not a fabricated v2 binding.
        state = self.store.dispatch(state['revision'], 'domain-modeling', 'synthetic-new', step_id='b')
        self.assertEqual(state['frames'][-1]['protocol_version'], 1)

    def test_previously_accepted_v1_history_is_qualified_without_replacing_old_event(self):
        frame, result, statement, record_path, counter = self.real_old_check()
        self.state = self.old('return', '--revision', str(self.state['revision']), '--payload', self.save('old-return', result))
        old_events = copy.deepcopy(self.state['events'])
        original = record_path.read_bytes()
        state = self.store.apply_migration(self.compatible_preview())['state']
        reviewer, state = self.professional_verification(frame, [statement], root=True)
        state = self.store.qualify_legacy(state['revision'], frame['frame_id'], reviewer['frame_id'])
        self.assertEqual(state['events'][:len(old_events)], old_events)
        self.assertEqual(sum(e['event_id'] == result['event_id'] for e in state['events']), 1)
        self.assertEqual(record_path.read_bytes(), original)
        self.assertEqual(counter.read_text(), '1')
        self.assertEqual(state['migration']['compatibility']['qualifications'][-1]['status'], 'qualified')
        before = self.store.path.read_bytes()
        self.assertEqual(self.store.receive(state['revision'], result), state)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_recorded_original_return_is_idempotent_after_adoption(self):
        frame = self.start_legacy()
        result = self.old_result(frame)
        self.state = self.old('return', '--revision', str(self.state['revision']),
                              '--payload', self.save('original-result', result))
        state = self.store.apply_migration(self.compatible_preview())['state']
        before = self.store.path.read_bytes()
        self.assertEqual(self.store.receive(state['revision'], result), state)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_returned_old_worker_does_not_block_independent_authorized_work(self):
        frame = self.start_legacy()
        state = self.store.apply_migration(self.compatible_preview())['state']
        state = self.store.receive(state['revision'], self.old_result(frame))
        self.assertEqual([s['step_id'] for s in ready_steps(state)], ['b'])
        state = self.store.dispatch(state['revision'], 'domain-modeling', 'synthetic-independent', step_id='b')
        self.assertEqual(state['frames'][-1]['step_id'], 'b')
        self.assertEqual(state['migration']['compatibility']['receipts'][0]['status'], 'pending')

    def assert_retained_reference_cannot_be_rebound(self, kind, historical=False):
        if kind == 'check':
            frame, result, statement, target, counter = self.real_old_check()
            statements = [statement]
        else:
            frame = self.start_legacy()
            result, statements = self.old_result(frame), []
            target = Path(result['artifacts'][0])
            if kind != 'artifact':
                target = self.project / ('retained-' + kind + '.md')
                target.write_text('Synthetic original retained evidence A.', encoding='utf-8')
                if kind == 'acceptance':
                    result['acceptance_checks'] = [{'id': '1', 'kind': 'technical', 'status': 'passed',
                        'completion_checked': True, 'evidence': [str(target)]}]
                else:
                    result['grill_delivery'] = {'document': str(target)}
        original_bytes = target.read_bytes()
        if historical:
            self.state = self.old('return', '--revision', str(self.state['revision']),
                                  '--payload', self.save('retained-history', result))
        state = self.store.apply_migration(self.compatible_preview())['state']
        if not historical:
            state = self.store.receive(state['revision'], result)
        reviewer, state = self.professional_verification(frame, statements, root=historical)
        before = self.store.path.read_bytes()
        target.write_bytes(original_bytes + b'\nSynthetic replacement B after professional return.')
        with self.assertRaises(ValueError):
            self.store.qualify_legacy(state['revision'], frame['frame_id'], reviewer['frame_id'])
        self.assertEqual(self.store.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.store.complete(state['revision'], [])
        self.assertEqual(self.store.path.read_bytes(), before)
        target.write_bytes(original_bytes)
        state = self.store.qualify_legacy(state['revision'], frame['frame_id'], reviewer['frame_id'])
        receipt = state['migration']['compatibility']['receipts'][-1]
        accepted = next(e for e in state['events'] if e['event_id'] == result['event_id'])
        self.assertEqual(accepted, receipt['normalized'])
        if not historical:
            self.assertEqual(receipt['payload'], result)
        if kind == 'check':
            self.assertEqual(counter.read_text(), '1')
        if kind in {'acceptance', 'grill'}:
            target.write_bytes(original_bytes + b'\nSynthetic later change after qualification.')
            with self.assertRaises(ValueError):
                self.store.complete(state['revision'], [])
            state = self.store.resume(state['revision'])
            self.assertEqual(state['frames'][0]['status'], 'stale')
            self.assertEqual(state['migration']['compatibility']['receipts'][-1]['normalized'], accepted)

    def test_qualification_cannot_rebind_retained_artifact(self):
        self.assert_retained_reference_cannot_be_rebound('artifact')

    def test_qualification_cannot_rebind_retained_acceptance_evidence(self):
        self.assert_retained_reference_cannot_be_rebound('acceptance')

    def test_qualification_cannot_rebind_retained_grill_document(self):
        self.assert_retained_reference_cannot_be_rebound('grill')

    def test_qualification_cannot_rebind_retained_check_record(self):
        self.assert_retained_reference_cannot_be_rebound('check')

    def test_historical_qualification_cannot_rebind_original_artifact(self):
        self.assert_retained_reference_cannot_be_rebound('artifact', historical=True)

    def test_insufficient_identity_or_unknown_calls_remain_pending_and_idempotent(self):
        frame = self.start_legacy()
        state = self.store.apply_migration(self.compatible_preview())['state']
        result = self.old_result(frame)
        state = self.store.receive(state['revision'], result)
        reviewer, state = self.professional_verification(frame, identity_status='insufficient', obligations_known=False)
        state = self.store.qualify_legacy(state['revision'], frame['frame_id'], reviewer['frame_id'])
        qualification = state['migration']['compatibility']['qualifications'][-1]
        self.assertEqual(qualification['status'], 'pending')
        self.assertEqual(state['frames'][0]['status'], 'running')
        self.assertEqual(state['migration']['compatibility']['receipts'][0]['payload'], result)
        self.assertNotIn('completion_obligations', state['frames'][0])
        before = self.store.path.read_bytes()
        self.assertEqual(self.store.qualify_legacy(state['revision'], frame['frame_id'], reviewer['frame_id']), state)
        self.assertEqual(self.store.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.store.complete(state['revision'], [])

    def test_cli_qualification_shares_the_public_transition(self):
        frame = self.start_legacy()
        state = self.store.apply_migration(self.compatible_preview())['state']
        state = self.store.receive(state['revision'], self.old_result(frame))
        reviewer, state = self.professional_verification(frame)
        command = [sys.executable, '-X', 'utf8', '-B', str(SKILL_ROOT / 'scripts/workflow_state.py'),
            'qualify-legacy', '--entry', 'develop-system', '--project-root', str(self.project),
            '--run-id', self.store.run_id, '--revision', str(state['revision']),
            '--frame-id', frame['frame_id'], '--source-frame-id', reviewer['frame_id']]
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        state = json.loads(result.stdout)
        self.assertEqual(state['frames'][0]['status'], 'completed')
        self.assertEqual(self.store.read(), state)

    def test_pause_retains_old_and_professional_returns_without_applying_qualification(self):
        frame = self.start_legacy()
        state = self.store.apply_migration(self.compatible_preview())['state']
        state = self.store.control(state['revision'], 'paused', 'Synthetic user pause')
        control = copy.deepcopy(state['control'])
        result = self.old_result(frame)
        state = self.store.receive(state['revision'], result)
        self.assertEqual(state['control'], control)
        self.assertEqual(state['next_decision']['action'], 'paused')
        self.assertEqual(self.store.receive(state['revision'], result), state)
        with self.assertRaises(ValueError):
            self.store.dispatch(state['revision'], 'code-review', 'verification', parent_frame_id=frame['frame_id'])
        state = self.store.resume(state['revision'], 'Synthetic explicit user continue')
        reviewer, state = self.professional_verification(frame, pause=True)
        before = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.qualify_legacy(state['revision'], frame['frame_id'], reviewer['frame_id'])
        self.assertEqual(self.store.path.read_bytes(), before)
        state = self.store.resume(state['revision'], 'Synthetic user continue after reviewer return')
        state = self.store.qualify_legacy(state['revision'], frame['frame_id'], reviewer['frame_id'])
        self.assertEqual(state['frames'][0]['status'], 'completed')

    def test_wrong_old_identity_and_protocol_reject_without_retention(self):
        frame = self.start_legacy()
        state = self.store.apply_migration(self.compatible_preview())['state']
        result = self.old_result(frame)
        before = self.store.path.read_bytes()
        for changes in ({'run_id': 'wrong'}, {'frame_id': 'unknown'}, {'input_version': 'obsolete'},
                {'parent_frame_id': 'wrong'}, {'skill_id': 'tdd'}, {'protocol_version': 1},
                {'status': 'imaginary'}, {'completion_checked': False}, {'checks': 'not-an-array'}):
            with self.subTest(changes=changes), self.assertRaises((ValueError, TypeError)):
                self.store.receive(state['revision'], {**result, **changes})
            self.assertEqual(self.store.path.read_bytes(), before)

    def test_superseded_old_execution_cannot_return_through_the_inventory(self):
        value = self.project / 'original-input.txt'
        value.write_text('original', encoding='utf-8')
        old_frame = self.start_legacy([value])
        value.write_text('changed', encoding='utf-8')
        self.state = self.old('resume', '--revision', str(self.state['revision']))
        self.state = self.old('dispatch', '--revision', str(self.state['revision']), '--skill-id',
            'codebase-design', '--step-id', 'a', '--input-version', 'synthetic-new-old-version')
        self.assertEqual(self.state['frames'][0]['status'], 'superseded')
        state = self.store.apply_migration(self.compatible_preview())['state']
        before = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.receive(state['revision'], self.old_result(old_frame))
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_incomplete_check_basis_requests_only_the_affected_execution_scope(self):
        frame, result, statement, record_path, counter = self.real_old_check()
        del statement['loaded']
        original = record_path.read_bytes()
        state = self.store.apply_migration(self.compatible_preview())['state']
        state = self.store.receive(state['revision'], result)
        reviewer, state = self.professional_verification(frame, [statement])
        state = self.store.qualify_legacy(state['revision'], frame['frame_id'], reviewer['frame_id'])
        need = state['migration']['compatibility']['qualifications'][-1]
        self.assertEqual(need['status'], 'pending')
        self.assertEqual([c['frame_id'] for c in need['revalidation']], [frame['frame_id']])
        self.assertEqual([c['record_id'] for c in need['revalidation']], [statement['record_id']])
        self.assertEqual(state['frames'][0]['status'], 'running')
        self.assertEqual(record_path.read_bytes(), original)
        self.assertEqual(counter.read_text(), '1')

    def test_failed_actual_old_check_cannot_support_reuse_pass(self):
        frame, result, statement, record_path, counter = self.real_old_check(fail=True)
        state = self.store.apply_migration(self.compatible_preview())['state']
        state = self.store.receive(state['revision'], result)
        reviewer, state = self.professional_verification(frame, [statement])
        before = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.qualify_legacy(state['revision'], frame['frame_id'], reviewer['frame_id'])
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(json.loads(record_path.read_text(encoding='utf-8'))['status'], 'failed')
        self.assertEqual(counter.read_text(), '1')

    def test_declared_mandatory_work_requires_the_actual_bound_child(self):
        frame = self.start_legacy()
        state = self.store.apply_migration(self.compatible_preview())['state']
        state = self.store.receive(state['revision'], self.old_result(frame))
        descriptor = next(s for s in state['registry']['skills'] if s['id'] == 'writing-for-agents')
        source = file_evidence(str((Path(state['registry_path']).parent / descriptor['path']).resolve()))
        duty = {'skill_id': 'writing-for-agents', 'skill_path': source['path'], 'skill_sha256': source['sha256']}
        reviewer, state = self.professional_verification(frame, required_calls=[duty])
        before = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.qualify_legacy(state['revision'], frame['frame_id'], reviewer['frame_id'])
        self.assertEqual(self.store.path.read_bytes(), before)
        state = self.store.dispatch(state['revision'], 'writing-for-agents', 'synthetic-actual-child', parent_frame_id=frame['frame_id'])
        child = state['frames'][-1]
        event = self.old_result(child, 'synthetic-writing-return')
        event['protocol_version'] = 1
        state = self.store.receive(state['revision'], event)
        state = self.store.qualify_legacy(state['revision'], frame['frame_id'], reviewer['frame_id'])
        self.assertEqual(state['frames'][0]['status'], 'completed')

    def test_changed_qualified_candidate_invalidates_only_its_related_work(self):
        frame, result, statement, record_path, counter = self.real_old_check()
        state = self.store.apply_migration(self.compatible_preview())['state']
        state = self.store.receive(state['revision'], result)
        reviewer, state = self.professional_verification(frame, [statement])
        state = self.store.qualify_legacy(state['revision'], frame['frame_id'], reviewer['frame_id'])
        state = self.store.dispatch(state['revision'], 'domain-modeling', 'synthetic-independent', step_id='b')
        independent = state['frames'][-1]
        event = self.old_result(independent, 'synthetic-independent-return'); event['protocol_version'] = 1
        state = self.store.receive(state['revision'], event)
        Path(statement['candidate'][0]['path']).write_text('VALUE = 18\n', encoding='utf-8')
        state = self.store.resume(state['revision'])
        self.assertEqual(state['frames'][0]['status'], 'stale')
        self.assertEqual(next(f for f in state['frames'] if f['frame_id'] == independent['frame_id'])['status'], 'completed')
        self.assertEqual(counter.read_text(), '1')

    def test_modern_event_cannot_reuse_a_pending_legacy_event_identity(self):
        frame = self.start_legacy()
        state = self.store.apply_migration(self.compatible_preview())['state']
        result = self.old_result(frame)
        state = self.store.receive(state['revision'], result)
        state = self.store.dispatch(state['revision'], 'code-review', 'synthetic-review', parent_frame_id=frame['frame_id'])
        event = self.old_result(state['frames'][-1], 'synthetic-modern-collision')
        event.update(protocol_version=1, event_id=result['event_id'])
        before = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.receive(state['revision'], event)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_old_parent_cannot_complete_over_an_unqualified_old_child(self):
        parent = self.start_legacy()
        self.state = self.old('dispatch', '--revision', str(self.state['revision']), '--skill-id', 'tdd',
            '--parent-frame-id', parent['frame_id'], '--input-version', 'synthetic-old-child')
        child = self.state['frames'][-1]
        self.state = self.old('return', '--revision', str(self.state['revision']), '--payload',
            self.save('original-child-event', self.old_result(child, 'synthetic-old-child-return')))
        state = self.store.apply_migration(self.compatible_preview())['state']
        state = self.store.receive(state['revision'], self.old_result(parent))
        reviewer, state = self.professional_verification(parent)
        before = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.qualify_legacy(state['revision'], parent['frame_id'], reviewer['frame_id'])
        self.assertEqual(self.store.path.read_bytes(), before)
        child_reviewer, state = self.professional_verification(child, parent=parent['frame_id'])
        state = self.store.qualify_legacy(state['revision'], child['frame_id'], child_reviewer['frame_id'])
        state = self.store.qualify_legacy(state['revision'], parent['frame_id'], reviewer['frame_id'])
        self.assertEqual(state['frames'][0]['status'], 'completed')

    def test_qualified_old_acceptance_can_close_complete_mixed_run(self):
        frame = self.start_legacy()
        state = self.store.apply_migration(self.compatible_preview())['state']
        result = self.old_result(frame)
        result['acceptance_checks'] = [{'id': '1', 'kind': 'technical', 'status': 'passed',
            'completion_checked': True, 'evidence': list(result['artifacts'])}]
        state = self.store.receive(state['revision'], result)
        reviewer, state = self.professional_verification(frame)
        state = self.store.qualify_legacy(state['revision'], frame['frame_id'], reviewer['frame_id'])
        state = self.store.dispatch(state['revision'], 'domain-modeling', 'synthetic-b', step_id='b')
        event = self.old_result(state['frames'][-1], 'synthetic-b-return'); event['protocol_version'] = 1
        state = self.store.receive(state['revision'], event)
        state = self.store.complete(state['revision'], [{'id': '1', 'source_frame_id': frame['frame_id']}])
        self.assertEqual(state['status'], 'completed')
        self.assertEqual(state['acceptance'][0]['source_frame_id'], frame['frame_id'])
        self.assertNotIn('protocol_version', next(e for e in state['events'] if e['event_id'] == result['event_id']))

    def test_compatibility_facts_do_not_clear_multiple_system_holds(self):
        frame = self.start_legacy()
        state = self.store.apply_migration(self.compatible_preview())['state']
        result = self.old_result(frame)
        state = self.store.receive(state['revision'], result)
        reviewer, state = self.professional_verification(frame, system_holds=True)
        restrictions = copy.deepcopy(state['restrictions'])
        self.assertEqual(len(restrictions), 2)
        state = self.store.qualify_legacy(state['revision'], frame['frame_id'], reviewer['frame_id'])
        self.assertEqual(state['restrictions'], restrictions)
        self.assertEqual(ready_steps(state), [])
        state = self.store.control(state['revision'], 'paused', 'Synthetic user pause on top of system holds')
        self.assertEqual(self.store.receive(state['revision'], result), state)
        self.assertEqual(state['restrictions'], restrictions)
        with self.assertRaises(ValueError):
            self.store.complete(state['revision'], [])

    def test_compatibility_inventory_cannot_omit_or_invent_original_frames(self):
        frame = self.start_legacy()
        adoption = self.compatible_preview()['adoption']
        before = self.store.path.read_bytes()
        for ids in ([], [frame['frame_id'], 'invented'], [frame['frame_id'], frame['frame_id']]):
            candidate = copy.deepcopy(adoption); candidate['compatibility']['frame_ids'] = ids
            with self.assertRaises(ValueError):
                self.store.preview_migration(adoption=candidate)
            self.assertEqual(self.store.path.read_bytes(), before)

    def test_newly_computed_freeze_cannot_replace_original_history_basis(self):
        frame, result, statement, record_path, counter = self.real_old_check()
        original = record_path.read_bytes()
        state = self.store.apply_migration(self.compatible_preview())['state']
        state = self.store.receive(state['revision'], result)
        later = self.project / 'reviewer-later-freeze.json'
        later.write_bytes(Path(statement['frozen']['path']).read_bytes())
        statement['frozen'] = file_evidence(str(later))
        reviewer, state = self.professional_verification(frame, [statement])
        before = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.qualify_legacy(state['revision'], frame['frame_id'], reviewer['frame_id'])
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(record_path.read_bytes(), original)
        self.assertEqual(counter.read_text(), '1')

    def test_qualified_legacy_block_is_local_and_does_not_complete_parent(self):
        frame = self.start_legacy()
        state = self.store.apply_migration(self.compatible_preview())['state']
        result = self.old_result(frame); result.update(status='blocked', blockers=['Synthetic local environment wait'])
        state = self.store.receive(state['revision'], result)
        reviewer, state = self.professional_verification(frame)
        state = self.store.qualify_legacy(state['revision'], frame['frame_id'], reviewer['frame_id'])
        self.assertEqual(state['status'], 'running')
        self.assertEqual(state['frames'][0]['status'], 'blocked')
        self.assertEqual([s['step_id'] for s in ready_steps(state)], ['b'])
        with self.assertRaises(ValueError):
            self.store.complete(state['revision'], [])


if __name__ == '__main__':
    unittest.main()
