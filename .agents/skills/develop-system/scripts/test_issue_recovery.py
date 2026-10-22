"""Interrupted synchronization through public interfaces; all remote facts are synthetic."""
import copy
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import unittest

from issue_sync import IssueSync
from issue_delivery import DeliveryPending
import test_issue_write as write_fixtures
from workflow_state import RunStore


class Interrupted(BaseException):
    """An execution boundary interruption must not become a transport rejection."""


class PersistentGitHub(write_fixtures.WritableGitHub):
    """Disk-backed synthetic remote boundary shared by two real Python processes."""
    def __init__(self, path, interrupt_kind=None, phase=None, *, hard_exit=False):
        super().__init__()
        self.path, self.interrupt_kind, self.phase = Path(path), interrupt_kind, phase
        self.hard_exit = hard_exit  # Actual process death must skip the product's finally blocks.
        self.trace, self.last_write = [], None
        if self.path.exists():
            value = json.loads(self.path.read_text(encoding='utf-8'))
            self.issue, self.writes, self.trace = value['issue'], value['writes'], value['trace']

    def publish(self):
        self.path.write_text(json.dumps({'issue': self.issue, 'writes': self.writes, 'trace': self.trace}), encoding='utf-8')  # Only external fixture facts, never execution authority.

    def stop(self):
        if self.hard_exit:
            os._exit(82)  # Terminate only this test-owned child, preserving the external fixture's applied facts.
        raise SystemExit(82)

    def get_issue(self, repository, number):
        self.trace.append('read')
        self.publish()
        if self.last_write == self.interrupt_kind and self.phase == 'before-receipt':
            self.stop()  # Remote read succeeded, local confirmation has not been published.
        return super().get_issue(repository, number)

    def perform(self, kind, action):
        if kind == self.interrupt_kind and self.phase == 'before':
            self.stop()  # Durable intent exists; request may not have left this process.
        action()
        self.trace.append(kind)
        self.last_write = kind
        self.publish()
        if kind == self.interrupt_kind and self.phase == 'hold':
            print('holding', flush=True)  # Parent observes a live public sync owner without polling its journal.
            if sys.stdin.readline().strip() != 'continue':
                raise SystemExit(84)
        if kind == self.interrupt_kind and self.phase == 'after':
            self.stop()  # Request effect is durable at the external boundary.
        return copy.deepcopy(self.issue)

    def update_issue_body(self, repository, number, body):
        return self.perform('body', lambda: super(PersistentGitHub, self).update_issue_body(repository, number, body))

    def remove_issue_label(self, repository, number, name):
        return self.perform('label', lambda: super(PersistentGitHub, self).remove_issue_label(repository, number, name))

    def close_issue(self, repository, number):
        return self.perform('close', lambda: super(PersistentGitHub, self).close_issue(repository, number))


class IssueRecoveryTests(unittest.TestCase):
    setUp = write_fixtures.IssueWriteTests.setUp
    create = write_fixtures.IssueWriteTests.create
    scope = write_fixtures.IssueWriteTests.scope
    finish = write_fixtures.IssueWriteTests.finish
    ready = write_fixtures.IssueWriteTests.ready

    def test_hard_exit_after_body_recovers_in_a_new_process_without_authority_changes(self):
        store = self.ready()  # Synthetic specialist conclusions pass through real temporary RunStore.
        state = store.read()
        store.complete(state['revision'], [{'id': '1', 'source_frame_id': state['frames'][0]['frame_id']}])
        original = {p.relative_to(self.project): p.read_bytes() for p in self.project.rglob('*')
                    if p.is_file() and 'issue-sync' not in p.parts}
        remote_path = self.project / 'synthetic-remote.json'
        PersistentGitHub(remote_path).publish()
        crashed = subprocess.run([sys.executable, '-B', __file__, '--hard-interrupt', str(self.project), str(remote_path), 'body', 'after'],
            capture_output=True, text=True, timeout=30)  # os._exit bypasses lock cleanup in this actual independent interpreter.
        self.assertEqual(crashed.returncode, 82, crashed.stderr)
        before = PersistentGitHub(remote_path)
        self.assertEqual([item[0] for item in before.writes], ['body'])
        source = self.project / 'candidate.py'
        accepted = source.read_bytes()
        source.write_bytes(b'changed = True  # Synthetic later unaccepted candidate.\n')
        denied = subprocess.run([sys.executable, '-B', __file__, '--continue', str(self.project), str(remote_path)],
            capture_output=True, text=True, timeout=30)  # Completed status and abandoned ownership cannot waive current accepted-source checks.
        self.assertEqual(denied.returncode, 0, denied.stderr)
        self.assertEqual(json.loads(denied.stdout)['reason'], 'acceptance_pending')
        self.assertEqual(PersistentGitHub(remote_path).writes, before.writes)
        source.write_bytes(accepted)
        continued = subprocess.run([sys.executable, '-B', __file__, '--continue', str(self.project), str(remote_path)],
            capture_output=True, text=True, timeout=30)  # A second fresh interpreter uses only public continuation and persisted facts.
        self.assertEqual(continued.returncode, 0, continued.stderr)
        result = json.loads(continued.stdout)
        self.assertEqual(result['status'], 'synced')
        remote = PersistentGitHub(remote_path)
        self.assertEqual(remote.trace[len(before.trace)], 'read')
        self.assertEqual([item[0] for item in remote.writes], ['body', 'label', 'close'])
        self.assertEqual(remote.issue['body'].count('<!-- develop-system:issue-sync:'), 1)
        self.assertTrue(all((self.project / name).read_bytes() == value for name, value in original.items()))

    def test_hard_exit_other_request_windows_preserve_unknown_or_missing_steps(self):
        for kind in ('body', 'label', 'close'):
            for phase in ('before', 'after', 'before-receipt'):
                if (kind, phase) == ('body', 'after'):
                    continue  # The completed-run/source-currentness test owns this already covered window.
                with self.subTest(kind=kind, phase=phase):
                    self.setUp()
                    store = self.ready()
                    original = {p.relative_to(self.project): p.read_bytes() for p in self.project.rglob('*')
                                if p.is_file() and 'issue-sync' not in p.parts}
                    remote_path = self.project / 'synthetic-remote.json'
                    PersistentGitHub(remote_path).publish()
                    child = subprocess.run([sys.executable, '-B', __file__, '--hard-interrupt', str(self.project), str(remote_path), kind, phase],
                        capture_output=True, text=True, timeout=30)
                    self.assertEqual(child.returncode, 82, child.stderr)
                    before = PersistentGitHub(remote_path)
                    for attempt in range(2):
                        previous = PersistentGitHub(remote_path)
                        child = subprocess.run([sys.executable, '-B', __file__, '--continue', str(self.project), str(remote_path)],
                            capture_output=True, text=True, timeout=30)  # Every recovery/replay uses another actual interpreter.
                        self.assertEqual(child.returncode, 0, child.stderr)
                        result = json.loads(child.stdout)
                        remote = PersistentGitHub(remote_path)
                        self.assertEqual(remote.trace[len(previous.trace)], 'read')
                        if phase == 'before' or kind == 'close':
                            self.assertEqual(result['status'], 'pending')
                            self.assertIn(result['reason'], ('operation_outcome_unknown', 'close_outcome_unknown'))
                            self.assertEqual(remote.writes, before.writes)  # Hard death supplies no permission to resend unknown requests.
                        else:
                            self.assertEqual(result['status'], 'synced')
                            self.assertEqual([item[0] for item in remote.writes], ['body', 'label', 'close'])
                        self.assertLessEqual(remote.issue['body'].count('<!-- develop-system:issue-sync:'), 1)
                    self.assertTrue(all((self.project / name).read_bytes() == value for name, value in original.items()))

    def test_live_process_owner_is_not_reclaimed_by_a_concurrent_public_continuation(self):
        store = self.ready()
        original = store.path.read_bytes()
        remote_path = self.project / 'synthetic-remote.json'
        PersistentGitHub(remote_path).publish()
        child = subprocess.Popen([sys.executable, '-B', __file__, '--hold', str(self.project), str(remote_path), 'body', 'hold'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            ready = queue.Queue()
            threading.Thread(target=lambda: ready.put(child.stdout.readline()), daemon=True).start()
            self.assertEqual(ready.get(timeout=20), 'holding\n')  # Bounded readiness receipt, no guessed sleep or process enumeration.
            self.assertIsNone(child.poll())
            lock = self.project / '.develop-system/issue-sync/.sync.lock'
            owner = lock.read_bytes()
            remote = PersistentGitHub(remote_path)
            before = (copy.deepcopy(remote.writes), copy.deepcopy(remote.trace))
            result = IssueSync(self.project, remote).continue_sync(store)
            self.assertEqual((result['status'], result['reason']), ('pending', 'local_lock_unavailable'))
            self.assertEqual(lock.read_bytes(), owner)
            self.assertEqual((remote.writes, remote.trace), before)
            self.assertTrue(Path(result['pending_record']).is_file())
            output, errors = child.communicate('continue\n', timeout=30)
            self.assertEqual(child.returncode, 0, errors)
            self.assertEqual([item[0] for item in PersistentGitHub(remote_path).writes], ['body', 'label', 'close'])
            self.assertEqual(store.path.read_bytes(), original)
        finally:
            if child.poll() is None:
                child.kill()  # Only a failed test's own blocked fixture is stopped; no foreign process is touched.
            child.communicate(timeout=10)

    def test_confirmed_effect_reversal_before_close_is_not_repeated(self):
        for field in ('label', 'body'):
            with self.subTest(field=field):
                self.setUp()
                store = self.ready()
                original_run, original_body = store.path.read_bytes(), self.github.issue['body']
                reader = self.github.get_pull_request
                def interrupt_before_close(*args):
                    if len(self.github.writes) >= 2:
                        raise Interrupted()  # Interrupt public final delivery qualification after both confirmations, before close intent.
                    return reader(*args)
                self.github.get_pull_request = interrupt_before_close
                with self.assertRaises(Interrupted):
                    self.sync.sync_once(store)
                self.github.get_pull_request = reader
                operations = copy.deepcopy(self.sync.status(store)['bindings'][0]['progress']['operations'])
                self.assertEqual([(op['kind'], op['status']) for op in operations], [('body', 'confirmed'), ('label', 'confirmed')])
                if field == 'label':
                    self.github.issue['labels'].append('ready-for-agent')  # Synthetic human restoration after a positively confirmed removal.
                else:
                    self.github.issue['body'] = original_body  # Restoring the binding baseline is still an observable reversal of confirmed body work.
                external = copy.deepcopy(self.github.issue)
                writes = copy.deepcopy(self.github.writes)
                for attempt in range(2):
                    result = IssueSync(self.project, self.github).continue_sync(store)
                    self.assertEqual(result['status'], 'pending')
                    self.assertEqual(result['operations'], operations)  # Historical confirmation remains a fact, not a fresh retry permission.
                    self.assertEqual(self.github.issue, external)
                    self.assertEqual(self.github.writes, writes)
                    self.assertEqual(store.path.read_bytes(), original_run)

    def test_new_instance_confirms_applied_body_and_only_sends_missing_steps(self):
        store = self.ready()  # Real temporary authority receives synthetic specialist results.
        def interrupt(kind):
            if kind == 'body':
                raise Interrupted()  # Remote applied the request, before local confirmation.
        self.github.after_write = interrupt
        with self.assertRaises(Interrupted):
            self.sync.sync_once(store)
        before = copy.deepcopy(self.github.writes)
        self.github.after_write = None
        restarted = IssueSync(self.project, self.github)
        result = restarted.sync_once(store)  # A new service instance reads durable intent.
        self.assertEqual(result['status'], 'synced')
        self.assertEqual([kind for kind, _ in self.github.writes[len(before):]], ['label', 'close'])
        self.assertEqual(self.github.issue['body'].count('<!-- develop-system:issue-sync:'), 1)
        self.assertEqual([op['status'] for op in result['operations']], ['confirmed'] * 3)

    def test_completed_run_has_explicit_read_only_continuation(self):
        store = self.ready()
        state = store.read()
        store.complete(state['revision'], [{'id': '1', 'source_frame_id': state['frames'][0]['frame_id']}])
        original = {str(p.relative_to(self.project)): p.read_bytes() for p in self.project.rglob('*')
                    if p.is_file() and 'issue-sync' not in p.parts}  # Includes original state/events/evidence/authorization files.
        result = IssueSync(self.project, self.github).continue_sync(store, ticket_id='77:501')
        self.assertEqual(result['status'], 'synced')
        self.assertEqual(store.read()['status'], 'completed')
        for name, contents in original.items():
            self.assertEqual((self.project / name).read_bytes(), contents)

    def test_lock_conflict_is_durable_local_pending_without_unlock_or_remote_requests(self):
        for owner in (b'another-process', b'0' * 32, b'', b'develop-system:issue-sync-lock:v2:' + b'0' * 32,
                      b'develop-system:issue-sync-lock:v1:partial'):
            with self.subTest(owner=owner):
                self.setUp()
                store = self.ready()
                lock = self.project / '.develop-system/issue-sync/.sync.lock'
                lock.write_bytes(owner)  # Unknown, legacy and incomplete owner formats carry no safe recovery authority.
                reader = self.github.get_pull_request
                requests = []
                def observe(*args):
                    requests.append('read')
                    return reader(*args)
                self.github.get_pull_request = observe
                result = self.sync.sync_once(store)
                self.assertEqual((result['status'], result['reason']), ('pending', 'local_lock_unavailable'))
                self.assertEqual(lock.read_bytes(), owner)
                self.assertEqual(json.loads(Path(result['pending_record']).read_text(encoding='utf-8'))['reason'], 'local_lock_unavailable')
                self.assertEqual(self.github.writes, [])
                self.assertEqual(requests, [])

    def test_separate_process_before_after_and_before_receipt_each_request(self):
        for kind in ('body', 'label', 'close'):
            for phase in ('before', 'after', 'before-receipt'):
                with self.subTest(kind=kind, phase=phase):
                    self.setUp()  # Each injection owns a separate temporary project and actual RunStore.
                    store = self.ready()
                    remote_path = self.project / 'synthetic-remote.json'
                    remote = PersistentGitHub(remote_path)
                    remote.publish()
                    process = subprocess.run([sys.executable, '-B', __file__, '--interrupt', str(self.project), str(remote_path), kind, phase],
                        capture_output=True, text=True, timeout=30)  # A fresh interpreter consumes original RunStore and persistent intent.
                    self.assertEqual(process.returncode, 82, process.stderr)
                    remote = PersistentGitHub(remote_path)
                    previous_writes, previous_trace = len(remote.writes), len(remote.trace)
                    result = IssueSync(self.project, remote).continue_sync(store)
                    self.assertEqual(remote.trace[previous_trace], 'read')  # Recovery observes remote fields before any mutation.
                    if phase == 'before' or kind == 'close':
                        self.assertEqual(result['status'], 'pending')
                        self.assertIn(result['reason'], ('operation_outcome_unknown', 'close_outcome_unknown'))
                        self.assertEqual(len(remote.writes), previous_writes)  # Ambiguous absence/closure is never blindly retried.
                    else:
                        self.assertEqual(result['status'], 'synced')
                        self.assertEqual([item[0] for item in remote.writes], ['body', 'label', 'close'])
                    self.assertLessEqual(remote.issue['body'].count('<!-- develop-system:issue-sync:'), 1)
                    again = IssueSync(self.project, remote).continue_sync(store)
                    self.assertEqual((again['status'], again['reason']), (result['status'], result['reason']))

    def test_timeout_after_effect_recovers_body_and_label_but_not_close_attribution(self):
        for kind in ('body', 'label', 'close'):
            with self.subTest(kind=kind):
                self.setUp()
                store = self.ready()
                def timeout(actual):
                    if actual == kind:
                        raise DeliveryPending('http_request_deadline')  # A timeout never means the request did not apply.
                self.github.after_write = timeout
                pending = self.sync.sync_once(store)
                self.assertEqual(pending['operations'][-1]['status'], 'unknown')
                self.github.after_write = None
                previous = len(self.github.writes)
                result = IssueSync(self.project, self.github).continue_sync(store)
                self.assertEqual(result['status'], 'pending' if kind == 'close' else 'synced')
                if kind == 'close':
                    self.assertEqual(result['reason'], 'close_outcome_unknown')
                    self.assertEqual(len(self.github.writes), previous)

    def test_timeout_unsent_or_manually_reverted_keeps_unknown_and_never_overwrites(self):
        for reverted in (False, True):
            with self.subTest(reverted=reverted):
                self.setUp()
                store = self.ready()
                original = self.github.issue['body']
                def timeout(*args):
                    raise DeliveryPending('http_request_deadline')
                if reverted:
                    self.github.after_write = timeout
                else:
                    self.github.before_write = timeout
                self.sync.sync_once(store)
                self.github.before_write = self.github.after_write = None
                self.github.issue['body'] = original  # Same visible state means unsent or applied then manually restored.
                previous = len(self.github.writes)
                result = IssueSync(self.project, self.github).continue_sync(store)
                self.assertEqual((result['status'], result['reason']), ('pending', 'operation_outcome_unknown'))
                self.assertEqual(len(self.github.writes), previous)
                self.assertEqual(self.github.issue['body'], original)

    def test_journal_revision_conflict_preserves_external_revision_and_pending(self):
        store = self.ready()
        journal = self.project / '.develop-system/issue-sync/bindings.json'
        changed = []
        def concurrent_publication(kind):
            if kind == 'body':
                value = json.loads(journal.read_text(encoding='utf-8'))
                value['revision'] += 1
                journal.write_text(json.dumps(value), encoding='utf-8')  # External persistence conflict input, never RunStore state.
                changed.append(journal.read_bytes())
        self.github.after_write = concurrent_publication
        result = self.sync.sync_once(store)
        self.assertEqual((result['status'], result['reason']), ('pending', 'local_revision_conflict'))
        self.assertEqual(journal.read_bytes(), changed[0])
        self.assertEqual([kind for kind, _ in self.github.writes], ['body'])
        self.assertTrue(Path(result['pending_record']).is_file())

    def test_unknown_persistence_versions_are_pending_without_rewriting_original(self):
        store = self.ready()
        journal = self.project / '.develop-system/issue-sync/bindings.json'
        journal.write_text('{"schema_version":99}', encoding='utf-8')  # Unknown disk format remains untouched.
        original = journal.read_bytes()
        result = self.sync.sync_once(store)
        self.assertEqual((result['status'], result['reason']), ('pending', 'local_record_invalid'))
        self.assertEqual(journal.read_bytes(), original)
        self.assertEqual(self.github.writes, [])

    def test_explicit_continue_can_retry_definitive_rate_rejection_only_after_wait(self):
        store = self.ready()
        requests = []
        def limited(kind, value):
            requests.append(kind)
            raise DeliveryPending('rate_limited', retry_not_before=100)
        self.github.before_write = limited
        self.sync.clock = lambda: 50
        first = self.sync.sync_once(store)
        self.assertEqual(first['operations'][-1]['status'], 'failed')
        self.assertEqual(self.sync.continue_sync(store)['reason'], 'rate_limited')
        self.assertEqual(requests, ['body'])
        self.github.before_write = None
        self.sync.clock = lambda: 200
        result = self.sync.continue_sync(store)  # Explicit continuation, never an internal retry loop.
        self.assertEqual(result['status'], 'synced')
        self.assertEqual([kind for kind, _ in self.github.writes], ['body', 'label', 'close'])

    def test_lost_close_response_records_effect_separately_from_source(self):
        store = self.ready()
        def lost(kind):
            if kind == 'close':
                raise DeliveryPending('http_request_deadline')
        self.github.after_write = lost
        self.sync.sync_once(store)
        self.github.after_write = None
        result = IssueSync(self.project, self.github).continue_sync(store)
        self.assertEqual(result['reason'], 'close_outcome_unknown')
        self.assertEqual(result['operations'][-1].get('observed_after'), {'state': 'closed', 'state_reason': 'completed'})
        self.assertEqual(result['operations'][-1]['status'], 'unknown')  # Observed effect is not proof of request attribution.

    def test_success_before_receipt_filesystem_failure_preserves_intent_and_remote_effect(self):
        for kind in ('body', 'label', 'close'):
            with self.subTest(kind=kind):
                self.setUp()
                store = self.ready()
                original_run = store.path.read_bytes()
                journal = self.project / '.develop-system/issue-sync/bindings.json'
                held = journal.with_name('held-intent.json')
                wrote = []
                def after(actual):
                    if actual == kind:
                        wrote.append(actual)
                def obstruct(read_number):
                    if wrote and not held.exists():
                        journal.rename(held)
                        journal.mkdir()  # Real filesystem publication failure after the matching remote read.
                self.github.after_write, self.github.on_read = after, obstruct
                pending = self.sync.sync_once(store)
                self.assertEqual(pending['reason'], 'local_persistence_unavailable')
                self.github.after_write = self.github.on_read = None
                journal.rmdir()
                held.rename(journal)  # Restore only the fixture's original durable intent, never synthesize a receipt.
                previous = len(self.github.writes)
                result = IssueSync(self.project, self.github).continue_sync(store)
                self.assertEqual(result['status'], 'pending' if kind == 'close' else 'synced')
                if kind == 'close':
                    self.assertEqual(len(self.github.writes), previous)
                else:
                    self.assertEqual([name for name, _ in self.github.writes], ['body', 'label', 'close'])
                self.assertEqual(store.path.read_bytes(), original_run)

    def test_partial_intent_survives_missing_pages_network_and_permissions(self):
        for reason in ('remote_pagination_incomplete', 'http_transport_error', 'permission_denied'):
            with self.subTest(reason=reason):
                self.setUp()
                store = self.ready()
                def interrupt(kind):
                    if kind == 'label':
                        raise Interrupted()
                self.github.after_write = interrupt
                with self.assertRaises(Interrupted):
                    self.sync.sync_once(store)
                self.github.after_write = None
                previous = copy.deepcopy(self.sync.status(store)['bindings'][0]['progress']['operations'])
                def fail(read_number):
                    raise DeliveryPending(reason)  # External read failures must not erase already confirmed body progress.
                self.github.on_read = fail
                pending = IssueSync(self.project, self.github).continue_sync(store)
                self.assertEqual(pending['reason'], reason)
                self.assertEqual(pending['operations'], previous)
                self.github.on_read = None
                self.assertEqual(IssueSync(self.project, self.github).continue_sync(store)['status'], 'synced')
                self.assertEqual([kind for kind, _ in self.github.writes], ['body', 'label', 'close'])

    def test_completed_continuation_rechecks_current_source_and_disabled_generation(self):
        store = self.ready()
        state = store.read()
        store.complete(state['revision'], [{'id': '1', 'source_frame_id': state['frames'][0]['frame_id']}])
        original = store.path.read_bytes()
        source = self.project / 'candidate.py'
        contents = source.read_bytes()
        source.write_bytes(b'changed = True\n')  # Completed status cannot waive current professional evidence.
        self.assertEqual(self.sync.continue_sync(store)['reason'], 'acceptance_pending')
        source.write_bytes(contents)
        self.sync.configure({**self.config, 'enabled': False}, user_reference='synthetic explicit disable')
        self.assertEqual(self.sync.continue_sync(store)['status'], 'unmanaged')
        self.assertEqual(store.path.read_bytes(), original)
        self.assertEqual(self.github.writes, [])

    def test_applied_http_mutation_with_invalid_response_is_unknown_then_reconciled(self):
        for kind in ('body', 'label', 'close'):
            with self.subTest(kind=kind):
                self.setUp()
                store = self.ready()
                with write_fixtures.MutationHTTP(self.github, malformed_kind=kind) as boundary:
                    sync = IssueSync(self.project, boundary)
                    pending = sync.sync_once(store)  # Actual HTTP/urllib worker receives malformed JSON after an applied write.
                    self.assertEqual(pending['reason'], 'remote_invalid_json')
                    self.assertEqual(pending['operations'][-1]['status'], 'unknown')
                    boundary.malformed_kind = None
                    result = IssueSync(self.project, boundary).continue_sync(store)
                    self.assertEqual(result['status'], 'pending' if kind == 'close' else 'synced')
                    self.assertEqual([name for name, _ in self.github.writes], ['body', 'label', 'close'])


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] in {'--interrupt', '--hard-interrupt', '--hold'}:
        project, remote, kind, phase = sys.argv[2:]
        IssueSync(project, PersistentGitHub(remote, kind, phase, hard_exit=sys.argv[1] == '--hard-interrupt')).sync_once(RunStore(project, 'new-run'))
        if sys.argv[1] == '--hold':
            raise SystemExit(0)  # The owned active-lock fixture finishes normally after the parent releases it.
        raise SystemExit(83)  # Failure to reach the intended interruption is a test failure.
    if len(sys.argv) > 1 and sys.argv[1] == '--continue':
        project, remote = sys.argv[2:]
        print(json.dumps(IssueSync(project, PersistentGitHub(remote)).continue_sync(RunStore(project, 'new-run'))))
        raise SystemExit(0)
    unittest.main()  # Public script entry supplies managed source-loading evidence.
