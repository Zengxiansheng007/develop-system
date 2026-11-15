"""Independent HTTP lifecycle. Bounded worker pattern adapted from issue_github.py.

No GitHub policy is changed. Python stdlib (PSF license), no third-party dependency.
"""
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


def require(condition, message):
    if not condition:
        raise ValueError(message)


def bounded(value, upper=3600):
    return type(value) in (int, float) and math.isfinite(value) and 0 < value <= upper


def path_ok(path):
    return isinstance(path, str) and path.startswith('/') and not path.startswith('//') and not any(c in path for c in '\r\n\\#')


def create_success(request, status):
    """Only completed, attested create operations can acquire cleanup ownership."""
    return (request.get('method') == 'POST' and request.get('status') in (200, 201)
            and status == request['status'] and bool(request.get('ownership_reason')))


def validate_http(check, root, files):
    from verify import owned_file, relative, python_invocation
    service, cleanup = check['service'], check['cleanup']
    require(service['mode'] in ('owned', 'existing'), 'Explicit service ownership required')
    origin = urlsplit(service['origin'])
    require(origin.scheme in ('http', 'https') and origin.hostname and not origin.username and not origin.password
            and not origin.path and not origin.query and not origin.fragment, 'Exact authorized origin required')
    require(origin.port is None or 0 < origin.port < 65536, 'Invalid origin port')
    for name in ('startup_timeout', 'ready_timeout', 'request_timeout', 'poll_interval'):
        require(bounded(service.get(name)), 'Bounded HTTP lifecycle budget required: ' + name)
    require(type(service.get('max_bytes')) is int and 0 < service['max_bytes'] <= 4 * 1024 * 1024, 'Bounded response size required')
    identity = service['identity']
    require(identity['source'] in files and identity.get('instance_id') and identity.get('provenance'), 'Candidate startup identity/provenance required')
    require(Path(identity['database']).is_absolute(), 'Explicit database identity required')
    require(path_ok(identity['path']), 'Invalid identity path')
    ready = service['ready']
    require(path_ok(ready['path']) and type(ready['status']) is int and 'json' in ready, 'Readiness status and body required')
    require(cleanup.get('reason') and isinstance(cleanup.get('remove_files'), list) and bounded(cleanup.get('timeout')), 'Owned cleanup and deadline required')
    if service['mode'] == 'owned':
        cwd = relative(root, service['cwd'])
        argv, position = service['argv'], service['entry_arg']
        require(cwd.is_dir() and service['entry'] in files, 'Actual service entry must be in candidate')
        require(isinstance(argv, list) and argv and all(isinstance(v, str) and v for v in argv), 'Explicit service argv required')
        require(Path(argv[0]).is_absolute() and Path(argv[0]).is_file(), 'Resolved service executable required')
        require(type(position) is int and 0 <= position < len(argv), 'Actual service entry position required')
        executable = Path(argv[0]).name.lower()
        if re.fullmatch(r'python(?:[0-9]+(?:\.[0-9]+)*)?(?:\.exe)?', executable):
            flags, target = python_invocation(argv)
            require(target[0] != '-m' and position == len(flags) + 1, 'Python file entry required')
        elif executable in ('node', 'node.exe'):
            require(position == 1 and not argv[1].startswith('-'), 'Node file entry required')
        else:
            require(position == 0 and Path(argv[0]).suffix.lower() not in ('.bat', '.cmd'), 'Unsupported service interpreter')
        require((cwd / argv[position]).resolve() == relative(root, service['entry']), 'Service argv differs from candidate entry')
        require(service['identity']['source'] == service['entry'], 'Startup source identity must bind actual entry')
        require(service.get('port_file') in cleanup['remove_files'] and origin.port is None, 'Owned service requires fresh port discovery file')
        require(Path(identity['database']).resolve() in {owned_file(cwd, n) for n in cleanup['remove_files']}, 'Owned database must have exact cleanup ownership')
        fresh_files(check, root)
    else:
        require(origin.port and type(identity.get('pid')) is int and identity['pid'] > 0, 'Existing service needs exact origin and process identity')
        require(not cleanup['remove_files'], 'Existing service never owns existing files')
        require(not any(k in service for k in ('argv', 'entry', 'port_file')), 'Existing mode cannot launch or acquire file ownership')
    requests = check['requests']
    require(isinstance(requests, list) and 0 < len(requests) <= 100, 'Explicit bounded business requests required')
    captures = set()
    for request in requests:
        validate_request(request)
        require(request['status'] != 202 or 'poll' in request, 'HTTP 202 acceptance requires terminal polling')
        if 'poll' in request:
            validate_poll(request)
        for name, path in request.get('capture', {}).items():
            require(re.fullmatch('[a-zA-Z][a-zA-Z0-9_]*', name) and name not in captures and path, 'Unique response ownership capture required')
            require(create_success(request, request['status']), 'Resource capture requires completed create success (200/201) and ownership reason')
            captures.add(name)
    cleanup_requests = cleanup.get('requests', [])
    require(isinstance(cleanup_requests, list) and len(cleanup_requests) <= 100, 'Explicit bounded cleanup operations required')
    for request in cleanup_requests:
        require('poll' not in request, 'Cleanup does not submit asynchronous operations')
        tokens = re.findall(r'\{([a-zA-Z][a-zA-Z0-9_]*)\}', request['path'])
        require(tokens and set(tokens) <= captures, 'Cleanup must address captured owned resource identifiers')
        validate_request(request)
    if service['mode'] == 'existing' and any(r['method'] != 'GET' for r in requests):
        require(cleanup_requests or cleanup.get('external_owner'), 'Existing-service writes need scoped cleanup or explicit external owner')


def validate_poll(request):
    poll = request['poll']
    require(isinstance(poll, dict), 'Explicit poll object required')
    require(request['method'] == 'POST' and request['status'] in (200, 201, 202), 'Polling requires successful operation acceptance')
    validate_request(poll)
    require(poll['method'] == 'GET' and 'json' not in poll and 'poll' not in poll and 'capture' not in poll, 'Polling is read-only GET')
    require(poll['path'].count('{correlation}') == 1 and
            not any(c in poll['path'].replace('{correlation}', '') for c in '{}'), 'Poll path must bind accepted correlation once')
    for path in (poll['correlation']['accepted_path'], poll['correlation']['response_path'], poll['state_path']):
        require(isinstance(path, list) and path and all(type(p) in (str, int) for p in path), 'Explicit correlation/state selectors required')
    require(not sensitive_correlation(poll) or not negative_poll_selector(request),
            'Sensitive async correlation requires nonnegative array selectors')
    require(not sensitive_async_capture(request), 'Sensitive async correlations cannot acquire cleanup captures')
    states = []
    for name in ('pending', 'success', 'failure'):
        values = poll[name]
        require(isinstance(values, list) and values and all(isinstance(v, str) and v for v in values), 'Explicit async state sets required')
        states.extend(values)
    require(len(states) == len(set(states)), 'Async states must be disjoint')
    require(bounded(poll.get('timeout')) and bounded(poll.get('interval')) and
            type(poll.get('max_attempts')) is int and 0 < poll['max_attempts'] <= 10000, 'Explicit bounded polling budgets required')


def validate_request(request):
    require(request.get('method') in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE') and path_ok(request.get('path')), 'Explicit supported HTTP method/path required')
    require(request.get('retry') == 'none', 'HTTP operations never implicitly retry')
    require(type(request.get('status')) is int and 100 <= request['status'] <= 599, 'Expected HTTP status required')
    require(isinstance(request.get('assertions'), list) and request['assertions'], 'Independent HTTP business assertions required')
    for assertion in request['assertions']:
        require(isinstance(assertion.get('path'), list) and all(type(p) in (str, int) for p in assertion['path']) and 'equals' in assertion, 'Explicit JSON assertion path/expectation required')
    require(isinstance(request.get('headers_env', {}), dict), 'HTTP headers must reference environment names')
    for name, variable in request.get('headers_env', {}).items():
        require(isinstance(name, str) and re.fullmatch('[A-Za-z0-9-]+', name) and isinstance(variable, str)
                and os.environ.get(variable) and not any(c in os.environ[variable] for c in '\r\n'), 'Declared header unavailable or invalid')


def fresh_files(check, root):
    from verify import owned_file, relative
    cwd = relative(root, check['service']['cwd'])
    for name in check['cleanup']['remove_files']:
        path = owned_file(cwd, name)
        require(not path.exists() and not path.is_symlink(), 'HTTP cleanup only owns fresh files')


def http_worker():
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    try:
        payload = json.loads(sys.stdin.buffer.read())
        request = Request(payload['url'], method=payload['method'], headers=payload['headers'],
                          data=json.dumps(payload['json']).encode('utf-8') if 'json' in payload else None)
        try:
            response = build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=payload['timeout'])
        except HTTPError as exc:
            response = exc
        with response:
            raw = response.read(payload['max_bytes'] + 1)
            status = response.getcode()
        if len(raw) > payload['max_bytes']:
            return {'error': 'http_response_too_large'}
        try:
            body = json.loads(raw) if raw else None
        except (ValueError, UnicodeError):
            return {'status': status, 'error': 'http_invalid_json'}
        return {'status': status, 'body': body, 'error': None}
    except TimeoutError:
        return {'error': 'http_request_deadline'}
    except Exception:
        return {'error': 'http_transport_error'}


def request_once(origin, request, service, timeout=None):
    started = time.monotonic()
    budget = min(service['request_timeout'], timeout) if timeout is not None else service['request_timeout']
    payload = {'url': origin + request['path'], 'method': request.get('method', 'GET'), 'headers': {'Content-Type': 'application/json'},
               'timeout': budget, 'max_bytes': service['max_bytes']}
    payload['headers'].update({k: os.environ[v] for k, v in request.get('headers_env', {}).items()})
    if 'json' in request:
        payload['json'] = request['json']
    process = None
    try:
        require(budget > 0, 'http_request_deadline')
        process = subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()), '--http-worker'],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        remaining = budget - (time.monotonic() - started)
        if remaining <= 0:
            raise subprocess.TimeoutExpired('http-worker', budget)
        raw, _ = process.communicate(json.dumps(payload).encode(), timeout=remaining)
        if process.returncode or len(raw) > service['max_bytes'] * 6 + 4096:
            return {'error': 'http_transport_error'}
        return json.loads(raw)
    except subprocess.TimeoutExpired:
        return {'error': 'http_request_deadline'}
    except (OSError, ValueError):
        return {'error': 'http_transport_error'}
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            try:
                process.communicate(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass


def select(value, path):
    for key in path:
        value = value[key]
    return value


def sensitive_selection(path):
    """Apply the same selection policy to observed values and echoed expectations."""
    return isinstance(path, list) and any(re.search(r'(?i)authorization|cookie|password|secret|token|api.?key', str(p)) for p in path)


def sensitive_correlation(poll):
    correlation = poll.get('correlation') if isinstance(poll, dict) else None
    return isinstance(correlation, dict) and any(sensitive_selection(correlation.get(k)) for k in ('accepted_path', 'response_path'))


def sensitive_async_capture(request):
    return isinstance(request, dict) and bool(request.get('capture')) and sensitive_correlation(request.get('poll'))


def negative_poll_selector(request):
    poll = request.get('poll')
    if not isinstance(poll, dict):
        return False
    correlation = poll.get('correlation')
    paths = [poll.get('state_path')]
    if isinstance(correlation, dict):
        paths.extend(correlation.get(k) for k in ('accepted_path', 'response_path'))
    for operation in (request, poll):
        if isinstance(operation.get('assertions'), list):
            paths.extend(a.get('path') for a in operation['assertions'] if isinstance(a, dict))
    return any(isinstance(path, list) and any(type(key) is int and key < 0 for key in path) for path in paths)


def mask_selected(value, path, selector):
    """Mask a known selected field, including inside ancestor object/array selections."""
    if not isinstance(path, list) or not isinstance(selector, list):
        return value
    if path[:len(selector)] == selector:
        return '[REDACTED]'
    if selector[:len(path)] != path:
        return value
    key = selector[len(path)]
    if isinstance(value, dict):
        return {k: mask_selected(v, path + [key], selector) if k == key else v for k, v in value.items()}
    if isinstance(value, list) and type(key) is int and -len(value) <= key < len(value):
        index = key if key >= 0 else len(value) + key
        return [mask_selected(v, path + [key], selector) if i == index else v for i, v in enumerate(value)]
    return value


def mask_selection_record(record, selector, redact_all=False):
    if not isinstance(record, dict):
        return record
    return {k: ('[REDACTED]' if redact_all else mask_selected(v, record.get('path'), selector))
            if k in ('actual', 'equals') else v for k, v in record.items()}


def sensitive(value):
    if isinstance(value, dict):
        def secret_key(key):
            return re.fullmatch(r'(?i)authorization|(?:set-)?cookie|password|secret|(?:access_|refresh_)?token|api.?key', str(key))
        protected_assertion = sensitive_selection(value.get('path'))
        result = {k: '[REDACTED]' if secret_key(k) or (protected_assertion and k in ('actual', 'equals')) else sensitive(v) for k, v in value.items()}
        poll = value.get('poll')
        if sensitive_correlation(poll):
            # Unsupported aliases/captures must not leak through the rejected plan's echo.
            redact_all = negative_poll_selector(value) or sensitive_async_capture(value)
            for target, selector in ((result, poll['correlation'].get('accepted_path')),
                                     (result['poll'], poll['correlation'].get('response_path'))):
                if isinstance(target.get('assertions'), list):
                    target['assertions'] = [mask_selection_record(a, selector, redact_all) for a in target['assertions']]
        requests = value.get('requests')
        cleanup = result.get('cleanup')
        if isinstance(requests, list) and any(sensitive_async_capture(r) for r in requests) and isinstance(cleanup, dict):
            if isinstance(cleanup.get('requests'), list):
                for request in cleanup['requests']:
                    if isinstance(request, dict) and isinstance(request.get('assertions'), list):
                        request['assertions'] = [mask_selection_record(a, None, True) for a in request['assertions']]
        return result
    if isinstance(value, list):
        return [sensitive(v) for v in value]
    return value


def observe_request(origin, request, service, clean, timeout=None, protected_selector=None, display_path=None):
    response = request_once(origin, request, service, timeout)
    assertions = []
    for assertion in request['assertions']:
        try:
            actual = select(response.get('body'), assertion['path'])
            passed = actual == assertion['equals']
        except (KeyError, IndexError, TypeError):
            actual, passed = None, False
        if sensitive_selection(assertion['path']):
            actual = '[REDACTED]'
        actual = mask_selected(actual, assertion['path'], protected_selector)
        assertions.append({'path': assertion['path'], 'actual': sensitive(actual), 'status': 'passed' if passed else 'failed'})
    passed = not response.get('error') and response.get('status') == request['status'] and all(a['status'] == 'passed' for a in assertions)
    return clean({'method': request['method'], 'path': display_path if display_path is not None else request['path'], 'attempts': 1, 'http_status': response.get('status'),
                  'status': 'passed' if passed else 'failed', 'execution_status': 'executed' if 'status' in response else 'failed',
                  'outcome': 'unknown' if response.get('error') and request['method'] != 'GET' else 'observed',
                  'error': response.get('error'), 'assertions': assertions, 'response_policy': 'headers/raw body omitted; selected JSON assertions only'}), response


def poll_terminal(origin, poll, accepted, service, clean):
    """Correlate read-only observations; acceptance never grants cleanup ownership."""
    result = {'status': 'failed', 'outcome': 'not_run', 'observations': [],
              'budget': {k: poll[k] for k in ('timeout', 'interval', 'max_attempts')}}
    started = time.monotonic()
    protected = sensitive_correlation(poll)
    try:
        value = select(accepted.get('body'), poll['correlation']['accepted_path'])
        require(type(value) in (str, int) and re.fullmatch('[A-Za-z0-9_-]+', str(value)), 'Unsafe or missing accepted correlation')
        result['accepted_correlation'] = {'path': poll['correlation']['accepted_path'], 'actual': '[REDACTED]' if protected else value}
        request = {**poll, 'path': poll['path'].replace('{correlation}', str(value))}
        deadline = started + poll['timeout']
        for _ in range(poll['max_attempts']):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                result['outcome'] = 'deadline'
                break
            observed, response = observe_request(origin, request, service, clean, remaining,
                protected_selector=poll['correlation']['response_path'] if protected else None,
                display_path=poll['path'] if protected else None)
            result['observations'].append(observed)
            if response.get('error') or response.get('status') != poll['status']:
                result['outcome'] = 'transport_failed'
                break
            actual = select(response.get('body'), poll['correlation']['response_path'])
            matched = type(actual) is type(value) and actual == value
            observed['correlation'] = {'path': poll['correlation']['response_path'], 'equals': '[REDACTED]' if protected else value,
                                       'actual': '[REDACTED]' if protected else actual, 'status': 'passed' if matched else 'failed'}
            if not matched:
                result['outcome'] = 'correlation_mismatch'
                break
            state = select(response.get('body'), poll['state_path'])
            observed['state'] = {'path': poll['state_path'], 'actual': state}
            if protected:
                observed['state'] = mask_selection_record(observed['state'], poll['correlation']['response_path'])
            if state in poll['success']:
                result['outcome'] = 'succeeded' if observed['status'] == 'passed' else 'business_failed'
                result['status'] = observed['status']
                break
            observed['assertions'] = []  # Success assertions apply only at successful terminal state.
            observed['status'] = 'failed'
            if state in poll['failure']:
                result['outcome'] = 'failed'
                break
            if state not in poll['pending']:
                result['outcome'] = 'unknown_state'
                break
            observed['status'] = 'pending'
            result['outcome'] = 'attempts_exhausted'
            time.sleep(min(poll['interval'], max(0, deadline - time.monotonic())))
        if result['outcome'] == 'attempts_exhausted' and time.monotonic() >= deadline:
            result['outcome'] = 'deadline'
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        result.update(outcome='invalid_observation', error=str(exc))
    result['elapsed_seconds'] = time.monotonic() - started
    return clean(sensitive(result))


def run_http(check, root, candidate, clean):
    from verify import owned_file, relative
    service, cleanup = check['service'], check['cleanup']
    result = {'kind': 'http', 'id': check['id'], 'required': check['required'], 'effective_required': check['required'],
              'status': 'failed', 'execution_status': 'not_run', 'requests': [], 'identity': [],
              'launch': {'mode': service['mode'], 'status': 'not_run', 'stdout': '[omitted]', 'stderr': '[omitted]'}}
    process, acquired, captures, origin = None, False, {}, service['origin']
    errors, cleanup_results = [], []
    cwd = relative(root, service['cwd']) if service['mode'] == 'owned' else root
    def identity(timeout=None):
        response = request_once(origin, {'path': service['identity']['path']}, service, timeout)
        expected = {k: service['identity'][k] for k in ('instance_id', 'database')}
        expected['source_sha256'] = next(f['sha256'] for f in candidate['files'] if f['path'] == service['identity']['source'])
        expected['pid'] = process.pid if process else service['identity']['pid']
        body = response.get('body')
        passed = response.get('status') == 200 and isinstance(body, dict) and all(body.get(k) == v for k, v in expected.items())
        result['identity'].append({'status': 'passed' if passed else 'failed', 'expected': expected,
                                   'actual': {k: body.get(k) for k in expected} if isinstance(body, dict) else None,
                                   'error': response.get('error')})
        require(passed, 'Service identity differs from candidate/instance/database/process')
    try:
        if service['mode'] == 'owned':
            fresh_files(check, root)
            process = subprocess.Popen(service['argv'], cwd=cwd, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            acquired = True
            result['launch'].update(status='started', pid=process.pid, argv=service['argv'], cwd=str(cwd))
            deadline = time.monotonic() + service['startup_timeout']
            discovery = owned_file(cwd, service['port_file'])
            while not discovery.is_file():
                require(process.poll() is None, 'Service exited before discovery')
                require(time.monotonic() < deadline, 'Service discovery deadline')
                time.sleep(min(service['poll_interval'], max(0, deadline - time.monotonic())))
            require(discovery.stat().st_size <= 4096, 'Oversized service discovery')
            port = json.loads(discovery.read_text(encoding='utf-8'))['port']
            require(type(port) is int and 0 < port < 65536, 'Invalid service discovery port')
            origin += ':' + str(port)
        else:
            result['launch']['status'] = 'reused'
        deadline = time.monotonic() + service['ready_timeout']
        probes = 0
        result['readiness'] = {'status': 'failed', 'probes': 0}
        while True:
            if process:
                require(process.poll() is None, 'Service exited before readiness')
            remaining = deadline - time.monotonic()
            require(remaining > 0, 'Service readiness deadline')
            response = request_once(origin, {'path': service['ready']['path']}, service, remaining)
            probes += 1
            result['readiness'].update(probes=probes, http_status=response.get('status'), error=response.get('error'))
            if response.get('status') == service['ready']['status'] and response.get('body') == service['ready']['json']:
                break
            time.sleep(min(service['poll_interval'], max(0, deadline - time.monotonic())))
        result['readiness'] = {'status': 'passed', 'probes': probes}
        identity()
        for request in check['requests']:
            poll = request.get('poll')
            observed, response = observe_request(origin, request, service, clean,
                protected_selector=poll['correlation']['accepted_path'] if sensitive_correlation(poll) else None)
            result['requests'].append(observed)
            result['execution_status'] = observed['execution_status']
            if create_success(request, response.get('status')) and not response.get('error'):
                for name, path in request.get('capture', {}).items():
                    value = select(response.get('body'), path)
                    require(type(value) in (str, int) and re.fullmatch('[A-Za-z0-9_-]+', str(value)), 'Unsafe resource identifier')
                    captures[name] = str(value)
            if 'poll' in request:
                if observed['status'] == 'passed':
                    observed['poll'] = poll_terminal(origin, request['poll'], response, service, clean)
                    if observed['poll']['status'] != 'passed':
                        observed['status'] = 'failed'
                else:
                    observed['poll'] = {'status': 'not_run', 'outcome': 'acceptance_failed', 'observations': []}
        identity()
        result['status'] = 'passed' if all(r['status'] == 'passed' for r in result['requests']) else 'failed'
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        result['error'] = str(exc)
    finally:
        deadline = time.monotonic() + cleanup['timeout']
        cleanup_identity = True
        if cleanup.get('requests') and captures:
            try:
                identity(max(0, deadline - time.monotonic()))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                cleanup_identity = False
                errors.append({'resource': 'captured resources', 'error': 'Cleanup identity unconfirmed; owned rows may remain'})
        for request in cleanup.get('requests', []):
            try:
                require(cleanup_identity, 'Cleanup identity changed; no mutation sent')
                path = request['path'].format(**captures)
                remaining = deadline - time.monotonic()
                require(remaining > 0, 'Cleanup deadline')
                observed, _ = observe_request(origin, {**request, 'path': path}, service, clean, remaining)
                cleanup_results.append(observed)
                require(observed['status'] == 'passed', 'Owned resource cleanup failed')
            except (OSError, ValueError, KeyError) as exc:
                errors.append({'resource': request['path'], 'error': str(exc)})
        if process is not None:
            try:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=max(0.01, deadline - time.monotonic()))
                result['launch']['exit_code'] = process.returncode
            except (OSError, subprocess.TimeoutExpired) as exc:
                errors.append({'pid': process.pid, 'error': type(exc).__name__, 'residual': process.poll() is None})
        if acquired:
            for name in cleanup['remove_files']:
                try:
                    owned_file(cwd, name).unlink(missing_ok=True)
                except (OSError, ValueError) as exc:
                    errors.append({'path': name, 'error': str(exc), 'residual': True})
        result['cleanup'] = {'status': 'failed' if errors else 'passed', 'errors': errors, 'requests': cleanup_results,
                             'process': 'owned' if acquired else 'existing preserved' if service['mode'] == 'existing' else 'not acquired',
                             'external_owner': cleanup.get('external_owner')}
        if errors:
            result['status'] = 'failed'
    return clean(sensitive(result))


if __name__ == '__main__' and sys.argv[1:] == ['--http-worker']:
    sys.stdout.write(json.dumps(http_worker()))
