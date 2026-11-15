# Independent HTTP lifecycle v1

Use the same `verify.py --plan --output` entry and common fields from [the CLI contract](cli-contract.md). Each HTTP check has `kind: http`, acceptance `id`, `required`, `behavior`, `expectation_source`, `service`, ordered `requests`, and `cleanup`. Existing CLI plans are unchanged. The task must authorize the exact origin, requests, test data and cleanup. Run against a real test service and real required dependency; declare unrelated simulations separately. Transport success never establishes business success.

Example check (replace executable, absolute database and task-specific values):

```json
{
  "kind": "http", "id": "AC-write", "required": true, "behavior": "success",
  "expectation_source": "TASK: create alpha; subsequent read returns alpha/test",
  "service": {
    "mode": "owned", "entry": "server.py", "entry_arg": 2,
    "argv": ["C:/Python311/python.exe", "-B", "server.py", "--db", "data.db", "--port-file", "port.json", "--instance-id", "unique-task-instance"],
    "cwd": ".", "origin": "http://127.0.0.1", "port_file": "port.json",
    "identity": {"path": "/identity", "source": "server.py", "instance_id": "unique-task-instance",
                 "database": "C:/authorized/project/data.db", "provenance": "server captures source hash at startup; entry inspected"},
    "ready": {"path": "/health", "status": 200, "json": {"status": "ready"}},
    "startup_timeout": 10, "ready_timeout": 10, "request_timeout": 5,
    "poll_interval": 0.1, "max_bytes": 65536
  },
  "requests": [
    {"method": "POST", "path": "/items", "json": {"name": "alpha"}, "retry": "none", "status": 201,
     "assertions": [{"path": ["name"], "equals": "alpha"}]},
    {"method": "GET", "path": "/items/1", "retry": "none", "status": 200,
     "assertions": [{"path": ["name"], "equals": "alpha"}, {"path": ["region"], "equals": "test"}]}
  ],
  "cleanup": {"remove_files": ["data.db", "port.json"], "reason": "fresh task-owned test database/discovery", "timeout": 5}
}
```

## Service identity and ownership

`owned` starts an explicit file entry with the same Python/Node/direct-binary forms as CLI verification, no shell. It supports a single foreground service process. The origin has no port; the service writes `{"port": 12345}` to the declared fresh `port_file`. Both discovery and database must be exact fresh cleanup files within the service cwd. Only the acquired `Popen` handle is terminated. Additional known output files (including database sidecars, where used) must be declared; no recursive directory deletion or process-name matching occurs. This is cooperative ownership, not an OS sandbox or arbitrary process-tree supervisor.

`existing` supplies the exact origin including port and `identity.pid`; omit argv, entry, cwd and port_file. Its files and process remain owned by the original launcher: `cleanup.remove_files` must be empty. Record the actual provenance of the existing instance and its immutable startup artifact. A hash computed from current disk content on each request is insufficient: an old process could claim new code. The adapter compares response fields `source_sha256`, `instance_id`, `pid`, `database` with the plan and current candidate. Owned source must equal its actual entry. The professional verifier must establish the startup hash's trustworthiness and identify other relevant configuration/dependency content in the candidate; this handshake is not full runtime module-load observation.

Readiness is a bounded GET loop with exact status and JSON equality. Discovery, readiness, each request, and cleanup have explicit budgets. Identity is checked before business requests, after them, and before captured-resource cleanup. Wrong identity, early exit or readiness deadline remains unaccepted. Existing services stay alive on failure too.

## Requests, readbacks and scoped cleanup

Requests use explicit `GET`, `POST`, `PUT`, `PATCH` or `DELETE`, an origin-relative path, optional synthetic `json`, exact expected `status`, and nonempty JSON assertions. Each assertion selects a list of object keys/array indices; `[]` selects the whole response. Expectations come from the requirement. Ordered GET readbacks must prove the state affected by writes. Include abnormal business checks as separately mapped acceptance checks. No implicit redirects, environment proxies or retries occur. Responses are JSON (empty bodies decode to null); unsupported binary/text business responses require another adapter.

A write timeout is `outcome: unknown` with one attempt. A later authorized GET may diagnose state; it is not permission to resend the write. HTTP error-status JSON remains observable so genuine abnormal business expectations can pass separately from normal success. Slow streaming is bounded by the parent worker deadline; `max_bytes` caps response size.

For existing-service writes, scope cleanup to resources created by this task. A POST may declare `"capture": {"created_id": ["id"]}` and an `ownership_reason` establishing create-only semantics. Cleanup then uses the captured identifier:

```json
{"remove_files": [], "reason": "only this task's created row", "timeout": 5,
 "requests": [{"method": "DELETE", "path": "/items/{created_id}", "retry": "none", "status": 200,
               "assertions": [{"path": ["deleted"], "equals": true}]}]}
```

Choose cleanup assertions from the actual service contract. Captures are single safe path components (letters, digits, `_`, `-`); cleanup templates must reference captured names. The operator must verify that POST really creates a fresh owned resource rather than upserting/reusing user data. Missing captures, unknown write outcomes, identity changes or failed cleanup leave visible residual uncertainty and keep acceptance unaccepted. A cooperating test harness may instead declare `cleanup.external_owner` naming its exact data/cleanup responsibility; this is an explicit boundary, not proof that adapter cleanup ran. Real tasks requiring adapter cleanup use captured-resource operations. No existing DB/table reset is supported.

## Evidence and redaction

Each `http-N.json` is an independent professional lifecycle artifact, referenced from `result.json`; it is not a managed command receipt. Common candidate-before/after, dependencies, coverage and explicit deferral semantics remain intact. Request transport status, assertion status, instance identity, launch and cleanup are separate fields. A successful HTTP status with wrong readback stays unaccepted. Prior evidence directories are never overwritten.

Service stdout/stderr go directly to the null device; response headers and raw bodies are always omitted from persistent evidence. Only selected JSON assertion values and identity fields are retained. Authentication headers use `headers_env`, for example `{"Authorization":"TASK_AUTHORIZATION"}`; values remain in process memory and automatically join redaction values. Named `redaction.secret_env` also protects arbitrary sensitive values. Common secret-bearing JSON keys and sensitive assertion selections are filtered before any evidence write, including plan echo. Plans must themselves contain only safe test data and environment names; the adapter cannot undo an already saved secret-bearing caller plan. Metadata, free text and unknown JSON field names still need the task's explicit safe-data assessment.

Required failures, missing real dependencies, unsupported lifecycle or cleanup obligations remain **运行未验收**. Preserve original failures, fix the application or asset under existing authority, and use a new evidence directory. Runtime results must be obtained through the actual implement workflow before final Standards/Spec review; fixture unit tests alone do not qualify skill behavior. Async terminal polling, managed completion adoption and UI verification are outside this T02 contract.
