"""Owned foreground lifecycle composition; IssueSync retains all qualification and recovery."""
from issue_sync import IssueSync, capture_enrollment, configuration
from issue_github import GitHubIssueAdapter
from issue_delivery import native_closing_reference


def synchronize_event(store, event, *, github=None, token_provider=None, event_id=None):
    state = store.read()  # A lifecycle notification is never another execution authority.
    enrollment = capture_enrollment(store.project)
    if enrollment is None or state.get('issue_sync_enrollment') != enrollment:
        return {'status': 'unmanaged', 'reason': 'not_enrolled_in_current_generation'}
    config = configuration(store.project)['configuration']
    adapter = github if github is not None else GitHubIssueAdapter(
        connection_reference=config['connection_reference'], token_provider=token_provider)  # The host injects its approved named resolver; no credential discovery occurs.
    sync = IssueSync(store.project, adapter)
    if state['status'] not in {'running', 'completed'}:
        if state['status'] == 'paused' and event == 'acceptance_available':
            sync.defer_acceptance(store, event_id=event_id)  # Only retain the already accepted receipt reference while the user pause remains in force.
        return {'status': 'pending', 'reason': 'run_control_stopped'}
    if event in {'acceptance_available', 'completed'}:
        sync.refresh_acceptance(store, event_id=event_id)  # Only an actual accepted-source node may adopt current professional references.
    if event == 'continued':
        deferred = dict.fromkeys(receipt['event_id'] for binding in sync.status(store)['bindings']
            for receipt in binding.get('acceptance_receipts', []) if receipt['status'] == 'deferred')  # Only real previously deferred acceptance nodes may be revisited.
        for receipt_id in deferred:
            sync.refresh_acceptance(store, event_id=receipt_id)  # Ordinary continuation otherwise never adopts changed sources.
        return sync.continue_sync(store)  # Explicit recovery and automatic execution share the original public sync implementation.
    return sync.sync_once(store)  # Creation and acceptance use the existing independently qualified public seam.


def pr_handoff(store, body):
    if not isinstance(body, str):
        raise ValueError('Explicit PR draft body required')
    state = store.read()
    enrollment = capture_enrollment(store.project)
    if enrollment is None or state.get('issue_sync_enrollment') != enrollment:
        return {'status': 'unmanaged', 'reason': 'not_enrolled_in_current_generation'}
    config = configuration(store.project)['configuration']
    bindings = IssueSync(store.project, None).status(store)['bindings']  # Draft preparation is entirely local and acquires no GitHub capability.
    references = []
    for binding in bindings:
        if native_closing_reference(body, config['repository'], binding['issue']['number']):
            return {'status': 'blocked', 'reason': 'native_early_close_conflict'}  # Preserve the draft for explicit author review instead of rewriting it.
        references.append('https://github.com/' + config['repository']['full_name'] + '/issues/' + str(binding['issue']['number']))
    return {'status': 'ready', 'body': body, 'issue_references': references, 'native_auto_close': False,
        'closing_policy': 'issue-sync-after-accepted-delivery-and-required-post-merge-ci'}  # The publisher receives the owned closing restriction with its unchanged safe draft.
