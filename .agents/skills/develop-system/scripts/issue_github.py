"""Narrow GitHub REST adapter for fixed delivery reads and scoped Issue mutations."""
import base64
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

from issue_delivery import DeliveryPending


class BoundedHTTPTransport:
    """A disposable standard-library worker bounds DNS, TLS and slow streaming together."""
    def __call__(self, request, *, timeout, max_bytes):
        if request.get_method() not in {'GET', 'PATCH', 'DELETE'} or (request.get_method() != 'PATCH' and request.data is not None):
            raise DeliveryPending('http_method_not_supported')  # No comments, merges or unrestricted verbs enter this transport.
        started = time.monotonic()
        payload = json.dumps({'url': request.full_url, 'headers': dict(request.header_items()),
                              'method': request.get_method(), 'data': base64.b64encode(request.data).decode('ascii') if request.data is not None else None,
                              'timeout': timeout, 'max_bytes': max_bytes}).encode('utf-8')
        process = None
        try:
            process = subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()), '--http-worker'],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise subprocess.TimeoutExpired('http-worker', timeout)
            output, _ = process.communicate(payload, timeout=remaining)
            if process.returncode != 0 or len(output) > max_bytes * 2 + 32768:
                raise DeliveryPending('http_transport_error')
            response = json.loads(output.decode('utf-8'))
            if response.get('error'):
                raise DeliveryPending(response['error'])
            response['body'] = base64.b64decode(response['body'], validate=True)
            return response
        except subprocess.TimeoutExpired:
            raise DeliveryPending('http_request_deadline') from None
        except DeliveryPending:
            raise
        except (OSError, ValueError, TypeError, KeyError):
            raise DeliveryPending('http_transport_error') from None
        finally:
            if process is not None and process.poll() is None:
                process.kill()  # No worker survives a timed-out foreground request.
                try:
                    process.communicate(timeout=0.25)
                except subprocess.TimeoutExpired:
                    pass  # Cleanup has its own finite cap; it never starts another request.


def http_worker():
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None  # No credential-bearing request follows even a same-origin redirect implicitly.
    try:
        payload = json.loads(sys.stdin.buffer.read())
        request = Request(payload['url'], headers=payload['headers'], method=payload['method'],
                          data=base64.b64decode(payload['data'], validate=True) if payload['data'] is not None else None)
        try:
            response = build_opener(NoRedirect()).open(request, timeout=payload['timeout'])
        except HTTPError as error:
            response = error  # Keep status/limited headers, never exception text.
        with response:
            status = response.getcode()
            headers = {key.lower(): value for key, value in response.headers.items()
                       if key.lower() in {'link', 'retry-after', 'x-ratelimit-reset', 'x-ratelimit-remaining', 'x-ratelimit-resource'}}  # Preserve the safe resource name needed to qualify a shared primary pool.
            body = response.read(payload['max_bytes'] + 1) if status == 200 or status in {403, 429} else b''
        if len(body) > payload['max_bytes']:
            return {'error': 'http_response_too_large'}
        return {'status': status, 'headers': headers, 'body': base64.b64encode(body).decode('ascii')}
    except TimeoutError:
        return {'error': 'http_request_deadline'}  # Socket timeout and the parent wall deadline share one safe pending classification.
    except Exception:
        return {'error': 'http_transport_error'}  # Worker stderr and exception details are never exported.


class GitHubReadAdapter:
    """Credentials stay in request memory; only safe reason codes leave this boundary."""
    def __init__(self, *, connection_reference, token_provider=None, transport=None,
                 request_timeout=10, round_timeout=60, max_requests=100, max_pages=10, max_bytes=4 * 1024 * 1024,
                 clock=time.monotonic, wall_clock=time.time):
        for value in (request_timeout, round_timeout):
            if type(value) not in (float, int) or not math.isfinite(value) or value <= 0:
                raise ValueError('Positive finite HTTP time limits required')
        for value in (max_requests, max_pages, max_bytes):
            if type(value) is not int or value <= 0:
                raise ValueError('Positive finite HTTP capacity limits required')
        self.connection_reference, self.token_provider = connection_reference, token_provider
        self.transport = transport if transport is not None else BoundedHTTPTransport()  # Default reads use real bounded HTTP.
        self.request_timeout, self.round_timeout = request_timeout, round_timeout
        self.max_requests, self.max_pages, self.max_bytes = max_requests, max_pages, max_bytes
        self.clock, self.wall_clock = clock, wall_clock
        self.deadline = None

    @contextmanager
    def round(self, connection_reference):
        if self.deadline is not None:
            raise DeliveryPending('http_round_already_active')  # One serial round never silently resets its budget.
        if connection_reference != self.connection_reference or not callable(self.token_provider):
            raise DeliveryPending('connection_unavailable')
        try:
            token = self.token_provider(connection_reference)
        except Exception:
            raise DeliveryPending('connection_unavailable') from None  # Provider errors may contain secret store details.
        if not isinstance(token, str) or not token or any(c in token for c in '\r\n'):
            raise DeliveryPending('connection_unavailable')
        self.deadline, self.requests, self.repositories, self.token = self.clock() + self.round_timeout, 0, set(), token
        try:
            yield
        finally:
            self.token, self.deadline = None, None  # Do not retain a credential after an operation.

    def _request(self, url, *, method='GET', data=None):
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise DeliveryPending('http_round_deadline')
        if self.requests >= self.max_requests:
            raise DeliveryPending('http_request_budget_exhausted')
        self.requests += 1
        headers = {'Authorization': 'Bearer ' + self.token, 'Accept': 'application/vnd.github+json',
                   'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'develop-system'}
        if data is not None:
            headers['Content-Type'] = 'application/json; charset=utf-8'  # Explicit UTF-8 preserves exact original Markdown.
        request = Request(url, headers=headers, method=method, data=data)
        if self.transport is None:
            raise DeliveryPending('connection_transport_unavailable')
        try:
            response = self.transport(request, timeout=min(self.request_timeout, remaining), max_bytes=self.max_bytes)
        except DeliveryPending:
            raise
        except Exception:
            raise DeliveryPending('http_transport_error') from None  # Never persist raw exceptions, headers or response bodies.
        if self.clock() >= self.deadline:
            raise DeliveryPending('http_round_deadline')
        status, body = response.get('status'), response.get('body')
        headers = {str(k).lower(): str(v) for k, v in response.get('headers', {}).items()}
        if status != 200:
            reason = {401: 'connection_unauthorized', 403: 'permission_denied', 404: 'remote_material_missing',
                      410: 'remote_material_gone', 422: 'remote_request_rejected', 429: 'rate_limited'}.get(status, 'remote_service_unavailable')
            rate_message = isinstance(body, bytes) and len(body) <= self.max_bytes and b'rate limit' in body.lower()
            if status == 429 or (status == 403 and (headers.get('x-ratelimit-remaining') == '0' or 'retry-after' in headers or rate_message)):
                now, waits = self.wall_clock(), []
                retry = self._retry_after(headers.get('retry-after'), now)
                if retry is not None:
                    waits.append(retry)  # Secondary limits remain binding with positive or absent primary allowance.
                resource = 'rest'
                if headers.get('x-ratelimit-remaining') == '0':
                    try:
                        reset = float(headers.get('x-ratelimit-reset', ''))
                        if math.isfinite(reset) and reset > now:
                            waits.append(reset)  # A healthy primary pool's reset cannot extend an unrelated secondary deadline.
                            if headers.get('x-ratelimit-resource') == 'core':
                                resource = 'core'
                    except ValueError:
                        pass  # Missing/malformed reset retains GitHub's finite conservative fallback.
                wait = max(waits) if waits else now + 60
                scope = {'host': 'api.github.com', 'connection_reference': self.connection_reference, 'resource': resource}  # REST requests share an explicit connection's service instruction, never inferred token aliases or other services.
                raise DeliveryPending('rate_limited', retry_not_before=wait, service_scope=scope)  # Record the absolute boundary; no sleep or retry loop.
            if status == 503:
                wait = self._retry_after(headers.get('retry-after'), self.wall_clock())
                if wait is not None:
                    raise DeliveryPending(reason, retry_not_before=wait, service_scope={'host': 'api.github.com',
                        'connection_reference': self.connection_reference, 'resource': 'rest'})  # RFC9110's explicit503 service instruction uses the same bounded foreground gate.
            if type(status) is int and 300 <= status < 400:
                reason = 'http_redirect_refused'
            raise DeliveryPending(reason)
        if not isinstance(body, bytes) or len(body) > self.max_bytes:
            raise DeliveryPending('http_response_too_large')
        try:
            value = json.loads(body.decode('utf-8'))
        except (UnicodeDecodeError, ValueError):
            raise DeliveryPending('remote_invalid_json') from None
        return value, headers

    @staticmethod
    def _retry_after(value, now):
        if not isinstance(value, str):
            return None
        try:
            value = value.strip()
            deadline = now + int(value) if re.fullmatch(r'[0-9]+', value) else parsedate_to_datetime(value).timestamp()  # RFC9110 permits integer delay-seconds or an HTTP date, not floats/exponents.
            return deadline if math.isfinite(deadline) and deadline > now else None
        except (ValueError, TypeError, OverflowError):
            return None  # Invalid, elapsed and nonfinite hints cannot create an unbounded or immediately bypassed rate wait.

    def _path(self, repository, suffix=''):
        if (not isinstance(repository, dict) or type(repository.get('id')) is not int or repository['id'] <= 0
                or not isinstance(repository.get('full_name'), str)
                or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository['full_name'])):
            raise DeliveryPending('repository_identity_unavailable')
        path = '/repos/' + '/'.join(quote(part, safe='') for part in repository['full_name'].split('/'))
        key = (repository['id'], repository['full_name'])
        if key not in self.repositories:
            actual, _ = self._request('https://api.github.com' + path)
            if (not isinstance(actual, dict) or type(actual.get('id')) is not int or actual['id'] != repository['id']
                    or not isinstance(actual.get('full_name'), str) or actual['full_name'].casefold() != repository['full_name'].casefold()):
                raise DeliveryPending('repository_identity_mismatch')
            self.repositories.add(key)
        return path + suffix

    def _get(self, repository, suffix, query=None):
        if self.deadline is None:
            with self.round(self.connection_reference):
                return self._get(repository, suffix, query)  # Standalone reads retain the same bounded operation contract.
        path = self._path(repository, suffix)
        value, _ = self._request('https://api.github.com' + path + ('?' + urlencode(query) if query else ''))
        if not isinstance(value, dict):
            raise DeliveryPending('remote_material_invalid')
        return value

    def _pages(self, repository, suffix, key, query=None, search=False):
        if self.deadline is None:
            with self.round(self.connection_reference):
                return self._pages(repository, suffix, key, query, search)
        path = self._path(repository, suffix)
        fixed = {str(k): str(v) for k, v in (query or {}).items()}
        fixed['per_page'] = '100'
        url = 'https://api.github.com' + path + '?' + urlencode(fixed)
        items, identifiers, total = [], set(), None
        for page in range(1, self.max_pages + 1):
            value, headers = self._request(url)
            if key is None:
                if not isinstance(value, list):
                    raise DeliveryPending('remote_pagination_incomplete')
                page_items = value  # Issue events use a bare array and Link, without an invented total_count.
            else:
                if (not isinstance(value, dict) or type(value.get('total_count')) is not int or value['total_count'] < 0
                        or not isinstance(value.get(key), list)):
                    raise DeliveryPending('remote_pagination_incomplete')
                if total is None:
                    total = value['total_count']
                if value['total_count'] != total or (search and total >= 1000):
                    raise DeliveryPending('remote_pagination_incomplete')  # GitHub's filtered Actions search cap cannot prove completeness.
                page_items = value[key]
            for item in page_items:
                if not isinstance(item, dict) or type(item.get('id')) is not int or item['id'] <= 0 or item['id'] in identifiers:
                    raise DeliveryPending('remote_pagination_incomplete')
                identifiers.add(item['id'])
                items.append(item)
            links = []
            for part in headers.get('link', '').split(',') if headers.get('link') else []:
                match = re.fullmatch(r'\s*<([^>]+)>\s*;\s*rel=(?:"([^"]+)"|([A-Za-z]+))\s*', part)
                if not match:
                    raise DeliveryPending('remote_pagination_incomplete')  # An unparsed Link can conceal a necessary next page.
                links.append((match.group(1), match.group(2) or match.group(3)))
            next_pages = [target for target, relation in links if relation == 'next']
            if len(next_pages) > 1:
                raise DeliveryPending('remote_pagination_incomplete')
            if not next_pages:
                if key is not None and len(items) != total:
                    raise DeliveryPending('remote_pagination_incomplete')
                return items
            candidate = urljoin(url, next_pages[0])
            parsed = urlsplit(candidate)
            parameters = parse_qs(parsed.query, keep_blank_values=True)
            expected = {k: [v] for k, v in fixed.items()} | {'page': [str(page + 1)]}
            if (parsed.scheme != 'https' or parsed.netloc != 'api.github.com' or parsed.path != path or parsed.fragment
                    or parameters != expected):
                raise DeliveryPending('remote_pagination_unauthorized')  # Reject origin, repository, endpoint and query widening before credentials.
            url = candidate
        raise DeliveryPending('http_page_budget_exhausted')

    def get_issue_events(self, repository, number):
        return self._pages(repository, '/issues/' + self._number(number) + '/events', None)  # The same finite origin/query/page guards cover native event arrays.

    def get_issue(self, repository, number):
        value = self._get(repository, '/issues/' + self._number(number))
        labels = value.get('labels')
        labels = ([label['name'] for label in labels] if isinstance(labels, list)
                  and all(isinstance(label, dict) and isinstance(label.get('name'), str) and label['name'] for label in labels) else None)
        return {'id': value.get('id'), 'number': value.get('number'), 'repository_id': repository['id'],
                'repository_full_name': repository['full_name'], 'type': 'pull_request' if 'pull_request' in value else 'issue',
                'body': value.get('body'), 'state': value.get('state'), 'state_reason': value.get('state_reason'),
                'labels': labels,  # Missing/malformed fields remain unknown instead of proving an empty remote set.
                'title': value.get('title'), 'assignees': value.get('assignees'), 'milestone': value.get('milestone')}

    def get_pull_request(self, repository, number):
        value = self._get(repository, '/pulls/' + self._number(number))
        base = value.get('base') or {}
        target = base.get('repo') or {}
        return {'id': value.get('id'), 'number': value.get('number'), 'repository_id': target.get('id'),
                'repository_full_name': target.get('full_name'), 'type': 'pull_request', 'base_ref': base.get('ref'),
                'merged': value.get('merged'), 'merged_at': value.get('merged_at'),
                'head_sha': (value.get('head') or {}).get('sha'), 'merge_commit_sha': value.get('merge_commit_sha'),
                'body': value.get('body')}  # Observe native-closing conflicts without editing or exporting the author's description.

    def get_commit_tree(self, repository, sha):
        value = self._get(repository, '/commits/' + self._sha(sha))
        if value.get('sha') != sha:
            raise DeliveryPending('remote_commit_mismatch')
        tree_sha = self._sha((value.get('commit') or {}).get('tree', {}).get('sha'))
        tree = self._get(repository, '/git/trees/' + tree_sha, {'recursive': '1'})
        if tree.get('sha') != tree_sha:
            raise DeliveryPending('remote_commit_mismatch')
        return tree

    def get_blob(self, repository, sha):
        value = self._get(repository, '/git/blobs/' + self._sha(sha))
        try:
            if value.get('sha') != sha or value.get('encoding') != 'base64' or not isinstance(value.get('content'), str):
                raise ValueError('Unsupported blob representation')
            raw = base64.b64decode(''.join(value['content'].split()), validate=True)
            if type(value.get('size')) is not int or value['size'] != len(raw) or len(raw) > self.max_bytes:
                raise ValueError('Unexpected blob size')
            if hashlib.sha1(b'blob ' + str(len(raw)).encode('ascii') + b'\0' + raw).hexdigest() != sha:
                raise ValueError('Unexpected Git blob identity')
            return raw
        except (ValueError, TypeError):
            raise DeliveryPending('remote_blob_invalid') from None

    def get_workflow(self, repository, workflow_id):
        return self._get(repository, '/actions/workflows/' + self._number(workflow_id))

    def get_workflow_runs(self, repository, sha, branch, event):
        return self._pages(repository, '/actions/runs', 'workflow_runs', {'head_sha': self._sha(sha), 'branch': branch, 'event': event}, search=True)

    def get_workflow_run(self, repository, run_id):
        return self._get(repository, '/actions/runs/' + self._number(run_id))

    def get_attempt_jobs(self, repository, run_id, attempt):
        return self._pages(repository, '/actions/runs/' + self._number(run_id) + '/attempts/' + self._number(attempt) + '/jobs', 'jobs')

    @staticmethod
    def _number(number):
        if type(number) is not int or number <= 0:
            raise DeliveryPending('remote_identity_invalid')
        return str(number)

    @staticmethod
    def _sha(sha):
        if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha):
            raise DeliveryPending('remote_commit_mismatch')
        return sha


class GitHubIssueAdapter(GitHubReadAdapter):
    """Explicit Issue-write capability; existing read-only clients keep their original surface."""
    def _mutate(self, repository, number, *, method, value=None, label=None):
        if self.deadline is None:
            with self.round(self.connection_reference):
                return self._mutate(repository, number, method=method, value=value, label=label)
        suffix = '/issues/' + self._number(number)
        if label is not None:
            if not isinstance(label, str) or not label.strip():
                raise DeliveryPending('remote_label_invalid')
            suffix += '/labels/' + quote(label, safe='')  # One encoded name cannot become an endpoint or replace the label set.
        url = 'https://api.github.com' + self._path(repository, suffix)
        data = json.dumps(value, ensure_ascii=False).encode('utf-8') if value is not None else None
        result, _ = self._request(url, method=method, data=data)
        return result  # The caller independently reads actual Issue fields before confirming progress.

    def update_issue_body(self, repository, number, body):
        if not isinstance(body, str):
            raise DeliveryPending('remote_body_invalid')
        return self._mutate(repository, number, method='PATCH', value={'body': body})

    def remove_issue_label(self, repository, number, name):
        return self._mutate(repository, number, method='DELETE', label=name)

    def close_issue(self, repository, number):
        return self._mutate(repository, number, method='PATCH', value={'state': 'closed', 'state_reason': 'completed'})


if __name__ == '__main__' and sys.argv[1:] == ['--http-worker']:
    sys.stdout.write(json.dumps(http_worker()))  # Credentials arrive only on stdin, never argv or disk.
