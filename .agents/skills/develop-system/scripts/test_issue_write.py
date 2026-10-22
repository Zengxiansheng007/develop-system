"""Synthetic single-ticket mutations through the public sync seam and real RunStore."""
import copy
import hashlib
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote
from urllib.request import Request

from issue_sync import IssueSync
import test_issue_delivery as delivery_fixtures


class WritableGitHub(delivery_fixtures.DeliveryGitHub):
    def __init__(self):
        super().__init__()  # Only external GitHub facts are controllable.
        self.issue.update(state='open', state_reason=None, labels=['ready-for-agent', 'keep'],
                          title='Keep title', assignees=[{'login': 'human'}], milestone={'number': 4})
        self.before_write = None  # Observe durable intent at the external request boundary.
        self.after_write, self.on_read, self.reads = None, None, 0

    def get_issue(self, repository, number):
        self.reads += 1
        if self.on_read:
            self.on_read(self.reads)  # Model changes visible in a later remote observation.
        return copy.deepcopy(self.issue)

    def update_issue_body(self, repository, number, body):
        if self.before_write:
            self.before_write('body', body)  # Intent must already be visible through status.
        self.writes.append(('body', body))
        self.issue['body'] = body
        if self.after_write:
            self.after_write('body')
        return copy.deepcopy(self.issue)

    def remove_issue_label(self, repository, number, name):
        if self.before_write:
            self.before_write('label', name)
        self.writes.append(('label', name))
        self.issue['labels'].remove(name)  # Model GitHub's per-name deletion, never whole-set replacement.
        if self.after_write:
            self.after_write('label')

    def close_issue(self, repository, number):
        if self.before_write:
            self.before_write('close', None)
        self.writes.append(('close', None))
        self.issue.update(state='closed', state_reason='completed')
        if self.after_write:
            self.after_write('close')
        return copy.deepcopy(self.issue)


class MutationHTTP:
    """External delivery facts plus real HTTP Issue reads/writes, without private production mocks."""
    def __init__(self, fixture, *, error_status=None, corrupt_body=False, missing_labels=False, read_only=False):
        self.fixture, self.error_status, self.corrupt_body = fixture, error_status, corrupt_body
        self.requests, self.errors = [], []
        self.missing_labels = missing_labels  # A malformed/incomplete response cannot prove removable-label state.
        self.read_only = read_only

    def __enter__(self):
        from issue_github import GitHubReadAdapter, GitHubIssueAdapter, BoundedHTTPTransport
        boundary = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def handle_request(self):
                raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                value = json.loads(raw) if raw else None
                boundary.requests.append((self.command, self.path, value))  # No credentials or raw headers leave memory.
                status, headers = 200, {}
                try:
                    if self.command != 'GET' and boundary.error_status:
                        status = boundary.error_status
                        headers = {'Retry-After': '120'} if status == 429 else {}
                        result = {'message': 'synthetic-token private-path raw-error'}
                    elif self.path == '/repos/fixture/project':
                        result = {'id': 77, 'full_name': 'fixture/project'}
                    else:
                        repository = {'id': 77, 'full_name': 'fixture/project'}
                        if self.command == 'PATCH' and set(value) == {'body'}:
                            boundary.fixture.update_issue_body(repository, 15, value['body'])
                            if boundary.corrupt_body:
                                boundary.fixture.issue['body'] += '\nExternal HTTP edit'
                        elif self.command == 'PATCH' and value == {'state': 'closed', 'state_reason': 'completed'}:
                            boundary.fixture.close_issue(repository, 15)
                        elif self.command == 'DELETE':
                            boundary.fixture.remove_issue_label(repository, 15, unquote(self.path.split('/labels/')[1]))
                        elif self.command != 'GET':
                            raise AssertionError('Unexpected mutation fields')
                        result = copy.deepcopy(boundary.fixture.issue)
                        result['labels'] = [{'name': name} for name in result['labels']]
                        if boundary.missing_labels:
                            result.pop('labels')
                    encoded = json.dumps(result).encode('utf-8')
                except Exception as error:
                    boundary.errors.append(type(error).__name__)
                    status, encoded = 500, b'{}'
                self.send_response(status)
                for name, content in headers.items():
                    self.send_header(name, content)
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
            local = 'http://127.0.0.1:' + str(self.server.server_port) + parsed.path
            return BoundedHTTPTransport()(Request(local, headers=dict(request.header_items()), method=request.get_method(), data=request.data), **limits)
        adapter_class = GitHubReadAdapter if self.read_only else GitHubIssueAdapter
        self.adapter = adapter_class(connection_reference='fixture-connection', token_provider=lambda _: 'synthetic-token', transport=transport)
        return self

    def __getattr__(self, name):
        source = self.adapter if name in {'deadline', 'round', 'get_issue', 'update_issue_body', 'remove_issue_label', 'close_issue'} else self.fixture
        return getattr(source, name)  # Compose external read facts at their public API boundary; actual mutations use the real Adapter.

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        if self.errors:
            raise AssertionError('Mutation HTTP fixture errors: ' + ', '.join(self.errors))


class IssueWriteTests(unittest.TestCase):
    setUp = delivery_fixtures.DeliveryTests.setUp
    create = delivery_fixtures.DeliveryTests.create
    scope = delivery_fixtures.DeliveryTests.scope
    finish = delivery_fixtures.DeliveryTests.finish

    def ready(self):
        store = delivery_fixtures.DeliveryTests.ready(self)
        self.github = WritableGitHub()  # Bind first against the same literal identity/body.
        self.sync = IssueSync(self.project, self.github)
        return store

    def test_qualified_body_checks_exact_mapping_and_records_one_safe_summary(self):
        store = self.ready()
        original = self.github.issue['body']
        seen = []
        def before_write(kind, value):
            progress = self.sync.status(store)['bindings'][0]['progress']
            seen.append(copy.deepcopy(progress['operations'][-1]))  # Public persisted intent, not an internal side channel.
        self.github.before_write = before_write
        result = self.sync.sync_once(store)
        body = self.github.issue['body']
        self.assertIn('- [x] verified behavior', body)
        self.assertEqual(body.count('<!-- develop-system:issue-sync:'), 1)
        self.assertIn('b' * 40, body)
        self.assertIn('https://github.com/fixture/project/pull/13', body)
        self.assertIn('/actions/runs/201', body)
        self.assertNotIn(str(self.project), body)
        self.assertIn(hashlib.sha256(b'synthetic specialist verification').hexdigest(), body)
        self.assertEqual(seen[0]['status'], 'intent')
        self.assertEqual(seen[0]['expected_before'], original)
        self.assertEqual(seen[0]['expected_after'], body)
        self.assertEqual(self.sync.status(store)['bindings'][0]['progress']['operations'][0]['status'], 'confirmed')

    def test_single_ticket_closes_last_preserves_metadata_and_repeats_without_writes(self):
        store = self.ready()
        before_run = store.path.read_bytes()
        metadata = {key: copy.deepcopy(self.github.issue[key]) for key in ('title', 'assignees', 'milestone')}
        intents = []
        def observe(kind, value):
            op = self.sync.status(store)['bindings'][0]['progress']['operations'][-1]
            intents.append((kind, op['status']))
            if kind == 'close':
                self.assertIn('- [x] verified behavior', self.github.issue['body'])
                self.assertEqual(self.github.issue['labels'], ['keep'])
        self.github.before_write = observe
        result = self.sync.sync_once(store)
        self.assertEqual(result['status'], 'synced')
        self.assertEqual(self.github.issue['state_reason'], 'completed')
        self.assertEqual([k for k, value in self.github.writes], ['body', 'label', 'close'])
        self.assertEqual(intents, [('body', 'intent'), ('label', 'intent'), ('close', 'intent')])
        self.assertEqual({key: self.github.issue[key] for key in metadata}, metadata)
        writes = copy.deepcopy(self.github.writes)
        body = self.github.issue['body']
        self.assertEqual(IssueSync(self.project, self.github).sync_once(store)['status'], 'synced')
        self.assertEqual(self.github.writes, writes)
        self.assertEqual(self.github.issue['body'], body)
        self.assertEqual(store.path.read_bytes(), before_run)

    def test_real_http_serializes_only_allowed_writes_and_reads_actual_fields(self):
        from issue_github import GitHubIssueAdapter, BoundedHTTPTransport
        store = self.ready()
        fixture, captured = self.github, []
        fixture.issue['labels'] = ['待执行 / ready', 'keep']  # The actual DELETE path must encode one label segment.
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # Never print authorization headers.

            def do_request(self):
                data = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                value = json.loads(data) if data else None
                captured.append((self.command, self.path, value, self.headers.get('Content-Type')))
                if self.path == '/repos/fixture/project':
                    result = {'id': 77, 'full_name': 'fixture/project'}
                elif self.command == 'PATCH':
                    fixture.issue.update(value)
                    result = fixture.issue
                elif self.command == 'DELETE':
                    fixture.issue['labels'].remove(unquote(self.path.split('/labels/')[1]))
                    result = [{'name': label} for label in fixture.issue['labels']]
                else:
                    result = fixture.issue
                if isinstance(result, dict) and 'body' in result:
                    result = {**result, 'labels': [{'name': label} for label in result['labels']]}
                raw = json.dumps(result, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_request
            do_PATCH = do_request
            do_DELETE = do_request
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        def transport(request, **limits):
            parsed = urlsplit(request.full_url)
            local = 'http://127.0.0.1:' + str(server.server_port) + parsed.path
            forwarded = Request(local, headers=dict(request.header_items()), method=request.get_method(), data=request.data)
            return BoundedHTTPTransport()(forwarded, **limits)  # Exercise the actual subprocess/urllib wire, not a fake serializer.
        adapter = GitHubIssueAdapter(connection_reference='fixture-connection', token_provider=lambda _: 'synthetic-token', transport=transport)
        repository = {'id': 77, 'full_name': 'fixture/project'}
        with adapter.round('fixture-connection'):
            adapter.update_issue_body(repository, 15, '准确正文 ✓')
            self.assertEqual(adapter.get_issue(repository, 15)['body'], '准确正文 ✓')
            adapter.remove_issue_label(repository, 15, '待执行 / ready')
            self.assertEqual(adapter.get_issue(repository, 15)['labels'], ['keep'])
            adapter.close_issue(repository, 15)
            actual = adapter.get_issue(repository, 15)
            self.assertEqual((actual['state'], actual['state_reason']), ('closed', 'completed'))
        writes = [item for item in captured if item[0] != 'GET']
        self.assertEqual([(v[0], v[2]) for v in writes], [('PATCH', {'body': '准确正文 ✓'}), ('DELETE', None),
                                                              ('PATCH', {'state': 'closed', 'state_reason': 'completed'})])
        self.assertIn('%2F', writes[1][1])
        self.assertEqual(writes[0][3], 'application/json; charset=utf-8')

    def test_explicit_failure_remains_durable_when_later_qualification_is_missing(self):
        from issue_delivery import DeliveryPending
        store = self.ready()
        def deny(kind, value):
            raise DeliveryPending('permission_denied')  # A definitive synthetic service rejection cannot become full success.
        self.github.before_write = deny
        result = self.sync.sync_once(store)
        self.assertEqual((result['status'], result['reason']), ('pending', 'permission_denied'))
        self.assertEqual(result['operations'][0]['status'], 'failed')
        self.assertEqual(self.github.writes, [])
        self.assertEqual(self.sync.sync_once(store)['reason'], 'previous_request_failed')
        self.github.jobs[1]['conclusion'] = 'failure'
        repeated = IssueSync(self.project, self.github).sync_once(store)
        self.assertEqual(repeated['reason'], 'required_ci_not_successful')
        self.assertEqual(repeated['operations'][0]['status'], 'failed')
        self.assertEqual(self.sync.status(store)['bindings'][0]['progress']['operations'], result['operations'])

    def test_final_remote_reads_cannot_hide_a_changed_accepted_candidate(self):
        store = self.ready()
        observations = []
        def final_read(run_id):
            observations.append(run_id)
            if len(observations) == 2:
                (self.project / 'candidate.py').write_text('value = 2  # Later local edit.\n', encoding='utf-8')
        self.github.on_jobs = final_read  # Local evidence changes during the final external requalification.
        result = self.sync.sync_once(store)
        self.assertEqual((result['status'], result['reason']), ('pending', 'qualification_changed_before_close'))
        self.assertEqual([kind for kind, value in self.github.writes], ['body', 'label'])
        self.assertEqual(self.github.issue['state'], 'open')

    def test_new_verified_ci_updates_the_same_summary_without_reclosing(self):
        store = self.ready()
        self.assertEqual(self.sync.sync_once(store)['status'], 'synced')
        previous_writes = len(self.github.writes)
        self.github.runs[0].update(id=202, run_number=2)
        for job in self.github.jobs:
            job['run_id'] = 202  # A later successful current run changes the actual evidence link.
        result = self.sync.sync_once(store)
        self.assertEqual(result['status'], 'synced')
        self.assertIn('/actions/runs/202', self.github.issue['body'])
        self.assertNotIn('/actions/runs/201', self.github.issue['body'])
        self.assertEqual(self.github.issue['body'].count('<!-- develop-system:issue-sync:'), 1)
        self.assertEqual([kind for kind, value in self.github.writes[previous_writes:]], ['body'])

    def test_detected_write_before_read_change_stops_without_any_mutation(self):
        store = self.ready()
        def change(read_number):
            if read_number == 2:
                self.github.issue['body'] += '\nHuman addition'  # The write-before re-read must notice a changed baseline.
        self.github.on_read = change
        result = self.sync.sync_once(store)
        self.assertEqual((result['status'], result['reason']), ('pending', 'issue_readback_mismatch'))
        self.assertEqual(self.github.writes, [])
        self.assertIn('Human addition', self.github.issue['body'])

    def test_body_readback_mismatch_preserves_intent_and_stops_labels_and_close(self):
        store = self.ready()
        self.github.after_write = lambda kind: self.github.issue.update(body='External changed body')
        result = self.sync.sync_once(store)
        self.assertEqual((result['status'], result['reason']), ('pending', 'issue_readback_mismatch'))
        self.assertEqual([kind for kind, value in self.github.writes], ['body'])
        saved = self.sync.status(store)['bindings'][0]['progress']
        self.assertEqual(saved['operations'][0]['status'], 'intent')
        self.assertEqual(self.github.issue['state'], 'open')

    def test_label_readback_mismatch_stops_closure(self):
        store = self.ready()
        def change(kind):
            if kind == 'label':
                self.github.issue['labels'].append('concurrent-label')
        self.github.after_write = change
        result = self.sync.sync_once(store)
        self.assertEqual(result['reason'], 'issue_readback_mismatch')
        self.assertEqual([kind for kind, value in self.github.writes], ['body', 'label'])
        self.assertEqual(self.github.issue['state'], 'open')

    def test_close_readback_mismatch_is_not_reported_as_synced(self):
        store = self.ready()
        def change(kind):
            if kind == 'close':
                self.github.issue['state_reason'] = 'not_planned'
        self.github.after_write = change
        result = self.sync.sync_once(store)
        self.assertEqual((result['status'], result['reason']), ('pending', 'close_reason_readback_mismatch'))
        self.assertEqual(self.sync.status(store)['bindings'][0]['progress']['operations'][-1]['status'], 'intent')

    def test_missing_current_delivery_or_local_acceptance_produces_zero_writes(self):
        store = self.ready()
        self.github.pull['merged'] = False
        self.assertEqual(self.sync.sync_once(store)['reason'], 'final_pr_not_merged')
        self.github.pull['merged'] = True
        self.github.jobs[1]['conclusion'] = 'failure'
        self.assertEqual(self.sync.sync_once(store)['reason'], 'required_ci_not_successful')
        self.github.jobs[1]['conclusion'] = 'success'
        (self.project / 'candidate.py').write_text('changed = True\n', encoding='utf-8')
        self.assertEqual(self.sync.sync_once(store)['reason'], 'acceptance_pending')
        self.assertEqual(self.github.writes, [])

    def test_changed_or_duplicate_mapping_never_uses_a_stale_line_number(self):
        store = self.ready()
        self.github.issue['body'] += '\n- [ ] verified behavior'
        self.assertEqual(self.sync.sync_once(store)['reason'], 'body_baseline_changed')
        self.assertEqual(self.github.writes, [])

    def test_subset_mapping_preserves_unmapped_accepted_and_manual_checkboxes(self):
        (self.project / 'candidate.py').write_bytes(b'value = 1  # Synthetic accepted candidate.\n')
        self.github = WritableGitHub()
        self.github.issue['body'] += '\n- [ ] second accepted behavior\n\n## Human notes\n- [ ] manual action\nOriginal prose.'
        original = self.github.issue['body']
        self.sync = IssueSync(self.project, self.github)
        self.config['required_ci'] = [{'type': 'actions', 'workflow_id': 301, 'workflow': '.github/workflows/check-skills.yml',
                                      'event': 'push', 'job': 'check (' + system + '-latest)'} for system in ('windows', 'ubuntu')]
        self.sync.configure(self.config, user_reference='synthetic subset mapping enablement')
        store, state = self.create(acceptance=['verified behavior', 'second accepted behavior'], ticket_scopes=[self.scope(['1', '2'])])
        self.finish(store, state, ids=['1', '2'])
        self.sync.bind(store, issue_number=15, final_pr={'number': 13, 'repository_id': 77},
                       acceptance_map={'1': {'text': 'verified behavior', 'context': '## Acceptance criteria'}})
        self.assertEqual(self.sync.sync_once(store)['status'], 'synced')
        body = self.github.issue['body'].split('\n\n<!-- develop-system:issue-sync:')[0]
        self.assertEqual(body, original.replace('- [ ] verified behavior', '- [x] verified behavior'))
        self.assertIn('Accepted criteria: 1, 2', self.github.issue['body'])

    def test_public_sync_normal_path_uses_real_http_mutations_and_persistent_readbacks(self):
        store = self.ready()
        with MutationHTTP(self.github) as boundary:
            self.sync = IssueSync(self.project, boundary)
            result = self.sync.sync_once(store)
            self.assertEqual(result['status'], 'synced')
            self.assertEqual([request[0] for request in boundary.requests if request[0] != 'GET'], ['PATCH', 'DELETE', 'PATCH'])
            self.assertEqual([op['status'] for op in self.sync.status(store)['bindings'][0]['progress']['operations']], ['confirmed'] * 3)

    def test_public_sync_http_permission_failure_is_pending_redacted_and_never_closes(self):
        store = self.ready()
        with MutationHTTP(self.github, error_status=403) as boundary:
            self.sync = IssueSync(self.project, boundary)
            result = self.sync.sync_once(store)
            self.assertEqual((result['status'], result['reason']), ('pending', 'permission_denied'))
            self.assertEqual(result['operations'][0]['status'], 'failed')
            self.assertEqual([request[0] for request in boundary.requests if request[0] != 'GET'], ['PATCH'])
        saved = json.dumps(self.sync.status(store))
        for content in ('synthetic-token', 'private-path', 'raw-error'):
            self.assertNotIn(content, saved)
        self.assertEqual(self.github.issue['state'], 'open')

    def test_public_sync_http_body_mismatch_never_removes_labels_or_closes(self):
        store = self.ready()
        with MutationHTTP(self.github, corrupt_body=True) as boundary:
            self.sync = IssueSync(self.project, boundary)
            result = self.sync.sync_once(store)
            self.assertEqual((result['status'], result['reason']), ('pending', 'issue_readback_mismatch'))
            self.assertEqual([request[0] for request in boundary.requests if request[0] != 'GET'], ['PATCH'])
        self.assertEqual(self.github.issue['labels'], ['ready-for-agent', 'keep'])

    def test_public_sync_http_missing_labels_cannot_be_treated_as_an_empty_set(self):
        store = self.ready()
        with MutationHTTP(self.github, missing_labels=True) as boundary:
            self.sync = IssueSync(self.project, boundary)
            result = self.sync.sync_once(store)
            self.assertEqual((result['status'], result['reason']), ('pending', 'issue_not_open_or_fields_unavailable'))
            self.assertEqual([request[0] for request in boundary.requests if request[0] != 'GET'], [])

    def test_existing_read_adapter_remains_qualification_only(self):
        store = self.ready()
        with MutationHTTP(self.github, read_only=True) as boundary:
            self.sync = IssueSync(self.project, boundary)
            result = self.sync.sync_once(store)
            self.assertEqual((result['status'], result['reason']), ('pending', 'delivery_qualified'))
            self.assertEqual(boundary.requests, [])  # An existing read-only connection does not acquire Issue-write capability.

    def test_confirmed_sync_survives_temporary_missing_ci_without_rewriting(self):
        store = self.ready()
        original_run = store.path.read_bytes()
        self.assertEqual(self.sync.sync_once(store)['status'], 'synced')
        writes, body = copy.deepcopy(self.github.writes), self.github.issue['body']
        jobs = self.github.jobs
        self.github.jobs = []  # Lose current eligibility without changing any previously confirmed remote operation.
        pending = self.sync.sync_once(store)
        self.assertEqual((pending['status'], pending['reason']), ('pending', 'required_ci_missing_or_ambiguous'))
        self.assertTrue(all(op['status'] == 'confirmed' for op in self.sync.status(store)['bindings'][0]['progress']['operations']))
        self.github.jobs = jobs
        self.assertEqual(self.sync.sync_once(store)['status'], 'synced')  # Use the same public object and freshly restored facts.
        self.assertEqual(self.sync.sync_once(store)['status'], 'synced')
        self.assertEqual(self.github.writes, writes)
        self.assertEqual(self.github.issue['body'], body)
        self.assertEqual(store.path.read_bytes(), original_run)

    def test_confirmed_sync_survives_temporary_read_failure_without_rewriting(self):
        from issue_delivery import DeliveryPending
        store = self.ready()
        self.assertEqual(self.sync.sync_once(store)['status'], 'synced')
        writes, body, original_run = copy.deepcopy(self.github.writes), self.github.issue['body'], store.path.read_bytes()
        reader = self.github.get_workflow_runs
        def unavailable(*args):
            raise DeliveryPending('remote_service_unavailable')  # Only the public external API boundary fails.
        self.github.get_workflow_runs = unavailable
        self.assertEqual(self.sync.sync_once(store)['reason'], 'remote_service_unavailable')
        self.github.get_workflow_runs = reader
        self.assertEqual(self.sync.sync_once(store)['status'], 'synced')
        self.assertEqual(self.github.writes, writes)
        self.assertEqual(self.github.issue['body'], body)
        self.assertEqual(store.path.read_bytes(), original_run)

    def confirmed_then_pending(self):
        store = self.ready()
        self.assertEqual(self.sync.sync_once(store)['status'], 'synced')
        writes = copy.deepcopy(self.github.writes)
        jobs = self.github.jobs
        self.github.jobs = []
        self.assertEqual(self.sync.sync_once(store)['reason'], 'required_ci_missing_or_ambiguous')
        self.github.jobs = jobs
        return store, writes  # Shared synthetic setup retains the same public object and real RunStore.

    def test_confirmed_repeat_after_pending_preserves_an_external_reopen(self):
        store, writes = self.confirmed_then_pending()
        self.github.issue.update(state='open', state_reason='reopened')
        result = self.sync.sync_once(store)
        self.assertEqual((result['status'], result['reason']), ('pending', 'remote_changed_after_sync'))
        self.assertEqual(self.github.writes, writes)
        self.assertEqual(self.github.issue['state'], 'open')

    def test_confirmed_repeat_after_pending_preserves_changed_remote_body(self):
        store, writes = self.confirmed_then_pending()
        self.github.issue['body'] += '\nHuman content'
        self.assertEqual(self.sync.sync_once(store)['reason'], 'body_baseline_changed')
        self.assertEqual(self.github.writes, writes)
        self.assertTrue(self.github.issue['body'].endswith('Human content'))

    def test_confirmed_repeat_after_pending_does_not_reclean_a_changed_label(self):
        store, writes = self.confirmed_then_pending()
        self.github.issue['labels'].append('ready-for-agent')
        self.assertEqual(self.sync.sync_once(store)['reason'], 'remote_changed_after_sync')
        self.assertEqual(self.github.writes, writes)
        self.assertIn('ready-for-agent', self.github.issue['labels'])

    def test_confirmed_repeat_after_pending_still_requires_current_acceptance(self):
        store, writes = self.confirmed_then_pending()
        original_run = store.path.read_bytes()
        (self.project / 'candidate.py').write_text('value = 9  # Later unaccepted contents.\n', encoding='utf-8')
        self.assertEqual(self.sync.sync_once(store)['reason'], 'acceptance_pending')
        self.assertEqual(self.github.writes, writes)
        self.assertEqual(store.path.read_bytes(), original_run)

    def test_confirmed_closure_of_another_delivery_is_not_reused(self):
        store, writes = self.confirmed_then_pending()
        self.github.pull['merge_commit_sha'] = 'd' * 40
        self.github.files['d' * 40] = copy.deepcopy(self.github.files['b' * 40])
        self.github.runs[0]['head_sha'] = 'd' * 40
        for job in self.github.jobs:
            job['head_sha'] = 'd' * 40  # All current external delivery facts now qualify a different actual merged SHA.
        result = self.sync.sync_once(store)
        self.assertEqual(result['delivery']['sha'], 'd' * 40)
        self.assertEqual((result['status'], result['reason']), ('pending', 'issue_not_open_or_fields_unavailable'))
        self.assertEqual(self.github.writes, writes)

    def test_manual_closed_issue_without_our_confirmed_close_is_not_claimed(self):
        store = self.ready()
        self.github.issue.update(state='closed', state_reason='completed')
        result = self.sync.sync_once(store)
        self.assertEqual((result['status'], result['reason']), ('pending', 'issue_not_open_or_fields_unavailable'))
        self.assertEqual(self.github.writes, [])

    def test_public_sync_http_repeat_recovers_fresh_facts_and_refreshes_same_summary(self):
        store = self.ready()
        original_run = store.path.read_bytes()
        with MutationHTTP(self.github) as boundary:
            self.sync = IssueSync(self.project, boundary)
            self.assertEqual(self.sync.sync_once(store)['status'], 'synced')
            jobs = self.github.jobs
            self.github.jobs = []
            self.assertEqual(self.sync.sync_once(store)['reason'], 'required_ci_missing_or_ambiguous')
            self.github.jobs = jobs
            self.assertEqual(self.sync.sync_once(store)['status'], 'synced')
            self.assertEqual(len(self.github.writes), 3)
            self.github.runs[0].update(id=202, run_number=2)
            for job in self.github.jobs:
                job['run_id'] = 202
            self.assertEqual(self.sync.sync_once(store)['status'], 'synced')
            self.assertEqual([kind for kind, _ in self.github.writes], ['body', 'label', 'close', 'body'])
            self.assertEqual([request[0] for request in boundary.requests if request[0] != 'GET'], ['PATCH', 'DELETE', 'PATCH', 'PATCH'])
        self.assertEqual(self.github.issue['body'].count('<!-- develop-system:issue-sync:'), 1)
        self.assertIn('/actions/runs/202', self.github.issue['body'])
        self.assertNotIn('/actions/runs/201', self.github.issue['body'])
        self.assertEqual(store.path.read_bytes(), original_run)

    def test_public_sync_http_repeat_survives_a_temporary_connection_failure(self):
        store = self.ready()
        with MutationHTTP(self.github) as boundary:
            self.sync = IssueSync(self.project, boundary)
            self.assertEqual(self.sync.sync_once(store)['status'], 'synced')
            original_provider = boundary.adapter.token_provider
            def unavailable(reference):
                raise RuntimeError('synthetic unavailable provider')  # Test the outer round failure path without credentials or a private mock.
            boundary.adapter.token_provider = unavailable
            self.assertEqual(self.sync.sync_once(store)['reason'], 'connection_unavailable')
            boundary.adapter.token_provider = original_provider
            self.assertEqual(self.sync.sync_once(store)['status'], 'synced')
            self.assertEqual([request[0] for request in boundary.requests if request[0] != 'GET'], ['PATCH', 'DELETE', 'PATCH'])


if __name__ == '__main__':
    unittest.main()
