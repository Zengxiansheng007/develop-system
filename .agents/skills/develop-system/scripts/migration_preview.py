"""Read-only migration descriptions. No state writer or adoption capability."""
import copy
import hashlib
import json
from pathlib import Path


def digest(content):
    return hashlib.sha256(content).hexdigest()


def target_binding(owner, registry_path):
    owner = Path(owner).resolve(strict=True)
    registry_path = Path(registry_path).resolve(strict=True)
    raw = registry_path.read_bytes()
    registry = json.loads(raw)
    if registry.get('schema_version') != 3:
        raise ValueError('Migration target requires registry v3')
    files = {str(registry_path): digest(raw)}
    pending = []

    def collect(root):
        remaining = [root]
        while remaining:
            path = remaining.pop()
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(root):
                raise ValueError('Target resource escapes its declared Skill package')
            if path.name == '__pycache__' or path.suffix == '.pyc':
                continue
            if path.is_dir():
                # Directory aliases could create cycles or silently expand the package.
                if resolved != path.absolute():
                    raise ValueError('Target package directory aliases are not supported')
                remaining.extend(path.iterdir())
            elif path.is_file():
                files[str(resolved)] = digest(resolved.read_bytes())

    collect(owner)
    for descriptor in registry.get('skills', []):
        path = (registry_path.parent / descriptor['path']).resolve()
        if not path.is_file():
            pending.append({'kind': 'target_skill', 'skill_id': descriptor['id'],
                            'reason': 'Selected target source is unavailable', 'path': str(path)})
        elif path.name == 'SKILL.md' and path.parent.is_relative_to(owner.parent):
            collect(path.parent)
        else:
            files[str(path)] = digest(path.read_bytes())
            pending.append({'kind': 'target_resources', 'skill_id': descriptor['id'],
                            'reason': 'Only the explicitly selected file is bound; package resources need qualification'})
    manifest = [{'path': path, 'sha256': sha} for path, sha in sorted(files.items())]
    return {'versions': {'state': 4, 'registry': 3, 'result': 1, 'check': 2},
            'engine': str(owner / 'scripts/workflow_state.py'),
            'registry': {'path': str(registry_path), 'sha256': digest(raw)},
            'scope': 'Loaded owner package and selected registry Skill packages; bytes only, not runtime qualification',
            'files': manifest, 'sha256': digest(json.dumps(manifest, sort_keys=True).encode('utf-8'))}, pending


def observe(reference):
    """Compare only against recorded identity; absent original digests stay unknown."""
    result = {'recorded': copy.deepcopy(reference), 'observation': 'unbound'}
    if not isinstance(reference, dict) or not reference.get('path') or not reference.get('sha256'):
        return result
    try:
        path = Path(reference['path'])
        if not path.is_absolute():
            return result
        content = path.read_bytes()
        result['observed_sha256'] = digest(content)
        result['observation'] = 'matches_recorded_bytes' if digest(content) == reference['sha256'] else 'changed'
    except (OSError, ValueError):
        result['observation'] = 'unavailable'
    return result


def historical_inventory(state, pending):
    def qualify(reference, kind, owner):
        item = observe(reference)
        if item['observation'] != 'matches_recorded_bytes':
            pending.append({'kind': kind, 'owner': owner, 'reason': item['observation']})
        return item

    engine = qualify(state.get('engine_binding'), 'historical_engine', state['run_id'])
    registry = qualify({'path': state.get('registry_path'), 'sha256': state.get('registry_sha256')},
                       'historical_registry', state['run_id'])
    frames = []
    for frame in state.get('frames', []):
        identity = frame['frame_id']
        original = {'path': frame.get('skill_path'), 'sha256': frame.get('skill_sha256')}
        entry = {'frame_id': identity, 'protocol_version': frame.get('protocol_version'),
                 'engine': qualify(frame.get('engine_binding', state.get('engine_binding')), 'historical_engine', identity),
                 'skill': qualify(original, 'historical_skill', identity),
                 'resources': [qualify(r, 'historical_resource', identity) for r in frame.get('resources', [])],
                 'checks': []}
        if 'resources' not in frame:
            pending.append({'kind': 'historical_resource', 'owner': identity, 'reason': 'Original resource closure not recorded'})
        for field, kind in [('completion_obligations', 'completion_obligations'),
                            ('worktree_binding', 'worktree'), ('protocol_version', 'frame_protocol')]:
            if field not in frame:
                pending.append({'kind': kind, 'owner': identity, 'reason': 'Absent in original frame; not satisfied by default'})
        for check in frame.get('checks', []):
            reference = check.get('record')
            observed = qualify(reference, 'legacy_check', identity)
            original_record = None
            if observed['observation'] == 'matches_recorded_bytes':
                try:
                    raw = Path(reference['path']).read_bytes()
                    if digest(raw) == reference['sha256']:
                        original_record = json.loads(raw)
                except (OSError, ValueError):
                    pass
            entry['checks'].append({'original_reference': copy.deepcopy(check), **observed,
                                    'original_record': original_record, 'qualification': 'pending'})
            pending.append({'kind': 'legacy_check', 'owner': identity,
                            'reason': 'Original execution identity retained; migration reuse requires separate qualification'})
        frames.append(entry)
    if not state.get('authorization'):
        pending.append({'kind': 'authorization', 'owner': state['run_id'], 'reason': 'No explicit original authorization field'})
    budget = state.get('budget')
    if budget is None or (isinstance(budget, dict) and any(v is None for v in budget.values())):
        pending.append({'kind': 'budget', 'owner': state['run_id'], 'reason': 'Unknown original budget remains unknown'})
    return {'engine': engine, 'registry': registry, 'frames': frames,
            'qualification': 'Observations only; matching bytes do not establish historical applicability or environment health'}


def evidence_inventory(state, pending):
    items = []

    def visit(value, location=''):
        if isinstance(value, list):
            for i, entry in enumerate(value):
                visit(entry, location + '/' + str(i))
        elif isinstance(value, dict):
            for key, entry in value.items():
                path = (location + '/' + key).lstrip('/')
                if key in {'inputs', 'artifacts', 'evidence'} and isinstance(entry, list):
                    for i, reference in enumerate(entry):
                        observed = observe(reference)
                        item = {'location': path + '/' + str(i), **observed}
                        items.append(item)
                        if observed['observation'] != 'matches_recorded_bytes':
                            pending.append({'kind': 'historical_evidence', 'owner': item['location'],
                                            'reason': observed['observation']})
                else:
                    visit(entry, path)
    visit(state)
    return items


def restriction_mapping(state, pending):
    frames = {f['frame_id']: f for f in state.get('frames', [])}
    steps = {s['step_id'] for s in state.get('plan', [])}

    def owner(frame_id):
        seen = set()
        while frame_id in frames and frame_id not in seen:
            seen.add(frame_id)
            frame = frames[frame_id]
            if frame.get('step_id') in steps:
                return frame['step_id']
            frame_id = frame.get('parent_frame_id')
        return None

    status = state.get('status')
    decision = state.get('next_decision', {})
    restrictions = []
    if decision.get('action') == 'missing_skill':
        step = decision.get('step_id')
        if step not in steps:
            step = owner(decision.get('parent_frame_id'))
        if step is not None and decision.get('reason'):
            restrictions.append({'kind': 'missing_skill', 'scope': 'local', 'step_id': step,
                                 'frame_id': decision.get('parent_frame_id'), 'basis': copy.deepcopy(decision)})
    for frame in frames.values():
        if frame.get('status') not in {'blocked', 'waiting_user', 'cancelled'} and not frame.get('findings'):
            continue
        step = owner(frame['frame_id'])
        scope = frame.get('block_scope', 'unknown') if step else 'unknown'
        if scope not in {'local', 'global'}:
            scope = 'unknown'
        restrictions.append({'kind': 'frame_result', 'scope': scope, 'step_id': step,
                             'frame_id': frame['frame_id'], 'basis': copy.deepcopy(frame)})
    # A local result does not explain an otherwise untraceable whole-run stop.
    if status == 'blocked' and decision.get('action') != 'missing_skill':
        restrictions.append({'kind': 'historical_hold', 'scope': 'unknown', 'basis': copy.deepcopy(decision)})
    if status == 'blocked' and not restrictions:
        restrictions.append({'kind': 'historical_hold', 'scope': 'unknown', 'basis': copy.deepcopy(decision)})
    for item in restrictions:
        if item['scope'] != 'local':
            pending.append({'kind': 'restriction_scope', 'owner': item.get('frame_id', state['run_id']),
                            'reason': 'Keep historical hold; current professional scope qualification required'})
    control = {'status': status if status in {'paused', 'completed', 'cancelled'} else 'running',
               'user_reason': decision.get('user_reason') if status == 'paused' else None}
    if status == 'paused' and not control['user_reason']:
        pending.append({'kind': 'pause_reason', 'owner': state['run_id'], 'reason': 'Paused state retained; original reason unknown'})
    if status not in {'running', 'blocked', 'paused', 'completed', 'cancelled'}:
        control['status'] = status
        pending.append({'kind': 'control', 'owner': state['run_id'], 'reason': 'Unknown original control state retained'})
    return {'original_status': status, 'control': control, 'restrictions': restrictions,
            'effect': 'Proposals only; do not apply or resume'}


def build_preview(state, raw, path, owner, target_registry):
    if state.get('schema_version') != 3:
        raise ValueError('Migration preview requires a v3 source')
    if not isinstance(state.get('revision'), int) or isinstance(state['revision'], bool):
        raise ValueError('Migration preview requires an actual source revision')
    target, pending = target_binding(owner, target_registry)
    historical = historical_inventory(state, pending)
    mapping = restriction_mapping(state, pending)
    evidence = evidence_inventory(state, pending)
    pending.append({'kind': 'migration_environment', 'owner': state['run_id'],
                    'reason': 'Before later adoption/reuse, requalify affected authority, source bindings and environment; bytes alone are insufficient'})
    for field in ('goal', 'plan', 'frames', 'acceptance', 'events'):
        if field not in state:
            pending.append({'kind': 'source_inventory', 'owner': field, 'reason': 'Original field absent; inventory is incomplete'})
    return {'preview_version': 1, 'action': 'preview_only',
            'source': {'path': str(path), 'run_id': state['run_id'], 'revision': state['revision'],
                       'sha256': digest(raw), 'versions': {'state': 3,
                           'registry': state.get('registry', {}).get('schema_version')}},
            'target': target, 'historical': historical, 'mapping': mapping,
            'inventory': {'snapshot': copy.deepcopy(state),
                          'goal': copy.deepcopy(state.get('goal')), 'plan': copy.deepcopy(state.get('plan')),
                          'frames': copy.deepcopy(state.get('frames')), 'acceptance': copy.deepcopy(state.get('acceptance')),
                          'authorization': copy.deepcopy(state.get('authorization')),
                          'budget': copy.deepcopy(state.get('budget')),
                          'events': copy.deepcopy(state.get('events')),
                          'evidence': evidence,
                          'in_flight': [copy.deepcopy(f) for f in state.get('frames', []) if f.get('status') == 'running']},
            'preservation': {'identity': 'Retain original run and frame identities',
                             'events': 'Retain original events without rewriting',
                             'acceptance': 'Retain historical acceptance as history, not new qualification',
                             'authorization': 'Preserve original scope; absent authority remains unknown',
                             'budget': 'Preserve all original counts and unknowns; never reset or replenish',
                             'in_flight': 'Keep original identities and contracts; later compatibility qualification required'},
            'pending': pending}
