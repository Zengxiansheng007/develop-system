---
name: runtime-verify
description: Independently verify an authorized CLI, script or HTTP service through its actual entry and business assertions, returning redacted candidate-bound evidence before final review.
---

# Runtime verification

For existing evidence, changed premises, or review repairs, read [the reuse contract](references/reuse-contract.md) before deciding what to execute again.

Read [the common plan and CLI contract](references/cli-contract.md); for a service, also read [the HTTP lifecycle contract](references/http-contract.md). For an explicitly managed task, read [the managed contract](references/managed-contract.md) before dispatch or execution. Prepare independent business expectations from the task, then use the existing project tool to execute the actual entry. The adapter reuses develop-system's command recorder, source inventory and bounded HTTP worker pattern.

Use `python -B <this-skill>/scripts/verify.py --plan <plan.json> --output <new-evidence-directory>`. Honor the host's command authorization/governance when invoking it. Resolve the installed executable; no dependency installation is implicit. CLI checks use bounded foreground processes. HTTP checks own one foreground service process or verify an existing service's immutable startup identity; daemon trees and unrelated processes are outside this adapter's cleanup scope.

Evaluate the result, original receipts and business assertions. Report task/acceptance IDs, candidate, command context, dependencies, execution state, assertion outcomes, cleanup and evidence paths. A passed recorder command is separate from runtime acceptance. Required failed/blocked/not-run items remain **运行未验收**; preserve all prior evidence. Refer product defects back to implement/diagnosing-bugs; environment/authorization gaps remain blockers; missing business expectations require clarification. Never weaken expectations automatically.

Only explicit user scope decisions allow a deferral. The JSON decision reference is an agent attestation: verify it against the actual conversation; the adapter cannot authenticate user consent. `passed_with_adjustment` means only the adjusted scope, never the original deferred criterion. A documentation-only change uses a professional nonapplicability explanation rather than an empty passing plan.

Keep plans free of secret values; reference environment variable names instead. Verify the data boundary before running a command. Default output omission plus explicit secret redaction happens before receipt persistence; output retention requires an explicit safe-data assessment. No scanner can identify all arbitrary confidential text. Targets themselves may write files: confine them to authorized test data and inspect their cleanup. Return these independent records as artifacts, not managed `checks`; do not create a fictitious run.
