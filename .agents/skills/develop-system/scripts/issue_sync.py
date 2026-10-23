"""Project-owned Issue synchronization; execution authority remains in RunStore."""
from contextlib import contextmanager
from contextlib import nullcontext
import copy
import difflib
import errno
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


def validate_service_wait(value):
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'host', 'connection_reference', 'resource', 'retry_not_before'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['host'] != 'api.github.com' or value['resource'] != 'core'
            or not isinstance(value['connection_reference'], str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}', value['connection_reference'])
            or type(value['retry_not_before']) not in (int, float) or not math.isfinite(value['retry_not_before'])):
        raise ValueError('Unknown or unsupported Issue service waiting scope')  # Additive progress metadata cannot import credentials, unknown versions or arbitrary service ownership.
    return value


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
        owner = b'develop-system:issue-sync-lock:v1:' + uuid.uuid4().hex.encode('ascii')
        with (self.root / '.sync.guard').open('a+b') as guard:  # Keep one stable inode; never unlink a guard another caller may have opened.
            guard.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(guard.fileno(), msvcrt.LK_NBLCK, 1)  # Nonblocking byte-range ownership also works on an empty file.
                else:
                    import fcntl
                    fcntl.flock(guard.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise FileExistsError('Issue synchronization has an active owner') from None
                raise  # Unsupported/failed filesystem locking never grants ownership.
            try:
                if lock.exists():
                    if lock.is_symlink() or not re.fullmatch(rb'develop-system:issue-sync-lock:v1:[0-9a-f]{32}', lock.read_bytes()):
                        raise FileExistsError('Unknown or legacy Issue synchronization owner')  # PID and age cannot prove a foreign owner dead.
                    lock.unlink()  # This versioned owner must hold our acquired OS guard while alive; its leftover marker is now abandoned.
                with lock.open('xb') as stream:
                    stream.write(owner)
                    stream.flush()
                    os.fsync(stream.fileno())  # Publish recognizable ownership before journal changes or external effects.
                try:
                    yield
                finally:
                    if lock.exists() and not lock.is_symlink() and lock.read_bytes() == owner:
                        lock.unlink()  # Never remove an unknown replacement marker.
            finally:
                guard.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(guard.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(guard.fileno(), fcntl.LOCK_UN)  # Process death also releases OS ownership without running finally.

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
        for binding in journal['bindings']:
            if isinstance(binding.get('progress'), dict) and 'service_wait' in binding['progress']:
                validate_service_wait(binding['progress']['service_wait'])  # Old progress without this optional marker remains compatible.
        return journal  # This file contains remote progress and references, never professional verdicts.

    def _capture_service_wait(self, progress, error):
        if error.reason == 'rate_limited' and error.service_scope is not None and error.retry_not_before is not None:
            progress['service_wait'] = validate_service_wait({'schema_version': 1, **error.service_scope,
                'retry_not_before': error.retry_not_before})  # Persist only safe recognized response facts, retaining the original absolute deadline.

    def _shared_service_wait(self, journal, connection_reference):
        waits = [binding['progress']['service_wait'] for binding in journal['bindings']
                 if isinstance(binding.get('progress'), dict) and 'service_wait' in binding['progress']
                 and binding['progress']['service_wait']['connection_reference'] == connection_reference
                 and binding['progress']['service_wait']['retry_not_before'] > self.clock()]
        return max(waits, key=lambda value: value['retry_not_before']) if waits else None  # Read only already-known synchronization progress; never scan or control development runs.

    def _hold_service_wait(self, journal, binding, wait):
        progress = {**copy.deepcopy(binding['progress']), 'status': 'pending', 'reason': 'rate_limited',
            'delivery_eligible': False, 'retry_not_before': wait['retry_not_before'], 'service_wait': copy.deepcopy(wait)}
        if binding['progress'].get('status') == 'human_paused':
            progress['human_pause'] = copy.deepcopy(binding['progress'].get('human_pause') or {'reason': 'independent_human_pause'})  # A service hold cannot erase a separate legacy human control.
        self._publish_progress(journal, binding, progress)  # Retain operations/body/human controls while explaining why this pool-dependent ticket cannot request yet.
        return {**progress, 'ticket_id': binding['ticket_id'], 'binding_id': binding['binding_id']}

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

    def _acceptance_source(self, state, scope, body, *, parent_spec=False):
        if not isinstance(body, str):
            raise ValueError('Original complete Issue acceptance source is unavailable')
        headings = list(re.finditer(r'(?mi)^##[ \t]+Acceptance criteria[ \t]*\r?$', body))
        source_format = 'implementation-checklist-v1'
        if not headings and parent_spec:
            headings = list(re.finditer(r'(?m)^### 原需求验收条件[ \t]*\r?$', body))  # Spec14 retains its approved plain AC list; parent binding does not rewrite it.
            source_format = 'parent-spec-ac-list-v1'
        if len(headings) != 1:
            raise ValueError('Original complete Issue acceptance source is unavailable')
        start = headings[0].end()
        following = re.search(r'(?m)^#{1,3}[ \t]+' if source_format == 'parent-spec-ac-list-v1' else r'(?m)^#{1,2}[ \t]+', body[start:])
        section = body[start:start + following.start()] if following else body[start:]
        actual = [m.group(1).strip() for m in re.finditer(r'(?m)^[ \t]*[-*] \[[ xX]\] ([^\r\n]+)\r?$', section)]
        if source_format == 'parent-spec-ac-list-v1':
            entries = list(re.finditer(r'(?m)^- \*\*(AC-[0-9]{3}) / REQ-[0-9]{3}\*\*：([^\r\n]+)\r?$', section))  # Exact approved text replaces no professional conclusions.
            if len(entries) != len(re.findall(r'(?m)^[ \t]*[-*][ \t]+', section)):
                raise ValueError('Original complete Issue acceptance source contains unsupported entries')  # Never ignore an unrecognized original criterion to approve a narrowed scope.
            if len({m.group(1) for m in entries}) != len(entries):
                raise ValueError('Original complete Issue acceptance source is ambiguous')
            actual = [m.group(2).strip() for m in entries]
        declared = [{'id': c['id'], 'text': c['criterion'].strip()} for c in state['acceptance'] if c['id'] in scope['acceptance_ids']]
        expected = [c['text'] for c in declared]
        if not actual or len(set(actual)) != len(actual) or len(expected) != len(actual) or set(expected) != set(actual):
            raise ValueError('Original complete Issue acceptance set does not match its source')
        return {'format': source_format, 'items': declared, 'source_id': fingerprint(declared)}  # Source completeness is independent of writeback mapping.

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

    def bind(self, store, *, issue_number, final_pr, acceptance_map, parent_spec=False):
        state = self._state(store)
        if not self._enrolled(state):
            raise ValueError('Run was not created in the current explicitly enabled generation')
        if type(issue_number) is not int or issue_number <= 0:
            raise ValueError('One explicit Issue number required')
        if type(parent_spec) is not bool:
            raise ValueError('Explicit parent specification marker must be boolean')  # References or child counts never infer the parent's role.
        config = configuration(self.project)['configuration']
        repository = config['repository']
        waiting = self._shared_service_wait(self._journal(), config['connection_reference'])
        if waiting is not None:
            raise DeliveryPending('rate_limited', retry_not_before=waiting['retry_not_before'])  # Binding reads cannot bypass a known applicable pool wait.
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
        source = self._acceptance_source(state, scope, issue.get('body') or '', parent_spec=parent_spec)
        mapping = self._mapping(acceptance_map, scope, issue.get('body') or '', source)
        projection = store.acceptance_projection(ticket_id)
        fixed = {'run_id': store.run_id, 'enrollment': state['issue_sync_enrollment'], 'scope': scope,
                 'issue': issue_identity, 'final_pr': pull_identity, 'mapping': mapping, 'acceptance_source': source,
                 'body_baseline_id': fingerprint(issue.get('body') or ''), 'body_baseline': issue.get('body') or ''}  # Preserve an exact local conflict baseline, never export it.
        if parent_spec:
            fixed['parent_spec'] = True  # Persist the explicit parent binding separately from its original complete scope.
        with self._locked():
            latest = self._state(store)
            if not self._enrolled(latest) or latest['revision'] != state['revision']:
                raise ValueError('Run or configuration changed while binding')
            journal = self._journal()
            existing = next((b for b in journal['bindings'] if b['ticket_id'] == ticket_id), None)
            if existing and (existing.get('parent_spec', False) != parent_spec or any(existing.get(k) != v for k, v in fixed.items())):
                raise ValueError('Issue already has a conflicting run, PR, scope or body binding')
            candidate_id = projection.get('candidate_id') if projection['eligible'] else None
            if existing and existing.get('candidate_id') == candidate_id:
                return copy.deepcopy(existing)
            binding = {**(existing or {}), **fixed, 'schema_version': 1, 'binding_id': existing['binding_id'] if existing else uuid.uuid4().hex,
                       'ticket_id': ticket_id, 'candidate_id': candidate_id,
                       'source_references': projection.get('sources', []),
                       'progress': copy.deepcopy(existing['progress']) if existing else
                           {'status': 'pending', 'reason': 'final_delivery_unverified' if projection['eligible'] else 'acceptance_pending'}}  # Candidate adoption never clears durable controls or remote operation history.
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
        self._mapping(mapping, binding['scope'], body, self._acceptance_source(state, binding['scope'], body, parent_spec=binding.get('parent_spec', False)))  # Revalidate unique text/context against current complete ACs.
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

    def _record_body_conflict(self, journal, binding, progress, expected, issue, stage, reason):
        observed = issue.get('body')
        conflict = {'conflict_id': uuid.uuid4().hex, 'status': 'unresolved', 'stage': stage, 'reason': reason,
                    'expected_body': expected, 'expected_body_id': fingerprint(expected) if isinstance(expected, str) else binding['body_baseline_id'],
                    'observed_body': observed, 'observed_body_id': fingerprint(observed),
                    'difference': ''.join(difflib.unified_diff((expected or '').splitlines(keepends=True),
                        (observed if isinstance(observed, str) else '').splitlines(keepends=True), fromfile='expected', tofile='observed')),
                    'unfinished_steps': ['body'] + ['label:' + name for name in configuration(self.project)['configuration']['remove_labels']
                        if name in issue.get('labels', [])] + (['close'] if issue.get('state') == 'open' or not any(
                            op['kind'] == 'close' and op['status'] == 'confirmed' for op in progress.get('operations', [])[binding.get('intervention', {}).get('operation_start', 0):]) else []),
                    'operations': copy.deepcopy(progress.get('operations', []))}  # Exact remote differences remain local and retain the original operation facts.
        binding.setdefault('body_conflicts', []).append(conflict)
        progress['body_conflict'] = copy.deepcopy(conflict)
        self._publish_progress(journal, binding, progress)  # Publish the pause before returning or permitting another operation.
        raise DeliveryPending(reason)

    def _resolve_body_conflict(self, journal, binding, progress, state, issue, value):
        conflicts = [c for c in binding.get('body_conflicts', []) if c['status'] == 'unresolved']
        fields = {'conflict_id', 'expected_body', 'acceptance_map', 'user_reference'}
        if (not conflicts or not isinstance(value, dict) or set(value) != fields
                or value['conflict_id'] != conflicts[-1]['conflict_id']
                or not isinstance(value['expected_body'], str) or not isinstance(value['user_reference'], str)
                or not value['user_reference'].strip() or not isinstance(value['acceptance_map'], dict)
                or set(value['acceptance_map']) != set(binding['mapping'])):
            raise DeliveryPending('body_resolution_invalid')  # Explicit review cannot enlarge or shrink the original permitted AC write set.
        if issue.get('body') != value['expected_body']:
            self._record_body_conflict(journal, binding, progress, value['expected_body'], issue,
                                       'resolution_read', 'body_resolution_stale')
        try:
            source = self._acceptance_source(state, binding['scope'], issue['body'], parent_spec=binding.get('parent_spec', False))
            mapping = self._mapping(value['acceptance_map'], binding['scope'], issue['body'], source)
        except ValueError:
            raise DeliveryPending('body_resolution_mapping_invalid') from None  # No old offset, duplicate selection or approximate text adoption.
        for op in progress['operations'][binding.get('intervention', {}).get('operation_start', 0):]:
            if op['kind'] == 'body' and op['status'] not in {'confirmed', 'failed'} and not op.get('response_received'):
                raise DeliveryPending('operation_outcome_unknown')  # A known rejection blocks its own operation; an uncertain body outcome cannot be superseded by review.
        resolution = {'user_reference': value['user_reference'], 'body_id': fingerprint(issue['body']),
                      'mapping': copy.deepcopy(mapping), 'operation_count': len(progress['operations'])}
        for conflict in conflicts:
            conflict.update(status='resolved', resolution=copy.deepcopy(resolution))  # Retain every original difference and historical operation status.
        binding.update(mapping=mapping, acceptance_source=source, body_baseline=issue['body'], body_baseline_id=resolution['body_id'],
                       body_reconciliation={**resolution, 'body': issue['body']})
        progress.pop('body_conflict', None)
        self._publish_progress(journal, binding, progress)  # Current-body review changes only this conflict, never independent human/run control.

    def _qualification_matches(self, state, projection, binding):
        return (self._enrolled(state) and state['status'] in {'running', 'completed'} and projection['eligible']
                and projection.get('candidate_id') == binding['candidate_id']
                and projection.get('sources') == binding['source_references'])  # Callers supply fresh reads at each distinct stage.

    def _stable_events(self, repository, number):
        reader = getattr(self.github, 'get_issue_events', None)
        if not callable(reader):
            raise DeliveryPending('issue_events_unavailable')  # A current flag or matching account cannot substitute for history.
        def snapshot():
            events, identifiers = reader(repository, number), set()
            if not isinstance(events, list):
                raise DeliveryPending('issue_events_incomplete')
            result = []
            for event in events:
                if (not isinstance(event, dict) or type(event.get('id')) is not int or event['id'] <= 0
                        or event['id'] in identifiers or not isinstance(event.get('node_id'), str) or not event['node_id']
                        or not isinstance(event.get('event'), str) or not event['event']
                        or not isinstance(event.get('created_at'), str) or not event['created_at']):
                    raise DeliveryPending('issue_events_incomplete')
                identifiers.add(event['id'])
                selected = {k: event[k] for k in ('id', 'node_id', 'event', 'created_at')}
                if event.get('commit_id') is not None:
                    if not isinstance(event['commit_id'], str) or not re.fullmatch('[0-9a-f]{40}', event['commit_id']):
                        raise DeliveryPending('issue_events_incomplete')
                    selected['commit_id'] = event['commit_id']  # A native closing commit is observable; actor identity still proves nothing.
                result.append(selected)  # Do not persist actors or remote arbitrary data.
            return result
        first, second = snapshot(), snapshot()
        if first != second:
            raise DeliveryPending('issue_events_unstable')  # Bounded repeated observations are not a remote snapshot transaction.
        return first

    def _observe_intervention(self, journal, binding, progress, repository, number):
        intervention = binding.get('intervention')
        if intervention and intervention.get('status') == 'manual_paused':
            progress.update(status='manual_paused', reason='external_reopen_observed')
            return True  # Persisted ticket control survives qualification and connection changes.
        if not intervention or not intervention.get('closure'):
            active = progress.get('operations', [])[binding.get('intervention', {}).get('operation_start', 0):]
            if any(op['kind'] == 'close' and op['status'] == 'confirmed' for op in active):
                raise DeliveryPending('close_event_interval_unknown')  # Old acknowledgements without an event association cannot be silently upgraded.
            return False
        closure = intervention['closure']
        events = self._stable_events(repository, number)
        previous = closure['events']
        if events[:len(previous)] != previous:
            raise DeliveryPending('close_event_interval_unknown')
        reopened = next((event for event in events[len(previous):] if event['event'] == 'reopened'), None)
        if reopened:
            intervention.update(status='manual_paused', reopened_event=reopened)
            progress.update(status='manual_paused', reason='external_reopen_observed')
            self._publish_progress(journal, binding, progress)  # Human pause is saved even if the Issue has already been closed again.
            return True
        return False

    def _external_closure_reason(self, repository, number):
        events = self._stable_events(repository, number)
        return ('native_early_close_conflict' if any(event['event'] == 'closed' and event.get('commit_id') for event in events)
                else 'externally_closed_issue')  # Native commits and unowned closures are reported without taking ownership.

    def _current_body_reference(self, binding, operations):
        start = binding.get('intervention', {}).get('operation_start', 0)
        confirmed = [(i, op['expected_after']) for i, op in enumerate(operations)
                     if i >= start and op['kind'] == 'body' and op['status'] == 'confirmed']  # Previous effect epochs retain history without defining the new body.
        reviewed = binding.get('body_reconciliation')
        if reviewed and reviewed['operation_count'] < start:
            reviewed = None  # A pre-takeover review cannot supersede the explicitly reacquired current baseline.
        if reviewed and (not confirmed or confirmed[-1][0] < reviewed['operation_count']):
            return reviewed['body']  # Explicit current-body review remains current until a later confirmed body write.
        return confirmed[-1][1] if confirmed else binding.get('body_baseline')

    def _reconcile_operations(self, journal, binding, progress, issue, *, explicit_continue=False):
        def pending(reason):
            expected = self._current_body_reference(binding, progress['operations'])
            if issue.get('body') != expected:
                self._record_body_conflict(journal, binding, progress, expected, issue, 'recovery_read', reason)
            raise DeliveryPending(reason)  # Preserve every observed body difference before any operation-specific early return.

        for index, op in enumerate(progress['operations']):
            if index < binding.get('intervention', {}).get('operation_start', 0):
                continue  # Explicit takeover preserves historical operations without reconciling them as new requests.
            if (op['kind'] == 'body' and index < binding.get('body_reconciliation', {}).get('operation_count', 0)
                    and (op['status'] == 'confirmed' or op.get('response_received'))):
                continue  # An explicit current-body review supersedes the old precondition without manufacturing confirmation.
            if op['status'] == 'confirmed':
                continue  # Current effects are checked together below; a newer confirmed summary supersedes an older body value.
            if op['status'] == 'failed':
                if op.get('retry_authorized'):
                    continue  # The original definitive rejection remains immutable history.
                previous = op['expected_before']
                unchanged = (issue.get('body') == previous if op['kind'] == 'body' else
                    isinstance(issue.get('labels'), list) and previous['name'] in issue['labels'] if op['kind'] == 'label' else
                    issue.get('state') == 'open')
                if (not explicit_continue or not unchanged
                        or issue.get('body') != self._current_body_reference(binding, progress['operations'])):
                    pending('previous_request_failed')  # A changed body must be held before any failed-operation retry is authorized.
                op['retry_authorized'] = 'explicit_continue_after_remote_read'
                self._publish_progress(journal, binding, progress)  # Only a new explicit invocation can retry a definitively rejected, still-missing effect.
                continue
            if op.get('candidate_id') != binding['candidate_id'] or op.get('delivery_sha') != progress['delivery']['sha']:
                pending('operation_source_changed')
            if op['kind'] == 'body':
                applied = issue.get('body') == op['expected_after']
            elif op['kind'] == 'label':
                applied = isinstance(issue.get('labels'), list) and op['expected_after']['name'] not in issue['labels']
            else:
                observed = {'state': issue.get('state'), 'state_reason': issue.get('state_reason')}
                if op.get('observed_after') != observed or op['status'] != 'unknown':
                    op.update(status='unknown', observed_after=observed, confirmation='source_unattributed')
                    self._publish_progress(journal, binding, progress)  # Preserve the actual closed effect separately from unresolved request attribution.
                pending('close_outcome_unknown')  # Observed closed state still cannot attribute this request.
            if not applied:
                pending('operation_outcome_unknown')  # Unsent and externally reverted requests remain indistinguishable.
            op['status'], op['confirmation'] = 'confirmed', 'remote_effect_observed'
            self._publish_progress(journal, binding, progress)  # Preserve reconciled steps before sending any missing operation.

    def _synchronize_issue(self, store, journal, binding, progress, state, *, explicit_continue=False, body_resolution=None):
        config = configuration(self.project)['configuration']
        repository, number = config['repository'], binding['issue']['number']
        if not all(callable(getattr(self.github, name, None)) for name in ('update_issue_body', 'remove_issue_label', 'close_issue')):
            return progress  # Existing read-only clients remain qualification-only.
        operations = copy.deepcopy(binding['progress'].get('operations', []))
        progress['operations'] = operations
        active_operations = operations[binding.get('intervention', {}).get('operation_start', 0):]  # Explicit takeover starts a new effect epoch, preserving prior operations.
        issue = self.github.get_issue(repository, number)
        if self._identity(issue, number, repository, 'issue') != binding['issue']:
            raise DeliveryPending('issue_identity_changed')
        if body_resolution is not None:
            self._resolve_body_conflict(journal, binding, progress, state, issue, body_resolution)
        self._reconcile_operations(journal, binding, progress, issue,
                                   explicit_continue=explicit_continue and body_resolution is None)  # Current-body review supplies no new failed-request retry authorization.
        before = issue.get('body')
        body_operations = [op for op in active_operations if op['kind'] == 'body' and op['status'] == 'confirmed']
        known_body = self._current_body_reference(binding, operations)  # Recovery exits and normal writes compare the same current body.
        if (not isinstance(before, str) or (body_operations and before != known_body)
                or (not body_operations and fingerprint(before) != binding['body_baseline_id'])):
            self._record_body_conflict(journal, binding, progress, known_body if body_operations else binding.get('body_baseline'),
                                       issue, 'initial_read', 'body_baseline_changed')
        close = next((op for op in reversed(active_operations) if op['kind'] == 'close'), None)
        already_synced = (close is not None and close['status'] == 'confirmed'
                          and close.get('expected_after') == {'state': 'closed', 'state_reason': 'completed'}
                          and close.get('candidate_id') == binding['candidate_id']
                          and close.get('delivery_sha') == progress['delivery']['sha'])  # Transient pending does not erase a confirmed closure of this exact accepted delivery.
        if already_synced and (before != known_body or issue.get('state') != 'closed' or issue.get('state_reason') != 'completed'
                               or set(issue.get('labels', [])) & set(config['remove_labels'])):
            raise DeliveryPending('remote_changed_after_sync')  # Never silently reclaim an externally changed closed ticket.
        expected_state = 'closed' if already_synced else 'open'
        if issue.get('state') == 'closed' and not already_synced:
            raise DeliveryPending(self._external_closure_reason(repository, number))  # Never reopen or claim an external/native closure as this feature's success.
        if issue.get('state') != expected_state or not isinstance(issue.get('labels'), list) or any(not isinstance(v, str) for v in issue['labels']):
            raise DeliveryPending('issue_not_open_or_fields_unavailable')
        if any(op['kind'] == 'label' and op['status'] == 'confirmed' and op['expected_after']['name'] in issue['labels'] for op in active_operations):
            raise DeliveryPending('confirmed_operation_reversed')  # A restored confirmed removal is external change, never another missing step.
        try:
            expected = self._body(binding, state, before, progress['delivery'])
        except ValueError:
            raise DeliveryPending('body_mapping_changed') from None
        def read(expected_body, expected_labels, expected_state=expected_state, *, stage='before_write'):
            current = self.github.get_issue(repository, number)
            if self._identity(current, number, repository, 'issue') != binding['issue']:
                raise DeliveryPending('issue_identity_changed')
            if current.get('body') != expected_body:
                self._record_body_conflict(journal, binding, progress, expected_body, current, stage, 'issue_readback_mismatch')
            if sorted(current.get('labels', [])) != sorted(expected_labels) or current.get('state') != expected_state:
                raise DeliveryPending('issue_readback_mismatch')
            return current  # Every subsequent write uses a fresh matching observation.

        def mutate(kind, previous, following, request, labels, next_state='open'):
            current_state = self._state(store)
            current_projection = store.acceptance_projection(binding['ticket_id'])
            if not self._qualification_matches(current_state, current_projection, binding):
                raise DeliveryPending('qualification_changed_before_close' if kind == 'close' else 'qualification_changed_before_write')
            before_events = self._stable_events(repository, number) if kind == 'close' else None
            if kind == 'close':
                read(expected, labels)  # Events and Issue fields are both rechecked before the close intent.
            op = {'kind': kind, 'status': 'intent', 'expected_before': previous, 'expected_after': following,
                  'candidate_id': binding['candidate_id'], 'delivery_sha': progress['delivery']['sha']}
            operations.append(op)
            self._publish_progress(journal, binding, progress)  # Persist exact intent and source before any external request.
            try:
                receipt = request()
            except DeliveryPending as exc:
                definitive = {'connection_unauthorized', 'permission_denied', 'remote_material_missing', 'remote_material_gone',
                              'remote_request_rejected', 'rate_limited', 'remote_body_invalid', 'remote_label_invalid'}
                op['status'] = 'failed' if exc.reason in definitive else 'unknown'  # Invalid/lost response bodies cannot prove the write was rejected.
                op['reason'] = exc.reason
                raise
            except Exception:
                op['status'] = 'unknown'
                raise DeliveryPending('remote_write_outcome_unknown') from None
            op['response_received'] = True  # A returned request is distinct from a confirmed remote effect.
            actual = read(expected, labels, next_state, stage='after_write')
            if next_state == 'closed' and actual.get('state_reason') != 'completed':
                raise DeliveryPending('close_reason_readback_mismatch')
            if kind == 'close':
                after_events = self._stable_events(repository, number)
                appended = after_events[len(before_events):]
                if (after_events[:len(before_events)] != before_events or len(appended) != 1 or appended[0]['event'] != 'closed'
                        or not isinstance(receipt, dict) or type(receipt.get('id')) is not int or receipt['id'] != binding['issue']['id']
                        or type(receipt.get('number')) is not int or receipt['number'] != number or receipt.get('state') != 'closed' or receipt.get('state_reason') != 'completed'):
                    op['status'] = 'unknown'
                    raise DeliveryPending('close_event_interval_unknown')  # An acknowledgement and an unambiguous observed interval are both necessary.
                binding['intervention'] = {**binding.get('intervention', {}), 'status': 'watching', 'closure': {'event': appended[0], 'events': after_events,
                    'acknowledgement': {'id': receipt['id'], 'number': receipt['number'], 'state': 'closed', 'state_reason': 'completed'},
                    'association': 'acknowledged_request_stable_interval'}}  # Correlation is explicit; this is not exclusive remote attribution.
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

    def continue_sync(self, store, *, ticket_id=None, body_resolution=None):
        return self._run_sync(store, ticket_id=ticket_id, explicit_continue=True, body_resolution=body_resolution)  # Explicit catch-up shares all gates and never calls RunStore.resume.

    def take_over(self, store, *, ticket_id, user_reference):
        if not isinstance(ticket_id, str) or not ticket_id or not isinstance(user_reference, str) or not user_reference.strip():
            raise ValueError('Explicit ticket identity and real user takeover reference required')
        return self._run_sync(store, ticket_id=ticket_id, explicit_continue=True, takeover_reference=user_reference)  # Caller must supply the actual ticket-specific human instruction.

    def _prepare_takeover(self, journal, binding, progress, state, user_reference):
        intervention = binding.get('intervention', {})
        if intervention.get('status') != 'manual_paused':
            raise DeliveryPending('ticket_not_manually_paused')
        repository = configuration(self.project)['configuration']['repository']
        issue = self.github.get_issue(repository, binding['issue']['number'])
        if self._identity(issue, binding['issue']['number'], repository, 'issue') != binding['issue']:
            raise DeliveryPending('issue_identity_changed')
        events = self._stable_events(repository, binding['issue']['number'])
        original = intervention['closure']['events']
        if events[:len(original)] != original or intervention['reopened_event'] not in events[len(original):]:
            raise DeliveryPending('close_event_interval_unknown')
        if issue.get('state') != 'open':
            raise DeliveryPending('externally_closed_issue')  # A takeover never reopens an Issue already closed by another mechanism.
        body = issue.get('body')
        try:
            mapping = {key: {k: value[k] for k in ('text', 'context')} for key, value in binding['mapping'].items()}
            self._mapping(mapping, binding['scope'], body, self._acceptance_source(state, binding['scope'], body, parent_spec=binding.get('parent_spec', False)))
            self._body(binding, state, body, progress['delivery'])  # Recheck the current complete source, exact map and managed summary.
        except (ValueError, TypeError):
            raise DeliveryPending('body_mapping_changed') from None
        takeovers = intervention.setdefault('takeovers', [])
        takeovers.append({'user_reference': user_reference, 'closure': copy.deepcopy(intervention['closure']),
                          'reopened_event': copy.deepcopy(intervention['reopened_event']), 'events': events,
                          'body_baseline_id': fingerprint(body)})
        if 'body_reconciliation' in binding:
            takeovers[-1]['body_reconciliation'] = binding.pop('body_reconciliation')  # Retain the old review with its takeover; it cannot define the newly accepted epoch.
        intervention.update(status='reclaimed', operation_start=len(progress['operations']))
        intervention.pop('closure')
        intervention.pop('reopened_event')
        binding.update(body_baseline=body, body_baseline_id=fingerprint(body))  # The new epoch starts from the exact currently reviewed Issue body.
        self._publish_progress(journal, binding, progress)  # Requalification preceded this new effect epoch; old operations remain intact.

    def sync_once(self, store, *, ticket_id=None):
        return self._run_sync(store, ticket_id=ticket_id)

    def _service_wait(self, binding):
        deadline = binding['progress'].get('retry_not_before')
        if type(deadline) in (float, int) and math.isfinite(deadline) and deadline > self.clock():
            return {**copy.deepcopy(binding['progress']), 'status': 'manual_paused'
                    if binding.get('intervention', {}).get('status') == 'manual_paused' else 'pending',
                    'reason': 'rate_limited', 'retry_not_before': deadline, 'delivery_eligible': False,
                    'ticket_id': binding['ticket_id'], 'binding_id': binding['binding_id']}  # Waiting is local control, never fresh delivery qualification.
        return None

    def _run_sync(self, store, *, ticket_id=None, explicit_continue=False, takeover_reference=None, body_resolution=None):
        self._state(store)  # Reject a foreign authority before converting local persistence errors into pending.
        try:
            return self._execute_once(store, ticket_id=ticket_id, explicit_continue=explicit_continue, takeover_reference=takeover_reference, body_resolution=body_resolution)
        except LocalJournalConflict:
            return self._local_pending(store, 'local_revision_conflict')
        except (ValueError, TypeError, KeyError):
            return self._local_pending(store, 'local_record_invalid')
        except FileExistsError:
            return self._local_pending(store, 'local_lock_unavailable')
        except OSError:
            return self._local_pending(store, 'local_persistence_unavailable')

    def _execute_once(self, store, *, ticket_id=None, explicit_continue=False, takeover_reference=None, body_resolution=None):
        state = self._state(store)
        selected = [binding for binding in self._journal()['bindings'] if binding['run_id'] == store.run_id
                    and (ticket_id is None or binding['ticket_id'] == ticket_id)]
        if ticket_id is None and len(selected) > 1:
            if body_resolution is not None or takeover_reference is not None:
                return {'status': 'pending', 'reason': 'explicit_ticket_selection_required'}  # Human review/takeover never broadcasts across bindings.
            tickets = []
            for binding in selected:
                result = (self.continue_sync(store, ticket_id=binding['ticket_id']) if explicit_continue
                          else self.sync_once(store, ticket_id=binding['ticket_id']))  # Serial public calls reread current authority/journal and isolate ticket-local stops.
                tickets.append({**result, 'ticket_id': binding['ticket_id']})  # Pending never implies the other tickets failed or the whole batch passed.
            return {'status': 'batch', 'tickets': tickets}
        if len(selected) == 1 and selected[0].get('intervention', {}).get('status') == 'manual_paused' and takeover_reference is None:
            binding = selected[0]
            return {**copy.deepcopy(binding['progress']), 'status': 'manual_paused', 'reason': 'external_reopen_observed',
                    'ticket_id': binding['ticket_id'], 'binding_id': binding['binding_id']}  # Local human control remains visible even when disabled or disconnected.
        if not self._enrolled(state):
            return {'status': 'unmanaged', 'reason': 'not_enrolled_in_current_generation'}
        if len(selected) == 1:
            waiting = self._service_wait(selected[0])
            if waiting is not None:
                return waiting  # Do not even initialize a replaced/unavailable connection while its ticket must wait.
        config = configuration(self.project)['configuration']
        if len(selected) == 1:
            with self._locked():
                journal = self._journal()
                current = next(b for b in journal['bindings'] if b['binding_id'] == selected[0]['binding_id'])
                waiting = self._shared_service_wait(journal, config['connection_reference'])
                if waiting is not None:
                    return self._hold_service_wait(journal, current, waiting)  # Reentry/new Adapter checks known shared deadlines before credential-provider initialization.
        try:
            with self.github.round(config['connection_reference']) if hasattr(self.github, 'round') else nullcontext():
                return self._sync_once(store, ticket_id=ticket_id, explicit_continue=explicit_continue, takeover_reference=takeover_reference, body_resolution=body_resolution)  # One finite Adapter round covers qualification, writes and final requalification.
        except DeliveryPending as exc:
            with self._locked():
                journal = self._journal()
                selected = [b for b in journal['bindings'] if b['run_id'] == store.run_id and (ticket_id is None or b['ticket_id'] == ticket_id)]
                progress = copy.deepcopy(selected[0]['progress']) if len(selected) == 1 else {}
                progress.update(status='pending', reason=exc.reason, delivery_eligible=False)  # Connection failures cannot discard an already persisted wait or operation history.
                if exc.retry_not_before is not None:
                    progress['retry_not_before'] = exc.retry_not_before
                self._capture_service_wait(progress, exc)
                if len(selected) == 1:
                    for key in ('operations', 'body_conflict', 'human_pause'):
                        if key in selected[0]['progress']:
                            progress[key] = copy.deepcopy(selected[0]['progress'][key])  # Remote read failures cannot discard independent controls or conflict evidence.
                    if selected[0]['progress'].get('status') == 'human_paused':
                        progress['human_pause'] = {'reason': 'independent_human_pause'}
                    self._publish_progress(journal, selected[0], progress)
                return progress

    def _sync_once(self, store, *, ticket_id=None, explicit_continue=False, takeover_reference=None, body_resolution=None):
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
            waiting = self._service_wait(binding)
            if waiting is not None:
                return waiting  # Recheck under ownership before events, PR/CI, body reads or takeover validation.
            waiting = self._shared_service_wait(journal, configuration(self.project)['configuration']['connection_reference'])
            if waiting is not None:
                return self._hold_service_wait(journal, binding, waiting)  # A concurrently published known wait is checked again before any remote request.
            prior_progress = copy.deepcopy(binding['progress'])
            try:
                if self._observe_intervention(journal, binding, prior_progress,
                        configuration(self.project)['configuration']['repository'], binding['issue']['number']):
                    if takeover_reference is None:
                        return {**prior_progress, 'ticket_id': binding['ticket_id'], 'binding_id': binding['binding_id']}
            except DeliveryPending as exc:
                prior_progress.update(status='pending', reason=exc.reason, delivery_eligible=False)
                if exc.retry_not_before is not None:
                    prior_progress['retry_not_before'] = exc.retry_not_before  # History failures carry the same service wait as every other Adapter request.
                self._capture_service_wait(prior_progress, exc)
                self._publish_progress(journal, binding, prior_progress)
                return {**prior_progress, 'ticket_id': binding['ticket_id'], 'binding_id': binding['binding_id']}
            delivery = None  # Facts from a previous round never bypass a fresh PR/CI read.
            conflict = next((c for c in reversed(binding.get('body_conflicts', [])) if c['status'] == 'unresolved'), None)
            projection = store.acceptance_projection(binding['ticket_id'])
            if state['status'] not in {'running', 'completed'}:
                reason = 'run_control_stopped'
            elif binding.get('human_pause') or binding['progress'].get('human_pause') or binding['progress'].get('status') == 'human_paused':
                reason = 'issue_human_paused'  # Body review is never an Issue-level human reacquisition.
            elif conflict is not None and body_resolution is None:
                reason = conflict['reason']  # Restoring an old copy cannot silently release a saved conflict.
            elif not projection['eligible']:
                reason = 'acceptance_pending'
            elif binding.get('candidate_id') != projection['candidate_id'] or binding['source_references'] != projection['sources']:
                reason = 'candidate_not_frozen_or_changed'
            elif next((s for s in state.get('ticket_scopes', []) if s['ticket_id'] == binding['ticket_id']), None) != binding['scope']:
                reason = 'original_scope_changed'
            elif [{'id': c['id'], 'text': c['criterion'].strip()} for c in state['acceptance'] if c['id'] in binding['scope']['acceptance_ids']] != binding['acceptance_source']['items']:
                reason = 'original_acceptance_source_changed'
            else:
                try:
                    delivery = qualify_delivery(self.github, configuration(self.project)['configuration']['repository'],
                                                configuration(self.project)['configuration'], binding, state)
                    reason = delivery['reason']
                except DeliveryPending as exc:
                    reason = exc.reason  # Read failures remain local synchronization pending, not professional failures.
                    if exc.retry_not_before is not None:
                        delivery = {'retry_not_before': exc.retry_not_before}
                        self._capture_service_wait(delivery, exc)
            if delivery and delivery.get('delivery_eligible'):
                latest_state = self._state(store)
                latest_projection = store.acceptance_projection(binding['ticket_id'])  # Remote waits cannot freeze professional currentness.
                if not self._qualification_matches(latest_state, latest_projection, binding):
                    reason, delivery = 'qualification_changed_during_read', None
            if (reason in {'required_ci_not_successful', 'required_ci_missing_or_ambiguous', 'final_pr_not_merged'}
                    and callable(getattr(self.github, 'close_issue', None))
                    and not any(op['kind'] == 'close' for op in binding['progress'].get('operations', []))):
                try:
                    repository = configuration(self.project)['configuration']['repository']
                    issue = self.github.get_issue(repository, binding['issue']['number'])
                    if self._identity(issue, binding['issue']['number'], repository, 'issue') != binding['issue']:
                        raise DeliveryPending('issue_identity_changed')
                    if issue.get('state') == 'closed':
                        reason = self._external_closure_reason(repository, binding['issue']['number'])  # Early native closure must be visible before CI becomes eligible.
                except DeliveryPending as exc:
                    reason = exc.reason
                    if exc.retry_not_before is not None:
                        delivery = {'retry_not_before': exc.retry_not_before}  # Never read beyond a newly returned service-wait boundary.
                        self._capture_service_wait(delivery, exc)
            progress = {'status': 'pending', 'reason': reason,
                        'qualification_reason': projection.get('reason') if not projection['eligible'] else None,
                        'operations': copy.deepcopy(binding['progress'].get('operations', []))}  # New missing prerequisites never erase confirmed or failed remote history.
            if binding['progress'].get('human_pause') or binding['progress'].get('status') == 'human_paused':
                progress['human_pause'] = copy.deepcopy(binding['progress'].get('human_pause') or {'reason': 'independent_human_pause'})  # Preserve control across pending reports.
            if conflict is not None:
                progress['body_conflict'] = copy.deepcopy(conflict)  # Keep the durable conflict visible through the public result.
            if delivery:
                progress.update(delivery)  # Qualification and successful remote synchronization remain distinct states.
            if progress.get('delivery_eligible'):
                try:
                    if takeover_reference is not None:
                        self._prepare_takeover(journal, binding, progress, state, takeover_reference)
                    progress = self._synchronize_issue(store, journal, binding, progress, state, explicit_continue=explicit_continue, body_resolution=body_resolution)
                except DeliveryPending as exc:
                    progress['reason'] = exc.reason  # A partial mutation remains pending until every readback matches.
                    if exc.retry_not_before is not None:
                        progress['retry_not_before'] = exc.retry_not_before
                    self._capture_service_wait(progress, exc)
                except (ValueError, TypeError, KeyError):
                    progress['reason'] = 'remote_material_invalid'
            if binding.get('intervention', {}).get('status') == 'manual_paused':
                progress['status'] = 'manual_paused'  # Failed takeover validation preserves the controlling pause.
            if binding['progress'] != progress:
                binding['progress'] = progress
                journal['revision'] += 1
                self._save(self.root / 'bindings.json', journal)
            return {**progress, 'ticket_id': binding['ticket_id'], 'binding_id': binding['binding_id']}
