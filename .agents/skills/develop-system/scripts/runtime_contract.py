"""Versioned professional runtime artifact qualification, separate from checks."""
import json
from pathlib import Path

from check_binding import source_inventory, validate_worktree, validate_binding, relative, digest


def require(value, message):
    if not value:
        raise ValueError(message)


def validate_contract(contract):
    require(isinstance(contract, dict) and contract.get('version') == 1, 'Unsupported runtime contract')
    criteria = contract.get('criteria')
    require(isinstance(criteria, dict) and criteria, 'Explicit runtime criteria required')
    for key, checks in criteria.items():
        require(isinstance(key, str) and isinstance(checks, list) and checks
                and all(isinstance(c, str) and c for c in checks) and len(set(checks)) == len(checks), 'Named runtime coverage required')
    require(isinstance(contract.get('scope_adjustments'), list), 'Explicit scope adjustments required')
    for adjustment in contract['scope_adjustments']:
        require(adjustment.get('action') == 'defer' and adjustment.get('user_reference')
                and adjustment.get('reason') and adjustment.get('check_ids')
                and set(adjustment['check_ids']) <= {c for ids in criteria.values() for c in ids}, 'Scoped user decision required')
        from workflow_state import file_evidence
        require('sha256' in adjustment.get('decision_evidence', {}), 'Frozen actual user decision evidence required')
        file_evidence(adjustment['decision_evidence'])


def producer_snapshot(skill_path):
    root = Path(skill_path).resolve().parents[1]
    ranges = [{'path': 'runtime-verify', 'role': 'source'}] + [
        {'path': 'develop-system/scripts/' + name, 'role': 'dependency'}
        for name in ('runtime_contract.py', 'record_check.py', 'check_binding.py', 'observe_python.py')]
    files = source_inventory(root, [r['path'] for r in ranges], ranges)
    return {'root': str(root), 'files': list(files.values()), 'id': digest(files)}


def validate_execution_scope(plan, binding):
    """Target process locations retain the original dispatched execution authority."""
    root = Path(plan['root']).resolve()
    for check in plan['checks']:
        if check.get('kind') == 'http':
            service = check['service']
            if service['mode'] != 'owned':
                continue  # Existing service is observed, never launched in a new target cwd.
            cwd = service['cwd']
        else:
            cwd = check['cwd']
        validate_binding(binding, relative(root, cwd))


def qualify(state, frame, evidence):
    """Validate provenance/currentness and professional consistency, not business truth."""
    from workflow_state import file_evidence
    contract = frame['runtime_contract']
    validate_contract(contract)
    file_evidence(evidence)
    envelope = json.loads(Path(evidence['path']).read_text(encoding='utf-8'))
    require(envelope.get('version') == 1 and envelope.get('context') == 'managed-professional', 'Managed professional envelope required')
    expected = {k: frame[k] for k in ('frame_id', 'parent_frame_id', 'input_version', 'skill_id')}
    expected['run_id'] = state['run_id']
    require(envelope.get('identity') == expected and envelope.get('binding') == frame.get('worktree_binding'), 'Runtime identity/binding mismatch')
    require(envelope.get('contract') == contract, 'Runtime task coverage changed')
    binding = frame.get('worktree_binding')
    require(binding, 'Managed runtime needs original worktree binding')
    validate_worktree(binding)
    require(envelope.get('producer') == producer_snapshot(frame['skill_path']), 'Runtime producer changed')
    result_ref = file_evidence(envelope['result'])
    result = json.loads(Path(result_ref['path']).read_text(encoding='utf-8'))
    require(result.get('context') == 'independent' and result.get('schema_version') == 1, 'Original runtime artifact required')
    plan = result['plan']
    require(plan['input_version'] == frame['input_version'] and plan['scope_adjustments'] == contract['scope_adjustments'], 'Runtime input or scope adjustment mismatch')
    require(Path(plan['root']).resolve() == Path(binding['worktree']['root']).resolve(), 'Runtime candidate root mismatch')
    validate_execution_scope(plan, binding)
    candidate = result.get('candidate')
    if candidate:
        files = source_inventory(Path(plan['root']), binding['source_ranges'], plan['candidate'])
        require(candidate['files'] == list(files.values()) and candidate['candidate_id'] == digest(files)
                and result['candidate_after'] == candidate, 'Runtime candidate changed')
    references = {r['name']: file_evidence(r) for r in envelope['records']}
    for check in result['checks']:
        if check.get('record'):
            require(check['record'] in references, 'Missing runtime execution evidence')
            require(Path(references[check['record']]['path']) == Path(result_ref['path']).parent / check['record'], 'Runtime record identity mismatch')
    require(len({r['id'] for r in result['checks']}) == len(result['checks']), 'Duplicate runtime coverage')
    results = {r['id']: r for r in result['checks']}
    planned = {r['id']: r for r in plan['checks']}
    required = {c for ids in contract['criteria'].values() for c in ids}
    require(required <= planned.keys() and all(planned[c]['required'] for c in required), 'Missing required runtime coverage')
    if result['blockers'] or not candidate or result.get('runtime_status') not in {'passed', 'passed_with_adjustment'}:
        return set()
    deferred = {c for a in contract['scope_adjustments'] for c in a['check_ids']}
    def accepted(check_id):
        check = results.get(check_id, {})
        if check_id in deferred:
            return check.get('status') == 'not_run' and check.get('effective_required') is False
        if check.get('status') != 'passed' or not check.get('record'):
            return False
        record = json.loads(Path(references[check['record']]['path']).read_text(encoding='utf-8'))
        if planned[check_id].get('kind') == 'http':
            if {k: v for k, v in check.items() if k != 'record'} != record:
                return False
            if not check.get('identity') or not check.get('requests') or check.get('readiness', {}).get('status') != 'passed':
                return False
            if any(r.get('status') != 'passed' for r in check['identity'] + check['requests']):
                return False
        else:
            from record_check import validate_record
            validate_record(record)
            if (record.get('context') != 'independent' or record.get('exit_code') != planned[check_id]['expected_exit']
                    or record.get('error') is not None or check.get('execution_status') != 'executed'
                    or len(check.get('assertions', [])) != len(planned[check_id]['assertions'])):
                return False
        # Business assertions are professional facts; contradictory local result fields fail closed.
        if check.get('cleanup', {}).get('status') != 'passed':
            return False
        if any(a.get('status') != 'passed' for a in check.get('assertions', [])):
            return False
        return True
    if any(not accepted(c) for c, check in planned.items() if check['required']):
        return set()
    return {criterion for criterion, ids in contract['criteria'].items() if all(accepted(c) for c in ids)}
