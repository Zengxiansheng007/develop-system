"""Project-owned Issue synchronization; execution authority remains in RunStore."""
from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import uuid


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
    if (type(record.get('schema_version')) is not int or record['schema_version'] != 1
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
    def __init__(self, project_root, github):
        self.project = Path(project_root).resolve(strict=True)
        self.root = self.project / '.develop-system/issue-sync'
        self.github = github  # Only the external API boundary is replaceable in behavior tests.

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
        if (type(journal.get('schema_version')) is not int or journal['schema_version'] != 1
                or journal.get('project_key') != fingerprint(str(self.project)) or type(journal.get('revision')) is not int
                or not isinstance(journal.get('bindings'), list)):
            raise ValueError('Unknown or foreign Issue sync journal')
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
        issue = self.github.get_issue(repository, issue_number)
        issue_identity = self._identity(issue, issue_number, repository, 'issue')
        pull = self.github.get_pull_request(repository, final_pr['number'])
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

    def sync_once(self, store, *, ticket_id=None):
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
            else:
                reason = 'final_delivery_unverified'  # #16 adds real delivery qualification; #15 never writes GitHub.
            progress = {'status': 'pending', 'reason': reason,
                        'qualification_reason': projection.get('reason') if not projection['eligible'] else None}
            if binding['progress'] != progress:
                binding['progress'] = progress
                journal['revision'] += 1
                self._save(self.root / 'bindings.json', journal)
            return {**progress, 'ticket_id': binding['ticket_id'], 'binding_id': binding['binding_id']}
