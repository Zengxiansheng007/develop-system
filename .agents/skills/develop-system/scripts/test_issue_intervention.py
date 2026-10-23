"""Human intervention through public synchronization seams and synthetic GitHub facts."""
import copy
import unittest

from issue_sync import IssueSync
from issue_delivery import DeliveryPending
import test_issue_write as write_fixtures


class EventGitHub(write_fixtures.WritableGitHub):
    """Literal remote event facts; no invented local operation IDs or actor ownership."""
    def __init__(self):
        super().__init__()
        self.events = []  # GitHub events are ordered external facts, independent of local progress.

    def event(self, kind):
        value = {'id': 700 + len(self.events), 'node_id': 'event-' + str(len(self.events)),
                 'event': kind, 'created_at': '2026-10-23T01:00:00Z', 'actor': {'id': 42, 'login': 'same-account'}}
        self.events.append(value)
        return value

    def get_issue_events(self, repository, number):
        return copy.deepcopy(self.events)  # A caller receives complete bounded observations.

    def close_issue(self, repository, number):
        return super().close_issue(repository, number)  # Shared external fixture emits exactly the API close event.

    def human_reopen(self, *, close_again=False):
        self.event('reopened')
        self.issue.update(state='open', state_reason='reopened')
        if close_again:
            self.event('closed')
            self.issue.update(state='closed', state_reason='completed')


class InterventionTests(unittest.TestCase):
    setUp = write_fixtures.IssueWriteTests.setUp
    create = write_fixtures.IssueWriteTests.create
    scope = write_fixtures.IssueWriteTests.scope
    finish = write_fixtures.IssueWriteTests.finish

    def ready(self):
        store = write_fixtures.IssueWriteTests.ready(self)
        self.github = EventGitHub()  # The bound identity/body exactly matches the independent fixture.
        self.sync = IssueSync(self.project, self.github)
        return store

    def test_later_same_account_reopen_and_reclose_persists_ticket_pause(self):
        store = self.ready()
        self.assertEqual(self.sync.sync_once(store)['status'], 'synced')
        original_run, writes = store.path.read_bytes(), copy.deepcopy(self.github.writes)
        self.github.human_reopen(close_again=True)
        result = IssueSync(self.project, self.github).continue_sync(store)
        self.assertEqual((result['status'], result['reason']), ('manual_paused', 'external_reopen_observed'))
        binding = self.sync.status(store)['bindings'][0]
        self.assertEqual(binding['intervention']['status'], 'manual_paused')
        self.assertEqual(binding['intervention']['reopened_event']['id'], 701)
        self.assertEqual(binding['intervention']['closure']['event']['id'], 700)
        self.assertEqual(self.github.writes, writes)
        self.assertEqual(store.path.read_bytes(), original_run)

    def test_only_explicit_ticket_takeover_rechecks_current_ci_and_body(self):
        store = self.ready()
        self.assertEqual(self.sync.sync_once(store)['status'], 'synced')
        self.github.human_reopen()
        self.assertEqual(self.sync.continue_sync(store)['status'], 'manual_paused')
        self.github.issue['body'] += '\nHuman explanation retained.'
        writes = copy.deepcopy(self.github.writes)
        self.sync.configure(self.config, user_reference='synthetic repeat enablement')
        self.assertEqual(IssueSync(self.project, self.github).continue_sync(store)['status'], 'manual_paused')
        self.github.jobs[1]['conclusion'] = 'failure'
        rejected = self.sync.take_over(store, ticket_id='77:501', user_reference='synthetic explicit user takeover of ticket 77:501')
        self.assertEqual(rejected['reason'], 'required_ci_not_successful')
        self.assertEqual(self.sync.status(store)['bindings'][0]['intervention']['status'], 'manual_paused')
        self.assertEqual(self.github.writes, writes)
        self.github.jobs[1]['conclusion'] = 'success'
        self.assertEqual(self.sync.continue_sync(store)['status'], 'manual_paused')
        result = self.sync.take_over(store, ticket_id='77:501', user_reference='synthetic explicit user takeover of ticket 77:501')
        self.assertEqual(result['status'], 'synced')
        self.assertIn('Human explanation retained.', self.github.issue['body'])
        self.assertEqual([kind for kind, _ in self.github.writes].count('close'), 2)
        self.assertEqual(len(self.sync.status(store)['bindings'][0]['intervention']['takeovers']), 1)

    def test_native_early_close_configuration_is_a_visible_conflict(self):
        store = self.ready()
        self.github.pull['body'] = 'Delivery details.\n\nFixes https://github.com/fixture/project/issues/15'
        original = copy.deepcopy(self.github.pull)
        self.github.jobs[1]['conclusion'] = 'failure'  # Native closing syntax could close before this required post-merge result.
        result = self.sync.sync_once(store)
        self.assertEqual((result['status'], result['reason']), ('pending', 'native_early_close_conflict'))
        self.assertEqual(self.github.writes, [])
        self.assertEqual(self.github.pull, original)

    def test_real_http_event_pages_detect_a_reopen_beyond_first_page(self):
        store = self.ready()
        self.assertEqual(self.sync.sync_once(store)['status'], 'synced')
        self.github.human_reopen(close_again=True)
        writes = copy.deepcopy(self.github.writes)
        with write_fixtures.MutationHTTP(self.github, event_page_size=1) as boundary:
            result = IssueSync(self.project, boundary).continue_sync(store)
            self.assertEqual((result['status'], result['reason']), ('manual_paused', 'external_reopen_observed'))
            paths = [path for method, path, _ in boundary.requests if '/events?' in path]
            self.assertTrue(any('page=3' in path for path in paths))
            self.assertTrue(all(method == 'GET' for method, _, _ in boundary.requests))
        self.assertEqual(self.github.writes, writes)

    def test_external_preclosure_is_explained_without_reopening_or_claiming_it(self):
        store = self.ready()
        self.github.issue.update(state='closed', state_reason='completed')
        self.github.event('closed')  # Same account is still external without our acknowledged request interval.
        result = self.sync.continue_sync(store)
        self.assertEqual((result['status'], result['reason']), ('pending', 'externally_closed_issue'))
        self.assertEqual(self.github.writes, [])
        self.assertNotIn('intervention', self.sync.status(store)['bindings'][0])
        self.assertEqual(self.github.issue['state'], 'closed')

    def pause(self):
        store = self.ready()
        self.assertEqual(self.sync.sync_once(store)['status'], 'synced')
        self.github.human_reopen()
        self.assertEqual(self.sync.continue_sync(store)['status'], 'manual_paused')
        return store

    def test_pause_survives_disablement_reenablement_and_connection_replacement(self):
        store = self.pause()
        writes = copy.deepcopy(self.github.writes)
        original = store.path.read_bytes()
        for changes in ({'enabled': False}, {'enabled': True}, {'connection_reference': 'replacement-connection'}):
            self.config.update(changes)
            self.sync.configure(self.config, user_reference='synthetic separate project configuration decision')
            self.assertEqual(IssueSync(self.project, self.github).continue_sync(store)['status'], 'manual_paused')
            self.assertEqual(self.sync.status(store)['bindings'][0]['intervention']['status'], 'manual_paused')
        self.assertEqual(self.github.writes, writes)
        self.assertEqual(store.path.read_bytes(), original)
        self.assertEqual(self.sync.take_over(store, ticket_id='77:501', user_reference='synthetic user request')['status'], 'unmanaged')

    def test_takeover_requires_explicit_ticket_and_rechecks_acceptance_pr_and_exact_mapping(self):
        for change, reason in (('source', 'acceptance_pending'), ('pr', 'final_pr_not_merged'), ('mapping', 'body_mapping_changed')):
            with self.subTest(change=change):
                self.setUp()
                store = self.pause()
                before = copy.deepcopy(self.github.writes)
                with self.assertRaises(ValueError):
                    self.sync.take_over(store, ticket_id='77:501', user_reference='')
                if change == 'source':
                    (self.project / 'candidate.py').write_text('changed = True  # Synthetic later unaccepted source.\n', encoding='utf-8')
                elif change == 'pr':
                    self.github.pull['merged'] = False
                else:
                    self.github.issue['body'] = self.github.issue['body'].replace('verified behavior', 'changed requirement')
                result = self.sync.take_over(store, ticket_id='77:501', user_reference='synthetic explicit ticket takeover')
                self.assertEqual(result['reason'], reason)
                self.assertEqual(result['status'], 'manual_paused')
                self.assertEqual(self.sync.status(store)['bindings'][0]['intervention']['status'], 'manual_paused')
                self.assertEqual(self.github.writes, before)

    def test_takeover_preserves_a_reclosed_human_state_and_never_reopens_it(self):
        store = self.ready()
        self.assertEqual(self.sync.sync_once(store)['status'], 'synced')
        self.github.human_reopen(close_again=True)
        self.assertEqual(self.sync.continue_sync(store)['status'], 'manual_paused')
        before = copy.deepcopy(self.github.writes)
        result = self.sync.take_over(store, ticket_id='77:501', user_reference='synthetic explicit user request')
        self.assertEqual((result['status'], result['reason']), ('manual_paused', 'externally_closed_issue'))
        self.assertEqual(self.github.writes, before)

    def test_lost_close_response_and_human_reopen_remains_unknown_without_retry(self):
        store = self.ready()
        def lost(kind):
            if kind == 'close':
                self.github.human_reopen(close_again=True)
                raise DeliveryPending('http_request_deadline')  # Applied request, user changes and no acknowledgement are deliberately ambiguous.
        self.github.after_write = lost
        self.assertEqual(self.sync.sync_once(store)['reason'], 'http_request_deadline')
        before = copy.deepcopy(self.github.writes)
        result = IssueSync(self.project, self.github).continue_sync(store)
        self.assertEqual((result['status'], result['reason']), ('pending', 'close_outcome_unknown'))
        self.assertEqual(result['operations'][-1]['status'], 'unknown')
        self.assertEqual(result['operations'][-1]['confirmation'], 'source_unattributed')
        self.assertNotIn('intervention', self.sync.status(store)['bindings'][0])
        self.assertEqual(self.github.writes, before)

    def test_missing_or_changing_event_interval_never_reuses_a_previous_close(self):
        for mode, reason in (('missing', 'close_event_interval_unknown'), ('changing', 'issue_events_unstable')):
            with self.subTest(mode=mode):
                self.setUp()
                store = self.ready()
                self.assertEqual(self.sync.sync_once(store)['status'], 'synced')
                before = copy.deepcopy(self.github.writes)
                if mode == 'missing':
                    self.github.events.clear()  # A history gap is not proof of no external intervention.
                else:
                    calls = []
                    def reader(repository, number):
                        calls.append(number)
                        if len(calls) == 2:
                            self.github.human_reopen()
                        return copy.deepcopy(self.github.events)
                    self.github.get_issue_events = reader
                result = self.sync.continue_sync(store)
                self.assertEqual((result['status'], result['reason']), ('pending', reason))
                self.assertEqual(self.github.writes, before)

    def test_native_commit_preclosure_is_an_explicit_conflict(self):
        store = self.ready()
        self.github.issue.update(state='closed', state_reason='completed')
        self.github.event('closed')['commit_id'] = 'd' * 40
        self.assertEqual(self.sync.sync_once(store)['reason'], 'native_early_close_conflict')
        self.assertEqual(self.github.writes, [])

    def test_native_preclosure_is_visible_even_when_required_ci_has_not_passed(self):
        store = self.ready()
        self.github.issue.update(state='closed', state_reason='completed')
        self.github.event('closed')['commit_id'] = 'd' * 40
        self.github.jobs[1]['conclusion'] = 'failure'
        self.assertEqual(self.sync.continue_sync(store)['reason'], 'native_early_close_conflict')
        self.assertEqual(self.github.writes, [])

    def test_an_independent_bound_ticket_can_sync_while_the_first_is_paused(self):
        paused_store = self.pause()
        original = paused_store.path.read_bytes()
        paused_remote = self.github
        self.github = EventGitHub()
        self.github.issue.update(id=502, number=16)
        self.sync = IssueSync(self.project, self.github)
        other_scope = {**self.scope(), 'ticket_id': '77:502'}
        other_store, state = self.create('other-run', ticket_scopes=[other_scope])
        self.finish(other_store, state)
        self.sync.bind(other_store, issue_number=16, final_pr={'number': 13, 'repository_id': 77},
                       acceptance_map={'1': {'text': 'verified behavior', 'context': '## Acceptance criteria'}})
        self.assertEqual(self.sync.sync_once(other_store, ticket_id='77:502')['status'], 'synced')
        self.assertEqual(self.sync.status(paused_store)['bindings'][0]['intervention']['status'], 'manual_paused')
        self.assertEqual(paused_remote.issue['state'], 'open')
        self.assertEqual(paused_store.path.read_bytes(), original)

    def test_real_http_event_failures_and_untrusted_next_pages_preserve_unknown(self):
        cases = [({'event_error_status': 410}, 'remote_material_gone'),
                 ({'event_page_size': 1, 'event_link_origin': 'https://untrusted.example'}, 'remote_pagination_unauthorized')]
        for options, reason in cases:
            with self.subTest(reason=reason):
                self.setUp()
                store = self.ready()
                self.assertEqual(self.sync.sync_once(store)['status'], 'synced')
                self.github.human_reopen(close_again=True)
                writes = copy.deepcopy(self.github.writes)
                with write_fixtures.MutationHTTP(self.github, **options) as boundary:
                    result = IssueSync(self.project, boundary).continue_sync(store)
                    self.assertEqual((result['status'], result['reason']), ('pending', reason))
                    self.assertTrue(all(method == 'GET' for method, _, _ in boundary.requests))
                    self.assertTrue(all('untrusted' not in path for _, path, _ in boundary.requests))
                self.assertEqual(self.github.writes, writes)
                self.assertNotIn('raw-event-error', str(self.sync.status(store)))


if __name__ == '__main__':
    unittest.main()
