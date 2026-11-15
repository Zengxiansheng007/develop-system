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
        for name, path in request.get('capture', {}).items():
            require(re.fullmatch('[a-zA-Z][a-zA-Z0-9_]*', name) and name not in captures and path, 'Unique response ownership capture required')
            require(request['method'] == 'POST' and request.get('ownership_reason'), 'Resource capture requires create semantics and ownership reason')
            captures.add(name)
    cleanup_requests = cleanup.get('requests', [])
    require(isinstance(cleanup_requests, list) and len(cleanup_requests) <= 100, 'Explicit bounded cleanup operations required')
    for request in cleanup_requests:
        tokens = re.findall(r'\{([a-zA-Z][a-zA-Z0-9_]*)\}', request['path'])
        require(tokens and set(tokens) <= captures, 'Cleanup must address captured owned resource identifiers')
        validate_request(request)
    if service['mode'] == 'existing' and any(r['method'] != 'GET' for r in requests):
        require(cleanup_requests or cleanup.get('external_owner'), 'Existing-service writes need scoped cleanup or explicit external owner')


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


def sensitive(value):
    if isinstance(value, dict):
        def secret_key(key):
            return re.fullmatch(r'(?i)authorization|(?:set-)?cookie|password|secret|(?:access_|refresh_)?token|api.?key', str(key))
        protected_assertion = isinstance(value.get('path'), list) and any(secret_key(p) for p in value['path'])
        return {k: '[REDACTED]' if secret_key(k) or (protected_assertion and k in ('actual', 'equals')) else sensitive(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sensitive(v) for v in value]
    return value


def observe_request(origin, request, service, clean, timeout=None):
    response = request_once(origin, request, service, timeout)
    assertions = []
    for assertion in request['assertions']:
        try:
            actual = select(response.get('body'), assertion['path'])
            passed = actual == assertion['equals']
        except (KeyError, IndexError, TypeError):
            actual, passed = None, False
        if any(re.search(r'(?i)authorization|cookie|password|secret|token|api.?key', str(p)) for p in assertion['path']):
            actual = '[REDACTED]'
        assertions.append({'path': assertion['path'], 'actual': sensitive(actual), 'status': 'passed' if passed else 'failed'})
    passed = not response.get('error') and response.get('status') == request['status'] and all(a['status'] == 'passed' for a in assertions)
    return clean({'method': request['method'], 'path': request['path'], 'attempts': 1, 'http_status': response.get('status'),
                  'status': 'passed' if passed else 'failed', 'execution_status': 'executed' if 'status' in response else 'failed',
                  'outcome': 'unknown' if response.get('error') and request['method'] != 'GET' else 'observed',
                  'error': response.get('error'), 'assertions': assertions, 'response_policy': 'headers/raw body omitted; selected JSON assertions only'}), response


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
            observed, response = observe_request(origin, request, service, clean)
            result['requests'].append(observed)
            result['execution_status'] = observed['execution_status']
            if response.get('status') == request['status'] and not response.get('error'):
                for name, path in request.get('capture', {}).items():
                    value = select(response.get('body'), path)
                    require(type(value) in (str, int) and re.fullmatch('[A-Za-z0-9_-]+', str(value)), 'Unsafe resource identifier')
                    captures[name] = str(value)
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
