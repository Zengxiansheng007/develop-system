"""Batch behavior at the public sync seam; all specialist/GitHub facts are synthetic."""
import copy
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit
from urllib.request import Request

from issue_sync import IssueSync
from issue_delivery import DeliveryPending
import test_issue_sync as enrollment_fixtures
import test_issue_write as write_fixtures


class BatchGitHub(write_fixtures.WritableGitHub):
    """Independently stored remote Issues sharing a synthetic delivered PR."""
    def __init__(self):
        super().__init__()  # Reuse only external delivery/CI fixture facts.
        self.issues, self.histories, self.actions = {}, {}, []
        self.on_issue_read = None  # Model an external change between ticket observations.
        self.failures = {}  # Remote boundary failures never replace internal rules.
        self.label_failures = {}  # Definitive rejected labels leave separately recoverable partial body progress.

    def get_issue(self, repository, number):
        if self.on_issue_read:
            self.on_issue_read(number)  # Current remote observations can invalidate another ticket.
        if number in self.failures:
            raise DeliveryPending(self.failures[number])
        return copy.deepcopy(self.issues[number])

    def get_issue_events(self, repository, number):
        return copy.deepcopy(self.histories[number])

    def update_issue_body(self, repository, number, body):
        self.actions.append((number, 'body'))  # Capture permitted remote mutations by stable selected Issue.
        self.issues[number]['body'] = body
        return copy.deepcopy(self.issues[number])

    def remove_issue_label(self, repository, number, name):
        if number in self.label_failures:
            raise DeliveryPending(self.label_failures[number])  # No remote effect is applied on a definitive rejection.
        self.actions.append((number, 'label'))  # Named removal preserves each Issue's unrelated labels.
        self.issues[number]['labels'].remove(name)

    def close_issue(self, repository, number):
        self.actions.append((number, 'close'))
        self.issues[number].update(state='closed', state_reason='completed')  # Model the real update contract.
        self.histories[number].append({'id': 1000 + number * 100 + len(self.histories[number]),
            'node_id': 'fixture-' + str(number) + '-' + str(len(self.histories[number])),
            'event': 'closed', 'created_at': '2026-10-23T01:00:00Z'})  # Synthetic acknowledged-close event.
        return copy.deepcopy(self.issues[number])


class BatchHTTP(write_fixtures.MutationHTTP):
    """Actual loopback HTTP Issue traffic for multiple independently identified tickets."""
    def __enter__(self):
        from issue_github import GitHubIssueAdapter, BoundedHTTPTransport
        boundary = self
        repository = {'id': 77, 'full_name': 'fixture/project'}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # Never log raw headers, credentials or server request contents.

            def handle_request(self):
                raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                value = json.loads(raw) if raw else None
                path = urlsplit(self.path).path
                boundary.requests.append((self.command, path, value))  # Observe exact per-ticket HTTP routing.
                try:
                    if path == '/repos/fixture/project':
                        result = repository
                    else:
                        parts = path.removeprefix('/repos/fixture/project/issues/').split('/')
                        number = int(parts[0])
                        if self.command == 'GET' and parts[-1] == 'events':
                            result = boundary.fixture.get_issue_events(repository, number)
                        else:
                            if self.command == 'PATCH' and set(value) == {'body'}:
                                boundary.fixture.update_issue_body(repository, number, value['body'])
                            elif self.command == 'PATCH' and value == {'state': 'closed', 'state_reason': 'completed'}:
                                boundary.fixture.close_issue(repository, number)
                            elif self.command == 'DELETE' and parts[1] == 'labels':
                                boundary.fixture.remove_issue_label(repository, number, unquote(parts[2]))
                            elif self.command != 'GET':
                                raise AssertionError('Unapproved mutation fields')
                            result = boundary.fixture.get_issue(repository, number)
                            result['labels'] = [{'name': name} for name in result['labels']]  # Real GitHub response shape crosses actual serialization.
                    encoded, status = json.dumps(result).encode('utf-8'), 200
                except Exception as error:
                    boundary.errors.append(type(error).__name__)
                    encoded, status = b'{}', 500  # Safe fixture error body contains no private exception data.
                self.send_response(status)
                self.send_header('Content-Length', str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            do_GET = handle_request
            do_PATCH = handle_request
            do_DELETE = handle_request
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        def transport(request, **limits):
            parsed = urlsplit(request.full_url)
            local = 'http://127.0.0.1:' + str(self.server.server_port) + parsed.path + ('?' + parsed.query if parsed.query else '')
            return BoundedHTTPTransport()(Request(local, headers=dict(request.header_items()), method=request.get_method(), data=request.data), **limits)  # Only the test transport rewrites origin.
        self.adapter = GitHubIssueAdapter(connection_reference='fixture-connection', token_provider=lambda _: 'synthetic-token', transport=transport)
        return self


class BatchTests(unittest.TestCase):
    setUp = enrollment_fixtures.IssueSyncTests.setUp  # Real disposable project/registry; no execution authority patching.

    def prepare(self, *, parent=False, necessary_review=False):
        self.github = BatchGitHub()
        self.sync = IssueSync(self.project, self.github)
        self.config['required_ci'] = [{'type': 'actions', 'workflow_id': 301,
            'workflow': '.github/workflows/check-skills.yml', 'event': 'push',
            'job': 'check (' + system + '-latest)'} for system in ('windows', 'ubuntu')]  # Both required jobs remain mandatory.
        self.sync.configure(self.config, user_reference='synthetic batch project enablement')
        self.criteria = ['first ticket behavior', 'second ticket behavior', 'second complete scope']
        self.declarations = [(15, 501, ['1'], 'first'), (16, 502, ['2', '3'], 'second')]
        if parent:
            self.criteria.append('whole batch accepted')
            self.declarations.append((14, 500, ['4'], 'parent'))  # Parent owns a distinct original criterion and work step.
        plan, scopes = [], []
        for number, issue_id, ids, name in self.declarations:
            content = ('value = ' + str(number) + '  # Synthetic accepted ' + name + '.\n').encode()
            (self.project / (name + '.py')).write_bytes(content)  # Exact independent accepted contents per ticket.
            for files in self.github.files.values():
                files[name + '.py'] = content
            plan.append({'step_id': name, 'skill_id': 'research'})
            scopes.append({'ticket_id': '77:' + str(issue_id), 'acceptance_ids': ids, 'required_steps': [name],
                'input_version': name + '-v1', 'candidate_paths': [name + '.py']})  # Fix whole scopes at actual creation.
            if name == 'parent':
                plan[-1]['depends_on'] = ['first', 'second']  # Parent's own batch closure includes the original child work.
                scopes[-1]['candidate_paths'] = ['first.py', 'second.py', 'parent.py']  # Whole batch delivery is independently verified.
            if name == 'second' and necessary_review:
                plan.append({'step_id': 'second-review', 'skill_id': 'to-spec', 'required': False})
                scopes[-1]['required_steps'].append('second-review')  # Whole ticket necessity strengthens global optionality.
            self.github.issues[number] = {**copy.deepcopy(self.github.issue), 'id': issue_id, 'number': number,
                'body': '## Acceptance criteria\n\n' + '\n'.join('- [ ] ' + self.criteria[int(i) - 1] for i in ids)}
            self.github.histories[number] = []
        from workflow_state import RunStore
        self.store = RunStore(self.project, 'batch-run')
        self.store.create('synthetic batch', plan, self.criteria, self.registry, ticket_scopes=scopes)  # Real enrollment and immutable scope.
        return self.store

    def finish_ticket(self, name, ids):
        state = self.store.read()
        state = self.store.dispatch(state['revision'], 'research', name + '-v1', name)
        frame = state['frames'][-1]
        evidence = self.project / ('evidence-' + name + '.txt')
        evidence.write_text('synthetic specialist conclusion for ' + name, encoding='utf-8')
        self.store.receive(state['revision'], {'protocol_version': 1, 'event_id': frame['frame_id'] + '-result',
            'run_id': self.store.run_id, 'frame_id': frame['frame_id'], 'parent_frame_id': None,
            'skill_id': 'research', 'input_version': name + '-v1', 'mode': 'skill', 'execution': 'sequential',
            'status': 'completed', 'return_to': 'develop-system', 'completion_checked': True,
            'artifacts': [str(evidence)], 'checks': [], 'findings': [], 'blockers': [], 'resolved_findings': [],
            'acceptance_checks': [{'id': i, 'status': 'passed', 'completion_checked': True,
                'kind': 'technical', 'evidence': [str(evidence)]} for i in ids]})  # Synthetic assertions travel through public receive.

    def bind_all(self):
        for number, issue_id, ids, name in self.declarations:
            self.sync.bind(self.store, issue_number=number, final_pr={'number': 13, 'repository_id': 77},
                acceptance_map={i: {'text': self.criteria[int(i) - 1], 'context': ''} for i in ids})  # Shared PR never supplies acceptance.

    def authority_bytes(self):
        return {p.name: p.read_bytes() for p in self.store.path.parent.iterdir() if p.is_file()}  # Observe original authority and event/budget artifacts.

    def test_default_batch_shared_pr_closes_only_independently_complete_ticket(self):
        self.prepare()
        self.finish_ticket('first', ['1'])
        self.finish_ticket('second', ['2'])  # Full second-ticket criterion3 deliberately has no conclusion.
        self.bind_all()
        before = self.authority_bytes()
        result = self.sync.sync_once(self.store)
        self.assertEqual(result['status'], 'batch')
        self.assertEqual([(r['ticket_id'], r['status']) for r in result['tickets']], [('77:501', 'synced'), ('77:502', 'pending')])
        self.assertEqual(result['tickets'][1]['reason'], 'acceptance_pending')
        self.assertEqual(self.github.issues[15]['state'], 'closed')
        self.assertEqual(self.github.issues[16]['state'], 'open')
        self.assertEqual(self.github.actions, [(15, 'body'), (15, 'label'), (15, 'close')])  # Missing full scope produces no writes.
        self.assertEqual(self.authority_bytes(), before)

    def test_batch_missing_necessary_closure_and_changed_candidate_remain_local(self):
        self.prepare(necessary_review=True)
        self.finish_ticket('first', ['1'])
        self.finish_ticket('second', ['2', '3'])  # Complete AC cannot replace the missing explicitly necessary review.
        self.bind_all()
        before = self.authority_bytes()
        results = self.sync.sync_once(self.store)['tickets']
        self.assertEqual([r['status'] for r in results], ['synced', 'pending'])
        self.assertIn('Necessary scoped steps', results[1]['qualification_reason'])
        self.assertEqual(self.github.issues[16]['state'], 'open')
        self.assertEqual(self.authority_bytes(), before)

    def test_batch_observes_changed_ticket_version_after_prior_ticket_finishes(self):
        self.prepare()
        self.finish_ticket('first', ['1'])
        self.finish_ticket('second', ['2', '3'])
        self.bind_all()
        before = self.authority_bytes()
        def change_candidate(number):
            if self.github.issues[15]['state'] == 'closed':
                (self.project / 'second.py').write_bytes(b'value = 999  # Changed after accepted source.\n')  # External timing boundary changes next ticket, never its authority.
        self.github.on_issue_read = change_candidate
        results = self.sync.sync_once(self.store)['tickets']
        self.assertEqual([r['status'] for r in results], ['synced', 'pending'])
        self.assertEqual(results[1]['reason'], 'acceptance_pending')
        self.assertEqual(self.github.issues[16]['state'], 'open')
        self.assertEqual(self.github.actions, [(15, 'body'), (15, 'label'), (15, 'close')])
        self.assertEqual(self.authority_bytes(), before)

    def test_explicit_parent_plain_ac_source_needs_own_acceptance_delivery_and_current_ci(self):
        self.prepare(parent=True)
        self.finish_ticket('first', ['1'])
        self.finish_ticket('second', ['2', '3'])
        self.declarations = self.declarations[:2]  # Bind only children before the separately declared parent.
        self.bind_all()
        parent_body = '## Testing Decisions\n\n### 原需求验收条件\n\n- **AC-001 / REQ-001**：whole batch accepted\n\n### 技术验收补充\n\nKeep parent notes.'
        self.github.issues[14]['body'] = parent_body  # Mirror the immutable Spec14 plain-bullet source format.
        binding = self.sync.bind(self.store, issue_number=14, final_pr={'number': 13, 'repository_id': 77},
            acceptance_map={}, parent_spec=True)  # No checkbox exists; the parent's whole original scope is still mandatory.
        self.assertTrue(binding['parent_spec'])
        before = self.authority_bytes()
        results = self.sync.sync_once(self.store)['tickets']
        self.assertEqual([r['status'] for r in results], ['synced', 'synced', 'pending'])
        self.assertEqual(results[-1]['reason'], 'acceptance_pending')
        self.assertEqual(self.github.issues[14]['state'], 'open')  # Both child closed flags supply no parent conclusion.
        self.assertEqual(self.github.issues[14]['body'], parent_body)
        self.assertEqual(self.authority_bytes(), before)
        self.finish_ticket('parent', ['4'])  # Actual original-parent professional return supplies its own whole AC.
        self.sync.bind(self.store, issue_number=14, final_pr={'number': 13, 'repository_id': 77},
            acceptance_map={}, parent_spec=True)  # Freeze only the now-current parent's candidate.
        self.github.jobs[0]['conclusion'] = 'failure'
        before = self.authority_bytes()
        self.assertEqual(self.sync.continue_sync(self.store, ticket_id='77:500')['reason'], 'required_ci_not_successful')
        self.assertEqual(self.github.issues[14]['state'], 'open')
        self.github.jobs[0]['conclusion'] = 'success'  # Current post-merge CI, not child closure, removes the remaining prerequisite.
        self.assertEqual(self.sync.continue_sync(self.store, ticket_id='77:500')['status'], 'synced')
        self.assertIn('- **AC-001 / REQ-001**：whole batch accepted', self.github.issues[14]['body'])
        self.assertIn('Keep parent notes.', self.github.issues[14]['body'])
        self.assertEqual(self.authority_bytes(), before)

    def test_batch_continuation_preserves_reopen_pause_and_recovers_other_partial_ticket(self):
        self.prepare()
        self.finish_ticket('first', ['1'])
        self.finish_ticket('second', ['2', '3'])
        self.bind_all()
        self.github.label_failures[16] = 'permission_denied'
        state = self.store.read()
        self.store.complete(state['revision'], [{'id': i, 'source_frame_id': next(f['frame_id'] for f in state['frames']
            if f['input_version'] == ('first-v1' if i == '1' else 'second-v1'))} for i in ['1', '2', '3']])  # Explicitly completed source can later catch up without resume.
        before = self.authority_bytes()
        first = self.sync.sync_once(self.store)['tickets']
        self.assertEqual([r['status'] for r in first], ['synced', 'pending'])
        self.assertEqual(first[1]['reason'], 'permission_denied')
        self.assertEqual([op['status'] for op in first[1]['operations']], ['confirmed', 'failed'])
        self.github.issues[15].update(state='open', state_reason='reopened')
        self.github.histories[15].append({'id': 9999, 'node_id': 'human-reopen', 'event': 'reopened',
            'created_at': '2026-10-23T02:00:00Z', 'actor': {'login': 'same-account'}})  # Same actor still cannot authorize reacquisition.
        self.github.label_failures.clear()
        writes = copy.deepcopy(self.github.actions)
        restarted = IssueSync(self.project, self.github)
        second = restarted.continue_sync(self.store)['tickets']
        self.assertEqual([r['status'] for r in second], ['manual_paused', 'synced'])
        self.assertEqual(self.github.actions[len(writes):], [(16, 'label'), (16, 'close')])  # Previously applied body is not repeated.
        self.assertEqual(self.github.issues[15]['state'], 'open')
        self.assertEqual(restarted.status(self.store)['bindings'][0]['intervention']['status'], 'manual_paused')
        self.assertEqual(self.authority_bytes(), before)

    def test_body_conflict_is_local_and_batch_cannot_broadcast_explicit_review(self):
        self.prepare()
        self.finish_ticket('first', ['1'])
        self.finish_ticket('second', ['2', '3'])
        self.bind_all()
        self.github.issues[15]['body'] += '\nHuman annotation.'
        before = self.authority_bytes()
        results = self.sync.sync_once(self.store)['tickets']
        self.assertEqual([r['status'] for r in results], ['pending', 'synced'])
        conflict = results[0]['body_conflict']
        writes = copy.deepcopy(self.github.actions)
        resolution = {'conflict_id': conflict['conflict_id'], 'expected_body': self.github.issues[15]['body'],
            'acceptance_map': {'1': {'text': self.criteria[0], 'context': ''}}, 'user_reference': 'synthetic ticket15 current body review'}
        self.assertEqual(self.sync.continue_sync(self.store, body_resolution=resolution)['reason'], 'explicit_ticket_selection_required')
        self.assertEqual(self.github.actions, writes)
        self.assertEqual(self.sync.continue_sync(self.store, ticket_id='77:501', body_resolution=resolution)['status'], 'synced')
        self.assertIn('Human annotation.', self.github.issues[15]['body'])
        self.assertEqual(self.authority_bytes(), before)

    def test_shared_pr_still_checks_each_ticket_delivered_contents(self):
        self.prepare()
        self.finish_ticket('first', ['1'])
        self.finish_ticket('second', ['2', '3'])
        self.bind_all()
        self.github.files['b' * 40]['second.py'] = b'value = 0  # Unaccepted delivered second ticket.\n'
        before = self.authority_bytes()
        results = self.sync.sync_once(self.store)['tickets']
        self.assertEqual([r['status'] for r in results], ['synced', 'pending'])
        self.assertEqual(results[1]['reason'], 'accepted_content_unverified')
        self.assertEqual(self.github.issues[16]['state'], 'open')
        self.assertEqual(self.authority_bytes(), before)

    def test_explicit_binding_refuses_ambiguous_parent_source_and_unsupported_delivery(self):
        self.prepare(parent=True)
        body = '## Testing Decisions\n\n### 原需求验收条件\n\n- **AC-001 / REQ-001**：whole batch accepted\n- **AC-002 / REQ-002**：another original parent criterion\n'
        self.github.issues[14]['body'] = body  # A caller's one-item declaration cannot conceal a second original source criterion.
        for marker in (False, True):
            with self.assertRaisesRegex(ValueError, 'complete.*acceptance'):
                self.sync.bind(self.store, issue_number=14, final_pr={'number': 13, 'repository_id': 77}, acceptance_map={}, parent_spec=marker)
        for final_pr in (None, [], [{'number': 13, 'repository_id': 77}], {'number': 13, 'repository_id': 88}):
            with self.subTest(final_pr=final_pr), self.assertRaisesRegex(ValueError, 'Exactly one final PR'):
                self.sync.bind(self.store, issue_number=15, final_pr=final_pr,
                    acceptance_map={'1': {'text': self.criteria[0], 'context': ''}})  # Missing/multiple/cross-repository deliveries never become bindings.
        self.assertEqual(self.sync.status(self.store)['bindings'], [])
        self.assertEqual(self.github.actions, [])

    def test_parent_source_with_an_unrecognized_original_ac_cannot_be_narrowed(self):
        self.prepare(parent=True)
        self.github.issues[14]['body'] = ('## Testing Decisions\n\n### 原需求验收条件\n\n'
            '- **AC-001 / REQ-001**：whole batch accepted\n'
            '- **AC-002 / REQ-002**: another original parent criterion\n')  # An unsupported delimiter must not hide an original list entry.
        with self.assertRaisesRegex(ValueError, 'complete.*acceptance'):
            self.sync.bind(self.store, issue_number=14, final_pr={'number': 13, 'repository_id': 77},
                acceptance_map={}, parent_spec=True)
        self.assertEqual(self.sync.status(self.store)['bindings'], [])
        self.assertEqual(self.github.actions, [])

    def test_duplicate_binding_and_foreign_repository_same_number_are_rejected(self):
        self.prepare()
        self.finish_ticket('first', ['1'])
        self.finish_ticket('second', ['2', '3'])
        self.bind_all()
        before = self.authority_bytes()
        current = self.sync.status(self.store)
        self.github.pull['number'], self.github.pull['id'] = 17, 902
        with self.assertRaisesRegex(ValueError, 'conflicting run, PR'):
            self.sync.bind(self.store, issue_number=15, final_pr={'number': 17, 'repository_id': 77},
                acceptance_map={'1': {'text': self.criteria[0], 'context': ''}})  # A second finalPR cannot replace the original one.
        self.github.issues[15].update(repository_id=88, repository_full_name='other/project')
        with self.assertRaisesRegex(ValueError, 'repository identity'):
            self.sync.bind(self.store, issue_number=15, final_pr={'number': 17, 'repository_id': 77},
                acceptance_map={'1': {'text': self.criteria[0], 'context': ''}})  # Same number is never a cross-repository identity.
        self.assertEqual(self.sync.status(self.store), current)
        self.assertEqual(self.authority_bytes(), before)
        self.assertEqual(self.github.actions, [])

    def test_real_http_batch_routes_serial_issue_mutations_without_touching_historical_run(self):
        from workflow_state import RunStore
        historical = RunStore(self.project, 'before-enablement')
        historical.create('unmanaged historical fixture', self.plan, ['verified behavior'], self.registry)  # Exists before the new project's explicit generation.
        old_bytes = {p.name: p.read_bytes() for p in historical.path.parent.iterdir() if p.is_file()}
        self.prepare()
        self.finish_ticket('first', ['1'])
        self.finish_ticket('second', ['2', '3'])
        self.bind_all()
        before = self.authority_bytes()
        with BatchHTTP(self.github) as boundary:
            results = IssueSync(self.project, boundary).continue_sync(self.store)['tickets']  # Real Adapter mutation and event contracts run for each ticket.
            self.assertEqual([r['status'] for r in results], ['synced', 'synced'])
            writes = [(method, path, value) for method, path, value in boundary.requests if method != 'GET']
            self.assertEqual([(method, path) for method, path, _ in writes], [
                ('PATCH', '/repos/fixture/project/issues/15'), ('DELETE', '/repos/fixture/project/issues/15/labels/ready-for-agent'),
                ('PATCH', '/repos/fixture/project/issues/15'), ('PATCH', '/repos/fixture/project/issues/16'),
                ('DELETE', '/repos/fixture/project/issues/16/labels/ready-for-agent'), ('PATCH', '/repos/fixture/project/issues/16')])
            self.assertEqual([set(value) for method, _, value in writes if method == 'PATCH'],
                [{'body'}, {'state', 'state_reason'}, {'body'}, {'state', 'state_reason'}])  # No title/assignee/milestone write is introduced by batch.
            self.assertEqual(boundary.errors, [])
            self.assertEqual(IssueSync(self.project, boundary).continue_sync(historical)['status'], 'unmanaged')
        self.assertEqual({p.name: p.read_bytes() for p in historical.path.parent.iterdir() if p.is_file()}, old_bytes)
        self.assertEqual(self.authority_bytes(), before)


if __name__ == '__main__':
    unittest.main()  # Same public Python test entry is used by managed source observation.
