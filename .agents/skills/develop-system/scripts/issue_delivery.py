"""Read-only final PR qualification; never creates professional acceptance facts."""
import re
import hashlib
from contextlib import nullcontext
from pathlib import Path, PurePosixPath


class DeliveryPending(Exception):
    def __init__(self, reason, *, retry_not_before=None):
        super().__init__(reason)  # Only static, safe reason codes cross the journal boundary.
        self.reason = reason
        self.retry_not_before = retry_not_before


def verify_contents(github, repository, binding, state, commits):
    expected = None  # All accepted professional sources must describe the same related contents.
    for reference in binding['source_references']:
        frame = next(f for f in state['frames'] if f['frame_id'] == reference['source_frame_id'])
        candidate = frame.get('ticket_candidates', {}).get(binding['ticket_id'])
        if not candidate:
            raise DeliveryPending('accepted_content_unverified')
        files = {f['path']: f['sha256'] for f in candidate['files']}
        if not files or (expected is not None and files != expected):
            raise DeliveryPending('accepted_content_unverified')
        if any((Path(candidate['root']) / name).is_symlink() for name in files):
            raise DeliveryPending('accepted_content_unverified')  # No implicit symlink/LFS/submodule transformations.
        expected = files
    if expected is None or not hasattr(github, 'get_commit_tree') or not hasattr(github, 'get_blob'):
        raise DeliveryPending('accepted_content_unverified')
    for sha in dict.fromkeys(commits):
        tree = github.get_commit_tree(repository, sha)
        if not isinstance(tree, dict) or tree.get('truncated') is not False or not isinstance(tree.get('tree'), list):
            raise DeliveryPending('accepted_content_unverified')  # A truncated recursive response cannot prove an exact set.
        related = {}
        for item in tree['tree']:
            name = item.get('path', '')
            if not isinstance(name, str) or not name or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts:
                raise DeliveryPending('accepted_content_unverified')
            if not any(name == path or name.startswith(path.rstrip('/') + '/') for path in binding['scope']['candidate_paths']):
                continue
            if item.get('type') == 'tree':
                continue  # Directory entries are represented by their complete descendant files.
            if name in related or item.get('type') != 'blob' or item.get('mode') != '100644':
                raise DeliveryPending('accepted_content_unverified')
            related[name] = item
        if set(related) != set(expected):
            raise DeliveryPending('accepted_content_unverified')  # Detect related additions, deletions and replacement types.
        for name, item in related.items():
            raw = github.get_blob(repository, item['sha'])
            if not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != expected[name]:
                raise DeliveryPending('accepted_content_unverified')  # Compare bytes as SHA256; never confuse Git blob SHA with it.


def qualify_ci(github, repository, config, sha):
    groups = {}  # Keep configured jobs from one workflow within one current attempt.
    for selector in config['required_ci']:
        required = {'type', 'workflow_id', 'workflow', 'event', 'job'}
        if (not required <= selector.keys() or not selector.keys() <= required | {'branch'}
                or selector['type'] != 'actions' or type(selector['workflow_id']) is not int or selector['workflow_id'] <= 0
                or not isinstance(selector['workflow'], str) or not isinstance(selector['job'], str) or not selector['job'].strip()
                or selector['event'] != 'push' or selector.get('branch', config['target_branch']) != config['target_branch']
                or not re.fullmatch(r'\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml', selector['workflow'])):
            raise DeliveryPending('required_ci_selector_unsupported')  # Generic Checks have no portable attempt/event semantics.
        key = (selector['workflow_id'], selector['workflow'])
        groups.setdefault(key, []).append(selector['job'])
    qualified, guards = [], []  # Final guards cover races caused while other configured workflows are read.
    for (workflow_id, path), names in groups.items():
        if len(names) != len(set(names)):
            raise DeliveryPending('required_ci_selector_ambiguous')
        workflow = github.get_workflow(repository, workflow_id)
        if type(workflow.get('id')) is not int or workflow['id'] != workflow_id or workflow.get('path') != path:
            raise DeliveryPending('required_ci_workflow_mismatch')
        def relevant(run, workflow_id=workflow_id):
            return (type(run.get('workflow_id')) is int and run['workflow_id'] == workflow_id and run.get('head_sha') == sha
                    and run.get('head_branch') == config['target_branch'] and run.get('event') == 'push')
        def latest(path=path, relevant=relevant):
            runs = [run for run in github.get_workflow_runs(repository, sha, config['target_branch'], 'push') if relevant(run)]
            if not runs or any(type(run.get('run_number')) is not int or run['run_number'] <= 0 for run in runs):
                raise DeliveryPending('required_ci_missing_or_ambiguous')
            newest = [run for run in runs if run['run_number'] == max(r['run_number'] for r in runs)]
            if len(newest) != 1 or newest[0].get('path') != path:
                raise DeliveryPending('required_ci_missing_or_ambiguous')
            return newest[0]  # Workflow run_number orders relevant executions; timestamps or response order do not.
        run = latest()
        if type(run.get('id')) is not int or type(run.get('run_attempt')) is not int or run['run_attempt'] <= 0:
            raise DeliveryPending('required_ci_attempt_unavailable')
        identity_fields = ('id', 'workflow_id', 'path', 'head_sha', 'head_branch', 'event', 'run_number', 'run_attempt', 'status', 'conclusion')
        def same(current, run=run):
            return all(current.get(key) == run.get(key) for key in identity_fields)
        current = github.get_workflow_run(repository, run['id'])
        if not same(current):
            raise DeliveryPending('required_ci_changed_during_read')  # List and current-run observations must agree.
        jobs = github.get_attempt_jobs(repository, run['id'], run['run_attempt'])  # Only configured jobs supply completion/success requirements; aggregate run state includes optional work.
        for name in names:
            found = [job for job in jobs if job.get('name') == name]
            if len(found) != 1:
                raise DeliveryPending('required_ci_missing_or_ambiguous')
            job = found[0]
            if (type(job.get('id')) is not int or job.get('run_id') != run['id'] or job.get('head_sha') != sha
                    or type(job.get('run_id')) is not int or ('run_attempt' in job and type(job['run_attempt']) is not int)
                    or job.get('run_attempt', run['run_attempt']) != run['run_attempt']
                    or job.get('status') != 'completed' or job.get('conclusion') != 'success'):
                raise DeliveryPending('required_ci_not_successful')
            qualified.append({'workflow_id': workflow_id, 'workflow': path, 'job': name, 'run_id': run['id'],
                              'run_attempt': run['run_attempt'], 'sha': sha,
                              'url': 'https://github.com/' + repository['full_name'] + '/actions/runs/' + str(run['id'])})
        if not same(github.get_workflow_run(repository, run['id'])) or not same(latest()):
            raise DeliveryPending('required_ci_changed_during_read')  # Do not combine jobs across a new attempt or newer relevant execution.
        guards.append((same, latest, run['id']))
    for same, latest, run_id in guards:
        if not same(github.get_workflow_run(repository, run_id)) or not same(latest()):
            raise DeliveryPending('required_ci_changed_during_read')  # A later workflow must not leave an earlier observation stale.
    return qualified


def qualify_delivery(github, repository, config, binding, state):
    try:
        with github.round(config['connection_reference']) if hasattr(github, 'round') else nullcontext():
            return _qualify_delivery(github, repository, config, binding, state)  # One deadline covers all related reads.
    except (KeyError, TypeError, ValueError, AttributeError):
        raise DeliveryPending('remote_material_invalid') from None  # Malformed remote data never escapes as a success or raw diagnostic.


def _qualify_delivery(github, repository, config, binding, state):
    pull = github.get_pull_request(repository, binding['final_pr']['number'])
    identity = ('id', 'number', 'repository_id', 'repository_full_name', 'type')  # Freeze stable PR identity, not a moving branch.
    if any(pull.get(key) != binding['final_pr'][key] for key in identity):
        raise DeliveryPending('final_pr_identity_changed')
    if not all(key in pull for key in ('merged', 'merged_at', 'base_ref', 'head_sha', 'merge_commit_sha')):
        raise DeliveryPending('final_delivery_unverified')  # Existing boundary clients lacking delivery facts remain pending.
    if pull['merged'] is not True or not pull['merged_at']:
        raise DeliveryPending('final_pr_not_merged')
    if pull['base_ref'] != config['target_branch']:
        raise DeliveryPending('final_pr_wrong_target_branch')
    if any(not isinstance(pull[key], str) or not re.fullmatch('[0-9a-f]{40}', pull[key]) for key in ('head_sha', 'merge_commit_sha')):
        raise DeliveryPending('final_pr_delivery_sha_unavailable')
    verify_contents(github, repository, binding, state, [pull['head_sha'], pull['merge_commit_sha']])
    ci = qualify_ci(github, repository, config, pull['merge_commit_sha'])
    final = github.get_pull_request(repository, binding['final_pr']['number'])
    if any(final.get(key) != pull.get(key) for key in (*identity, 'merged', 'merged_at', 'base_ref', 'head_sha', 'merge_commit_sha')):
        raise DeliveryPending('final_pr_changed_during_read')  # A force-push or corrected merge fact invalidates this observation.
    return {'status': 'pending', 'reason': 'delivery_qualified', 'delivery_eligible': True,
            'delivery': {'sha': pull['merge_commit_sha'], 'pr_head_sha': pull['head_sha'], 'pr_number': pull['number'],
                         'content_verified': True, 'ci': ci}}  # Eligibility is not remote synchronization or closure.
