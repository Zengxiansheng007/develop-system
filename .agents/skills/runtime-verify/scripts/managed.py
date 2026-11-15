"""Execute the runtime professional under an already dispatched task identity."""
import argparse
import json
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'develop-system/scripts'))
from workflow_state import RunStore, file_evidence, require_current
from runtime_contract import producer_snapshot, qualify, require, validate_execution_scope
from check_binding import source_inventory, validate_worktree
from verify import verify


def execute(store, frame_id, plan, output):
    state = store.read()
    require_current(state)
    frame = next(f for f in state['frames'] if f['frame_id'] == frame_id)
    require(state['status'] == 'running' and frame['status'] == 'running'
            and frame['skill_id'] == 'runtime-verify' and frame.get('runtime_contract'), 'Active managed runtime task required')
    require(Path(frame['skill_path']).resolve() == Path(__file__).resolve().parents[1] / 'SKILL.md', 'Execute selected runtime source')
    binding = frame['worktree_binding']
    require(binding, 'Original worktree binding required')
    validate_worktree(binding)
    require(Path(plan['root']).resolve() == Path(binding['worktree']['root']).resolve(), 'Wrong candidate worktree')
    validate_execution_scope(plan, binding)
    source_inventory(Path(plan['root']), binding['source_ranges'], plan['candidate'])
    require(plan['input_version'] == frame['input_version'] and plan['scope_adjustments'] == frame['runtime_contract']['scope_adjustments'], 'Wrong input or scope')
    producer = producer_snapshot(frame['skill_path'])
    result = verify(plan, output)
    output = Path(output).resolve()
    envelope = {'version': 1, 'context': 'managed-professional',
        'identity': {**{k: frame[k] for k in ('frame_id', 'parent_frame_id', 'input_version', 'skill_id')}, 'run_id': state['run_id']},
        'binding': binding, 'contract': frame['runtime_contract'], 'producer': producer,
        'result': file_evidence(str(output / 'result.json')),
        'records': [dict(file_evidence(str(output / r['record'])), name=r['record']) for r in result['checks'] if r.get('record')]}
    path = output / 'managed-envelope.json'
    path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    evidence = file_evidence(str(path))
    accepted = qualify(state, frame, evidence)
    criteria = frame['runtime_contract']['criteria']
    event = {'protocol_version': 1, 'event_id': uuid.uuid4().hex, **envelope['identity'],
        'mode': 'skill', 'execution': 'sequential', 'status': 'completed', 'return_to': 'develop-system',
        'completion_checked': True, 'artifacts': [evidence], 'checks': [], 'findings': [],
        'blockers': [] if set(criteria) == accepted else ['运行未验收: required runtime evidence failed or blocked'],
        'runtime_evidence': evidence,
        'acceptance_checks': [{'id': c, 'kind': 'technical', 'status': 'passed' if c in accepted else 'pending',
            'completion_checked': c in accepted, 'evidence': [evidence]} for c in criteria], 'resolved_findings': []}
    (output / 'return.json').write_text(json.dumps(event, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return event


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('project', 'run', 'frame', 'plan', 'output'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    event = execute(RunStore(args.project, args.run), args.frame,
                    json.loads(Path(args.plan).read_text(encoding='utf-8')), args.output)
    print(json.dumps({'return': str(Path(args.output).resolve() / 'return.json'), 'blockers': event['blockers']}))
    return 1 if event['blockers'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
