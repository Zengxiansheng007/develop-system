"""Observable body-conflict protection with real RunStore and synthetic GitHub facts."""
import copy
import json
import unittest
from issue_sync import IssueSync
import test_issue_write as write_fixtures


class MultipleIssues(write_fixtures.WritableGitHub):
    def __init__(self):
        super().__init__()  # Two synthetic Issues share the configured project and final PR.
        self.issues = {15: self.issue, 16: {**copy.deepcopy(self.issue), 'id': 502, 'number': 16}}

    def get_issue(self, repository, number):
        self.issue = self.issues[number]
        return super().get_issue(repository, number)

    def update_issue_body(self, repository, number, body):
        self.issue = self.issues[number]
        return super().update_issue_body(repository, number, body)

    def remove_issue_label(self, repository, number, name):
        self.issue = self.issues[number]
        return super().remove_issue_label(repository, number, name)

    def close_issue(self, repository, number):
        self.issue = self.issues[number]
        return super().close_issue(repository, number)


class IssueConflictTests(unittest.TestCase):
    setUp = write_fixtures.IssueWriteTests.setUp
    create = write_fixtures.IssueWriteTests.create
    scope = write_fixtures.IssueWriteTests.scope
    finish = write_fixtures.IssueWriteTests.finish
    ready = write_fixtures.IssueWriteTests.ready

    def test_detected_baseline_change_saves_difference_and_requires_deliberate_resolution(self):
        store = self.ready()  # Synthetic professional facts enter a real temporary execution authority.
        original = self.github.issue['body']
        self.github.issue['body'] += '\n\nHuman decision: retain this prose.'
        changed = self.github.issue['body']
        pending = self.sync.sync_once(store)
        self.assertEqual((pending['status'], pending['reason']), ('pending', 'body_baseline_changed'))
        saved = IssueSync(self.project, self.github).status(store)['bindings'][0]
        conflict = saved['body_conflicts'][-1]  # Observe durable public conflict history, never private rule helpers.
        self.assertEqual(conflict['status'], 'unresolved')
        self.assertEqual((conflict['expected_body'], conflict['observed_body']), (original, changed))
        self.assertIn('+Human decision: retain this prose.', conflict['difference'])
        self.assertEqual(conflict['unfinished_steps'], ['body', 'label:ready-for-agent', 'close'])
        self.github.issue['body'] = original  # A later matching copy alone must not erase an observed human change.
        again = IssueSync(self.project, self.github).continue_sync(store)
        self.assertEqual(again['status'], 'pending')
        self.assertEqual(again['body_conflict']['conflict_id'], conflict['conflict_id'])
        self.assertEqual(self.github.writes, [])


    def test_detected_prewrite_and_postwrite_changes_preserve_difference_and_stop_dependants(self):
        for read_number, stage, written in ((2, 'before_write', []), (3, 'after_write', ['body']),
                                            (4, 'before_write', ['body']), (6, 'before_write', ['body', 'label']),
                                            (7, 'after_write', ['body', 'label', 'close'])):
            with self.subTest(read_number=read_number):
                self.setUp()  # Every observation window owns an independent temporary project.
                store = self.ready()
                def edit(number):
                    if number == read_number:
                        self.github.issue['body'] += '\nDetected external edit.'  # Only the GitHub observation boundary changes.
                self.github.on_read = edit
                result = self.sync.sync_once(store)
                self.assertEqual((result['status'], result['reason']), ('pending', 'issue_readback_mismatch'))
                saved = IssueSync(self.project, self.github).status(store)['bindings'][0]
                conflict = saved['body_conflicts'][-1]
                self.assertEqual(conflict['stage'], stage)
                self.assertIn('Detected external edit.', conflict['observed_body'])
                self.assertNotIn('Detected external edit.', conflict['expected_body'])
                self.assertIn('close', conflict['unfinished_steps'])
                self.assertEqual([kind for kind, _ in self.github.writes], written)
                self.assertEqual(self.github.issue['state'], 'closed' if 'close' in written else 'open')
                self.assertEqual(saved['progress']['operations'], conflict['operations'])


    def resolution(self, store):
        conflict = self.sync.status(store)['bindings'][0]['body_conflicts'][-1]
        return {'conflict_id': conflict['conflict_id'], 'expected_body': self.github.issue['body'],
                'acceptance_map': {'1': {'text': 'verified behavior', 'context': '## Acceptance criteria'}},
                'user_reference': 'synthetic explicit current-map review'}  # Test data does not claim an actual human review.

    def test_explicit_current_mapping_resolution_preserves_human_prose_and_continues(self):
        store = self.ready()
        original_run = store.path.read_bytes()
        self.github.issue['body'] = '## Acceptance criteria\n\nNotes before the moved item.\n- [ ] verified behavior\n\n## Human notes\nKeep deliberate edit.'
        self.assertEqual(self.sync.sync_once(store)['reason'], 'body_baseline_changed')
        resolution = self.resolution(store)
        result = IssueSync(self.project, self.github).continue_sync(store, ticket_id='77:501', body_resolution=resolution)
        self.assertEqual(result['status'], 'synced')
        self.assertIn('Notes before the moved item.\n- [x] verified behavior', self.github.issue['body'])
        self.assertIn('## Human notes\nKeep deliberate edit.', self.github.issue['body'])
        saved = self.sync.status(store)['bindings'][0]
        self.assertEqual(saved['body_conflicts'][-1]['status'], 'resolved')
        self.assertEqual(saved['body_conflicts'][-1]['resolution']['user_reference'], resolution['user_reference'])
        self.assertEqual(saved['body_conflicts'][-1]['observed_body'], resolution['expected_body'])
        self.assertEqual([kind for kind, _ in self.github.writes], ['body', 'label', 'close'])
        self.assertEqual(store.path.read_bytes(), original_run)


    def test_current_body_review_does_not_clear_a_persisted_human_pause(self):
        store = self.ready()
        self.github.issue['body'] += '\nHuman body edit.'
        self.sync.sync_once(store)
        resolution = self.resolution(store)
        journal = self.project / '.develop-system/issue-sync/bindings.json'
        value = json.loads(journal.read_text(encoding='utf-8'))
        value['bindings'][0]['progress']['status'] = 'human_paused'
        journal.write_text(json.dumps(value), encoding='utf-8')  # External future pause input, never a forged execution acceptance.
        for attempt in range(2):
            result = IssueSync(self.project, self.github).continue_sync(store, body_resolution=resolution)
            self.assertEqual((result['status'], result['reason']), ('pending', 'issue_human_paused'))
            self.assertEqual(self.github.writes, [])
        saved = self.sync.status(store)['bindings'][0]
        self.assertEqual(saved['body_conflicts'][-1]['status'], 'unresolved')


    def test_unknown_request_with_detected_body_change_keeps_difference_and_cannot_be_resolved_as_success(self):
        from issue_delivery import DeliveryPending
        store = self.ready()
        def timeout(kind, value):
            raise DeliveryPending('http_request_deadline')  # No response can establish whether this synthetic request applied.
        self.github.before_write = timeout
        pending = self.sync.sync_once(store)
        self.assertEqual(pending['operations'][0]['status'], 'unknown')
        self.github.before_write = None
        self.github.issue['body'] += '\nHuman content after uncertain request.'
        pending = IssueSync(self.project, self.github).continue_sync(store)
        self.assertEqual(pending['reason'], 'operation_outcome_unknown')
        conflict = self.sync.status(store)['bindings'][0]['body_conflicts'][-1]
        self.assertIn('Human content after uncertain request.', conflict['observed_body'])
        result = self.sync.continue_sync(store, body_resolution=self.resolution(store))
        self.assertEqual(result['reason'], 'operation_outcome_unknown')
        self.assertEqual(self.sync.status(store)['bindings'][0]['body_conflicts'][-1]['status'], 'unresolved')
        self.assertEqual(result['operations'][0]['status'], 'unknown')
        self.assertEqual(self.github.writes, [])


    def test_rebinding_changed_candidate_cannot_clear_body_conflict_history(self):
        store = self.ready()
        original_body = self.github.issue['body']
        self.github.issue['body'] += '\nHuman edit requiring explicit review.'
        self.sync.sync_once(store)
        conflict = copy.deepcopy(self.sync.status(store)['bindings'][0]['body_conflicts'][-1])
        self.github.issue['body'] = original_body  # Returning an old copy alone does not resolve the recorded difference.
        source = self.project / 'candidate.py'
        accepted = source.read_bytes()
        source.write_bytes(b'value = 2  # Unaccepted temporary candidate.\n')
        arguments = {'issue_number': 15, 'final_pr': {'number': 13, 'repository_id': 77},
                     'acceptance_map': {'1': {'text': 'verified behavior', 'context': ''}}}
        self.sync.bind(store, **arguments)
        source.write_bytes(accepted)
        self.sync.bind(store, **arguments)
        self.assertEqual(self.sync.status(store)['bindings'][0]['body_conflicts'][-1], conflict)
        self.assertEqual(self.sync.continue_sync(store)['status'], 'pending')
        self.assertEqual(self.github.writes, [])


    def test_unknown_close_keeps_body_difference_and_unfinished_attribution(self):
        from issue_delivery import DeliveryPending
        store = self.ready()
        def timeout(kind):
            if kind == 'close':
                raise DeliveryPending('http_request_deadline')  # Applied state does not establish the request's provenance.
        self.github.after_write = timeout
        self.sync.sync_once(store)
        self.github.after_write = None
        self.github.issue['body'] += '\nHuman note after uncertain close.'
        writes = copy.deepcopy(self.github.writes)
        result = IssueSync(self.project, self.github).continue_sync(store)
        self.assertEqual(result['reason'], 'close_outcome_unknown')
        conflict = self.sync.status(store)['bindings'][0]['body_conflicts'][-1]
        self.assertIn('Human note after uncertain close.', conflict['observed_body'])
        self.assertIn('close', conflict['unfinished_steps'])
        self.assertEqual(result['operations'][-1]['status'], 'unknown')
        self.assertEqual(self.github.writes, writes)


    def test_duplicate_moved_and_changed_checkboxes_never_use_old_offsets_or_fuzzy_text(self):
        bodies = ('## Acceptance criteria\n\n- [ ] verified behavior\n- [ ] verified behavior',
                  '## Acceptance criteria\n\nMoved section prose.\n- [ ] verified behavior',
                  '## Acceptance criteria\n\n- [ ] verified behaviors')
        for body in bodies:
            with self.subTest(body=body):
                self.setUp()
                store = self.ready()
                self.github.issue['body'] = body
                result = self.sync.sync_once(store)
                self.assertEqual(result['reason'], 'body_baseline_changed')
                self.assertEqual(self.sync.continue_sync(store)['status'], 'pending')
                self.assertEqual(self.github.issue['body'], body)
                self.assertEqual(self.github.writes, [])

    def test_resolution_rejects_duplicate_changed_or_wrong_context_mapping(self):
        for mode in ('duplicate', 'text', 'context', 'unmapped', 'no-reference'):
            with self.subTest(mode=mode):
                self.setUp()
                store = self.ready()
                self.github.issue['body'] += '\nReviewed edit.'
                if mode == 'duplicate':
                    self.github.issue['body'] += '\n- [ ] verified behavior'
                elif mode == 'text':
                    self.github.issue['body'] = self.github.issue['body'].replace('verified behavior', 'verified behaviors')
                self.sync.sync_once(store)
                resolution = self.resolution(store)
                if mode == 'context':
                    resolution['acceptance_map']['1']['context'] = 'Wrong context'
                elif mode == 'unmapped':
                    resolution['acceptance_map'] = {}
                elif mode == 'no-reference':
                    resolution['user_reference'] = ''
                result = self.sync.continue_sync(store, body_resolution=resolution)
                self.assertIn(result['reason'], ('body_resolution_mapping_invalid', 'body_resolution_invalid'))
                self.assertEqual(self.sync.status(store)['bindings'][0]['body_conflicts'][-1]['status'], 'unresolved')
                self.assertEqual(self.github.writes, [])

    def test_stale_review_saves_a_new_difference_and_does_not_write(self):
        store = self.ready()
        self.github.issue['body'] += '\nFirst human review.'
        self.sync.sync_once(store)
        resolution = self.resolution(store)
        self.github.issue['body'] += '\nA later human decision.'
        result = self.sync.continue_sync(store, body_resolution=resolution)
        self.assertEqual(result['reason'], 'body_resolution_stale')
        saved = self.sync.status(store)['bindings'][0]['body_conflicts']
        self.assertEqual(len(saved), 2)
        self.assertEqual(saved[-1]['stage'], 'resolution_read')
        self.assertIn('A later human decision.', saved[-1]['observed_body'])
        self.assertEqual(self.github.writes, [])

    def test_human_edited_summary_is_held_until_exact_review_and_refreshes_one_block(self):
        store = self.ready()
        self.assertEqual(self.sync.sync_once(store)['status'], 'synced')
        operations = copy.deepcopy(self.sync.status(store)['bindings'][0]['progress']['operations'])
        self.github.issue['body'] = self.github.issue['body'].replace('### Verified delivery', '### Human edited delivery')
        self.assertEqual(self.sync.continue_sync(store)['reason'], 'body_baseline_changed')
        writes = len(self.github.writes)
        self.assertIn('Human edited delivery', self.github.issue['body'])
        result = self.sync.continue_sync(store, body_resolution=self.resolution(store))
        self.assertEqual(result['status'], 'synced')
        self.assertEqual([kind for kind, _ in self.github.writes[writes:]], ['body'])
        self.assertEqual(self.github.issue['body'].count('<!-- develop-system:issue-sync:'), 1)
        self.assertEqual(self.sync.status(store)['bindings'][0]['progress']['operations'][:3], operations)
        self.assertEqual(sum(kind == 'close' for kind, _ in self.github.writes), 1)

    def test_successful_response_conflict_review_preserves_unconfirmed_operation_history(self):
        store = self.ready()
        def edit(kind):
            if kind == 'body':
                self.github.issue['body'] += '\nHuman note outside the managed block.'
        self.github.after_write = edit
        self.sync.sync_once(store)
        old = copy.deepcopy(self.sync.status(store)['bindings'][0]['progress']['operations'][0])
        self.assertEqual(old['status'], 'intent')
        self.github.after_write = None
        result = self.sync.continue_sync(store, body_resolution=self.resolution(store))
        self.assertEqual(result['status'], 'synced')
        self.assertEqual(result['operations'][0], old)
        self.assertIn('Human note outside the managed block.', self.github.issue['body'])
        writes = copy.deepcopy(self.github.writes)
        self.assertEqual(self.sync.continue_sync(store)['status'], 'synced')
        self.assertEqual(self.github.writes, writes)

    def test_body_review_still_requires_current_ci_and_run_control(self):
        for stopped in (False, True):
            with self.subTest(stopped=stopped):
                self.setUp()
                store = self.ready()
                self.github.issue['body'] += '\nExplicit review remains pending.'
                self.sync.sync_once(store)
                if stopped:
                    state = store.read()
                    store.control(state['revision'], 'paused', 'synthetic actual user pause')
                else:
                    self.github.jobs[1]['conclusion'] = 'failure'
                original = store.path.read_bytes()
                result = self.sync.continue_sync(store, body_resolution=self.resolution(store))
                self.assertEqual(result['reason'], 'run_control_stopped' if stopped else 'required_ci_not_successful')
                self.assertEqual(self.sync.status(store)['bindings'][0]['body_conflicts'][-1]['status'], 'unresolved')
                self.assertEqual(self.github.writes, [])
                self.assertEqual(store.path.read_bytes(), original)

    def test_conflict_in_one_ticket_does_not_block_an_independent_ticket_in_the_project(self):
        first = self.ready()
        self.github = MultipleIssues()
        self.sync = IssueSync(self.project, self.github)
        scope = {**self.scope(), 'ticket_id': '77:502'}
        second, state = self.create('independent-run', ticket_scopes=[scope])
        self.finish(second, state)
        self.sync.bind(second, issue_number=16, final_pr={'number': 13, 'repository_id': 77},
                       acceptance_map={'1': {'text': 'verified behavior', 'context': ''}})
        self.github.issues[15]['body'] += '\nConflict belongs to the first ticket.'
        self.assertEqual(self.sync.sync_once(first, ticket_id='77:501')['reason'], 'body_baseline_changed')
        conflict = copy.deepcopy(self.sync.status(first)['bindings'][0]['body_conflicts'][-1])
        result = self.sync.sync_once(second, ticket_id='77:502')
        self.assertEqual(result['status'], 'synced')
        self.assertEqual(self.github.issues[15]['state'], 'open')
        self.assertIn('Conflict belongs to the first ticket.', self.github.issues[15]['body'])
        self.assertEqual(self.sync.status(first)['bindings'][0]['body_conflicts'][-1], conflict)
        self.assertEqual(self.github.issues[16]['state'], 'closed')

    def test_single_writer_contract_exposes_the_last_read_to_patch_window(self):
        store = self.ready()
        def concurrent_edit(kind, body):
            if kind == 'body':
                self.github.issue['body'] += '\nUnobservable edit after the last GET.'  # Deliberately violate the documented single-writer agreement.
        self.github.before_write = concurrent_edit
        result = self.sync.sync_once(store)
        self.assertEqual(result['status'], 'synced')
        self.assertNotIn('Unobservable edit after the last GET.', self.github.issue['body'])  # PATCH can overwrite this window; no CAS or zero-overwrite claim is made.
        self.assertNotIn('body_conflicts', self.sync.status(store)['bindings'][0])


    def test_exact_body_review_can_resolve_only_the_body_while_close_remains_unknown(self):
        from issue_delivery import DeliveryPending
        store = self.ready()
        def timeout(kind):
            if kind == 'close':
                raise DeliveryPending('http_request_deadline')
        self.github.after_write = timeout
        self.sync.sync_once(store)
        self.github.after_write = None
        self.github.issue['body'] += '\nReviewed body after unknown close.'
        self.sync.continue_sync(store)
        before = copy.deepcopy(self.sync.status(store)['bindings'][0]['progress']['operations'])
        writes = copy.deepcopy(self.github.writes)
        result = self.sync.continue_sync(store, body_resolution=self.resolution(store))
        self.assertEqual(result['reason'], 'close_outcome_unknown')
        conflicts = self.sync.status(store)['bindings'][0]['body_conflicts']
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]['status'], 'resolved')
        self.assertEqual(result['operations'], before)
        self.assertEqual(result['operations'][-1]['status'], 'unknown')
        self.assertEqual(self.github.writes, writes)
        self.assertEqual(self.github.issue['state'], 'closed')


if __name__ == '__main__':
    unittest.main()
