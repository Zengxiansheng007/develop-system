# Independent CLI plan and result v1

Python 3.11+; sibling `develop-system/scripts` is required. No additional package is used. Plans are UTF-8 JSON. Evidence directories must be new; each execution reserves new receipts. This contract describes observable business checks, not full module-load provenance for arbitrary runtimes.

```json
{
  "schema_version": 1,
  "task": "Export invoice total",
  "input_version": "ticket-26-candidate-1",
  "authorization_reference": "current task: local test data",
  "root": "C:/authorized/project",
  "candidate": [
    {"path": "invoice.cjs", "role": "source"},
    {"path": "fixtures/invoice.json", "role": "test"}
  ],
  "environment": "local isolated fixture; installed Node version recorded here",
  "dependencies": [{"name": "Node and local filesystem", "mode": "real", "required": true, "available": true, "evidence": "resolved installed executable; fixture writable"}],
  "redaction": {"secret_env": [], "omit_output": true},
  "checks": [{
    "id": "AC-total", "required": true, "behavior": "success",
    "expectation_source": "requirement example: invoice total is 42",
    "entry": "invoice.cjs", "entry_arg": 1,
    "argv": ["C:/Program Files/nodejs/node.exe", "invoice.cjs", "fixtures/invoice.json", "result.txt"],
    "cwd": ".", "timeout": 20, "expected_exit": 0,
    "assertions": [{"kind": "file_text", "path": "result.txt", "equals": "total=42\n"}],
    "cleanup": {"remove_files": ["result.txt"], "reason": "new owned fixture output"}
  }],
  "coverage": {"success": ["AC-total"], "abnormal": [], "abnormal_not_applicable": "explain why this change has no affected abnormal path"},
  "scope_adjustments": []
}
```

- `root` is absolute. Candidate ranges are explicit source/test/config/dependency paths, including relevant dirty and new files. Common credential paths are excluded by the existing source-inventory contract. Record chosen dependency/runtime versions in `environment`; the executable and entry are separately identified. Git HEAD is nullable and never substitutes for file hashes.
- `argv[0]` is an absolute installed executable. `entry_arg` points to the actual script/executable argument; its resolution against `cwd` must equal `root/entry`, and the entry must be in the candidate. Supported forms are Node followed immediately by its script, Python script with optional `-B`/`-u`/`-X utf8`, or a directly executed candidate binary (`entry_arg: 0`). This file-entry adapter does not support inline code, module-name entry resolution, other interpreters, `.bat` or `.cmd`. Use a dedicated adapter for those forms; do not hide a different target behind a wrapper.
- Assertions support exact UTF-8 `file_text`, `stdout_text` or `stderr_text` with a requirement-derived `equals`, and `file_absent` with a `path` (no equals needed). File outputs must be fresh; existing files are not overwritten as fixtures. Negative behavior uses its own acceptance ID, `behavior: abnormal`, expected exit and meaningful business assertions (for example exact stderr plus no output file). An expected nonzero exit may satisfy an abnormal criterion while the raw command receipt remains failed. Results retain a SHA-256 of observed redacted text or the observed file existence before cleanup, alongside the assertion outcome and original expected value.
- `dependencies` explicitly records real or simulated boundaries and availability. A dependency required to be real cannot silently become simulated. No dependencies is an explicit empty list, explained in environment. Optional checks require `applicability_reason`; they cannot silently replace task-required checks.
- Cleanup removes only exact new files named before execution. Freshness is rechecked immediately before every command; a retained previous output blocks the next check and stays untouched. Existing files, symlinks, directories and outside paths are not recursively removed. Empty removal list requires a reason (such as intentionally retained deliverable). Cleanup errors stay visible and block required checks. This is cooperative ownership, not a process/filesystem sandbox.
- `redaction.secret_env` contains environment names, never secret values. Matching values are replaced recursively in plan, metadata, argv, cwd, errors and outputs before the first persistent write. Missing named values block execution. Common bearer/token/password patterns receive supplemental filtering. `omit_output: true` omits stdout/stderr from receipts after in-memory assertions. Only set false for reviewed safe data. The caller-owned plan file must itself contain no secrets.
- Deferral shape: `{"check_ids":["AC-total"],"action":"defer","reason":"user postpones this requirement","user_reference":"actual conversation decision"}`. Original checks/expectations remain intact and are returned as not_run. Unchanged requirements remain required; no user reference means no deferral. Never invent a decision reference.

The result contains independent identity, plan, candidate before/after, per-check execution/exit/business assertions/cleanup, blockers and `runtime_status` (`passed`, `unaccepted`, `passed_with_adjustment`). Invalid plans are blocked before command execution. An empty plan never passes. When a genuine user decision defers every original check, `passed_with_adjustment` means closure of the adjusted scope only: all original checks stay not_run, there are no command receipts and no original runtime criterion has passed. Command receipts are check-record v2 independent and must never be relabeled managed. Missing dependencies and unsupported lifecycle coverage must remain visible even when code is delivered.
