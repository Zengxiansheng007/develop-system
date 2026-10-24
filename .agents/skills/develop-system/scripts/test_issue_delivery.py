"""Synthetic delivery facts observed through real temporary RunStore and sync_once."""
import copy
import base64
import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from urllib.parse import parse_qs, urlsplit, urlencode
from urllib.request import Request

from issue_sync import IssueSync
import test_issue_sync as enrollment_fixtures  # Import helpers without rediscovering their TestCase twice.


class ControlledHTTP:
    """Real local HTTP wire bridge; origin rewriting exists only in this synthetic test fixture."""
    def __init__(self, responder):
        self.responder, self.paths, self.errors = responder, [], []

    def __enter__(self):
        fixture = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # No request headers or raw responses reach test logs.

            def do_GET(self):
                fixture.paths.append(self.path)
                try:
                    request = Request('https://api.github.com' + self.path, headers=dict(self.headers.items()), method='GET')
                    result = fixture.responder(request, timeout=1, max_bytes=4 * 1024 * 1024)
                    self.send_response(result['status'])
                    for key, value in result.get('headers', {}).items():
                        self.send_header(key, value)
                    self.send_header('Content-Length', str(len(result['body'])))
                    self.end_headers()
                    self.wfile.write(result['body'])
                except Exception as error:
                    fixture.errors.append(type(error).__name__)  # Assertion failures remain visible without raw HTTP material.
                    self.send_error(500)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __call__(self, request, *, timeout, max_bytes):
        from issue_github import BoundedHTTPTransport
        parsed = urlsplit(request.full_url)
        local = 'http://127.0.0.1:' + str(self.server.server_port) + parsed.path + ('?' + parsed.query if parsed.query else '')
        return BoundedHTTPTransport()(Request(local, headers=dict(request.header_items()), method=request.get_method()), timeout=timeout, max_bytes=max_bytes)

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        if self.errors:
            raise AssertionError('Controlled HTTP responder failed: ' + ', '.join(self.errors))


class DeliveryGitHub(enrollment_fixtures.GitHubFixture):
    def __init__(self):
        super().__init__()  # All identities below are synthetic external facts.
        self.pull.update(merged=True, merged_at='2026-10-22T01:00:00Z', base_ref='main',
                         head_sha='a' * 40, merge_commit_sha='b' * 40)
        self.files = {sha: {'candidate.py': b'value = 1  # Synthetic accepted candidate.\n'} for sha in ('a' * 40, 'b' * 40)}  # Independently specified Git contents.
        self.tree_changes = {}  # Boundary facts can represent additions, modes and truncated trees.
        self.workflow = {'id': 301, 'path': '.github/workflows/check-skills.yml'}  # A stable workflow identity, not its display name.
        self.workflows = {301: self.workflow}
        self.runs = [{'id': 201, 'workflow_id': 301, 'path': '.github/workflows/check-skills.yml',
                      'head_sha': 'b' * 40, 'head_branch': 'main', 'event': 'push', 'run_attempt': 1,
                      'run_number': 1, 'status': 'completed', 'conclusion': 'success'}]
        self.jobs = [{'id': 401 + i, 'run_id': 201, 'head_sha': 'b' * 40, 'run_attempt': 1,
                      'name': 'check (' + system + '-latest)', 'status': 'completed', 'conclusion': 'success',
                      'steps': [{'name': 'platform conditional test', 'conclusion': 'skipped'}]}
                     for i, system in enumerate(('windows', 'ubuntu'))]  # Step-level skips do not skip a successful job.
        self.on_recheck = None  # Simulate a new attempt arriving while jobs are read.
        self.detail_reads = 0
        self.after_jobs = None
        self.on_jobs = None

    def get_workflow(self, repository, workflow_id):
        return copy.deepcopy(self.workflows[workflow_id])

    def get_workflow_runs(self, repository, sha, branch, event):
        return copy.deepcopy(self.runs)

    def get_workflow_run(self, repository, run_id):
        run = next(r for r in self.runs if r['id'] == run_id)
        self.detail_reads += 1  # Count only external read observations for the controlled race.
        if self.on_recheck and self.detail_reads > 1:
            run = {**run, **self.on_recheck}  # The current endpoint can supersede list observations.
        return copy.deepcopy(run)

    def get_attempt_jobs(self, repository, run_id, attempt):
        if self.after_jobs:
            self.after_jobs()  # External observations can race local/source and PR changes.
        if self.on_jobs:
            self.on_jobs(run_id)  # Later configured workflows can invalidate an earlier workflow's observation.
        return copy.deepcopy(self.jobs)

    def get_commit_tree(self, repository, sha):
        return self.tree_changes.get(sha, {'truncated': False, 'tree': [
            {'path': path, 'type': 'blob', 'mode': '100644', 'sha': sha + ':' + path} for path in self.files[sha]]})

    def get_blob(self, repository, sha):
        commit, path = sha.split(':', 1)  # Synthetic blob IDs are explicitly not Git object IDs.
        return self.files[commit][path]


class DeliveryTests(unittest.TestCase):
    setUp = enrollment_fixtures.IssueSyncTests.setUp  # Reuse real public RunStore fixture construction, never edit its authority.
    create = enrollment_fixtures.IssueSyncTests.create
    scope = enrollment_fixtures.IssueSyncTests.scope
    finish = enrollment_fixtures.IssueSyncTests.finish

    def ready(self, extra_ci=None, directory=False):
        (self.project / 'candidate.py').write_bytes(b'value = 1  # Synthetic accepted candidate.\n')  # Fix exact accepted fixture bytes across OS newline translation.
        self.github = DeliveryGitHub()  # Only the GitHub boundary is controllable.
        self.sync = IssueSync(self.project, self.github)
        self.config['required_ci'] = [{'type': 'actions', 'workflow_id': 301, 'workflow': '.github/workflows/check-skills.yml',
                                      'event': 'push', 'job': 'check (' + system + '-latest)'} for system in ('windows', 'ubuntu')]
        if extra_ci:
            self.config['required_ci'].append(extra_ci)  # Additional required workflows are fixed before real run creation.
        self.sync.configure(self.config, user_reference='synthetic delivery enablement')
        scope = self.scope()
        if directory:
            (self.project / 'src').mkdir()
            (self.project / 'candidate.py').rename(self.project / 'src/candidate.py')
            scope['candidate_paths'] = ['src']  # A directory scope owns additions and deletions too.
            self.github.files = {sha: {'src/candidate.py': raw['candidate.py']} for sha, raw in self.github.files.items()}
        store, state = self.create(ticket_scopes=[scope])
        self.finish(store, state)
        self.sync.bind(store, issue_number=15, final_pr={'number': 13, 'repository_id': 77},
                       acceptance_map={'1': {'text': 'verified behavior', 'context': ''}})
        return store

    def test_merged_delivery_uses_actual_sha_and_rejects_wrong_target_or_temporary_sha(self):
        store = self.ready()  # The PR head differs from the API's actual delivered SHA.
        result = self.sync.sync_once(store)
        self.assertEqual(result.get('delivery', {}).get('sha'), 'b' * 40)
        self.assertEqual(result['status'], 'pending')
        for change in ({'base_ref': 'other'}, {'merged': False}, {'merge_commit_sha': None}, {'id': 902}):
            with self.subTest(change=change):
                original = copy.deepcopy(self.github.pull)
                self.github.pull.update(change)
                self.assertIsNone(self.sync.sync_once(store).get('delivery'))
                self.github.pull = original
        self.assertEqual(self.github.writes, [])

    def test_real_adapter_traverses_exact_delivery_and_paginated_attempt_jobs(self):
        from issue_github import GitHubReadAdapter  # Import the real adapter only at this public boundary.
        store = self.ready()
        fixture = self.github
        requests = []
        raw = b'value = 1  # Synthetic accepted candidate.\n'
        blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()  # Independent standard Git object fixture.

        def transport(request, *, timeout, max_bytes):
            requests.append(request)  # Capture real Request serialization at the HTTP seam.
            self.assertEqual(request.get_method(), 'GET')
            self.assertIsNone(request.data)
            self.assertGreater(timeout, 0)
            self.assertEqual(request.get_header('Accept'), 'application/vnd.github+json')
            self.assertEqual(request.get_header('X-github-api-version'), '2022-11-28')
            url = urlsplit(request.full_url)
            path, query = url.path.removeprefix('/repos/fixture/project'), parse_qs(url.query)
            headers = {}
            if path == '':
                value = {'id': 77, 'full_name': 'fixture/project'}
            elif path == '/pulls/13':
                value = {'id': 901, 'number': 13, 'merged': True, 'merged_at': '2026-10-22T01:00:00Z',
                         'base': {'ref': 'main', 'repo': {'id': 77, 'full_name': 'fixture/project'}},
                         'head': {'sha': 'a' * 40}, 'merge_commit_sha': 'b' * 40}
            elif path.startswith('/commits/'):
                value = {'sha': path.split('/')[-1], 'commit': {'tree': {'sha': 'c' * 40}}}
            elif path.startswith('/git/trees/'):
                value = {'sha': 'c' * 40, 'truncated': False, 'tree': [{'path': 'candidate.py', 'type': 'blob', 'mode': '100644', 'sha': blob}]}
            elif path.startswith('/git/blobs/'):
                value = {'sha': blob, 'size': len(raw), 'encoding': 'base64', 'content': base64.b64encode(raw).decode()}
            elif path == '/actions/workflows/301':
                value = fixture.workflow
            elif path == '/actions/runs':
                self.assertEqual(query.get('head_sha'), ['b' * 40])
                self.assertEqual(query.get('event'), ['push'])
                self.assertEqual(query.get('branch'), ['main'])
                page = int(query.get('page', ['1'])[0])
                value = {'total_count': len(fixture.runs), 'workflow_runs': fixture.runs[page - 1:page]}
                if len(fixture.runs) > page:
                    parameters = {'head_sha': 'b' * 40, 'branch': 'main', 'event': 'push', 'per_page': '100', 'page': str(page + 1)}
                    headers['link'] = '<https://api.github.com/repos/fixture/project/actions/runs?' + urlencode(parameters) + '>; rel="next"'
            elif path.startswith('/actions/runs/') and path.removeprefix('/actions/runs/').isdigit():
                value = next(run for run in fixture.runs if run['id'] == int(path.split('/')[-1]))  # Serve the newer run's real detail endpoint too.
            elif path == '/actions/runs/201/attempts/1/jobs':
                page = int(query.get('page', ['1'])[0])
                value = {'total_count': 2, 'jobs': fixture.jobs[page - 1:page]}
                if page == 1:
                    headers['link'] = '<https://api.github.com/repos/fixture/project/actions/runs/201/attempts/1/jobs?per_page=100&page=2>; rel="next"'
            elif path == '/actions/runs/202/attempts/1/jobs':
                value = {'total_count': 0, 'jobs': []}  # The newest queued attempt has no published required jobs yet.
            else:
                self.fail('Unexpected synthetic HTTP endpoint: ' + path)
            return {'status': 200, 'headers': headers, 'body': json.dumps(value).encode()}

        with ControlledHTTP(transport) as actual_http:
            adapter = GitHubReadAdapter(connection_reference='fixture-connection', token_provider=lambda _: 'synthetic-token', transport=actual_http)
            self.sync = IssueSync(self.project, adapter)
            before = store.path.read_bytes()
            result = self.sync.sync_once(store)
            self.assertTrue(result['delivery_eligible'])
            fixture.jobs[1]['conclusion'] = 'failure'
            failed = self.sync.sync_once(store)
            self.assertFalse(failed.get('delivery_eligible', False))  # A failed second HTTP page cannot disappear.
            self.assertEqual(failed['reason'], 'required_ci_not_successful')  # A transport deadline cannot stand in for a selected job failure.
            fixture.jobs[1]['conclusion'] = 'success'
            fixture.runs.append({**fixture.runs[0], 'id': 202, 'run_number': 2, 'status': 'queued', 'conclusion': None})
            pending = self.sync.sync_once(store)
            self.assertFalse(pending.get('delivery_eligible', False))  # A newer pending execution arrives on page two.
            self.assertEqual(pending['reason'], 'required_ci_missing_or_ambiguous')  # Require the actual missing-job fact.
            self.assertIn('/repos/fixture/project/actions/runs/202/attempts/1/jobs', [urlsplit(request.full_url).path for request in requests])
        self.assertTrue(any('page=2' in request.full_url for request in requests))
        self.assertTrue(all(request.get_header('Authorization') == 'Bearer synthetic-token' for request in requests))
        self.assertEqual(store.path.read_bytes(), before)
        self.assertNotIn('synthetic-token', json.dumps(self.sync.status()))

    def test_real_http_errors_are_pending_redacted_and_preserve_rate_waits(self):
        from issue_github import GitHubReadAdapter
        store = self.ready()
        before = store.path.read_bytes()
        cases = [(401, {}, 'connection_unauthorized', None), (403, {}, 'permission_denied', None),
                 (404, {}, 'remote_material_missing', None), (410, {}, 'remote_material_gone', None),
                 (422, {}, 'remote_request_rejected', None), (500, {}, 'remote_service_unavailable', None),
                 (302, {'Location': 'https://other.example/secret'}, 'http_redirect_refused', None),
                 (403, {'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': '1500'}, 'rate_limited', 1500),
                 (429, {'Retry-After': '120'}, 'rate_limited', 1120), (429, {}, 'rate_limited', 1060),
                 (429, {'Retry-After': 'Thu, 01 Jan 1970 00:20:00 GMT'}, 'rate_limited', 1200)]
        for status, headers, reason, expected_wait in cases:
            with self.subTest(status=status):
                def responder(request, **kwargs):
                    return {'status': status, 'headers': headers, 'body': b'{"message":"synthetic-token private-path raw-error"}'}
                with ControlledHTTP(responder) as actual_http:
                    adapter = GitHubReadAdapter(connection_reference='fixture-connection', token_provider=lambda _: 'synthetic-token',
                                                transport=actual_http, wall_clock=lambda: 1000)
                    self.sync = IssueSync(self.project, adapter)
                    result = self.sync.sync_once(store)
                    self.assertEqual(result['reason'], reason)
                    self.assertEqual(len(actual_http.paths), 1)  # Errors never trigger automatic retries or CI mutations.
                    if expected_wait is not None:
                        self.assertEqual(result['retry_not_before'], expected_wait)
                saved = json.dumps(self.sync.status())
                for secret in ('synthetic-token', 'private-path', 'raw-error'):
                    self.assertNotIn(secret, saved)
        self.assertEqual(store.path.read_bytes(), before)

    def test_real_http_pagination_never_treats_an_unparsed_next_link_as_complete(self):
        from issue_delivery import DeliveryPending
        from issue_github import GitHubReadAdapter
        def responder(request, **kwargs):
            if urlsplit(request.full_url).path == '/repos/fixture/project':
                return {'status': 200, 'headers': {}, 'body': b'{"id":77,"full_name":"fixture/project"}'}
            return {'status': 200, 'headers': {'Link': '<https://other.example/secret>; rel=next'},
                    'body': b'{"total_count":1,"workflow_runs":[{"id":201}]}'}
        with ControlledHTTP(responder) as actual_http:
            adapter = GitHubReadAdapter(connection_reference='fixture-connection', token_provider=lambda _: 'synthetic-token', transport=actual_http)
            with self.assertRaises(DeliveryPending) as raised:
                adapter.get_workflow_runs({'id': 77, 'full_name': 'fixture/project'}, 'b' * 40, 'main', 'push')
            self.assertEqual(raised.exception.reason, 'remote_pagination_unauthorized')  # A deadline cannot impersonate the rejected next-page origin.
            self.assertEqual(len(actual_http.paths), 2)  # Credentials never leave the configured repository origin/endpoint.

    def test_real_http_pagination_caps_counts_duplicates_and_query_changes(self):
        from issue_delivery import DeliveryPending
        from issue_github import GitHubReadAdapter
        base = 'https://api.github.com/repos/fixture/project/actions/runs?head_sha=' + 'b' * 40 + '&branch=main&event=push&per_page=100&page=2'
        cases = [({'total_count': 2, 'workflow_runs': [{'id': 201}]}, {}, 10, 'remote_pagination_incomplete'),
                 ({'total_count': 1000, 'workflow_runs': [{'id': 201}]}, {}, 10, 'remote_pagination_incomplete'),
                 ({'total_count': 2, 'workflow_runs': [{'id': 201}]}, {'Link': '<' + base + '>; rel="next"'}, 1, 'http_page_budget_exhausted'),
                 ({'total_count': 2, 'workflow_runs': [{'id': 201}]}, {'Link': '<' + base.replace('branch=main', 'branch=other') + '>; rel="next"'}, 10, 'remote_pagination_unauthorized'),
                 ({'total_count': 2, 'workflow_runs': [{'id': 201}]}, {'Link': '<' + base.replace('/fixture/project/', '/other/project/') + '>; rel="next"'}, 10, 'remote_pagination_unauthorized'),
                 ({'total_count': 2, 'workflow_runs': [{'id': 201}]}, {'Link': '<' + base + '>; rel="next"'}, 10, 'remote_pagination_incomplete')]
        for value, headers, pages, reason in cases:
            with self.subTest(reason=reason, headers=headers, pages=pages):
                def responder(request, **kwargs):
                    if urlsplit(request.full_url).path == '/repos/fixture/project':
                        return {'status': 200, 'headers': {}, 'body': b'{"id":77,"full_name":"fixture/project"}'}
                    return {'status': 200, 'headers': headers, 'body': json.dumps(value).encode()}
                with ControlledHTTP(responder) as actual_http:
                    adapter = GitHubReadAdapter(connection_reference='fixture-connection', token_provider=lambda _: 'synthetic-token',
                                                transport=actual_http, max_pages=pages)
                    with self.assertRaises(DeliveryPending) as raised:
                        adapter.get_workflow_runs({'id': 77, 'full_name': 'fixture/project'}, 'b' * 40, 'main', 'push')
                    self.assertEqual(raised.exception.reason, reason)
                    self.assertLessEqual(len(actual_http.paths), 3)

    def test_real_http_round_deadline_and_request_budget_are_shared_across_reads(self):
        from issue_github import GitHubReadAdapter
        store = self.ready()
        for options, expected, requests in (({'max_requests': 1}, 'http_request_budget_exhausted', 1),
                                            ({'round_timeout': 10, 'request_timeout': 10}, 'http_round_deadline', 2)):
            clock = [0.0]
            def responder(request, **kwargs):
                clock[0] += 6  # Simulate elapsed wall time between real successful HTTP responses.
                value = {'id': 77, 'full_name': 'fixture/project'} if urlsplit(request.full_url).path == '/repos/fixture/project' else {}
                return {'status': 200, 'headers': {}, 'body': json.dumps(value).encode()}
            with ControlledHTTP(responder) as actual_http:
                adapter = GitHubReadAdapter(connection_reference='fixture-connection', token_provider=lambda _: 'synthetic-token', transport=actual_http,
                                            clock=lambda: clock[0], **options)
                self.sync = IssueSync(self.project, adapter)
                self.assertEqual(self.sync.sync_once(store)['reason'], expected)
                self.assertEqual(len(actual_http.paths), requests)
        self.sync = IssueSync(self.project, GitHubReadAdapter(connection_reference='fixture-connection'))
        self.assertEqual(self.sync.sync_once(store)['reason'], 'connection_unavailable')

    def test_service_wait_boundary_is_respected_on_an_immediate_continue(self):
        from issue_github import GitHubReadAdapter
        store = self.ready()
        clock = [1000]
        def responder(request, **kwargs):
            return {'status': 429, 'headers': {'Retry-After': '120'}, 'body': b'{"message":"rate limited"}'}
        with ControlledHTTP(responder) as actual_http:
            adapter = GitHubReadAdapter(connection_reference='fixture-connection', token_provider=lambda _: 'synthetic-token',
                                        transport=actual_http, wall_clock=lambda: clock[0])
            self.sync = IssueSync(self.project, adapter, clock=lambda: clock[0])
            self.assertEqual(self.sync.sync_once(store)['retry_not_before'], 1120)
            self.assertEqual(self.sync.sync_once(store)['retry_not_before'], 1120)
            self.assertEqual(len(actual_http.paths), 1)  # An immediate explicit continue still honors the server wait.
            clock[0] = 1121
            self.assertEqual(self.sync.sync_once(store)['retry_not_before'], 1241)
            self.assertEqual(len(actual_http.paths), 2)

    def test_invalid_json_and_oversized_real_http_responses_are_explanatory_pending(self):
        from issue_github import GitHubReadAdapter
        store = self.ready()
        for body, limit, reason in ((b'{malformed synthetic-token', 100, 'remote_invalid_json'),
                                    (b'x' * 101, 100, 'http_response_too_large')):
            def responder(request, **kwargs):
                return {'status': 200, 'headers': {}, 'body': body}
            with ControlledHTTP(responder) as actual_http:
                adapter = GitHubReadAdapter(connection_reference='fixture-connection', token_provider=lambda _: 'synthetic-token',
                                            transport=actual_http, max_bytes=limit)
                self.sync = IssueSync(self.project, adapter)
                self.assertEqual(self.sync.sync_once(store)['reason'], reason)
                self.assertNotIn('synthetic-token', json.dumps(self.sync.status()))

    def test_limits_are_positive_finite_and_unknown_ci_sources_are_not_ignored(self):
        from issue_github import GitHubReadAdapter
        for options in ({'round_timeout': 0}, {'request_timeout': float('nan')}, {'round_timeout': float('inf')},
                        {'max_pages': 0}, {'max_requests': True}, {'max_bytes': -1}):
            with self.assertRaises(ValueError):
                GitHubReadAdapter(connection_reference='fixture-connection', **options)
        store = self.ready(extra_ci={'type': 'third-party', 'check_name': 'looks green', 'app_id': 99})
        self.assertEqual(self.sync.sync_once(store)['reason'], 'required_ci_selector_unsupported')

    def test_real_http_transport_has_a_wall_deadline_and_refuses_redirects(self):
        from issue_github import BoundedHTTPTransport
        from issue_delivery import DeliveryPending
        captured = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # The local fixture never prints headers or tokens.

            def do_GET(self):
                captured.append((self.path, self.command))
                if self.path == '/redirect':
                    self.send_response(302)
                    self.send_header('Location', '/forbidden')
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()
                if self.path in {'/slow', '/stream'}:
                    for _ in range(400 if self.path == '/stream' else 40):
                        try:
                            self.wfile.write(b'x')
                            self.wfile.flush()
                        except OSError:
                            return
                        time.sleep(0.05)  # Trickle traffic defeats a per-read timeout without a wall deadline.
                else:
                    self.wfile.write(b'{"ok":true}')

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        transport = BoundedHTTPTransport()
        origin = 'http://127.0.0.1:' + str(server.server_port)
        self.assertEqual(transport(Request(origin + '/ok'), timeout=10, max_bytes=100)['body'], b'{"ok":true}')  # Use the production default for positive controls, including Windows worker startup.
        self.assertEqual(transport(Request(origin + '/redirect'), timeout=10, max_bytes=100)['status'], 302)
        self.assertNotIn(('/forbidden', 'GET'), captured)
        started = time.monotonic()
        with self.assertRaises(DeliveryPending) as raised:
            transport(Request(origin + '/slow'), timeout=0.4, max_bytes=100)
        self.assertEqual(raised.exception.reason, 'http_request_deadline')
        self.assertLess(time.monotonic() - started, 2)
        started = time.monotonic()
        with self.assertRaises(DeliveryPending) as streaming:
            transport(Request(origin + '/stream'), timeout=10, max_bytes=1000)
        self.assertEqual(streaming.exception.reason, 'http_request_deadline')
        self.assertIn(('/stream', 'GET'), captured)  # Prove the worker reached an actively trickling response, rather than expiring during startup.
        self.assertLess(time.monotonic() - started, 12)  # The production request budget also bounds the complete streaming operation.

    def test_delivery_and_professional_sources_are_rechecked_after_remote_reads(self):
        store = self.ready()
        self.github.after_jobs = lambda: self.github.pull.update(head_sha='d' * 40)
        self.assertFalse(self.sync.sync_once(store).get('delivery_eligible', False))
        self.github.pull['head_sha'] = 'a' * 40
        self.github.after_jobs = lambda: (self.project / 'candidate.py').write_bytes(b'changed during remote observation\n')
        result = self.sync.sync_once(store)
        self.assertFalse(result.get('delivery_eligible', False))
        self.assertEqual(self.github.writes, [])

    def test_accepted_contents_must_match_both_pr_head_and_actual_delivery(self):
        store = self.ready()
        self.assertTrue(self.sync.sync_once(store)['delivery']['content_verified'])  # Content qualification is independent of the later CI slice.
        for sha in ('a' * 40, 'b' * 40):
            for tree in ({'truncated': True, 'tree': []}, {'truncated': False, 'tree': []},
                         {'truncated': False, 'tree': [{'path': 'candidate.py', 'type': 'blob', 'mode': '120000', 'sha': 'candidate.py'}]}):
                with self.subTest(sha=sha, tree=tree):
                    self.github.tree_changes = {sha: tree}
                    self.assertEqual(self.sync.sync_once(store)['reason'], 'accepted_content_unverified')
        self.github.tree_changes = {}
        self.github.files['a' * 40]['candidate.py'] = b'conflict resolution changed accepted behavior\n'
        self.assertEqual(self.sync.sync_once(store)['reason'], 'accepted_content_unverified')
        self.assertEqual(self.github.writes, [])

    def test_directory_scope_detects_added_deleted_and_changed_delivered_files(self):
        store = self.ready(directory=True)
        self.assertTrue(self.sync.sync_once(store)['delivery_eligible'])
        for sha in ('a' * 40, 'b' * 40):
            original = copy.deepcopy(self.github.files[sha])
            for files in ({**original, 'src/unreviewed.py': b'new behavior\n'}, {}, {'src/candidate.py': b'changed merge resolution\n'}):
                self.github.files[sha] = files
                self.assertEqual(self.sync.sync_once(store)['reason'], 'accepted_content_unverified')
            self.github.files[sha] = original

    def test_merge_squash_and_rebase_use_the_api_reported_actual_result(self):
        store = self.ready()
        for strategy, actual in (('merge', 'b' * 40), ('squash', 'd' * 40), ('rebase', 'a' * 40)):
            with self.subTest(strategy=strategy):
                self.github.pull['merge_commit_sha'] = actual
                self.github.files[actual] = {'candidate.py': b'value = 1  # Synthetic accepted candidate.\n'}
                self.github.runs[0]['head_sha'] = actual
                self.github.jobs = [{**job, 'head_sha': actual} for job in self.github.jobs]
                self.assertEqual(self.sync.sync_once(store)['delivery']['sha'], actual)  # Strategy labels explain synthetic API outputs, not extra GitHub fields.

    def test_newest_related_run_and_attempt_supersede_old_green_results(self):
        store = self.ready()
        newer = {**self.github.runs[0], 'id': 202, 'run_number': 2, 'run_attempt': 2}
        self.github.runs.append(newer)
        self.github.jobs = [{**job, 'run_id': 202, 'run_attempt': 2} for job in self.github.jobs]
        self.assertTrue(self.sync.sync_once(store).get('delivery_eligible'))
        for status, conclusion in (('queued', None), ('in_progress', None), ('completed', 'failure')):
            newer.update(status=status, conclusion=conclusion)
            self.github.jobs[1].update(status=status, conclusion=conclusion)  # A newer pending/failed configured job blocks qualification independently of optional aggregate state.
            self.assertFalse(self.sync.sync_once(store).get('delivery_eligible', False))
        newer.update(status='completed', conclusion='success')
        self.github.jobs[1].update(status='completed', conclusion='success')  # Isolate the subsequent attempt-change race from a required-job pending/failure result.
        self.github.detail_reads = 0
        self.github.on_recheck = {'run_attempt': 3, 'status': 'queued', 'conclusion': None}
        self.assertFalse(self.sync.sync_once(store).get('delivery_eligible', False))
        self.assertEqual(self.github.writes, [])

    def test_all_workflows_remain_current_when_a_later_workflow_is_read(self):
        selector = {'type': 'actions', 'workflow_id': 302, 'workflow': '.github/workflows/release.yml', 'event': 'push', 'job': 'package'}
        store = self.ready(extra_ci=selector)
        self.github.workflows[302] = {'id': 302, 'path': '.github/workflows/release.yml'}
        self.github.runs.append({**self.github.runs[0], 'id': 203, 'workflow_id': 302, 'path': '.github/workflows/release.yml'})
        self.github.jobs.append({**self.github.jobs[0], 'id': 403, 'run_id': 203, 'name': 'package'})
        self.assertTrue(self.sync.sync_once(store).get('delivery_eligible'))
        def rerun_first(run_id):
            if run_id == 203:
                self.github.runs[0].update(run_attempt=2, status='queued', conclusion=None)
        self.github.on_jobs = rerun_first
        self.assertFalse(self.sync.sync_once(store).get('delivery_eligible', False))

    def test_all_required_jobs_must_succeed_in_the_same_exact_workflow_attempt(self):
        store = self.ready()
        self.assertTrue(self.sync.sync_once(store).get('delivery_eligible'))
        original = copy.deepcopy(self.github.jobs)
        for conclusion in ('failure', 'cancelled', 'neutral', 'skipped', None):
            with self.subTest(conclusion=conclusion):
                self.github.jobs = copy.deepcopy(original)
                self.github.jobs[1]['conclusion'] = conclusion
                self.assertFalse(self.sync.sync_once(store).get('delivery_eligible', False))
        for status in ('queued', 'in_progress'):
            self.github.jobs = [{**job, 'status': status} for job in original]
            self.assertFalse(self.sync.sync_once(store).get('delivery_eligible', False))
        for jobs in (original[:1], original + [copy.deepcopy(original[1])],
                     [{**j, 'head_sha': 'a' * 40} for j in original],
                     [{**j, 'run_id': 999} for j in original],
                     [{**j, 'run_attempt': 2} for j in original]):
            self.github.jobs = jobs
            self.assertFalse(self.sync.sync_once(store).get('delivery_eligible', False))
        self.github.jobs = original
        for change in ({'event': 'pull_request'}, {'head_branch': 'other'}, {'head_sha': 'a' * 40}, {'workflow_id': 999}):
            original_run = copy.deepcopy(self.github.runs[0])
            self.github.runs[0].update(change)
            self.assertFalse(self.sync.sync_once(store).get('delivery_eligible', False))
            self.github.runs[0] = original_run
        self.assertEqual(self.github.writes, [])

    def test_unconfigured_job_failure_does_not_veto_successful_required_matrix(self):
        store = self.ready()  # Real temporary RunStore; professional assertions remain explicitly synthetic.
        before = store.path.read_bytes()
        self.github.runs[0]['conclusion'] = 'failure'  # The workflow aggregate reflects an optional advisory failure.
        self.github.jobs.append({**self.github.jobs[0], 'id': 403, 'name': 'optional-advisory', 'conclusion': 'failure'})
        result = self.sync.sync_once(store)
        self.assertTrue(result.get('delivery_eligible'))  # Only the configured Windows/Ubuntu requirements decide successful checks.
        self.assertEqual({check['job'] for check in result['delivery']['ci']},
                         {'check (windows-latest)', 'check (ubuntu-latest)'})
        self.assertEqual(store.path.read_bytes(), before)  # Qualification cannot rewrite professional execution authority.
        self.assertEqual(self.github.writes, [])

    def test_unfinished_optional_job_does_not_add_a_workflow_completion_requirement(self):
        store = self.ready()  # The configured matrix has completed in this exact latest attempt.
        before = store.path.read_bytes()
        self.github.runs[0].update(status='in_progress', conclusion=None)
        required = copy.deepcopy(self.github.jobs)
        for status in ('queued', 'in_progress'):
            with self.subTest(optional_status=status):
                self.github.jobs = required + [{**required[0], 'id': 403, 'name': 'optional-advisory', 'status': status, 'conclusion': None}]
                self.assertTrue(self.sync.sync_once(store).get('delivery_eligible'))  # Optional work cannot silently become another configured completion obligation.
        for status in ('queued', 'in_progress'):
            self.github.jobs = copy.deepcopy(required)
            self.github.jobs[1].update(status=status, conclusion=None)  # A configured job still owes its own completion even when the aggregate is already in progress.
            self.assertFalse(self.sync.sync_once(store).get('delivery_eligible', False))
        self.assertEqual(store.path.read_bytes(), before)
        self.assertEqual(self.github.writes, [])


if __name__ == '__main__':
    unittest.main()
