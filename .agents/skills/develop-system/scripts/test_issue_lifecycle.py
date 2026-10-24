"""Production lifecycle behavior with real temporary RunStore and controlled GitHub facts."""
import json
import io
from contextlib import redirect_stdout
import unittest

from workflow_state import RunStore, SKILL_ROOT, main as workflow_main
from issue_sync import IssueSync, validate_config
import test_issue_sync as enrollment_fixtures
from test_issue_write import WritableGitHub
import test_issue_write as write_fixtures
import test_issue_batch as batch_fixtures
import test_issue_intervention as intervention_fixtures


class IssueLifecycleTests(unittest.TestCase):
    setUp = enrollment_fixtures.IssueSyncTests.setUp  # Reuse only public temporary authority construction.
    scope = enrollment_fixtures.IssueSyncTests.scope
    finish = enrollment_fixtures.IssueSyncTests.finish

    def prepare(self):
        (self.project / 'candidate.py').write_bytes(b'value = 1  # Synthetic accepted candidate.\n')  # Match independently declared remote bytes on either OS.
        self.github = WritableGitHub()  # Only the external GitHub system is controllable.
        self.sync = IssueSync(self.project, self.github)
        self.config['required_ci'] = [{'type': 'actions', 'workflow_id': 301, 'workflow': '.github/workflows/check-skills.yml',
            'event': 'push', 'job': 'check (' + system + '-latest)'} for system in ('windows', 'ubuntu')]  # Require both synthetic post-merge jobs.
        self.sync.configure(self.config, user_reference='synthetic explicit lifecycle enablement')

    def test_accepted_return_refreshes_initial_binding_and_synchronizes_automatically(self):
        self.prepare()  # No real project configuration or remote write is involved.
        store = RunStore(self.project, 'new-run', github=self.github)  # This is the actual production authority entry.
        state = store.create('fixture lifecycle', self.plan, ['verified behavior'], self.registry, ticket_scopes=[self.scope()])
        binding = self.sync.bind(store, issue_number=15, final_pr={'number': 13, 'repository_id': 77},
            acceptance_map={'1': {'text': 'verified behavior', 'context': ''}})  # Bind the original full ticket before acceptance exists.
        self.assertIsNone(binding['candidate_id'])  # Binding alone supplies no professional acceptance.
        state = self.finish(store, state)  # A real dispatch/receive creates the synthetic specialist source.
        progress = self.sync.status(store)['bindings'][0]['progress']  # Observe persistent public progress, never private rules.
        self.assertEqual(progress['status'], 'synced')
        self.assertEqual(self.github.issue['state'], 'closed')
        self.assertEqual(self.github.issue['state_reason'], 'completed')
        self.assertIn('- [x] verified behavior', self.github.issue['body'])
        self.assertEqual(self.github.issue['labels'], ['keep'])
        self.assertEqual(store.read(), state)  # Synchronization cannot insert another execution authority into RunStore.

    def pending_assessment(self, *, extra_required=False, declare_resolution=True, paused_receipt=False, enabled=True, bind=True):
        self.prepare()
        if not enabled:
            self.sync.configure({**self.config, 'enabled': False}, user_reference='synthetic disabled assessment project')  # This disposable run has no creation-time enrollment.
        self.plan.extend([{'step_id': 'ticket-review', 'skill_id': 'research'},
            {'step_id': 'unrelated-next', 'skill_id': 'to-spec'}])  # Ticket qualification cannot require this independent later work.
        scope = self.scope()
        scope['required_steps'].append('ticket-review')
        if extra_required:
            self.plan.append({'step_id': 'ticket-final', 'skill_id': 'research'})
            scope['required_steps'].append('ticket-final')  # The original ticket still owns this genuinely required unfinished work.
        store = RunStore(self.project, 'assessment-node', github=self.github)
        state = store.create('synthetic assessment lifecycle', self.plan, ['verified behavior'], self.registry, ticket_scopes=[scope])
        if bind:
            self.sync.bind(store, issue_number=15, final_pr={'number': 13, 'repository_id': 77},
                acceptance_map={'1': {'text': 'verified behavior', 'context': ''}})
        original = None
        for step in ('ticket-work', 'ticket-review'):
            state = store.dispatch(state['revision'], 'research', 'ticket-v1', step)
            frame = state['frames'][-1]
            evidence = self.project / (step + '-assessment-evidence.txt')
            evidence.write_text('Explicit synthetic professional conclusion for ' + step, encoding='utf-8')
            event = {'protocol_version': 1, 'event_id': frame['frame_id'] + '-result', 'run_id': store.run_id,
                'frame_id': frame['frame_id'], 'parent_frame_id': None, 'skill_id': 'research', 'input_version': 'ticket-v1',
                'mode': 'skill', 'execution': 'sequential', 'status': 'completed', 'return_to': 'develop-system',
                'completion_checked': True, 'artifacts': [str(evidence)], 'checks': [],
                'findings': ['Synthetic finding requiring independent resolution'] if original is None else [],
                'blockers': [], 'resolved_findings': [original] if original is not None and declare_resolution else [],
                'acceptance_checks': [{'id': '1', 'status': 'passed', 'completion_checked': True,
                    'kind': 'technical', 'evidence': [str(evidence)]}]}  # Synthetic claims enter only the genuine public receive API.
            if original is not None and paused_receipt:
                state = store.control(state['revision'], 'paused', 'synthetic user pause while resolution is in flight')
            state = store.receive(state['revision'], event)
            if original is None:
                original = frame['frame_id']
        return store, state, original, frame['frame_id'], evidence

    def test_final_professional_assessment_synchronizes_its_ticket_before_unrelated_work(self):
        store, state, original, resolver, evidence = self.pending_assessment()  # Reproduce the independent IS22-SPEC-001 scenario.
        self.assertFalse(store.acceptance_projection('77:501')['eligible'])
        self.assertIsNone(self.sync.status(store)['bindings'][0]['candidate_id'])
        self.assertEqual(self.github.writes, [])
        state = store.assess(state['revision'], original, 'Register actual synthetic specialist resolution',
            [str(evidence)], source_frame_id=resolver)
        self.assertTrue(store.acceptance_projection('77:501')['eligible'])
        self.assertEqual(self.github.issue['state'], 'closed')  # Actual professional assessment must trigger foreground synchronization itself.
        self.assertEqual(self.sync.status(store)['bindings'][0]['progress']['status'], 'synced')
        self.assertEqual([kind for kind, _ in self.github.writes], ['body', 'label', 'close'])
        self.assertEqual(state['status'], 'running')
        self.assertEqual(next(step for step in state['plan'] if step['step_id'] == 'unrelated-next')['status'], 'pending')
        self.assertEqual(store.read(), state)  # The hook adds no synthetic authority or accounting reset.
        before = store.path.read_bytes()
        writes = list(self.github.writes)
        self.assertEqual(store.continue_project(user_reference='synthetic explicit continuation after actual assessment')['status'], 'synced')
        self.assertEqual(store.path.read_bytes(), before)
        self.assertEqual(self.github.writes, writes)

    def test_nonfinal_assessment_preserves_original_required_work_and_cannot_adopt_a_candidate(self):
        store, state, original, resolver, evidence = self.pending_assessment(extra_required=True)
        state = store.assess(state['revision'], original, 'Register a valid partial specialist resolution', [str(evidence)], source_frame_id=resolver)
        self.assertFalse(store.acceptance_projection('77:501')['eligible'])
        self.assertIsNone(self.sync.status(store)['bindings'][0]['candidate_id'])
        self.assertEqual(self.github.writes, [])
        before = store.path.read_bytes()
        self.assertEqual(store.continue_project(user_reference='synthetic continuation with unfinished required work')['reason'], 'acceptance_pending')
        self.assertEqual(store.path.read_bytes(), before)

    def test_assessment_requires_a_current_specialist_resolution_and_its_actual_evidence(self):
        for invalid in ('missing-source', 'undeclared-resolution', 'changed-evidence', 'stale-source'):
            with self.subTest(invalid=invalid):
                self.setUp()
                store, state, original, resolver, evidence = self.pending_assessment(declare_resolution=invalid != 'undeclared-resolution')
                if invalid == 'changed-evidence':
                    evidence.write_text('Altered after the accepted specialist return', encoding='utf-8')
                if invalid == 'stale-source':
                    (self.project / 'research.md').write_text('Changed specialist source after the return', encoding='utf-8')
                before = store.path.read_bytes()
                with self.assertRaises(ValueError):
                    store.assess(state['revision'], original, 'A caller assertion alone is insufficient', [str(evidence)],
                        source_frame_id=None if invalid == 'missing-source' else resolver)
                self.assertEqual(store.path.read_bytes(), before)
                self.assertEqual(self.github.writes, [])
                self.assertIsNone(self.sync.status(store)['bindings'][0]['candidate_id'])

    def test_assessment_does_not_qualify_a_candidate_changed_after_the_professional_return(self):
        store, state, original, resolver, evidence = self.pending_assessment()
        (self.project / 'candidate.py').write_text('value = 2  # Unreviewed after the specialist return.\n', encoding='utf-8')
        before = store.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'Resolution source is stale'):
            store.assess(state['revision'], original, 'Register the unchanged specialist conclusion only', [str(evidence)], source_frame_id=resolver)
        self.assertEqual(store.path.read_bytes(), before)  # Existing source currentness rejects the assessment before the new notification can run.
        self.assertFalse(store.acceptance_projection('77:501')['eligible'])
        self.assertIsNone(self.sync.status(store)['bindings'][0]['candidate_id'])
        self.assertEqual(self.github.writes, [])
        self.assertEqual(store.continue_project(user_reference='synthetic continuation cannot review changed source')['reason'], 'acceptance_pending')

    def test_paused_resolution_waits_for_explicit_resume_and_actual_final_assessment(self):
        store, state, original, resolver, evidence = self.pending_assessment(paused_receipt=True)
        before = store.path.read_bytes()
        with self.assertRaises(ValueError):
            store.assess(state['revision'], original, 'Cannot assess during a user pause', [str(evidence)], source_frame_id=resolver)
        self.assertEqual(store.path.read_bytes(), before)
        self.assertEqual(store.continue_project(user_reference='synthetic sync-only request during pause')['reason'], 'run_control_stopped')
        store = RunStore(self.project, store.run_id, github=self.github)  # Deferred receipt survives foreground reconstruction.
        state = store.resume(state['revision'], user_reference='synthetic explicit resume after paused resolution')
        self.assertFalse(store.acceptance_projection('77:501')['eligible'])
        self.assertIsNone(self.sync.status(store)['bindings'][0]['candidate_id'])
        self.assertEqual(self.github.writes, [])  # Explicit resume still cannot replace the missing genuine assessment.
        state = store.assess(state['revision'], original, 'Register actual resumed specialist resolution', [str(evidence)], source_frame_id=resolver)
        self.assertEqual(self.github.issue['state'], 'closed')
        self.assertEqual(self.sync.status(store)['bindings'][0]['acceptance_receipts'][0]['status'], 'applied')
        self.assertEqual(store.read(), state)

    def test_cancelled_run_cannot_register_assessment_or_resume_synchronization(self):
        store, state, original, resolver, evidence = self.pending_assessment()
        state = store.control(state['revision'], 'cancelled', 'synthetic explicit cancellation before assessment')
        before = store.path.read_bytes()
        with self.assertRaises(ValueError):
            store.assess(state['revision'], original, 'Cannot revive a cancelled goal', [str(evidence)], source_frame_id=resolver)
        self.assertEqual(store.continue_project(user_reference='synthetic sync-only request')['reason'], 'run_control_stopped')
        self.assertEqual(store.path.read_bytes(), before)
        self.assertEqual(self.github.writes, [])

    def test_assessment_retains_default_off_unbound_and_creation_generation_boundaries(self):
        for enrollment in ('default-off', 'historical', 'unbound', 'old-generation'):
            with self.subTest(enrollment=enrollment):
                self.setUp()
                store, state, original, resolver, evidence = self.pending_assessment(
                    enabled=enrollment not in {'default-off', 'historical'}, bind=enrollment == 'old-generation')
                if enrollment in {'historical', 'old-generation'}:
                    self.sync.configure({**self.config, 'enabled': False}, user_reference='synthetic explicit disablement')
                    self.sync.configure(self.config, user_reference='synthetic explicit new enablement generation')
                state = store.assess(state['revision'], original, 'Register real local specialist resolution', [str(evidence)], source_frame_id=resolver)
                self.assertTrue(store.acceptance_projection('77:501')['eligible'])
                self.assertEqual(store.last_issue_sync['reason'], 'issue_not_bound' if enrollment == 'unbound' else 'not_enrolled_in_current_generation')
                self.assertEqual(self.github.writes, [])
                self.assertEqual(store.read(), state)

    def test_actual_cli_assessment_runs_the_same_qualified_foreground_hook(self):
        store, state, original, resolver, evidence = self.pending_assessment()
        output = io.StringIO()
        with redirect_stdout(output):
            code = workflow_main(['assess', '--entry', 'develop-system', '--project-root', str(self.project), '--run-id', store.run_id,
                '--revision', str(state['revision']), '--frame-id', original, '--source-frame-id', resolver,
                '--resolution', 'Register actual synthetic CLI specialist resolution', '--evidence', str(evidence)], github=self.github)
        self.assertEqual(code, 0, output.getvalue())
        self.assertEqual(json.loads(output.getvalue()), store.read())
        self.assertEqual(self.github.issue['state'], 'closed')
        self.assertEqual(self.sync.status(store)['bindings'][0]['progress']['status'], 'synced')

    def test_control_during_assessment_sync_only_saves_the_already_inflight_receipt(self):
        for control in ('paused', 'cancelled'):
            with self.subTest(control=control):
                self.setUp()
                store, state, original, resolver, evidence = self.pending_assessment()
                def after_write(kind):
                    if kind == 'body':
                        store.control(store.read()['revision'], control, 'synthetic user control during the assessment-triggered request')
                self.github.after_write = after_write  # Only the real external response boundary changes user control.
                store.assess(state['revision'], original, 'Register actual specialist resolution before the issued request', [str(evidence)], source_frame_id=resolver)
                self.assertEqual([kind for kind, _ in self.github.writes], ['body'])
                self.assertEqual(self.sync.status(store)['bindings'][0]['progress']['operations'][-1]['status'], 'confirmed')
                self.assertEqual(store.read()['status'], control)
                self.assertEqual(self.github.issue['state'], 'open')

    def test_whole_run_completion_uses_the_same_sync_entry_for_its_existing_binding(self):
        self.prepare()
        store = RunStore(self.project, 'batch-completion', github=self.github)
        state = store.create('fixture lifecycle', self.plan, ['verified behavior'], self.registry, ticket_scopes=[self.scope()])
        state = self.finish(store, state)  # Acceptance is valid before this explicit delivery binding is provided.
        self.sync.bind(store, issue_number=15, final_pr={'number': 13, 'repository_id': 77},
            acceptance_map={'1': {'text': 'verified behavior', 'context': ''}})
        self.assertEqual(self.github.writes, [])
        completed = store.complete(state['revision'], [{'id': '1', 'source_frame_id': state['frames'][0]['frame_id']}])
        self.assertEqual(completed['status'], 'completed')
        self.assertEqual(self.sync.status(store)['bindings'][0]['progress']['status'], 'synced')
        self.assertEqual(self.github.issue['state'], 'closed')
        self.assertEqual(self.github.issue['state_reason'], 'completed')
        self.assertIn('- [x] verified behavior', self.github.issue['body'])
        self.assertEqual(self.github.issue['labels'], ['keep'])
        self.assertEqual(store.read(), completed)  # The lifecycle hook preserves the newly completed authority exactly.

    def test_pause_or_cancel_during_body_request_only_confirms_that_inflight_receipt(self):
        for control in ('paused', 'cancelled'):
            with self.subTest(control=control):
                self.setUp()  # Each control scenario owns a separate disposable project and binding.
                self.prepare()
                store = RunStore(self.project, control + '-inflight', github=self.github)
                state = store.create('fixture lifecycle', self.plan, ['verified behavior'], self.registry, ticket_scopes=[self.scope()])
                self.sync.bind(store, issue_number=15, final_pr={'number': 13, 'repository_id': 77},
                    acceptance_map={'1': {'text': 'verified behavior', 'context': ''}})
                reads_at_control = []
                def after_write(kind):
                    if kind == 'body':
                        current = store.read()
                        store.control(current['revision'], control, 'synthetic user control during the issued request')  # The request is already in flight.
                        reads_at_control.append(self.github.reads)
                self.github.after_write = after_write  # Control the external response boundary, never an internal qualifier.
                self.finish(store, state)
                progress = self.sync.status(store)['bindings'][0]['progress']
                self.assertEqual([kind for kind, _ in self.github.writes], ['body'])
                self.assertEqual(progress['operations'][-1]['status'], 'confirmed')  # Save the real body receipt even after control changed.
                self.assertEqual(self.github.reads, reads_at_control[0] + 1)  # Only that request's Issue readback may follow.
                self.assertEqual(store.read()['status'], control)
                self.assertEqual(self.github.issue['state'], 'open')
                self.assertEqual(store.delivery_updated()['reason'], 'run_control_stopped')
                self.assertEqual([kind for kind, _ in self.github.writes], ['body'])

    def test_pause_or_cancel_during_final_close_reads_prevents_a_new_close_request(self):
        class ControlledClosingRead(WritableGitHub):
            callback = None  # Only the external event-history response is controlled.

            def get_issue_events(self, repository, number):
                if [kind for kind, _ in self.writes] == ['body', 'label'] and self.callback is not None:
                    callback, self.callback = self.callback, None
                    callback()  # User control changes before this still-read-only final close observation returns.
                return super().get_issue_events(repository, number)

        for control in ('paused', 'cancelled'):
            with self.subTest(control=control):
                self.setUp()
                self.prepare()
                self.github = ControlledClosingRead()
                self.sync = IssueSync(self.project, self.github)
                store = RunStore(self.project, control + '-before-close', github=self.github)
                state = store.create('fixture lifecycle', self.plan, ['verified behavior'], self.registry, ticket_scopes=[self.scope()])
                self.sync.bind(store, issue_number=15, final_pr={'number': 13, 'repository_id': 77},
                    acceptance_map={'1': {'text': 'verified behavior', 'context': ''}})
                def stop_before_request():
                    current = store.read()
                    store.control(current['revision'], control, 'synthetic user control while final close facts are read')
                self.github.callback = stop_before_request
                self.finish(store, state)
                self.assertEqual([kind for kind, _ in self.github.writes], ['body', 'label'])  # The close request has not been issued yet and must remain absent.
                self.assertEqual(self.github.issue['state'], 'open')
                self.assertEqual(store.read()['status'], control)
                progress = self.sync.status(store)['bindings'][0]['progress']
                self.assertEqual([operation['status'] for operation in progress['operations']], ['confirmed', 'confirmed'])
                if control == 'paused':
                    current = store.read()
                    store.resume(current['revision'], user_reference='synthetic explicit resume after prevented close')
                    self.assertEqual([kind for kind, _ in self.github.writes], ['body', 'label', 'close'])  # Only the genuinely missing close is performed after fresh qualification.

    def test_delivery_update_rechecks_pending_merge_and_finishes_without_another_acceptance(self):
        self.prepare()
        self.github.pull.update(merged=False, merged_at=None)  # The actual final PR is still awaiting delivery.
        store = RunStore(self.project, 'delivery-run', github=self.github)
        state = store.create('fixture lifecycle', self.plan, ['verified behavior'], self.registry, ticket_scopes=[self.scope()])
        self.sync.bind(store, issue_number=15, final_pr={'number': 13, 'repository_id': 77},
            acceptance_map={'1': {'text': 'verified behavior', 'context': ''}})
        self.finish(store, state)  # Professional acceptance becomes available while merge is pending.
        self.assertEqual(self.sync.status(store)['bindings'][0]['progress']['reason'], 'final_pr_not_merged')
        self.assertEqual(self.github.writes, [])
        before = store.path.read_bytes()
        self.github.pull.update(merged=True, merged_at='2026-10-23T01:00:00Z')  # Supply a later external fact, never a professional assertion.
        result = store.delivery_updated()  # The production delivery node invokes the same public synchronizer.
        self.assertEqual(result['status'], 'synced')
        self.assertEqual(self.github.issue['state'], 'closed')
        self.assertEqual(store.path.read_bytes(), before)  # Delivery notification cannot resume or rewrite development.

    def test_completed_explicit_continue_only_synchronizes_and_keeps_authority_bytes(self):
        self.prepare()
        self.github.jobs[0].update(status='in_progress', conclusion=None)  # Candidate and acceptance cannot replace pending post-merge CI.
        store = RunStore(self.project, 'completed-run', github=self.github)
        state = store.create('fixture lifecycle', self.plan, ['verified behavior'], self.registry, ticket_scopes=[self.scope()])
        self.sync.bind(store, issue_number=15, final_pr={'number': 13, 'repository_id': 77},
            acceptance_map={'1': {'text': 'verified behavior', 'context': ''}})
        state = self.finish(store, state)
        state = store.complete(state['revision'], [{'id': '1', 'source_frame_id': state['frames'][0]['frame_id']}])  # Real completion retains the original professional source.
        before = {path: path.read_bytes() for path in store.root.rglob('*') if path.is_file()}  # Include every original authority artifact, not just its status field.
        self.github.jobs[0].update(status='completed', conclusion='success')
        result = store.continue_project(user_reference='synthetic explicit continuation of this project task')
        self.assertEqual(result['status'], 'synced')
        self.assertEqual(store.read()['status'], 'completed')
        self.assertEqual({path: path.read_bytes() for path in store.root.rglob('*') if path.is_file()}, before)
        self.assertEqual(self.github.issue['state'], 'closed')

    def test_enabled_unbound_creation_does_not_initialize_a_credential_resolver(self):
        self.prepare()
        resolutions = []
        store = RunStore(self.project, 'unbound-run', token_provider=lambda reference: resolutions.append(reference))  # Observe only the external credential boundary.
        state = store.create('unbound lifecycle', self.plan, ['verified behavior'], self.registry, ticket_scopes=[self.scope()])
        self.assertIn('issue_sync_enrollment', state)
        self.assertEqual(resolutions, [])  # An enabled project alone grants no ticket-specific remote work.
        self.assertEqual(store.delivery_updated()['reason'], 'issue_not_bound')
        self.assertEqual(resolutions, [])
        self.assertEqual(self.github.writes, [])

    def test_explicit_user_resume_releases_run_pause_then_synchronizes_without_candidate_adoption(self):
        self.prepare()
        self.github.pull.update(merged=False, merged_at=None)
        store = RunStore(self.project, 'paused-resume', github=self.github)
        state = store.create('fixture lifecycle', self.plan, ['verified behavior'], self.registry, ticket_scopes=[self.scope()])
        self.sync.bind(store, issue_number=15, final_pr={'number': 13, 'repository_id': 77},
            acceptance_map={'1': {'text': 'verified behavior', 'context': ''}})
        state = self.finish(store, state)
        state = store.control(state['revision'], 'paused', 'synthetic user pause')
        self.github.pull.update(merged=True, merged_at='2026-10-23T01:00:00Z')
        self.assertEqual(store.continue_project(user_reference='synthetic sync-only continuation')['reason'], 'run_control_stopped')  # Sync-only continuation never overrides a user pause.
        self.assertEqual(self.github.writes, [])
        state = store.resume(state['revision'], user_reference='synthetic explicit user resume of project development')
        self.assertEqual(state['control']['status'], 'running')
        self.assertEqual(self.github.issue['state'], 'closed')  # The original real resume node now performs the authorized foreground catch-up.
        self.assertEqual(store.read(), state)

    def test_owned_pr_handoff_rejects_native_early_close_and_keeps_a_safe_draft(self):
        self.prepare()
        store = RunStore(self.project, 'pr-handoff', github=self.github)
        store.create('fixture lifecycle', self.plan, ['verified behavior'], self.registry, ticket_scopes=[self.scope()])
        self.sync.bind(store, issue_number=15, final_pr={'number': 13, 'repository_id': 77},
            acceptance_map={'1': {'text': 'verified behavior', 'context': ''}})
        draft = 'Implement the approved behavior.\n\nRelated to #15.'  # A reference carries no native closing instruction.
        result = store.pr_handoff(draft)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['body'], draft)
        self.assertFalse(result['native_auto_close'])
        self.assertEqual(result['issue_references'], ['https://github.com/fixture/project/issues/15'])
        for directive in ('Fixes #15', 'Closes fixture/project#15', 'Resolves https://github.com/fixture/project/issues/15'):
            with self.subTest(directive=directive):
                rejected = store.pr_handoff(draft + '\n\n' + directive)
                self.assertEqual((rejected['status'], rejected['reason']), ('blocked', 'native_early_close_conflict'))
                self.assertNotIn('body', rejected)  # The handoff cannot silently rewrite or approve an unsafe draft.
        self.assertEqual(self.github.writes, [])

    def test_cli_explicit_continue_handles_completed_run_without_revision_or_resume(self):
        self.prepare()
        self.github.jobs[0].update(status='in_progress', conclusion=None)
        store = RunStore(self.project, 'cli-completed', github=self.github)
        for name, value in (('plan', self.plan), ('acceptance', ['verified behavior']), ('scopes', [self.scope()])):
            (self.project / (name + '.json')).write_text(json.dumps(value), encoding='utf-8')  # Actual CLI file inputs are fixed before new-run creation.
        with redirect_stdout(io.StringIO()):
            created = workflow_main(['init', '--entry', 'develop-system', '--project-root', str(self.project), '--run-id', store.run_id,
                '--goal', 'fixture lifecycle', '--plan', str(self.project / 'plan.json'), '--acceptance', str(self.project / 'acceptance.json'),
                '--registry', str(self.registry), '--ticket-scopes', str(self.project / 'scopes.json')], github=self.github)
        self.assertEqual(created, 0)
        state = store.read()
        self.assertEqual(state['ticket_scopes'], [self.scope()])
        self.sync.bind(store, issue_number=15, final_pr={'number': 13, 'repository_id': 77},
            acceptance_map={'1': {'text': 'verified behavior', 'context': ''}})
        state = self.finish(store, state)
        store.complete(state['revision'], [{'id': '1', 'source_frame_id': state['frames'][0]['frame_id']}])
        before = store.path.read_bytes()
        self.github.jobs[0].update(status='completed', conclusion='success')
        output = io.StringIO()
        with redirect_stdout(output):
            code = workflow_main(['continue-project', '--entry', 'develop-system', '--project-root', str(self.project),
                '--run-id', store.run_id, '--user-reference', 'synthetic explicit CLI project continuation'], github=self.github)  # Exercise the actual CLI parser and production dispatch.
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())['status'], 'synced')
        self.assertEqual(store.path.read_bytes(), before)

    def test_result_received_while_paused_is_saved_then_applied_only_after_explicit_resume(self):
        self.prepare()
        store = RunStore(self.project, 'paused-receipt', github=self.github)
        state = store.create('fixture lifecycle', self.plan, ['verified behavior'], self.registry, ticket_scopes=[self.scope()])
        self.sync.bind(store, issue_number=15, final_pr={'number': 13, 'repository_id': 77},
            acceptance_map={'1': {'text': 'verified behavior', 'context': ''}})
        state = store.dispatch(state['revision'], 'research', 'ticket-v1', 'ticket-work')
        frame = state['frames'][-1]
        state = store.control(state['revision'], 'paused', 'synthetic pause with specialist work already in flight')
        evidence = self.project / 'paused-specialist.txt'
        evidence.write_text('synthetic specialist completion received during pause', encoding='utf-8')
        event = {'protocol_version': 1, 'event_id': frame['frame_id'] + '-result', 'run_id': store.run_id,
            'frame_id': frame['frame_id'], 'parent_frame_id': None, 'skill_id': 'research', 'input_version': 'ticket-v1',
            'mode': 'skill', 'execution': 'sequential', 'status': 'completed', 'return_to': 'develop-system',
            'completion_checked': True, 'artifacts': [str(evidence)], 'checks': [], 'findings': [], 'blockers': [],
            'resolved_findings': [], 'acceptance_checks': [{'id': '1', 'status': 'passed', 'completion_checked': True,
            'kind': 'technical', 'evidence': [str(evidence)]}]}  # Actual protocol input; no manual state or candidate pass is created.
        state = store.receive(state['revision'], event)
        self.assertEqual(state['status'], 'paused')
        self.assertIsNone(self.sync.status(store)['bindings'][0]['candidate_id'])  # Paused receipt does not adopt closing qualification.
        self.assertEqual(self.github.writes, [])
        store = RunStore(self.project, store.run_id, github=self.github)  # Foreground reconstruction must not lose the preserved receipt.
        store.resume(state['revision'], user_reference='synthetic explicit resume after paused receipt')
        self.assertEqual(self.github.issue['state'], 'closed')

    def test_shipped_first_project_template_is_valid_disabled_and_does_not_adopt_this_project(self):
        template = json.loads((SKILL_ROOT / 'references/issue-sync-project.disabled.json').read_text(encoding='utf-8'))  # Load the resource from the actual source or generated package.
        self.assertEqual(validate_config(template), template)
        self.assertFalse(template['enabled'])
        self.assertEqual(template['repository'], {'id': 1416632993, 'full_name': 'Zengxiansheng007/develop-system'})
        self.assertEqual(template['target_branch'], 'main')
        self.assertEqual([(selector['workflow_id'], selector['workflow'], selector['event'], selector['branch'], selector['job'])
            for selector in template['required_ci']], [(383018657, '.github/workflows/check-skills.yml', 'push', 'main', 'check (windows-latest)'),
            (383018657, '.github/workflows/check-skills.yml', 'push', 'main', 'check (ubuntu-latest)')])  # Literal identities come from the dated primary reference, not a display-name guess.
        self.assertEqual(template['connection_reference'], 'develop-system')
        self.assertEqual(template['remove_labels'], ['ready-for-agent'])
        self.assertFalse((self.project / '.develop-system/issue-sync/config.json').exists())
        self.assertFalse(IssueSync(self.project, None).status()['enabled'])

    def test_default_off_and_generation_changes_preserve_old_run_identity(self):
        old_store = RunStore(self.project, 'pre-enable', github=self.github)
        old_state = old_store.create('historical fixture', self.plan, ['verified behavior'], self.registry, ticket_scopes=[self.scope()])
        original = old_store.path.read_bytes()
        self.assertNotIn('issue_sync_enrollment', old_state)
        self.assertEqual(old_store.continue_project(user_reference='synthetic request')['status'], 'unmanaged')
        self.prepare()  # Enable only after the historical run already exists.
        self.assertEqual(old_store.delivery_updated()['status'], 'unmanaged')
        self.assertEqual(old_store.path.read_bytes(), original)
        store = RunStore(self.project, 'first-generation', github=self.github)
        state = store.create('new fixture', self.plan, ['verified behavior'], self.registry, ticket_scopes=[self.scope()])
        before = store.path.read_bytes()
        self.sync.configure({**self.config, 'enabled': False}, user_reference='synthetic explicit disablement')
        self.assertEqual(store.delivery_updated()['status'], 'unmanaged')
        enabled = self.sync.configure(self.config, user_reference='synthetic explicit re-enablement')
        self.assertNotEqual(enabled['generation'], state['issue_sync_enrollment']['generation'])
        self.assertEqual(store.continue_project(user_reference='synthetic request')['status'], 'unmanaged')
        self.assertEqual(store.path.read_bytes(), before)
        self.assertEqual(self.github.writes, [])

    def test_stop_then_open_show_and_next_do_not_sync_or_start_another_ticket(self):
        self.prepare()
        self.github.pull.update(merged=False, merged_at=None)
        self.plan.append({'step_id': 'independent-next', 'skill_id': 'to-spec'})  # A ready next ticket must remain merely ready.
        store = RunStore(self.project, 'inactive-project', github=self.github)
        state = store.create('fixture lifecycle', self.plan, ['verified behavior'], self.registry, ticket_scopes=[self.scope()])
        self.sync.bind(store, issue_number=15, final_pr={'number': 13, 'repository_id': 77},
            acceptance_map={'1': {'text': 'verified behavior', 'context': ''}})
        state = self.finish(store, state)
        before, reads = store.path.read_bytes(), self.github.reads
        self.github.pull.update(merged=True, merged_at='2026-10-23T01:00:00Z')
        del store  # A stopped foreground caller owns no worker or scheduled continuation.
        reopened = RunStore(self.project, 'inactive-project', github=self.github)  # Opening the project alone is inert.
        self.assertEqual(reopened.read(), state)
        for command in ('show', 'next'):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(workflow_main([command, '--entry', 'develop-system', '--project-root', str(self.project),
                    '--run-id', reopened.run_id], github=self.github), 0)  # Exercise actual navigation entry branches.
        with self.assertRaises(ValueError):
            RunStore(self.project, 'inactive-project', entry='ask-matt', github=self.github)
        self.assertEqual(reopened.path.read_bytes(), before)
        self.assertEqual(self.github.reads, reads)
        self.assertEqual(self.github.writes, [])
        self.assertEqual(len(reopened.read()['frames']), 1)

    def test_plain_continue_never_adopts_source_changes_without_a_new_professional_result(self):
        self.prepare()
        self.github.pull.update(merged=False, merged_at=None)
        store = RunStore(self.project, 'changed-source', github=self.github)
        state = store.create('fixture lifecycle', self.plan, ['verified behavior'], self.registry, ticket_scopes=[self.scope()])
        self.sync.bind(store, issue_number=15, final_pr={'number': 13, 'repository_id': 77},
            acceptance_map={'1': {'text': 'verified behavior', 'context': ''}})
        self.finish(store, state)
        binding = self.sync.status(store)['bindings'][0]
        (self.project / 'candidate.py').write_text('value = 2  # Unreviewed later candidate.\n', encoding='utf-8')
        self.github.pull.update(merged=True, merged_at='2026-10-23T01:00:00Z')
        before = store.path.read_bytes()
        result = store.continue_project(user_reference='synthetic explicit continuation')
        self.assertEqual(result['reason'], 'acceptance_pending')
        self.assertEqual(self.sync.status(store)['bindings'][0]['candidate_id'], binding['candidate_id'])
        self.assertEqual(store.path.read_bytes(), before)
        self.assertEqual(self.github.writes, [])

    def test_real_http_lifecycle_writes_and_local_permission_or_service_waits(self):
        for response in (None, 403, 429):
            with self.subTest(response=response):
                self.setUp()  # Each HTTP scenario owns its own project and service-wait journal.
                self.prepare()
                with write_fixtures.MutationHTTP(self.github, error_status=response) as boundary:
                    sync = IssueSync(self.project, boundary)
                    store = RunStore(self.project, 'http-lifecycle', github=boundary)
                    state = store.create('fixture lifecycle', self.plan, ['verified behavior'], self.registry, ticket_scopes=[self.scope()])
                    sync.bind(store, issue_number=15, final_pr={'number': 13, 'repository_id': 77},
                        acceptance_map={'1': {'text': 'verified behavior', 'context': ''}})
                    state = self.finish(store, state)  # Actual production hook crosses real loopback HTTP requests.
                    progress = sync.status(store)['bindings'][0]['progress']
                    if response is None:
                        self.assertEqual(progress['status'], 'synced')
                        self.assertEqual(self.github.issue['state'], 'closed')
                        self.assertEqual([method for method, _, _ in boundary.requests if method != 'GET'], ['PATCH', 'DELETE', 'PATCH'])
                    else:
                        self.assertEqual(progress['status'], 'pending')
                        self.assertEqual(progress['reason'], 'permission_denied' if response == 403 else 'rate_limited')
                        self.assertEqual(store.read()['status'], 'running')  # A local remote-service error does not fail development.
                        self.assertEqual(self.github.writes, [])
                        if response == 429:
                            requests = len(boundary.requests)
                            self.assertEqual(store.continue_project(user_reference='synthetic continuation during service wait')['reason'], 'rate_limited')
                            self.assertEqual(len(boundary.requests), requests)  # Explicit catch-up honors the already persisted service boundary.
                    self.assertEqual(store.read(), state)

    def test_ordinary_project_continue_keeps_a_human_reopened_ticket_paused(self):
        self.prepare()
        self.github = intervention_fixtures.EventGitHub()  # Only the external history gains a manual reopen.
        self.sync = IssueSync(self.project, self.github)
        store = RunStore(self.project, 'human-pause', github=self.github)
        state = store.create('fixture lifecycle', self.plan, ['verified behavior'], self.registry, ticket_scopes=[self.scope()])
        self.sync.bind(store, issue_number=15, final_pr={'number': 13, 'repository_id': 77},
            acceptance_map={'1': {'text': 'verified behavior', 'context': ''}})
        self.finish(store, state)
        self.github.human_reopen()
        writes = list(self.github.writes)
        result = store.continue_project(user_reference='synthetic continuation without takeover')
        self.assertEqual(result['status'], 'manual_paused')
        self.assertEqual(self.github.issue['state'], 'open')
        self.assertEqual(self.github.writes, writes)


class BatchLifecycleTests(unittest.TestCase):
    setUp = batch_fixtures.BatchTests.setUp  # Build real multi-ticket scope and synthetic delivery facts through the public fixture.
    finish_ticket = batch_fixtures.BatchTests.finish_ticket

    def test_actual_professional_nodes_isolate_local_failure_and_keep_parent_acceptance_independent(self):
        batch_fixtures.BatchTests.prepare(self, parent=True)
        self.store = RunStore(self.project, self.store.run_id, github=self.github)  # Attach only the external GitHub dependency to the existing actual identity.
        for number, _, ids, name in self.declarations:
            self.sync.bind(self.store, issue_number=number, final_pr={'number': 13, 'repository_id': 77},
                acceptance_map={i: {'text': self.criteria[int(i) - 1], 'context': ''} for i in ids}, parent_spec=name == 'parent')
        self.finish_ticket('first', ['1'])
        self.assertEqual(self.github.issues[15]['state'], 'closed')
        self.assertEqual(self.github.issues[16]['state'], 'open')
        self.assertEqual(self.github.issues[14]['state'], 'open')
        self.github.failures[15] = 'permission_denied'  # This ticket-local read failure cannot consume another ticket's authorization.
        self.finish_ticket('second', ['2', '3'])
        self.assertEqual(self.github.issues[16]['state'], 'closed')
        self.assertEqual(self.github.issues[14]['state'], 'open')  # Children closed never supply the parent's own professional conclusion.
        self.finish_ticket('parent', ['4'])
        self.assertEqual(self.github.issues[14]['state'], 'closed')
        self.assertEqual(self.store.read()['status'], 'running')  # Remote progress does not perform whole-run completion or dispatch the next step.


if __name__ == '__main__':
    unittest.main()  # Keep the public test executable from both source and generated package.
