"""Explicit old-contract retention and professional qualification; never executes old work."""
import copy
import json
from pathlib import Path


def bind(preview, declaration):
    from run_migration import bound_file
    if not isinstance(declaration, dict) or set(declaration) != {'contract', 'frame_ids', 'basis'}:
        raise ValueError('Compatibility requires an explicit contract, full frame inventory and basis')
    frames = preview['inventory']['snapshot']['frames']
    ids = declaration['frame_ids']
    if (declaration['contract'] != 'state-v3' or not isinstance(ids, list)
            or len(ids) != len(set(ids)) or set(ids) != {f['frame_id'] for f in frames}):
        raise ValueError('Only the exact reviewed v3 frame inventory can adopt compatibility')
    for frame in frames:
        if ('protocol_version' in frame or not all(frame.get(key) for key in
                ('frame_id', 'skill_id', 'input_version', 'skill_path', 'skill_sha256'))
                or frame.get('mode') != 'skill'):
            raise ValueError('Unsupported original frame contract; cannot infer compatibility')
    return {'contract': 'state-v3', 'frame_ids': list(ids), 'basis': bound_file(declaration['basis'])}


def inventory(preview):
    declaration = preview['adoption'].get('compatibility')
    if declaration is None:
        return None
    return {**copy.deepcopy(declaration),
        'frames': {f['frame_id']: copy.deepcopy(f) for f in preview['inventory']['snapshot']['frames']},
        'event_ids': [e['event_id'] for e in preview['inventory']['snapshot']['events']],
        'receipts': [], 'qualifications': []}


def original_frame(state, frame):
    compatibility = state.get('migration', {}).get('compatibility') or {}
    original = compatibility.get('frames', {}).get(frame.get('frame_id'))
    if not original or compatibility.get('contract') != 'state-v3':
        return None
    keys = ('frame_id', 'parent_frame_id', 'step_id', 'skill_id', 'input_version', 'mode', 'skill_path', 'skill_sha256')
    if any(original.get(key) != frame.get(key) for key in keys) or 'protocol_version' in frame:
        raise ValueError('Current frame no longer matches the preserved legacy identity')
    return original


def normalize_payload(state, result):
    """Match the original v3 file-reference representation without altering its payload."""
    from workflow_state import file_evidence
    from record_check import validate_record
    normalized = copy.deepcopy(result)
    normalized['artifacts'] = [file_evidence(a) for a in result['artifacts']]
    for check in normalized['checks']:
        if not isinstance(check, dict) or 'record' not in check:
            raise ValueError('Legacy check requires an original machine receipt')
        check['record'] = file_evidence(check['record'])
        record = json.loads(Path(check['record']['path']).read_text(encoding='utf-8'))
        actual = validate_record(record)
        if (record['schema_version'] != 1 or actual != check.get('status')
                or ('command' in check and check['command'] != record['command'])
                or not Path(record['cwd']).resolve().is_relative_to(Path(state['project_root']).resolve())):
            raise ValueError('Original v3 check contract or claimed execution does not match')
    for criterion in normalized.get('acceptance_checks', []):
        criterion['evidence'] = [file_evidence(e) for e in criterion.get('evidence', [])]
    if isinstance(normalized.get('grill_delivery'), dict) and 'document' in normalized['grill_delivery']:
        normalized['grill_delivery']['document'] = file_evidence(normalized['grill_delivery']['document'])
    return normalized


def receive(state, result):
    from codex_runtime import RETURN_STATUSES
    from run_migration import bound_file
    from workflow_state import evidence_is_current
    control = state['control']['status']
    if control not in {'running', 'paused'} or state['status'] != control:
        raise ValueError('Closed runs cannot receive legacy work')
    required = {'event_id', 'run_id', 'frame_id', 'parent_frame_id', 'skill_id', 'status', 'return_to',
        'completion_checked', 'artifacts', 'checks', 'findings', 'blockers', 'input_version', 'mode', 'execution'}
    if not required <= result.keys() or 'protocol_version' in result:
        raise ValueError('Incomplete or incorrect preserved v3 result contract')
    frame = next((f for f in state['frames'] if f['frame_id'] == result['frame_id']), None)
    if not frame or not original_frame(state, frame):
        raise ValueError('Only inventoried old frames can use compatibility reception')
    if (result['run_id'] != state['run_id'] or result['return_to'] != 'develop-system'
            or result['execution'] not in {'delegated', 'sequential'}
            or any(result[key] != frame[key] for key in ('skill_id', 'input_version', 'mode', 'parent_frame_id'))):
        raise ValueError('Legacy result identity does not match its original execution')
    if not isinstance(result['event_id'], str) or not result['event_id'] or result['status'] not in RETURN_STATUSES:
        raise ValueError('Invalid legacy event identity or status')
    if any(not isinstance(result[field], list) for field in ('artifacts', 'checks', 'findings', 'blockers')):
        raise ValueError('Legacy result fields must be arrays')
    compatibility = state['migration']['compatibility']
    if frame['status'] in {'superseded', 'stale', 'interrupted'}:
        raise ValueError('Legacy execution is no longer current')
    existing = next((r for r in compatibility['receipts'] if r['payload']['event_id'] == result['event_id']), None)
    if existing:
        repeated = normalize_payload(state, result) if existing.get('origin') == 'history' else result
        if existing['payload'] != repeated:
            raise ValueError('Conflicting duplicate legacy event')
        return state
    historical = next((e for e in state['events'] if e['event_id'] == result['event_id']), None)
    if historical:
        if result['event_id'] in compatibility['event_ids'] and normalize_payload(state, result) == historical:
            return state
        raise ValueError('Event identity already belongs to another accepted fact')
    if frame['status'] not in {'running', 'waiting_user', 'blocked'} or any(
            r['payload']['frame_id'] == frame['frame_id'] and r['status'] == 'pending' for r in compatibility['receipts']):
        raise ValueError('Stale execution or unresolved prior legacy return')
    parent = next((f for f in state['frames'] if f['frame_id'] == frame['parent_frame_id']), None)
    if frame['parent_frame_id'] and (not parent or parent['status'] != 'running'):
        raise ValueError('Legacy parent is missing or no longer active')
    if not all(evidence_is_current(e) for e in frame.get('inputs', [])):
        raise ValueError('Legacy execution input changed')
    for reference in state['migration']['historical']['files']:
        bound_file(reference)
    if result['status'] == 'completed' and result['completion_checked'] is not True:
        raise ValueError('Legacy specialist has not checked completion')
    if result['status'] == 'skipped' and (frame['required'] or not result.get('skip_reason')):
        raise ValueError('Required legacy work cannot be skipped')
    normalized = normalize_payload(state, result)
    if result['status'] == 'completed' and not normalized['artifacts'] and not normalized['checks']:
        raise ValueError('Legacy completion has no artifact or check')
    compatibility['receipts'].append({'payload': copy.deepcopy(result), 'normalized': normalized,
        'status': 'pending', 'reason': 'Identity, evidence and mandatory-call applicability need current professional qualification'})
    state['next_decision'] = ({'action': 'paused', 'user_reason': state['control']['user_reason']}
        if control == 'paused' else {'action': 'qualify_legacy', 'frame_id': frame['frame_id']})
    return state


def current_qualification(state, frame, visiting=()):
    from workflow_state import frame_is_current, evidence_is_current
    if not original_frame(state, frame) or frame['frame_id'] in visiting:
        return None
    values = state['migration']['compatibility']['qualifications']
    item = next((q for q in reversed(values) if q['frame_id'] == frame['frame_id']), None)
    if not item or item['status'] != 'qualified':
        return None
    source = next((f for f in state['frames'] if f['frame_id'] == item['source_frame_id']), None)
    if (not source or source['status'] != 'completed' or source.get('needs_assessment')
            or not frame_is_current(state, source, (*visiting, frame['frame_id']))
            or not all(evidence_is_current(e) for e in item['evidence'])):
        return None
    return item


def obligations(state, frame):
    item = current_qualification(state, frame)
    return item['conclusion']['obligations']['required_calls'] if item else None


def qualify(state, frame_id, source_frame_id):
    from workflow_state import file_evidence, frame_is_current
    if state['control']['status'] != 'running':
        raise ValueError('Legacy qualification requires explicit user continuation')
    frame = next((f for f in state['frames'] if f['frame_id'] == frame_id), None)
    if not frame or not original_frame(state, frame) or frame['status'] in {'superseded', 'stale', 'interrupted'}:
        raise ValueError('Only current inventoried work can receive legacy qualification')
    source = next((f for f in state['frames'] if f['frame_id'] == source_frame_id), None)
    event = next((e for e in reversed(state['events']) if e['frame_id'] == source_frame_id), None)
    if (not source or source_frame_id == frame_id or source['status'] != 'completed'
            or source.get('needs_assessment') or not frame_is_current(state, source)
            or not event or source['skill_id'] in {'develop-system', 'ask-matt'}):
        raise ValueError('Qualification requires a current completed professional source')
    conclusions = [q for q in event.get('legacy_verifications', []) if q.get('frame_id') == frame_id]
    if len(conclusions) != 1:
        raise ValueError('Professional result must name exactly this legacy execution')
    conclusion = copy.deepcopy(conclusions[0])
    if (conclusion.get('input_version') != frame['input_version']
            or conclusion.get('source_sha256') != state['migration']['source']['sha256']):
        raise ValueError('Qualification is not bound to the original input and migration source')
    identity, duties = conclusion.get('identity', {}), conclusion.get('obligations', {})
    if identity.get('status') not in {'verified', 'insufficient'} or duties.get('required_calls') is not None and not isinstance(duties['required_calls'], list):
        raise ValueError('Invalid professional identity or applicability conclusion')
    evidence = []
    for part in (identity, duties):
        if not part.get('evidence'):
            raise ValueError('Professional applicability needs explicit source evidence')
        for value in part['evidence']:
            ref = file_evidence(value)
            if ref not in event['artifacts']:
                raise ValueError('Qualification basis must be a professional returned artifact')
            evidence.append(ref)
    for duty in duties.get('required_calls') or []:
        if not isinstance(duty, dict) or set(duty) != {'skill_id', 'skill_path', 'skill_sha256'}:
            raise ValueError('Mandatory call must bind an actual Skill version')
        file_evidence({'path': duty['skill_path'], 'sha256': duty['skill_sha256']})
    compatibility = state['migration']['compatibility']
    previous = next((q for q in reversed(compatibility['qualifications']) if q['frame_id'] == frame_id), None)
    if previous and previous['source_frame_id'] == source_frame_id and previous['conclusion'] == conclusion:
        return None
    receipt = next((r for r in reversed(compatibility['receipts']) if r['payload']['frame_id'] == frame_id), None)
    if receipt is None:
        original_result = next((e for e in reversed(state['events']) if e['event_id'] in compatibility['event_ids']
            and e['frame_id'] == frame_id and e.get('input_version') == frame['input_version']), None)
        if original_result is not None:
            receipt = {'payload': copy.deepcopy(original_result), 'normalized': copy.deepcopy(original_result),
                'origin': 'history', 'status': 'pending'}
            compatibility['receipts'].append(receipt)
    if not receipt or receipt['status'] != 'pending':
        raise ValueError('No pending original return to qualify')
    # Revalidate the first receipt's hashes, never recapture the raw payload's paths.
    retained = normalize_payload(state, receipt['normalized'])
    evidence.extend(retained['artifacts'])
    evidence.extend(check['record'] for check in retained['checks'])
    for criterion in retained.get('acceptance_checks', []):
        evidence.extend(criterion.get('evidence', []))
    delivery = retained.get('grill_delivery')
    if isinstance(delivery, dict) and delivery.get('document'):
        evidence.append(delivery['document'])
    revalidation = []
    if identity['status'] != 'verified':
        revalidation.append({'frame_id': frame_id, 'kind': 'identity', 'reason': 'Original identity basis is insufficient'})
    if duties.get('required_calls') is None:
        revalidation.append({'frame_id': frame_id, 'kind': 'obligations', 'reason': 'Mandatory-call applicability remains unknown'})
    check_evidence = []
    if not revalidation:
        check_evidence, revalidation = qualify_checks(state, frame, receipt['normalized'], conclusion, event)
    evidence.extend(check_evidence)
    compatibility['qualifications'].append({'frame_id': frame_id, 'source_frame_id': source_frame_id,
        'conclusion': conclusion, 'evidence': evidence, 'status': 'pending' if revalidation else 'qualified',
        'revalidation': revalidation})
    if revalidation:
        state['next_decision'] = {'action': 'revalidate_legacy_candidate', 'frame_id': frame_id, 'checks': revalidation}
        return None
    receipt['status'] = 'qualified'
    if receipt.get('origin') == 'history':
        state['next_decision'] = {'action': 'reassess_parent_or_next', 'frame_id': frame_id}
        return None
    return retained


def qualify_checks(state, frame, returned, conclusion, event):
    from workflow_state import file_evidence
    from record_check import validate_record
    statements = conclusion.get('checks', [])
    if not isinstance(statements, list) or len(statements) != len(returned.get('checks', [])):
        raise ValueError('Every original check needs one explicit reuse or scoped revalidation conclusion')
    evidence, revalidation, seen = [], [], set()
    original = original_frame(state, frame)
    frozen_basis = original.get('inputs', []) + original.get('artifacts', [])
    loaded_basis = original.get('artifacts', []) + returned.get('artifacts', [])
    for check in returned.get('checks', []):
        reference = file_evidence(check['record'])
        record = json.loads(Path(reference['path']).read_text(encoding='utf-8'))
        matched = [s for s in statements if s.get('record') == reference and s.get('record_id') == record['record_id']]
        if len(matched) != 1 or record['record_id'] in seen:
            raise ValueError('Qualification must preserve the exact original check identity')
        seen.add(record['record_id'])
        statement = matched[0]
        if statement.get('decision') not in {'reuse', 'reverify'} or not statement.get('evidence'):
            raise ValueError('Historical check needs a sourced professional decision')
        for value in statement['evidence']:
            ref = file_evidence(value)
            if ref not in event['artifacts']:
                raise ValueError('Check qualification must cite the professional returned basis')
            evidence.append(ref)
        actual = validate_record(record)
        if record['schema_version'] != 1 or actual != check['status']:
            raise ValueError('Historical execution contract or status changed')
        evidence.append(reference)
        if statement['decision'] == 'reverify' or not all(statement.get(k) for k in ('frozen', 'loaded', 'candidate')):
            revalidation.append({'frame_id': frame['frame_id'], 'record': reference, 'record_id': record['record_id'],
                'reason': 'Original candidate/loading basis insufficient; revalidate only this affected execution scope'})
            continue
        if actual != 'passed':
            raise ValueError('Failed historical execution cannot support a reuse pass')
        frozen, loaded = file_evidence(statement['frozen']), file_evidence(statement['loaded'])
        if frozen not in frozen_basis or loaded not in loaded_basis:
            raise ValueError('Freeze/loading material must belong to the original frame, not be invented by the reviewer')
        frozen_files = json.loads(Path(frozen['path']).read_text(encoding='utf-8')).get('files')
        loaded_files = json.loads(Path(loaded['path']).read_text(encoding='utf-8')).get('files')
        if (not isinstance(frozen_files, list) or not frozen_files or not isinstance(loaded_files, list)
                or not loaded_files or statement['candidate'] != frozen_files
                or any(item not in frozen_files for item in loaded_files)):
            raise ValueError('Actual historical loading and candidate must agree with the original freeze')
        evidence.extend([frozen, loaded])
        evidence.extend(file_evidence(item) for item in frozen_files)
    return evidence, revalidation


def validate_check(state, frame, check, record, visiting=()):
    ancestry = visiting[:-1] if visiting[-1:] == (frame['frame_id'],) else visiting
    item = current_qualification(state, frame, ancestry)
    if not item or record.get('schema_version') != 1 or check.get('reference') is not None:
        raise ValueError('Legacy checks require a current original-executor qualification')
    matches = [s for s in item['conclusion']['checks'] if s.get('record') == check['record']
        and s.get('record_id') == record['record_id'] and s.get('decision') == 'reuse']
    if len(matches) != 1:
        raise ValueError('No qualification for this original execution record')
