"""Versioned professional runtime artifact qualification, separate from checks."""
import json
from datetime import datetime
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
        if check.get('requalification') and check.get('kind') != 'http':
            validate_binding(binding, relative(root, check['requalification']['cwd']))
        if check.get('kind') == 'http':
            service = check['service']
            if service['mode'] != 'owned':
                continue  # Existing service is observed, never launched in a new target cwd.
            cwd = service['cwd']
        else:
            cwd = check['cwd']
        validate_binding(binding, relative(root, cwd))


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    require(parsed.tzinfo is not None, 'Runtime timestamp needs a timezone')
    return parsed


def validate_entry(plan, operation, candidate):
    """Link declared argv to the named candidate entry, without a loaded-source claim."""
    cwd = relative(Path(plan['root']).resolve(), operation['cwd'])
    argv, position = operation['argv'], operation['entry_arg']
    require(isinstance(argv, list) and type(position) is int and 0 <= position < len(argv), 'Invalid runtime entry position')
    require(operation['entry'] in {f['path'] for f in candidate['files']}
            and (cwd / argv[position]).resolve() == relative(Path(plan['root']).resolve(), operation['entry']),
            'Runtime argv does not identify declared candidate entry')
    return cwd


def qualify(state, frame, evidence):
    """Validate provenance/currentness and professional consistency, not business truth."""
    from workflow_state import file_evidence
    contract = frame['runtime_contract']
    validate_contract(contract)
    file_evidence(evidence)
    envelope = json.loads(Path(evidence['path']).read_text(encoding='utf-8'))
    version = frame.get('runtime_evidence_version', 1)
    require(envelope.get('version') == version and version in (1, 2, 3)
            and envelope.get('context') == 'managed-professional', 'Dispatched professional envelope version required')
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
    require(result.get('context') == 'independent' and result.get('schema_version') in (1, 2), 'Original runtime artifact required')
    window = None
    if version >= 2:
        window = (timestamp(envelope['execution_window']['started_at']), timestamp(envelope['execution_window']['finished_at']))
        require(window[0] <= timestamp(result['created_at']) <= window[1], 'Runtime result lies outside invocation interval')
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
    require(len(references) == len(envelope['records']), 'Duplicate runtime record reference')
    used_records = [c['record'] for c in result['checks'] if c.get('record')]
    require(len(set(used_records)) == len(used_records) and set(used_records) == set(references), 'Runtime records must match executed checks exactly')
    receipt_ids = []
    for reference in references.values():
        recorded = json.loads(Path(reference['path']).read_text(encoding='utf-8'))
        if recorded.get('context') == 'independent':
            receipt_ids.append(recorded.get('record_id'))
    require(len(set(receipt_ids)) == len(receipt_ids), 'One CLI execution receipt cannot stand for multiple operations')
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
        reused = check.get('execution_status') == 'reused'
        check_window = window
        if reused:
            require(version == 3, 'Reuse requires professional envelope v3')
            import sys
            scripts = str(Path(frame['skill_path']).parent / 'scripts')
            if scripts not in sys.path:
                sys.path.insert(0, scripts)
            from reuse import validate_reuse
            original_ref = file_evidence(envelope['original_envelopes'][check_id])
            original_envelope = json.loads(Path(original_ref['path']).read_text(encoding='utf-8'))
            identity = original_envelope['identity']
            source = next((f for f in state['frames'] if f['frame_id'] == identity['frame_id']), None)
            require(source and source['frame_id'] != frame['frame_id']
                    and source.get('runtime_evidence') == original_ref
                    and identity['run_id'] == state['run_id']
                    and original_envelope['binding']['worktree'] == binding['worktree']
                    and original_envelope['result'] == check['original']['result'],
                    'Reuse must reference admitted original same-run execution')
            check, probe = validate_reuse(check, plan, planned[check_id], candidate)
            require(window[0] <= timestamp(probe['started_at']) <= timestamp(probe['finished_at']) <= window[1],
                    'Environment observation lies outside current invocation')
            check_window = (timestamp(original_envelope['execution_window']['started_at']),
                            timestamp(original_envelope['execution_window']['finished_at']))
            record = json.loads(Path(results[check_id]['original']['record']['path']).read_text(encoding='utf-8'))
        if check.get('status') != 'passed' or not check.get('record'):
            return False
        if not reused:
            record = json.loads(Path(references[check['record']]['path']).read_text(encoding='utf-8'))
        if planned[check_id].get('kind') == 'http':
            if {k: v for k, v in check.items() if k not in ('record', 'record_reference', 'premises', 'environment_observation')} != record:
                return False
            if not check.get('identity') or not check.get('requests') or check.get('readiness', {}).get('status') != 'passed':
                return False
            if any(r.get('status') != 'passed' for r in check['identity'] + check['requests']):
                return False
            service = planned[check_id]['service']
            require(check['launch']['mode'] == service['mode'], 'HTTP launch mode differs from plan')
            if service['mode'] == 'owned':
                cwd = validate_entry(plan, service, candidate)
                require(check['launch'].get('argv') == service['argv'] and check['launch'].get('cwd') == str(cwd),
                        'HTTP launch operation differs from plan')
            requests = planned[check_id]['requests']
            require(len(check['requests']) == len(requests) and all(
                observed.get('method') == requested['method'] and observed.get('path') == requested['path']
                for observed, requested in zip(check['requests'], requests)), 'HTTP business operations differ from plan')
        else:
            from record_check import validate_record
            validate_record(record)
            cwd = validate_entry(plan, planned[check_id], candidate)
            require(record.get('command') == planned[check_id]['argv']
                    and record.get('execution_command') == planned[check_id]['argv']
                    and record.get('cwd') == str(cwd), 'CLI receipt operation differs from planned entry')
            if check_window:
                require(check_window[0] <= timestamp(record['started_at']) <= timestamp(record['finished_at']) <= check_window[1],
                        'CLI receipt lies outside invocation interval')
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
