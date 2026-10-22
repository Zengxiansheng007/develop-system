"""Project-owned Issue synchronization; execution authority remains in RunStore."""
from contextlib import contextmanager
from contextlib import nullcontext
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
import uuid
from issue_delivery import DeliveryPending, qualify_delivery  # Remote qualification remains outside execution authority.


class LocalJournalConflict(Exception):
    """A conflicting local publisher must not be rewritten as remote failure."""


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()  # Stable references contain no credentials.


def validate_config(value):
    fields = {'schema_version', 'enabled', 'project_id', 'github_host', 'repository', 'target_branch',
              'required_ci', 'remove_labels', 'connection_reference', 'body_policy'}  # Reject unreviewed or credential-bearing fields.
    if not isinstance(value, dict) or set(value) != fields or type(value.get('schema_version')) is not int or value['schema_version'] != 1:
        raise ValueError('Unsupported Issue sync configuration')
    if type(value['enabled']) is not bool or value['github_host'] != 'github.com' or value['body_policy'] != 'single-writer':
        raise ValueError('Explicit GitHub and single-writer configuration required')
    for name in ('project_id', 'connection_reference'):
        if not isinstance(value[name], str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}', value[name]):
            raise ValueError('Named non-secret configuration reference required')
    repository = value['repository']
    if (not isinstance(repository, dict) or set(repository) != {'id', 'full_name'} or type(repository['id']) is not int
            or repository['id'] <= 0 or not isinstance(repository['full_name'], str)
            or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository['full_name'])):
        raise ValueError('Explicit stable repository identity required')
    if not isinstance(value['target_branch'], str) or not value['target_branch'].strip() or any(c.isspace() for c in value['target_branch']):
        raise ValueError('Explicit target branch required')
    if (not isinstance(value['required_ci'], list) or not value['required_ci']
            or any(not isinstance(c, dict) or not c or any(not isinstance(k, str) or not isinstance(v, (str, int))
                or isinstance(v, bool) or not str(v).strip() for k, v in c.items()) for c in value['required_ci'])):
        raise ValueError('Nonempty required CI identities required')
    ci_fields = {'workflow', 'workflow_id', 'job', 'event', 'app_id', 'context', 'source', 'name', 'matrix', 'check_name', 'type', 'branch'}
    if any(not set(c) <= ci_fields for c in value['required_ci']):
        raise ValueError('Only named CI selector fields are supported')  # Authentication values never belong in selectors.
    if (not isinstance(value['remove_labels'], list) or any(not isinstance(label, str) or not label.strip() for label in value['remove_labels'])
            or len(set(value['remove_labels'])) != len(value['remove_labels'])):
        raise ValueError('Unique label names required')
    return copy.deepcopy(value)


def configuration(project):
    path = Path(project).resolve() / '.develop-system/issue-sync/config.json'
    if not path.exists():
        return None  # Absence is disabled and never initializes historical state.
    record = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(record, dict) or type(record.get('schema_version')) is not int or record['schema_version'] != 1  # Reject non-object records before accessing version fields.
            or record.get('project_key') != fingerprint(str(Path(project).resolve()))
            or not isinstance(record.get('generation'), str) or not record['generation']
            or not isinstance(record.get('enablement_reference'), str) or not record['enablement_reference'].strip()):
        raise ValueError('Unknown or foreign Issue sync configuration record')
    value = validate_config(record['configuration'])
    if record.get('configuration_id') != fingerprint(value):
        raise ValueError('Issue sync configuration changed without explicit adoption')
    return record


def capture_enrollment(project):
    record = configuration(project)
    if record is None or not record['configuration']['enabled']:
        return None  # No timestamp comparison or historical-run backfill is supported.
    return {key: record[key] for key in ('schema_version', 'project_key', 'generation', 'configuration_id', 'enablement_reference')}


class IssueSync:
    """Small public seam for configuration, binding and one bounded synchronization."""
    def __init__(self, project_root, github, *, clock=time.time):
        self.project = Path(project_root).resolve(strict=True)
        self.root = self.project / '.develop-system/issue-sync'
        self.github = github  # Only the external API boundary is replaceable in behavior tests.
        self.clock = clock  # A wall clock preserves service wait boundaries across explicit continuations.

    @contextmanager
    def _locked(self):
        self.root.mkdir(parents=True, exist_ok=True)
        lock = self.root / '.sync.lock'
        owner = uuid.uuid4().hex.encode('ascii')
        with lock.open('xb') as stream:
            stream.write(owner)  # Existing locks fail closed; no forced unlock.
        try:
            yield
        finally:
            if lock.exists() and lock.read_bytes() == owner:
                lock.unlink()  # Never remove another writer's lock.

    def _save(self, path, value):
        temporary = path.with_name('.' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('x', encoding='utf-8', newline='\n') as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())  # Durable publication precedes later dependent actions.
            if path == self.root / 'bindings.json' and self._journal()['revision'] != value['revision'] - 1:
                raise LocalJournalConflict()  # Cooperative local locking plus revision validation; never overwrite a newer publisher.
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def configure(self, value, *, user_reference):
        value = validate_config(value)
        if not isinstance(user_reference, str) or not user_reference.strip():
            raise ValueError('Explicit project configuration authorization reference required')
        with self._locked():
            previous = configuration(self.project)
            if previous and previous['configuration'] == value:
                return copy.deepcopy(previous)  # Repeating the same adoption does not change its generation.
            record = {'schema_version': 1, 'project_key': fingerprint(str(self.project)), 'generation': uuid.uuid4().hex,
                      'configuration_id': fingerprint(value), 'configuration': value,
                      'enablement_reference': user_reference}
            self._save(self.root / 'config.json', record)
            return copy.deepcopy(record)

    def _journal(self):
        path = self.root / 'bindings.json'
        if not path.exists():
            return {'schema_version': 1, 'project_key': fingerprint(str(self.project)), 'revision': 0, 'bindings': []}
        journal = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(journal, dict) or type(journal.get('schema_version')) is not int or journal['schema_version'] != 1  # The journal envelope follows the same typed version boundary.
                or journal.get('project_key') != fingerprint(str(self.project)) or type(journal.get('revision')) is not int
                or not isinstance(journal.get('bindings'), list)):
            raise ValueError('Unknown or foreign Issue sync journal')
        if any(not isinstance(binding, dict) or type(binding.get('schema_version')) is not int
                or binding['schema_version'] != 1 for binding in journal['bindings']):
            raise ValueError('Unknown Issue sync binding version')  # Validate every nested record before any consumer exposes, selects or rewrites it.
        return journal  # This file contains remote progress and references, never professional verdicts.

    def _state(self, store):
        from workflow_state import RunStore  # The public entry consumes the actual execution authority.
        if not isinstance(store, RunStore) or store.project != self.project:
            raise ValueError('RunStore belongs to another project')
        return store.read()

    def _enrolled(self, state):
        current = capture_enrollment(self.project)
        return current is not None and state.get('issue_sync_enrollment') == current

    def _identity(self, value, number, repository, kind):
        if (not isinstance(value, dict) or type(value.get('id')) is not int or value['id'] <= 0
                or type(value.get('number')) is not int or value['number'] != number
                or type(value.get('repository_id')) is not int or value['repository_id'] != repository['id']
                or not isinstance(value.get('repository_full_name'), str)
                or value['repository_full_name'].casefold() != repository['full_name'].casefold()
                or value.get('type') != kind):
            raise ValueError('GitHub object type or stable repository identity does not match')
        return {k: value[k] for k in ('id', 'number', 'repository_id', 'repository_full_name', 'type')}

    def _acceptance_source(self, state, scope, body):
        if not isinstance(body, str):
            raise ValueError('Original complete Issue acceptance source is unavailable')
        headings = list(re.finditer(r'(?mi)^##[ \t]+Acceptance criteria[ \t]*\r?$', body))
        if len(headings) != 1:
            raise ValueError('Original complete Issue acceptance source is unavailable')
        start = headings[0].end()
        following = re.search(r'(?m)^#{1,2}[ \t]+', body[start:])
        section = body[start:start + following.start()] if following else body[start:]
        actual = [m.group(1).strip() for m in re.finditer(r'(?m)^[ \t]*[-*] \[[ xX]\] ([^\r\n]+)\r?$', section)]
        declared = [{'id': c['id'], 'text': c['criterion'].strip()} for c in state['acceptance'] if c['id'] in scope['acceptance_ids']]
        expected = [c['text'] for c in declared]
        if not actual or len(set(actual)) != len(actual) or len(expected) != len(actual) or set(expected) != set(actual):
            raise ValueError('Original complete Issue acceptance set does not match its source')
        return {'format': 'implementation-checklist-v1', 'items': declared, 'source_id': fingerprint(declared)}  # Source completeness is independent of writeback mapping.

    def _mapping(self, value, scope, body, source):
        if not isinstance(value, dict) or not set(value) <= set(scope['acceptance_ids']):
            raise ValueError('Writeback mapping must belong to the original complete ticket acceptance set')
        expected = {c['id']: c['text'] for c in source['items']}
        normalized = {}
        used = set()
        for criterion_id, item in value.items():
            if (not isinstance(item, dict) or set(item) != {'text', 'context'} or not isinstance(item['text'], str)
                    or not item['text'].strip() or '\n' in item['text'] or not isinstance(item['context'], str)
                    or item['text'].strip() != expected[criterion_id]):
                raise ValueError('Exact acceptance text and context required')
            matches = list(re.finditer(r'(?m)^[ \t]*[-*] \[[ xX]\] ' + re.escape(item['text']) + r'\r?$', body))
            if len(matches) != 1 or matches[0].start() in used:
                raise ValueError('Acceptance checkbox mapping is absent or ambiguous')
            if item['context'] and (body.count(item['context']) != 1 or body.index(item['context']) > matches[0].start()):
                raise ValueError('Acceptance checkbox context does not match')
            used.add(matches[0].start())
            normalized[criterion_id] = {**item, 'line_fingerprint': fingerprint(matches[0].group())}
        return normalized  # Persist stable text/context, never a stale line-number-only selector.

    def bind(self, store, *, issue_number, final_pr, acceptance_map):
        state = self._state(store)
        if not self._enrolled(state):
            raise ValueError('Run was not created in the current explicitly enabled generation')
        if type(issue_number) is not int or issue_number <= 0:
            raise ValueError('One explicit Issue number required')
        config = configuration(self.project)['configuration']
        repository = config['repository']
        if (not isinstance(final_pr, dict) or set(final_pr) != {'number', 'repository_id'}
                or type(final_pr['number']) is not int or final_pr['number'] <= 0
                or type(final_pr['repository_id']) is not int or final_pr['repository_id'] != repository['id']):
            raise ValueError('Exactly one final PR in the configured repository is supported')
        with self.github.round(config['connection_reference']) if hasattr(self.github, 'round') else nullcontext():
            issue = self.github.get_issue(repository, issue_number)
            pull = self.github.get_pull_request(repository, final_pr['number'])  # Binding reads share the same finite request budget.
        issue_identity = self._identity(issue, issue_number, repository, 'issue')
        pull_identity = self._identity(pull, final_pr['number'], repository, 'pull_request')
        ticket_id = str(repository['id']) + ':' + str(issue_identity['id'])
        scope = next((s for s in state.get('ticket_scopes', []) if s['ticket_id'] == ticket_id), None)
        if scope is None:
            raise ValueError('Original complete ticket scope is unavailable')
        source = self._acceptance_source(state, scope, issue.get('body') or '')
        mapping = self._mapping(acceptance_map, scope, issue.get('body') or '', source)
        projection = store.acceptance_projection(ticket_id)
        fixed = {'run_id': store.run_id, 'enrollment': state['issue_sync_enrollment'], 'scope': scope,
                 'issue': issue_identity, 'final_pr': pull_identity, 'mapping': mapping, 'acceptance_source': source,
                 'body_baseline_id': fingerprint(issue.get('body') or '')}
        with self._locked():
            latest = self._state(store)
            if not self._enrolled(latest) or latest['revision'] != state['revision']:
                raise ValueError('Run or configuration changed while binding')
            journal = self._journal()
            existing = next((b for b in journal['bindings'] if b['ticket_id'] == ticket_id), None)
            if existing and any(existing[k] != v for k, v in fixed.items()):
                raise ValueError('Issue already has a conflicting run, PR, scope or body binding')
            candidate_id = projection.get('candidate_id') if projection['eligible'] else None
            if existing and existing.get('candidate_id') == candidate_id:
                return copy.deepcopy(existing)
            binding = {**fixed, 'schema_version': 1, 'binding_id': existing['binding_id'] if existing else uuid.uuid4().hex,
                       'ticket_id': ticket_id, 'candidate_id': candidate_id,
                       'source_references': projection.get('sources', []),
                       'progress': {'status': 'pending', 'reason': 'final_delivery_unverified' if projection['eligible'] else 'acceptance_pending'}}
            if existing:
                journal['bindings'].remove(existing)
            journal['bindings'].append(binding)
            journal['revision'] += 1
            self._save(self.root / 'bindings.json', journal)
            return copy.deepcopy(binding)

    def status(self, store=None):
        record = configuration(self.project)
        bindings = self._journal()['bindings']
        if store is not None:
            self._state(store)
            bindings = [b for b in bindings if b['run_id'] == store.run_id]
        return {'schema_version': 1, 'enabled': bool(record and record['configuration']['enabled']),
                'generation': record['generation'] if record else None, 'bindings': copy.deepcopy(bindings)}

    def _body(self, binding, state, body, delivery):
        mapping = {key: {k: item[k] for k in ('text', 'context')} for key, item in binding['mapping'].items()}
        self._mapping(mapping, binding['scope'], body, self._acceptance_source(state, binding['scope'], body))  # Revalidate unique text/context against current complete ACs.
        for item in mapping.values():
            body = re.sub(r'(?m)^([ \t]*[-*] \[)[ xX](\] ' + re.escape(item['text']) + r'\r?)$', r'\g<1>x\2', body)
        repository = binding['issue']['repository_full_name']
        evidence_hashes = set()
        for source in binding['source_references']:
            event = next(e for e in reversed(state['events']) if e['frame_id'] == source['source_frame_id'])
            check = next(c for c in event['acceptance_checks'] if c['id'] == source['id'])
            evidence_hashes.update(e['sha256'] for e in check['evidence'])  # Export accepted evidence fingerprints, never private paths or file contents.
        start = '<!-- develop-system:issue-sync:' + binding['binding_id'] + ' -->'
        end = '<!-- /develop-system:issue-sync:' + binding['binding_id'] + ' -->'
        summary = [start, '### Verified delivery', 'Accepted criteria: ' + ', '.join(binding['scope']['acceptance_ids']),
                   'Acceptance provenance SHA256: `' + binding['candidate_id'] + '`',
                   'Accepted evidence SHA256: ' + ', '.join('`' + value + '`' for value in sorted(evidence_hashes)),
                   'Final PR: https://github.com/' + repository + '/pull/' + str(delivery['pr_number']),
                   'Delivered SHA: `' + delivery['sha'] + '`']
        summary += ['Required CI: ' + ci['job'] + ' — success; ' + ci['url'] for ci in delivery['ci']]
        summary += ['Synchronization: allowed body verified; closure requires final requalification.', end]
        section = '\n'.join(summary)
        if start in body or end in body:
            if body.count(start) != 1 or body.count(end) != 1 or body.index(start) >= body.index(end):
                raise ValueError('Ambiguous managed summary')
            return body[:body.index(start)] + section + body[body.index(end) + len(end):]
        return body + '\n\n' + section  # No comments or unrelated body fields are generated.

    def _publish_progress(self, journal, binding, progress):
        binding['progress'] = copy.deepcopy(progress)
        journal['revision'] += 1
        self._save(self.root / 'bindings.json', journal)  # Durable progress is independent of execution authority.

    def _qualification_matches(self, state, projection, binding):
        return (self._enrolled(state) and state['status'] in {'running', 'completed'} and projection['eligible']
                and projection.get('candidate_id') == binding['candidate_id']
                and projection.get('sources') == binding['source_references'])  # Callers supply fresh reads at each distinct stage.

    def _reconcile_operations(self, journal, binding, progress, issue, *, explicit_continue=False):
        for op in progress['operations']:
            if op['status'] == 'confirmed':
                continue  # Already confirmed effects still face the ordinary full remote readback.
            if op['status'] == 'failed':
                if op.get('retry_authorized'):
                    continue  # The original definitive rejection remains immutable history.
                previous = op['expected_before']
                unchanged = (issue.get('body') == previous if op['kind'] == 'body' else
                    isinstance(issue.get('labels'), list) and previous['name'] in issue['labels'] if op['kind'] == 'label' else
                    issue.get('state') == 'open')
                if not explicit_continue or not unchanged:
                    raise DeliveryPending('previous_request_failed')
                op['retry_authorized'] = 'explicit_continue_after_remote_read'
                self._publish_progress(journal, binding, progress)  # Only a new explicit invocation can retry a definitively rejected, still-missing effect.
                continue
            if op.get('candidate_id') != binding['candidate_id'] or op.get('delivery_sha') != progress['delivery']['sha']:
                raise DeliveryPending('operation_source_changed')
            if op['kind'] == 'body':
                applied = issue.get('body') == op['expected_after']
            elif op['kind'] == 'label':
                applied = isinstance(issue.get('labels'), list) and op['expected_after']['name'] not in issue['labels']
            else:
                observed = {'state': issue.get('state'), 'state_reason': issue.get('state_reason')}
                if op.get('observed_after') != observed or op['status'] != 'unknown':
                    op.update(status='unknown', observed_after=observed, confirmation='source_unattributed')
                    self._publish_progress(journal, binding, progress)  # Preserve the actual closed effect separately from unresolved request attribution.
                raise DeliveryPending('close_outcome_unknown')  # Closed state alone cannot attribute a lost response to this request.
            if not applied:
                raise DeliveryPending('operation_outcome_unknown')  # Unsent and externally reverted requests are indistinguishable.
            op['status'], op['confirmation'] = 'confirmed', 'remote_effect_observed'
            self._publish_progress(journal, binding, progress)  # Preserve reconciled steps before sending any missing operation.

    def _synchronize_issue(self, store, journal, binding, progress, state, *, explicit_continue=False):
        config = configuration(self.project)['configuration']
        repository, number = config['repository'], binding['issue']['number']
        if not all(callable(getattr(self.github, name, None)) for name in ('update_issue_body', 'remove_issue_label', 'close_issue')):
            return progress  # Existing read-only clients remain qualification-only.
        operations = copy.deepcopy(binding['progress'].get('operations', []))
        progress['operations'] = operations
        issue = self.github.get_issue(repository, number)
        if self._identity(issue, number, repository, 'issue') != binding['issue']:
            raise DeliveryPending('issue_identity_changed')
        self._reconcile_operations(journal, binding, progress, issue, explicit_continue=explicit_continue)  # A new instance always observes the remote before recovering intent.
        before = issue.get('body')
        body_operations = [op for op in operations if op['kind'] == 'body' and op['status'] == 'confirmed']
        known_body = body_operations[-1]['expected_after'] if body_operations else None
        if not isinstance(before, str) or (before != known_body and fingerprint(before) != binding['body_baseline_id']):
            raise DeliveryPending('body_baseline_changed')
        close = next((op for op in reversed(operations) if op['kind'] == 'close'), None)
        already_synced = (close is not None and close['status'] == 'confirmed'
                          and close.get('expected_after') == {'state': 'closed', 'state_reason': 'completed'}
                          and close.get('candidate_id') == binding['candidate_id']
                          and close.get('delivery_sha') == progress['delivery']['sha'])  # Transient pending does not erase a confirmed closure of this exact accepted delivery.
        if already_synced and (before != known_body or issue.get('state') != 'closed' or issue.get('state_reason') != 'completed'
                               or set(issue.get('labels', [])) & set(config['remove_labels'])):
            raise DeliveryPending('remote_changed_after_sync')  # Never silently reclaim an externally changed closed ticket.
        expected_state = 'closed' if already_synced else 'open'
        if issue.get('state') != expected_state or not isinstance(issue.get('labels'), list) or any(not isinstance(v, str) for v in issue['labels']):
            raise DeliveryPending('issue_not_open_or_fields_unavailable')
        try:
            expected = self._body(binding, state, before, progress['delivery'])
        except ValueError:
            raise DeliveryPending('body_mapping_changed') from None
        def read(expected_body, expected_labels, expected_state=expected_state):
            current = self.github.get_issue(repository, number)
            if self._identity(current, number, repository, 'issue') != binding['issue']:
                raise DeliveryPending('issue_identity_changed')
            if current.get('body') != expected_body or sorted(current.get('labels', [])) != sorted(expected_labels) or current.get('state') != expected_state:
                raise DeliveryPending('issue_readback_mismatch')
            return current  # Every subsequent write uses a fresh matching observation.

        def mutate(kind, previous, following, request, labels, next_state='open'):
            current_state = self._state(store)
            current_projection = store.acceptance_projection(binding['ticket_id'])
            if not self._qualification_matches(current_state, current_projection, binding):
                raise DeliveryPending('qualification_changed_before_close' if kind == 'close' else 'qualification_changed_before_write')
            op = {'kind': kind, 'status': 'intent', 'expected_before': previous, 'expected_after': following,
                  'candidate_id': binding['candidate_id'], 'delivery_sha': progress['delivery']['sha']}
            operations.append(op)
            self._publish_progress(journal, binding, progress)  # Persist exact intent and source before any external request.
            try:
                request()
            except DeliveryPending as exc:
                definitive = {'connection_unauthorized', 'permission_denied', 'remote_material_missing', 'remote_material_gone',
                              'remote_request_rejected', 'rate_limited', 'remote_body_invalid', 'remote_label_invalid'}
                op['status'] = 'failed' if exc.reason in definitive else 'unknown'  # Invalid/lost response bodies cannot prove the write was rejected.
                op['reason'] = exc.reason
                raise
            except Exception:
                op['status'] = 'unknown'
                raise DeliveryPending('remote_write_outcome_unknown') from None
            actual = read(expected, labels, next_state)
            if next_state == 'closed' and actual.get('state_reason') != 'completed':
                raise DeliveryPending('close_reason_readback_mismatch')
            op['status'] = 'confirmed'
            self._publish_progress(journal, binding, progress)  # Dependent writes wait for actual readback confirmation.

        labels = list(issue['labels'])
        read(before, labels)  # Re-read immediately before the first intent, rather than trust the binding snapshot.
        if before != expected:
            mutate('body', before, expected, lambda: self.github.update_issue_body(repository, number, expected), labels, expected_state)
        if already_synced:
            return {**progress, 'status': 'synced', 'reason': 'remote_verified'}  # Refresh the same stable summary without another close request.
        for name in config['remove_labels']:
            if name in labels:
                read(expected, labels)
                following = [label for label in labels if label != name]
                mutate('label', {'name': name, 'present': True}, {'name': name, 'present': False},
                       lambda name=name: self.github.remove_issue_label(repository, number, name), following)
                labels = following
        latest = self._state(store)
        projection = store.acceptance_projection(binding['ticket_id'])
        if not self._qualification_matches(latest, projection, binding):
            raise DeliveryPending('qualification_changed_before_close')
        current_delivery = qualify_delivery(self.github, repository, config, binding, latest)
        if current_delivery['delivery'] != progress['delivery']:
            raise DeliveryPending('delivery_changed_before_close')
        read(expected, labels)
        mutate('close', {'state': 'open'}, {'state': 'closed', 'state_reason': 'completed'},
               lambda: self.github.close_issue(repository, number), labels, 'closed')
        return {**progress, 'status': 'synced', 'reason': 'remote_verified'}

    def _local_pending(self, store, reason):
        progress = {'schema_version': 1, 'status': 'pending', 'reason': reason, 'run_id': store.run_id}
        path = self.root / ('pending-' + uuid.uuid4().hex + '.json')
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self._save(path, progress)  # Independent diagnostic publication never rewrites a locked or unknown journal.
        except OSError:
            return {**progress, 'persistence': 'unavailable'}
        return {**progress, 'pending_record': str(path)}

    def continue_sync(self, store, *, ticket_id=None):
        return self._run_sync(store, ticket_id=ticket_id, explicit_continue=True)  # Explicit catch-up shares all gates and never calls RunStore.resume.

    def sync_once(self, store, *, ticket_id=None):
        return self._run_sync(store, ticket_id=ticket_id)

    def _run_sync(self, store, *, ticket_id=None, explicit_continue=False):
        self._state(store)  # Reject a foreign authority before converting local persistence errors into pending.
        try:
            return self._execute_once(store, ticket_id=ticket_id, explicit_continue=explicit_continue)
        except LocalJournalConflict:
            return self._local_pending(store, 'local_revision_conflict')
        except (ValueError, TypeError, KeyError):
            return self._local_pending(store, 'local_record_invalid')
        except FileExistsError:
            return self._local_pending(store, 'local_lock_unavailable')
        except OSError:
            return self._local_pending(store, 'local_persistence_unavailable')

    def _execute_once(self, store, *, ticket_id=None, explicit_continue=False):
        state = self._state(store)
        if not self._enrolled(state):
            return {'status': 'unmanaged', 'reason': 'not_enrolled_in_current_generation'}
        config = configuration(self.project)['configuration']
        try:
            with self.github.round(config['connection_reference']) if hasattr(self.github, 'round') else nullcontext():
                return self._sync_once(store, ticket_id=ticket_id, explicit_continue=explicit_continue)  # One finite Adapter round covers qualification, writes and final requalification.
        except DeliveryPending as exc:
            with self._locked():
                journal = self._journal()
                selected = [b for b in journal['bindings'] if b['run_id'] == store.run_id and (ticket_id is None or b['ticket_id'] == ticket_id)]
                progress = {'status': 'pending', 'reason': exc.reason}
                if exc.retry_not_before is not None:
                    progress['retry_not_before'] = exc.retry_not_before
                if len(selected) == 1:
                    progress['operations'] = selected[0]['progress'].get('operations', [])
                    self._publish_progress(journal, selected[0], progress)
                return progress

    def _sync_once(self, store, *, ticket_id=None, explicit_continue=False):
        state = self._state(store)
        if not self._enrolled(state):
            return {'status': 'unmanaged', 'reason': 'not_enrolled_in_current_generation'}
        with self._locked():
            journal = self._journal()
            bindings = [b for b in journal['bindings'] if b['run_id'] == store.run_id and (ticket_id is None or b['ticket_id'] == ticket_id)]
            if not bindings:
                return {'status': 'pending', 'reason': 'issue_not_bound'}
            if len(bindings) != 1:
                return {'status': 'pending', 'reason': 'explicit_ticket_selection_required'}
            binding = bindings[0]
            delivery = None  # Facts from a previous round never bypass a fresh PR/CI read.
            projection = store.acceptance_projection(binding['ticket_id'])
            if state['status'] not in {'running', 'completed'}:
                reason = 'run_control_stopped'
            elif not projection['eligible']:
                reason = 'acceptance_pending'
            elif binding.get('candidate_id') != projection['candidate_id'] or binding['source_references'] != projection['sources']:
                reason = 'candidate_not_frozen_or_changed'
            elif next((s for s in state.get('ticket_scopes', []) if s['ticket_id'] == binding['ticket_id']), None) != binding['scope']:
                reason = 'original_scope_changed'
            elif [{'id': c['id'], 'text': c['criterion'].strip()} for c in state['acceptance'] if c['id'] in binding['scope']['acceptance_ids']] != binding['acceptance_source']['items']:
                reason = 'original_acceptance_source_changed'
            elif (type(binding['progress'].get('retry_not_before')) in (float, int)
                    and math.isfinite(binding['progress']['retry_not_before']) and binding['progress']['retry_not_before'] > self.clock()):
                reason = 'rate_limited'
                delivery = {'retry_not_before': binding['progress']['retry_not_before']}  # A new explicit continue never bypasses a service wait.
            else:
                try:
                    delivery = qualify_delivery(self.github, configuration(self.project)['configuration']['repository'],
                                                configuration(self.project)['configuration'], binding, state)
                    reason = delivery['reason']
                except DeliveryPending as exc:
                    reason = exc.reason  # Read failures remain local synchronization pending, not professional failures.
                    if exc.retry_not_before is not None:
                        delivery = {'retry_not_before': exc.retry_not_before}
            if delivery and delivery.get('delivery_eligible'):
                latest_state = self._state(store)
                latest_projection = store.acceptance_projection(binding['ticket_id'])  # Remote waits cannot freeze professional currentness.
                if not self._qualification_matches(latest_state, latest_projection, binding):
                    reason, delivery = 'qualification_changed_during_read', None
            progress = {'status': 'pending', 'reason': reason,
                        'qualification_reason': projection.get('reason') if not projection['eligible'] else None,
                        'operations': copy.deepcopy(binding['progress'].get('operations', []))}  # New missing prerequisites never erase confirmed or failed remote history.
            if delivery:
                progress.update(delivery)  # Qualification and successful remote synchronization remain distinct states.
            if progress.get('delivery_eligible'):
                try:
                    progress = self._synchronize_issue(store, journal, binding, progress, state, explicit_continue=explicit_continue)
                except DeliveryPending as exc:
                    progress['reason'] = exc.reason  # A partial mutation remains pending until every readback matches.
                    if exc.retry_not_before is not None:
                        progress['retry_not_before'] = exc.retry_not_before
                except (ValueError, TypeError, KeyError):
                    progress['reason'] = 'remote_material_invalid'
            if binding['progress'] != progress:
                binding['progress'] = progress
                journal['revision'] += 1
                self._save(self.root / 'bindings.json', journal)
            return {**progress, 'ticket_id': binding['ticket_id'], 'binding_id': binding['binding_id']}
