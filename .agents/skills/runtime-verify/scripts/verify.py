"""Independent CLI business evidence using the existing command recorder."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import uuid

SCRIPTS = Path(__file__).resolve().parents[2] / 'develop-system/scripts'
sys.path.insert(0, str(SCRIPTS))
from record_check import run_record, now
from check_binding import source_inventory, relative, digest
from observe_python import python_invocation


def require(condition, message):
    if not condition:
        raise ValueError(message)


def redactor(plan):
    """Explicit secret values stay in process memory; no raw intermediate logs."""
    policy = plan.get('redaction', {})
    names = policy.get('secret_env', [])
    require(isinstance(names, list) and all(isinstance(n, str) for n in names), 'Explicit secret environment names required')
    http = any(isinstance(c, dict) and c.get('kind') == 'http' for c in plan.get('checks', []))
    header_names = []
    for check in plan.get('checks', []):
        if isinstance(check, dict) and check.get('kind') == 'http':
            for request in check.get('requests', []) + check.get('cleanup', {}).get('requests', []):
                header_names.extend(request.get('headers_env', {}).values())
                poll = request.get('poll')
                if isinstance(poll, dict) and isinstance(poll.get('headers_env'), dict):
                    header_names.extend(poll['headers_env'].values())
    values = [os.environ[n] for n in names + header_names if isinstance(n, str) and os.environ.get(n)]
    def clean(value):
        if isinstance(value, str):
            for secret in sorted(values, key=len, reverse=True):
                value = value.replace(secret, '[REDACTED]')
            value = re.sub(r'(?i)(bearer\s+)[^\s\"\']+', r'\1[REDACTED]', value)
            return re.sub(r'(?i)((?:token|password|secret|api[_-]?key)\s*[=:]\s*)[^\s&\"\']+', r'\1[REDACTED]', value)
        if isinstance(value, dict):
            result = {clean(k): clean(v) for k, v in value.items()}
            if http:
                from http_runtime import sensitive
                return sensitive(result)
            return result
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value
    return clean


def owned_file(cwd, name):
    path = relative(cwd, name)
    lexical = cwd / name
    require(not lexical.is_symlink() and not any(p.is_symlink() for p in lexical.parents), 'Output links are not owned')
    return path


def fresh_outputs(check, cwd):
    for assertion in check['assertions']:
        if assertion['kind'] in ('file_text', 'file_absent'):
            require(not owned_file(cwd, assertion['path']).exists(), 'File assertion requires a fresh output path')
    for name in check['cleanup']['remove_files']:
        path = owned_file(cwd, name)
        require(not path.exists() and not path.is_symlink(), 'Cleanup only owns new output files')


def validate_plan(plan):
    require(plan.get('schema_version') == 1, 'Unsupported runtime plan')
    for field in ('task', 'input_version', 'authorization_reference', 'environment'):
        require(isinstance(plan.get(field), str) and plan[field].strip(), 'Missing ' + field)
    require(Path(plan['root']).is_absolute(), 'Absolute candidate root required')
    require(type(plan.get('redaction', {}).get('omit_output')) is bool, 'Explicit output redaction policy required')
    for name in plan['redaction']['secret_env']:
        require(bool(os.environ.get(name)), 'Declared redaction secret unavailable')
    candidate = snapshot(plan)
    root = Path(candidate['root'])
    files = {item['path'] for item in candidate['files']}
    require(isinstance(plan.get('checks'), list) and plan['checks'], 'Explicit acceptance checks required')
    require(isinstance(plan.get('dependencies'), list), 'Declare real and simulated dependencies')
    for dep in plan['dependencies']:
        require(dep.get('mode') in ('real', 'simulated') and dep.get('evidence'), 'Dependency mode/evidence missing')
        require(type(dep.get('required')) is bool and type(dep.get('available')) is bool, 'Dependency applicability missing')
    ids = set()
    for check in plan['checks']:
        require(check.get('id') and check['id'] not in ids, 'Unique acceptance id required')
        ids.add(check['id'])
        require(type(check.get('required')) is bool, 'Explicit required flag needed')
        require(check['required'] or check.get('applicability_reason'), 'Optional check needs applicability reason')
        require(check.get('expectation_source'), 'Independent business expectation source required')
        require(check.get('behavior') in ('success', 'abnormal'), 'Behavior coverage missing')
        if check.get('kind') == 'http':
            from http_runtime import validate_http
            validate_http(check, root, files)
            continue
        require(check.get('kind', 'cli') == 'cli', 'Unsupported runtime kind')
        cwd = relative(root, check['cwd'])
        require(cwd.is_dir(), 'Working directory missing')
        entry = relative(root, check['entry'])
        require(check['entry'] in files and entry.is_file(), 'Entry must be in candidate')
        argv = check['argv']
        require(isinstance(argv, list) and argv and all(isinstance(v, str) and v for v in argv), 'Nonempty argv required')
        require(Path(argv[0]).is_absolute(), 'Resolve executable before verification')
        require(Path(argv[0]).suffix.lower() not in ('.cmd', '.bat'), 'Batch shell entry needs a dedicated adapter')
        position = check['entry_arg']
        require(type(position) is int and 0 <= position < len(argv), 'Entry argv position required')
        executable = Path(argv[0]).name.lower()
        if re.fullmatch(r'python(?:[0-9]+(?:\.[0-9]+)*)?(?:\.exe)?', executable):
            flags, target = python_invocation(argv)
            require(target[0] != '-m' and position == len(flags) + 1, 'Python file entry must be the executed script')
        elif executable in ('node', 'node.exe'):
            require(position == 1 and not argv[1].startswith('-'), 'Node file entry must immediately follow executable')
        else:
            require(position == 0, 'Unsupported interpreter: use a dedicated file-entry adapter')
        require((cwd / argv[position]).resolve() == entry, 'Actual entry/cwd differs from candidate')
        require(isinstance(check.get('timeout'), (float, int)) and 0 < check['timeout'] <= 3600, 'Bounded timeout required')
        require(type(check.get('expected_exit')) is int, 'Expected exit code required')
        require(isinstance(check.get('assertions'), list) and check['assertions'], 'Business assertions required')
        for assertion in check['assertions']:
            require(assertion.get('kind') in ('file_text', 'stdout_text', 'stderr_text', 'file_absent'), 'Unsupported assertion kind')
            if assertion['kind'] != 'file_absent':
                require(isinstance(assertion.get('equals'), str), 'Independent expected text required')
        require(check.get('cleanup', {}).get('reason'), 'Cleanup ownership/reason required')
        require(isinstance(check['cleanup'].get('remove_files'), list), 'Explicit cleanup files required')
        fresh_outputs(check, cwd)
    coverage = plan['coverage']
    require(set(coverage['success']) == {c['id'] for c in plan['checks'] if c['behavior'] == 'success'}, 'Success coverage mismatch')
    require(set(coverage['abnormal']) == {c['id'] for c in plan['checks'] if c['behavior'] == 'abnormal'}, 'Abnormal coverage mismatch')
    require(coverage['success'], 'Successful behavior required')
    require(coverage['abnormal'] or coverage.get('abnormal_not_applicable'), 'Explain abnormal behavior gap')
    require(isinstance(plan.get('scope_adjustments'), list), 'Preserve scope adjustment records')
    for adjustment in plan['scope_adjustments']:
        require(adjustment.get('action') == 'defer' and adjustment.get('user_reference') and adjustment.get('reason'), 'Explicit user deferral reference required')
        require(adjustment.get('check_ids') and set(adjustment['check_ids']) <= ids, 'Deferral must identify original checks')
    return candidate


def snapshot(plan):
    root = Path(plan['root']).resolve(strict=True)
    ranges = plan['candidate']
    files = source_inventory(root, [item['path'] for item in ranges], ranges)
    try:
        head = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'],
                                       stderr=subprocess.PIPE, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        head = None
    return {'root': str(root), 'git_head': head, 'files': list(files.values()),
            'candidate_id': digest(files)}


def verify(plan, output, previous=None):
    from reuse import premises, observe, reusable, reference
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    candidate = None
    results = []
    blockers = []
    clean = redactor(plan)
    try:
        candidate = validate_plan(plan)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        blockers.append(str(exc))
    root = Path(candidate['root']) if candidate else None
    for index, check in enumerate(plan['checks'] if not blockers else []):
        deferred = any(check['id'] in a['check_ids'] for a in plan['scope_adjustments'])
        if deferred:
            results.append({'id': check['id'], 'required': check['required'], 'effective_required': False,
                            'status': 'not_run', 'execution_status': 'not_run', 'reason': 'explicit user deferral'})
            continue
        try:
            require(all(not d['required'] or (d['available'] and d['mode'] == 'real') for d in plan['dependencies']),
                    'Required real dependency unavailable')
            observation = observe(plan, check, output, index, clean)
            current_premises = premises(plan, check, candidate)
            origin = reusable(previous, plan, check, candidate)
            if observation and observation['status'] != 'passed':
                raise ValueError('Environment requalification failed: ' + check['id'])
            if origin:
                old = json.loads(Path(origin['result']['path']).read_text(encoding='utf-8'))
                original = next(c for c in old['checks'] if c['id'] == check['id'])
                results.append({**{k: v for k, v in original.items() if k not in ('record', 'record_reference')},
                    'execution_status': 'reused', 'original': origin, 'premises': current_premises,
                    'environment_observation': observation})
                continue
        except (OSError, ValueError, KeyError, TypeError) as exc:
            blockers.append(str(exc))
            results.append({'id': check['id'], 'required': check['required'], 'effective_required': check['required'],
                            'status': 'blocked', 'execution_status': 'not_run', 'error': str(exc)})
            continue
        if check.get('kind') == 'http':
            from http_runtime import run_http
            result = run_http(check, root, candidate, clean)
            name = f'http-{index}.json'
            (output / name).write_text(json.dumps(clean(result), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            results.append({**result, 'record': name, 'record_reference': reference(output / name),
                            'premises': current_premises, 'environment_observation': observation})
            continue
        cwd = relative(root, check['cwd'])
        try:
            fresh_outputs(check, cwd)
        except (OSError, ValueError) as exc:
            results.append({'id': check['id'], 'required': check['required'], 'effective_required': check['required'],
                            'status': 'blocked', 'execution_status': 'not_run', 'error': str(exc),
                            'cleanup': {'status': 'not_run', 'reason': 'no output ownership acquired'}})
            continue
        assertions = []
        def collect(record):
            for assertion in check['assertions']:
                error = None
                evidence = {}
                try:
                    path = owned_file(cwd, assertion['path']) if assertion['kind'] in ('file_text', 'file_absent') else None
                    if assertion['kind'] == 'file_absent':
                        actual = not path.exists()
                        evidence['actual_exists'] = not actual
                    else:
                        actual = (path.read_text(encoding='utf-8') if path.is_file() else None) if path else record['stderr' if assertion['kind'] == 'stderr_text' else 'stdout']
                        if actual is not None:
                            evidence['actual_sha256'] = hashlib.sha256(clean(actual).encode('utf-8')).hexdigest()
                            evidence['digest_scope'] = 'redacted UTF-8 text'
                except (OSError, ValueError) as exc:
                    actual, error = None, str(exc)
                assertions.append({'kind': assertion['kind'], 'path': assertion.get('path'),
                                   'status': 'passed' if error is None and actual == (True if assertion['kind'] == 'file_absent' else assertion['equals']) else 'failed',
                                   'error': error, **evidence})
            if plan['redaction']['omit_output']:
                record.update(stdout='[omitted]', stderr='[omitted]')
            return clean(record)
        name = f'command-{index}.json'
        cleanup_errors = []
        record = {'exit_code': None, 'error': 'recorder did not finish'}
        try:
            record = run_record(check['argv'], cwd, output / name, check['timeout'], before_persist=collect)
        except (OSError, ValueError) as exc:
            record['error'] = str(exc)
        finally:
            for name_to_remove in check['cleanup']['remove_files']:
                try:
                    owned_file(cwd, name_to_remove).unlink(missing_ok=True)
                except (OSError, ValueError) as exc:
                    cleanup_errors.append({'path': name_to_remove, 'error': str(exc)})
        passed = record['exit_code'] == check['expected_exit'] and record['error'] is None and bool(assertions) and all(a['status'] == 'passed' for a in assertions) and not cleanup_errors
        results.append({'id': check['id'], 'required': check['required'], 'status': 'passed' if passed else 'failed',
                        'effective_required': check['required'],
                        'execution_status': 'executed' if record['exit_code'] is not None else 'failed',
                        'exit_code': record['exit_code'], 'error': record['error'],
                        'record': name, 'assertions': assertions,
                        'record_reference': reference(output / name) if (output / name).exists() else None,
                        'premises': current_premises, 'environment_observation': observation,
                        'cleanup': {'status': 'failed' if cleanup_errors else 'passed', 'errors': cleanup_errors}})
    candidate_after = None
    if candidate:
        try:
            candidate_after = snapshot(plan)
            if candidate_after != candidate:
                blockers.append('Candidate changed during runtime verification')
        except (OSError, ValueError) as exc:
            blockers.append(str(exc))
    status = 'unaccepted'
    if not blockers and results and all(r['status'] == 'passed' for r in results if r['effective_required']):
        status = 'passed_with_adjustment' if plan['scope_adjustments'] else 'passed'
    result = {'schema_version': 2, 'context': 'independent', 'result_id': uuid.uuid4().hex,
              'created_at': now(), 'plan': plan, 'candidate': candidate, 'candidate_after': candidate_after,
              'checks': results, 'blockers': blockers, 'runtime_status': status}
    result = clean(result)
    (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--previous')
    args = parser.parse_args()
    result = verify(json.loads(Path(args.plan).read_text(encoding='utf-8')), args.output, args.previous)
    print(json.dumps({'result': str(Path(args.output).resolve() / 'result.json'), 'runtime_status': result['runtime_status']}))
    return 0 if result['runtime_status'] in ('passed', 'passed_with_adjustment') else 1


if __name__ == '__main__':
    raise SystemExit(main())
