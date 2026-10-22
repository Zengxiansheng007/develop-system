"""Public sync seam with real temporary RunStore; GitHub is a boundary fixture."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from workflow_state import RunStore, SKILL_ROOT
from issue_sync import IssueSync


class GitHubFixture:
    """Controlled external boundary; no real network or professional execution."""
    def __init__(self):
        self.writes = []  # Remote mutations are observable without internal mocks.
        self.issue = {'id': 501, 'number': 15, 'repository_id': 77, 'repository_full_name': 'fixture/project',
                      'type': 'issue', 'body': '## Acceptance criteria\n\n- [ ] verified behavior'}
        self.pull = {'id': 901, 'number': 13, 'repository_id': 77, 'repository_full_name': 'fixture/project', 'type': 'pull_request'}

    def get_issue(self, repository, number):
        return copy.deepcopy(self.issue)  # The fixture supplies an independent literal identity.

    def get_pull_request(self, repository, number):
        return copy.deepcopy(self.pull)  # Delivery facts arrive in the next implementation slice.


class IssueSyncTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)
        (self.project / 'candidate.py').write_text('value = 1  # Synthetic accepted candidate.\n', encoding='utf-8')
        self.github = GitHubFixture()
        self.sync = IssueSync(self.project, self.github)
        self.config = {'schema_version': 1, 'enabled': True, 'project_id': 'fixture-project',
                       'github_host': 'github.com', 'repository': {'id': 77, 'full_name': 'fixture/project'},
                       'target_branch': 'main', 'required_ci': [{'workflow': 'Check skills', 'job': 'ubuntu'}],
                       'remove_labels': ['ready-for-agent'], 'connection_reference': 'fixture-connection',
                       'body_policy': 'single-writer'}  # Explicitly synthetic, credential-free configuration.
        self.registry = self.project / 'registry.json'
        registry = json.loads((SKILL_ROOT / 'registry.json').read_text(encoding='utf-8'))
        for skill in registry['skills']:
            path = self.project / (skill['id'] + '.md')
            path.write_text('---\nname: ' + skill['id'] + '\ndescription: fixture\n---\n', encoding='utf-8')
            skill.update(path=str(path), required_calls=[])  # These are protocol fixtures, not real model calls.
        self.registry.write_text(json.dumps(registry), encoding='utf-8')
        self.plan = [{'step_id': 'ticket-work', 'skill_id': 'research'}]

    def create(self, run_id='new-run', **kwargs):
        store = RunStore(self.project, run_id)
        state = store.create('fixture ticket', self.plan, kwargs.pop('acceptance', ['verified behavior']), self.registry, **kwargs)
        return store, state

    def scope(self, ids=None):
        return {'ticket_id': '77:501', 'acceptance_ids': ids or ['1'], 'required_steps': ['ticket-work'],
                'input_version': 'ticket-v1', 'candidate_paths': ['candidate.py']}  # Original scope is fixed with the new run.

    def finish(self, store, state, ids=None, **changes):
        state = store.dispatch(state['revision'], 'research', 'ticket-v1', 'ticket-work')
        frame = state['frames'][-1]
        evidence = self.project / ('evidence-' + store.run_id + '.txt')
        evidence.write_text('synthetic specialist verification', encoding='utf-8')
        event = {'protocol_version': 1, 'event_id': frame['frame_id'] + '-result', 'run_id': store.run_id,
                 'frame_id': frame['frame_id'], 'parent_frame_id': None, 'skill_id': 'research', 'input_version': 'ticket-v1',
                 'mode': 'skill', 'execution': 'sequential', 'status': 'completed', 'return_to': 'develop-system',
                 'completion_checked': True, 'artifacts': [str(evidence)], 'checks': [], 'findings': [], 'blockers': [],
                 'acceptance_checks': [{'id': i, 'status': 'passed', 'completion_checked': True, 'kind': 'technical',
                                        'evidence': [str(evidence)]} for i in (ids or ['1'])],
                 'resolved_findings': [], **changes}  # Professional assertions are explicit fixture input through receive().
        return store.receive(state['revision'], event)

    def test_default_disabled_and_creation_time_enrollment_cannot_be_backfilled(self):
        old_store, old = self.create('old-run')
        self.assertEqual(self.sync.status()['enabled'], False)
        self.assertNotIn('issue_sync_enrollment', old)
        enabled = self.sync.configure(self.config, user_reference='synthetic explicit project enablement')
        store, state = self.create()
        self.assertEqual(state['issue_sync_enrollment']['generation'], enabled['generation'])
        self.assertEqual(self.sync.sync_once(old_store)['status'], 'unmanaged')
        self.assertEqual(self.sync.sync_once(store)['reason'], 'issue_not_bound')
        self.assertEqual(self.github.writes, [])
        self.assertEqual(old_store.read(), old)

    def test_read_only_ticket_projection_requires_full_professional_acceptance(self):
        store, state = self.create(acceptance=['first behavior', 'second behavior'], ticket_scopes=[self.scope(['1', '2'])])
        self.assertFalse(store.acceptance_projection('77:501')['eligible'])
        state = self.finish(store, state, ids=['1'])
        before = store.path.read_bytes()
        projection = store.acceptance_projection('77:501')
        self.assertFalse(projection['eligible'])
        self.assertIn('Acceptance lacks a passed specialist conclusion', projection['reason'])
        self.assertEqual(store.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            store.complete(state['revision'], [{'id': '1', 'source_frame_id': state['frames'][0]['frame_id']}])

    def test_bind_fixed_issue_scope_and_candidate_then_return_delivery_pending(self):
        self.sync.configure(self.config, user_reference='synthetic explicit project enablement')
        store, state = self.create(ticket_scopes=[self.scope()])
        state = self.finish(store, state)
        before = store.path.read_bytes()
        projection = store.acceptance_projection('77:501')
        self.assertTrue(projection['eligible'])
        binding = self.sync.bind(store, issue_number=15, final_pr={'number': 13, 'repository_id': 77},
                                 acceptance_map={'1': {'text': 'verified behavior', 'context': ''}})
        self.assertEqual(binding['candidate_id'], projection['candidate_id'])
        result = self.sync.sync_once(store)
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(result['reason'], 'final_delivery_unverified')
        restarted = IssueSync(self.project, self.github)
        self.assertEqual(restarted.status(store)['bindings'][0]['progress']['reason'], 'final_delivery_unverified')
        self.assertEqual(store.path.read_bytes(), before)
        self.assertEqual(self.github.writes, [])

    def test_complete_run_keeps_its_authority_unchanged_during_pending_sync(self):
        self.sync.configure(self.config, user_reference='synthetic enablement')
        store, state = self.create(ticket_scopes=[self.scope()])
        state = self.finish(store, state)
        state = store.complete(state['revision'], [{'id': '1', 'source_frame_id': state['frames'][0]['frame_id']}])
        before = store.path.read_bytes()
        self.sync.bind(store, issue_number=15, final_pr={'number': 13, 'repository_id': 77},
                       acceptance_map={'1': {'text': 'verified behavior', 'context': ''}})
        self.assertEqual(self.sync.sync_once(store)['reason'], 'final_delivery_unverified')
        self.assertEqual(store.path.read_bytes(), before)
        self.assertEqual(store.read()['status'], 'completed')
        self.assertEqual(self.github.writes, [])

    def test_independent_pending_work_does_not_invalidate_this_ticket(self):
        self.plan.append({'step_id': 'independent-work', 'skill_id': 'to-spec'})
        store, state = self.create(ticket_scopes=[self.scope()])
        state = self.finish(store, state)
        self.assertEqual(state['status'], 'running')
        self.assertTrue(store.acceptance_projection('77:501')['eligible'])
        with self.assertRaises(ValueError):
            store.complete(state['revision'], [{'id': '1', 'source_frame_id': state['frames'][0]['frame_id']}])

    def test_findings_and_changed_evidence_block_the_same_shared_qualification(self):
        for run_id, changes in [('findings', {'findings': ['unresolved fixture finding']}), ('stale', {})]:
            store, state = self.create(run_id, ticket_scopes=[self.scope()])
            state = self.finish(store, state, **changes)
            if run_id == 'stale':
                (self.project / ('evidence-' + run_id + '.txt')).write_text('changed after acceptance', encoding='utf-8')
            before = store.path.read_bytes()
            self.assertFalse(store.acceptance_projection('77:501')['eligible'])
            with self.assertRaises(ValueError):
                store.complete(state['revision'], [{'id': '1', 'source_frame_id': state['frames'][0]['frame_id']}])
            self.assertEqual(store.path.read_bytes(), before)

    def test_missing_helper_and_human_confirmation_cannot_be_promoted_by_projection(self):
        registry = json.loads(self.registry.read_text(encoding='utf-8'))
        next(s for s in registry['skills'] if s['id'] == 'research')['required_calls'] = ['domain-modeling']
        self.registry.write_text(json.dumps(registry), encoding='utf-8')
        store, state = self.create('helper', ticket_scopes=[self.scope()])
        with self.assertRaises(ValueError):
            self.finish(store, state)
        self.assertFalse(store.acceptance_projection('77:501')['eligible'])
        registry = json.loads(self.registry.read_text(encoding='utf-8'))
        next(s for s in registry['skills'] if s['id'] == 'research')['required_calls'] = []
        self.registry.write_text(json.dumps(registry), encoding='utf-8')
        store, state = self.create('human', ticket_scopes=[self.scope()], acceptance=[{'criterion': 'user verifies behavior', 'kind': 'human'}])
        with self.assertRaises(ValueError):
            self.finish(store, state, acceptance_checks=[{'id': '1', 'status': 'passed', 'completion_checked': True,
                                                         'kind': 'human', 'evidence': [str(self.registry)]}])
        self.assertFalse(store.acceptance_projection('77:501')['eligible'])

    def test_configuration_generation_changes_do_not_adopt_earlier_runs(self):
        self.sync.configure(self.config, user_reference='synthetic enablement')
        store, state = self.create(ticket_scopes=[self.scope()])
        before = store.path.read_bytes()
        changed = {**self.config, 'target_branch': 'release'}
        self.sync.configure(changed, user_reference='synthetic explicit target change')
        self.assertEqual(self.sync.sync_once(store)['status'], 'unmanaged')
        with self.assertRaises(ValueError):
            self.sync.bind(store, issue_number=15, final_pr={'number': 13, 'repository_id': 77},
                           acceptance_map={'1': {'text': 'verified behavior', 'context': ''}})
        self.assertEqual(store.path.read_bytes(), before)
        self.assertEqual(self.github.writes, [])

    def test_partial_writeback_mapping_cannot_narrow_full_professional_acceptance(self):
        self.sync.configure(self.config, user_reference='synthetic enablement')
        store, state = self.create(acceptance=['first', 'second'], ticket_scopes=[self.scope(['1', '2'])])
        state = self.finish(store, state, ids=['1'])
        self.github.issue['body'] = '## Acceptance criteria\n\n- [ ] first\n- [ ] second'
        self.sync.bind(store, issue_number=15, final_pr={'number': 13, 'repository_id': 77},
                       acceptance_map={'1': {'text': 'first', 'context': ''}})
        self.assertEqual(self.sync.sync_once(store)['reason'], 'acceptance_pending')
        with self.assertRaises(TypeError):
            self.sync.sync_once(store, passed=True)
        self.assertEqual(self.github.writes, [])

    def test_original_scope_with_a_missing_issue_ac_is_rejected_even_if_declared_at_creation(self):
        self.sync.configure(self.config, user_reference='synthetic enablement')
        store, state = self.create(acceptance=['first', 'second'], ticket_scopes=[self.scope(['1'])])
        state = self.finish(store, state, ids=['1'])
        self.github.issue['body'] = '## Acceptance criteria\n\n- [ ] first\n- [ ] second'
        with self.assertRaisesRegex(ValueError, 'complete.*acceptance'):
            self.sync.bind(store, issue_number=15, final_pr={'number': 13, 'repository_id': 77},
                           acceptance_map={'1': {'text': 'first', 'context': ''}})
        self.assertEqual(self.github.writes, [])

    def test_full_acceptance_allows_subset_writeback_and_preserves_other_manual_checkboxes(self):
        self.sync.configure(self.config, user_reference='synthetic enablement')
        store, state = self.create(acceptance=['first', 'second'], ticket_scopes=[self.scope(['1', '2'])])
        state = self.finish(store, state, ids=['1', '2'])
        self.github.issue['body'] = '## Acceptance criteria\n\n- [ ] first\n- [ ] second\n\n## Human notes\n- [ ] manual signoff'
        binding = self.sync.bind(store, issue_number=15, final_pr={'number': 13, 'repository_id': 77},
                                 acceptance_map={'1': {'text': 'first', 'context': ''}})
        self.assertEqual(set(binding['mapping']), {'1'})
        self.assertEqual(self.sync.sync_once(store)['reason'], 'final_delivery_unverified')
        self.assertIn('- [ ] manual signoff', self.github.issue['body'])

    def test_unverifiable_full_acceptance_source_is_not_assumed_complete(self):
        self.sync.configure(self.config, user_reference='synthetic enablement')
        store, state = self.create(ticket_scopes=[self.scope()])
        self.github.issue['body'] = '- [ ] verified behavior\n\nUnclassified checklist without an acceptance source.'
        with self.assertRaisesRegex(ValueError, 'complete.*acceptance'):
            self.sync.bind(store, issue_number=15, final_pr={'number': 13, 'repository_id': 77},
                           acceptance_map={'1': {'text': 'verified behavior', 'context': ''}})

    def test_issue_pr_types_repository_and_single_final_pr_are_explicit(self):
        self.sync.configure(self.config, user_reference='synthetic enablement')
        store, state = self.create(ticket_scopes=[self.scope()])
        for final_pr in (None, [], [{'number': 13, 'repository_id': 77}], {'number': 13, 'repository_id': 88}, {'number': True, 'repository_id': 77}):
            with self.assertRaises(ValueError):
                self.sync.bind(store, issue_number=15, final_pr=final_pr,
                               acceptance_map={'1': {'text': 'verified behavior', 'context': ''}})
        self.github.issue['type'] = 'pull_request'
        with self.assertRaises(ValueError):
            self.sync.bind(store, issue_number=15, final_pr={'number': 13, 'repository_id': 77},
                           acceptance_map={'1': {'text': 'verified behavior', 'context': ''}})
        self.github.issue['type'] = 'issue'
        self.github.pull['repository_id'] = 88
        with self.assertRaises(ValueError):
            self.sync.bind(store, issue_number=15, final_pr={'number': 13, 'repository_id': 77},
                           acceptance_map={'1': {'text': 'verified behavior', 'context': ''}})
        self.assertEqual(self.github.writes, [])

    def test_duplicate_binding_cannot_move_an_issue_to_another_run(self):
        self.sync.configure(self.config, user_reference='synthetic enablement')
        first, _ = self.create('first', ticket_scopes=[self.scope()])
        second, _ = self.create('second', ticket_scopes=[self.scope()])
        arguments = {'issue_number': 15, 'final_pr': {'number': 13, 'repository_id': 77},
                     'acceptance_map': {'1': {'text': 'verified behavior', 'context': ''}}}
        binding = self.sync.bind(first, **arguments)
        self.assertEqual(self.sync.bind(first, **arguments), binding)
        with self.assertRaises(ValueError):
            self.sync.bind(second, **arguments)
        self.assertEqual(self.github.writes, [])

    def test_invalid_or_credential_bearing_configuration_is_rejected_before_writing(self):
        for changes in ({'schema_version': 2}, {'schema_version': True}, {'token': 'placeholder'}, {'required_ci': []},
                        {'required_ci': [{'token': 'placeholder'}]}, {'remove_labels': [{'name': 'ready'}]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.sync.configure({**self.config, **changes}, user_reference='synthetic authorization')
        self.assertFalse(self.sync.status()['enabled'])
        self.assertFalse(self.sync.root.exists())

    def test_source_changes_invalidate_acceptance_even_when_the_review_report_is_unchanged(self):
        store, state = self.create(ticket_scopes=[self.scope()])
        state = self.finish(store, state)
        self.assertTrue(store.acceptance_projection('77:501')['eligible'])
        before = store.path.read_bytes()
        (self.project / 'candidate.py').write_text('value = 2  # Changed after the professional conclusion.\n', encoding='utf-8')
        self.assertFalse(store.acceptance_projection('77:501')['eligible'])
        with self.assertRaises(ValueError):
            store.complete(state['revision'], [{'id': '1', 'source_frame_id': state['frames'][0]['frame_id']}])
        self.assertEqual(store.path.read_bytes(), before)

    def test_another_input_version_or_unscoped_completion_does_not_supply_ticket_acceptance(self):
        scope = {**self.scope(), 'input_version': 'another-input-version'}
        store, state = self.create('wrong-input', ticket_scopes=[scope])
        state = self.finish(store, state)
        self.assertFalse(store.acceptance_projection('77:501')['eligible'])
        self.assertIn('another ticket input version', store.acceptance_projection('77:501')['reason'])
        self.sync.configure(self.config, user_reference='synthetic enablement')
        store, state = self.create('unscoped')
        state = self.finish(store, state)
        state = store.complete(state['revision'], [{'id': '1', 'source_frame_id': state['frames'][0]['frame_id']}])
        self.assertEqual(state['status'], 'completed')
        with self.assertRaises(ValueError):
            self.sync.bind(store, issue_number=15, final_pr={'number': 13, 'repository_id': 77},
                           acceptance_map={'1': {'text': 'verified behavior', 'context': ''}})

    def test_pending_sync_rechecks_changed_actual_source_without_mutating_run_authority(self):
        self.sync.configure(self.config, user_reference='synthetic enablement')
        store, state = self.create(ticket_scopes=[self.scope()])
        state = self.finish(store, state)
        self.sync.bind(store, issue_number=15, final_pr={'number': 13, 'repository_id': 77},
                       acceptance_map={'1': {'text': 'verified behavior', 'context': ''}})
        before = store.path.read_bytes()
        (self.project / 'candidate.py').write_text('value = 3\n', encoding='utf-8')
        result = self.sync.sync_once(store)
        self.assertEqual(result['reason'], 'acceptance_pending')
        self.assertIn('Revalidate changed artifacts', result['qualification_reason'])
        self.assertEqual(store.path.read_bytes(), before)
        self.assertEqual(self.github.writes, [])

    def test_same_issue_number_in_different_project_repositories_has_separate_identity(self):
        self.sync.configure(self.config, user_reference='synthetic first project enablement')
        first, _ = self.create(ticket_scopes=[self.scope()])
        self.sync.bind(first, issue_number=15, final_pr={'number': 13, 'repository_id': 77},
                       acceptance_map={'1': {'text': 'verified behavior', 'context': ''}})
        other_root = self.project / 'other-project'
        other_root.mkdir()
        (other_root / 'candidate.py').write_text('value = 1\n', encoding='utf-8')
        other_api = GitHubFixture()
        for obj in (other_api.issue, other_api.pull):
            obj.update(repository_id=88, repository_full_name='fixture/other')
        other = IssueSync(other_root, other_api)
        other.configure({**self.config, 'project_id': 'other-project', 'repository': {'id': 88, 'full_name': 'fixture/other'}},
                        user_reference='synthetic second project enablement')
        second = RunStore(other_root, 'new-run')
        second.create('other ticket', self.plan, ['verified behavior'], self.registry,
                      ticket_scopes=[{**self.scope(), 'ticket_id': '88:501'}])
        other.bind(second, issue_number=15, final_pr={'number': 13, 'repository_id': 88},
                   acceptance_map={'1': {'text': 'verified behavior', 'context': ''}})
        self.assertEqual(self.sync.status(first)['bindings'][0]['issue']['repository_id'], 77)
        self.assertEqual(other.status(second)['bindings'][0]['issue']['repository_id'], 88)
        with self.assertRaises(ValueError):
            self.sync.sync_once(second)
        self.assertEqual(self.github.writes + other_api.writes, [])

    def test_unknown_journal_version_and_ambiguous_checkbox_mapping_are_not_adopted(self):
        self.sync.configure(self.config, user_reference='synthetic enablement')
        store, _ = self.create(ticket_scopes=[self.scope()])
        self.github.issue['body'] = '- [ ] verified behavior\n- [ ] verified behavior'
        with self.assertRaises(ValueError):
            self.sync.bind(store, issue_number=15, final_pr={'number': 13, 'repository_id': 77},
                           acceptance_map={'1': {'text': 'verified behavior', 'context': ''}})
        journal = self.project / '.develop-system/issue-sync/bindings.json'
        journal.write_text('{"schema_version": 99}', encoding='utf-8')  # Persistence boundary fixture, not a passed execution state.
        with self.assertRaises(ValueError):
            self.sync.status(store)
        self.assertEqual(self.github.writes, [])

    def test_candidate_ranges_apply_the_existing_sensitive_file_exclusion(self):
        (self.project / '.env').write_text('fixture-only-private-value', encoding='utf-8')
        store, state = self.create(ticket_scopes=[{**self.scope(), 'candidate_paths': ['.env']}])
        with self.assertRaisesRegex(ValueError, 'Sensitive'):
            self.finish(store, state)
        self.assertFalse(store.acceptance_projection('77:501')['eligible'])


if __name__ == '__main__':
    unittest.main()
