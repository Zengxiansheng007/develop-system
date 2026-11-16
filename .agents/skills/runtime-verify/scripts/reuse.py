"""Direct original execution references and bounded environment observations."""
import hashlib
import json
from pathlib import Path

from check_binding import relative, digest
from record_check import run_record, validate_record, now


def reference(path):
    path = Path(path).resolve(strict=True)
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def read_reference(ref):
    if reference(ref['path']) != ref:
        raise ValueError('Original evidence changed')
    return json.loads(Path(ref['path']).read_text(encoding='utf-8'))


def premises(plan, check, candidate):
    from runtime_contract import producer_snapshot
    paths = check.get('candidate_paths')
    files = candidate['files']
    if paths is not None:
        if not isinstance(paths, list) or not paths or not set(paths) <= {f['path'] for f in files}:
            raise ValueError('Reuse scope must name actual candidate files')
        entry = check.get('entry', check.get('service', {}).get('identity', {}).get('source'))
        if entry and entry not in paths:
            raise ValueError('Reuse scope must include actual entry')
        files = [f for f in files if f['path'] in paths]
    executable = check.get('argv', check.get('service', {}).get('argv', []))
    executable_ref = reference(executable[0]) if executable and Path(executable[0]).is_file() else None
    return {'files': files, 'check': check, 'environment': plan['environment'], 'executable': executable_ref,
            'dependencies': plan['dependencies'], 'root': candidate['root'],
            'producer': producer_snapshot(Path(__file__).resolve().parents[1] / 'SKILL.md')}


def observe(plan, check, output, index, clean):
    probe = check.get('requalification')
    if not probe:
        return None
    if check.get('kind') == 'http':
        service = check['service']
        if service['mode'] == 'owned':
            return None  # Each owned lifecycle has a new process/database identity.
        from http_runtime import request_once
        from verify import snapshot
        if not probe.get('scope'):
            raise ValueError('HTTP requalification scope required')
        started = now()
        ready = request_once(service['origin'], {'path': service['ready']['path']}, service, service['ready_timeout'])
        identity = request_once(service['origin'], {'path': service['identity']['path']}, service, service['request_timeout'])
        expected = {k: service['identity'][k] for k in ('instance_id', 'database', 'pid')}
        expected['source_sha256'] = next(f['sha256'] for f in snapshot(plan)['files'] if f['path'] == service['identity']['source'])
        body = identity.get('body')
        json_matches = ready.get('body') == service['ready']['json']
        passed = (ready.get('status') == service['ready']['status'] and json_matches
                  and identity.get('status') == 200 and isinstance(body, dict)
                  and all(body.get(k) == v for k, v in expected.items()))
        record = clean({'context': 'http-requalification', 'schema_version': 2,
                        'started_at': started, 'finished_at': now(), 'origin': service['origin'],
                        'ready': {'status': ready.get('status'), 'json_matches': json_matches},
                        'identity': {'status': identity.get('status'),
                                     'fields': {k: body.get(k) for k in expected} if isinstance(body, dict) else {}},
                        'expected': expected,
                        'status': 'passed' if passed else 'failed'})
        path = output / f'environment-{index}.json'
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return {'record': reference(path), 'status': record['status'], 'scope': probe['scope']}
    argv = probe['argv']
    if (not isinstance(argv, list) or not argv or not Path(argv[0]).is_absolute()
            or not Path(argv[0]).is_file() or Path(argv[0]).suffix.lower() in ('.bat', '.cmd')
            or not probe.get('scope') or not isinstance(probe.get('stdout'), str)
            or not 0 < probe['timeout'] <= 300):
        raise ValueError('Explicit bounded environment probe required')
    cwd = relative(Path(plan['root']).resolve(strict=True), probe['cwd'])  # Match the canonical independent candidate scope.
    passed = False
    def collect(record):
        nonlocal passed
        passed = record['exit_code'] == 0 and not record['error'] and record['stdout'] == probe['stdout']
        if plan['redaction']['omit_output']:
            record.update(stdout='[omitted]', stderr='[omitted]')
        return clean(record)
    path = output / f'environment-{index}.json'
    run_record(argv, cwd, path, probe['timeout'], before_persist=collect)
    return {'record': reference(path), 'status': 'passed' if passed else 'failed', 'scope': probe['scope']}


def reusable(previous, plan, check, candidate, direct=False):
    if (not previous or not check.get('requalification')
            or (check.get('kind') == 'http' and check['service']['mode'] == 'owned')):
        return None
    ref = reference(previous)
    old = read_reference(ref)
    if old.get('context') != 'independent' or old.get('schema_version') != 2:
        return None
    item = next((c for c in old['checks'] if c['id'] == check['id']), None)
    if item and item.get('execution_status') == 'reused':
        if direct:
            raise ValueError('Reference-of-reference is not an original execution')
        origin = item['original']
        read_reference(origin['result'])
        resolved = reusable(origin['result']['path'], plan, check, candidate, direct=True)
        if resolved and resolved != origin:
            raise ValueError('Original reuse identity changed')
        return resolved
    if (not item or item.get('status') != 'passed' or item.get('execution_status') != 'executed'
            or item.get('premises') != premises(plan, check, candidate)
            or not item.get('environment_observation') or item['environment_observation']['status'] != 'passed'):
        return None
    scoped_files = item['premises']['files']
    scoped_paths = {f['path'] for f in scoped_files}
    for boundary in ('candidate', 'candidate_after'):
        snapshot = old.get(boundary)
        if (not snapshot or snapshot.get('root') != candidate['root']
                or [f for f in snapshot['files'] if f['path'] in scoped_paths] != scoped_files):
            return None  # Unrelated blockers do not erase this check's own integrity boundary.
    record = reference(Path(previous).parent / item['record'])
    read_reference(item['environment_observation']['record'])
    if record != item.get('record_reference'):
        raise ValueError('Original execution receipt changed')
    return {'version': 1, 'result': ref, 'result_id': old['result_id'], 'check_id': check['id'], 'record': record}


def validate_reuse(item, plan, check, candidate):
    origin = item['original']
    if origin.get('version') != 1 or origin.get('check_id') != check['id']:
        raise ValueError('Direct versioned original reference required')
    old = read_reference(origin['result'])
    if old['result_id'] != origin['result_id']:
        raise ValueError('Original execution identity changed')
    expected = reusable(origin['result']['path'], plan, check, candidate)
    if expected != origin:
        raise ValueError('Original check is no longer reusable')
    original = next(c for c in old['checks'] if c['id'] == check['id'])
    if item['premises'] != premises(plan, check, candidate):
        raise ValueError('Reuse premises changed')
    observation = item['environment_observation']
    record = read_reference(observation['record'])
    if check.get('kind') == 'http':
        service = check['service']
        body = record.get('identity', {}).get('fields')
        expected = {k: service['identity'][k] for k in ('instance_id', 'database', 'pid')}
        expected['source_sha256'] = next(f['sha256'] for f in candidate['files'] if f['path'] == service['identity']['source'])
        if (type(record.get('schema_version')) is not int or record['schema_version'] != 2
                or observation.get('status') != 'passed'
                or record.get('context') != 'http-requalification' or record.get('status') != 'passed'
                or record['origin'] != service['origin'] or record['expected'] != expected
                or record.get('ready') != {'status': service['ready']['status'], 'json_matches': True}
                or record.get('ready', {}).get('json_matches') is not True
                or record.get('identity') != {'status': 200, 'fields': expected}
                or not isinstance(body, dict)):
            raise ValueError('Current HTTP identity/readiness evidence missing')
        return original, record
    validate_record(record)
    probe = check['requalification']
    if (observation['status'] != 'passed' or record['status'] != 'passed'
            or record['command'] != probe['argv'] or record['execution_command'] != probe['argv']
            or record['cwd'] != str(relative(Path(plan['root']).resolve(strict=True), probe['cwd']))):  # Use the same scope as observation.
        raise ValueError('Current environment observation missing')
    return original, record
