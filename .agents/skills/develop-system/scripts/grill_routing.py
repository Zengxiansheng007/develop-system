"""Read-only interview decisions; no dispatch, model calls or state writes."""
import copy
import os
from pathlib import Path


def project_key(path):
    return os.path.normcase(str(Path(path).resolve()))


def decide_entry(context, candidates):
    """Require an exact goal/scope or an explained binding, never recency."""
    output = {'action':'independent', 'reason':'No matching active run',
              'source':'entry-context', 'input_version':context.get('input_version'),
              'run_id':None, 'frame_id':None}
    def decision(action, reason, run=None, frame=None):
        return {**output, 'action':action, 'reason':reason,
                'run_id':run.get('run_id') if run else None, 'frame_id':frame}
    if context.get('independent') is True:
        return decision('independent', 'Explicit independent instruction')
    binding = context.get('run_id') or (context.get('session_binding') or {}).get('run_id')
    frame_id = context.get('frame_id') or (context.get('session_binding') or {}).get('frame_id')
    project = project_key(context['project_root'])
    active = [c for c in candidates if c.get('project_root') and project_key(c['project_root']) == project
              and c.get('status') not in {'completed','cancelled'}]
    if binding:
        selected = [c for c in active if c.get('run_id') == binding]
        if len(selected) != 1:
            return decision('clarify', 'Explicit run binding is missing, closed or belongs to another project')
    else:
        selected = []
        uncertain = False
        matches = {m['run_id']:m for m in context.get('matches', [])}
        for candidate in active:
            exact = (candidate.get('goal') == context.get('goal') and context.get('goal')
                     and candidate.get('scope') == context.get('scope'))
            attestation = matches.get(candidate.get('run_id'))
            strong = (attestation and attestation.get('goal') == candidate.get('goal')
                      and attestation.get('scope') == candidate.get('scope')
                      and isinstance(attestation.get('reason'), str) and attestation['reason'].strip())
            if exact or strong:
                selected.append(candidate)
            elif attestation or (context.get('goal') and candidate.get('goal') and
                                (context['goal'] in candidate['goal'] or candidate['goal'] in context['goal'])):
                uncertain = True
        if uncertain or len(selected) > 1:
            return decision('clarify', 'Goal ownership is ambiguous or supported only by weak evidence')
        if not selected:
            return output
    run = selected[0]
    if (run.get('schema_version') != 3 or run.get('mode') != 'execute'
            or run.get('entrypoint') != 'develop-system' or run.get('controller_policy') != 'dispatch-only'
            or not isinstance(run.get('revision'), int) or not isinstance(run.get('frames'), list)):
        return decision('clarify', 'Candidate requires explicit migration or repair', run)
    if not frame_id and not context.get('parent_frame_id'):
        interviews = [f for f in run['frames'] if f.get('skill_id') == 'grill-with-docs' and f.get('status') == 'running']
        if len(interviews) > 1:
            return decision('clarify', 'Multiple active interview frames require explicit ownership', run)
        if len(interviews) == 1:
            frame_id = interviews[0]['frame_id']
    if frame_id:
        frame = next((f for f in run['frames'] if f.get('frame_id') == frame_id), None)
        if not frame or frame.get('status') not in {'running','waiting_user','blocked'}:
            return decision('clarify', 'Frame binding is missing or no longer active', run)
    status = run.get('status')
    if status == 'paused':
        return decision('resume' if context.get('resume_paused') is True else 'clarify',
                        'Paused run requires an explicit resume instruction', run, frame_id)
    if status in {'waiting_user','blocked'}:
        return decision('resume' if context.get('recovery_ready') is True else 'clarify',
                        'Check the original waiting or blocking condition before resuming', run, frame_id)
    if status != 'running':
        return decision('clarify', 'Candidate status cannot be inherited', run, frame_id)
    if run.get('evidence_current') is False:
        return decision('resume', 'Evidence changed; resume and revalidate before registration', run, frame_id)
    return decision('managed', 'Validated project and explicit goal ownership', run, frame_id)


def independent_finish(delivery, *, confirmed, choice=None):
    """A document confirmation is separate from a subsequent explicit route choice."""
    result = {'action':'await_document_confirmation', 'delivery':copy.deepcopy(delivery),
              'candidates':[], 'selected':None, 'reason':'Current document confirmation is required'}
    if confirmed is not True:
        return result
    required = ('document','version','confirmation_reference','checklist')
    if any(not delivery.get(key) for key in required):
        raise ValueError('Confirmed delivery needs a document, version, real confirmation reference and checklist')
    return {**result, 'action':'selected' if choice in {'to-spec','implement'} else 'await_route_choice',
            'candidates':['to-spec','implement'], 'selected':choice if choice in {'to-spec','implement'} else None,
            'reason':'to-spec records an implementation spec; implement executes sufficiently defined work. Recommendation is not selection.'}


DIRECT_CONDITIONS = ('single_task','scope_clear','constraints_clear','verification_sufficient','single_session')


def decide_root_route(delivery):
    """Use specialist declarations, preserving each reason instead of inferring size."""
    if any(delivery.get(key) is not True for key in ('confirmed','core_questions_resolved','helpers_resolved')):
        return {'action':'wait', 'skills':[], 'reason':'Interview completion conditions are unresolved'}
    conditions = copy.deepcopy(delivery.get('direct_conditions', {}))
    direct = all(isinstance(conditions.get(key), dict) and conditions[key].get('satisfied') is True
                 and isinstance(conditions[key].get('evidence'), str) and conditions[key]['evidence'].strip()
                 for key in DIRECT_CONDITIONS)
    return {'action':'route', 'skills':['implement'] if direct else ['to-spec','to-tickets'],
            'conditions':conditions, 'requirement_version':delivery['version'],
            'reason':'All five implementation conditions satisfied' if direct else 'Specification and dependency analysis required'}
