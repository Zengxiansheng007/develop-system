---
name: runtime-verify
description: Independently verify an authorized CLI or script through its actual entry and business assertions, returning redacted candidate-bound evidence before final review.
---

# Runtime verification

Read [the CLI contract](references/cli-contract.md), prepare the plan from task requirements, then use the existing project tool to execute the real entry. The bundled adapter is a thin consumer of develop-system's command recorder and source inventory, not a model executor. This initial capability supports independent foreground CLI/scripts; HTTP, managed receipt integration and upgrade qualification are separate work.

Use `python -B <this-skill>/scripts/verify.py --plan <plan.json> --output <new-evidence-directory>`. Honor the host's command authorization/governance when invoking it. Resolve the installed executable; no dependency installation is implicit. The target must be a bounded foreground process without spawned background services. For process trees/services, return a coverage gap until an owned lifecycle adapter is qualified.

Evaluate the result, original receipts and business assertions. Report task/acceptance IDs, candidate, command context, dependencies, execution state, assertion outcomes, cleanup and evidence paths. A passed recorder command is separate from runtime acceptance. Required failed/blocked/not-run items remain **运行未验收**; preserve all prior evidence. Refer product defects back to implement/diagnosing-bugs; environment/authorization gaps remain blockers; missing business expectations require clarification. Never weaken expectations automatically.

Only explicit user scope decisions allow a deferral. The JSON decision reference is an agent attestation: verify it against the actual conversation; the adapter cannot authenticate user consent. `passed_with_adjustment` means only the adjusted scope, never the original deferred criterion. A documentation-only change uses a professional nonapplicability explanation rather than an empty passing plan.

Keep plans free of secret values; reference environment variable names instead. Verify the data boundary before running a command. Default output omission plus explicit secret redaction happens before receipt persistence; output retention requires an explicit safe-data assessment. No scanner can identify all arbitrary confidential text. Targets themselves may write files: confine them to authorized test data and inspect their cleanup. Return these independent records as artifacts, not managed `checks`; do not create a fictitious run.
