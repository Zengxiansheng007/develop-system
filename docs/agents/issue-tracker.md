# Issue tracker: GitHub

Repository: `Zengxiansheng007/develop-system` on `github.com`.

New develop-system specs, decisions and implementation tickets belong here. Always pass this repository explicitly to connector/API operations or `gh --repo Zengxiansheng007/develop-system`; the current directory and unrelated connector defaults do not select the tracker.

## Authentication

Use an available authenticated GitHub connection, or the user's local credential entry `develop-system`. Keep the credential source path in an ignored machine-local binding. Keep token values only in process memory or an approved credential store. The existing shared account note contains multiple entries; do not pass it to a helper that requires a single PAT, copy its contents into the repository, or create a plaintext token extraction file.

Record connector and PAT verification separately. Public repository reads do not prove authentication or write permissions. Do not create test Issues or comments to check access.

## Issue operations

Read the target and check for duplicates before creating or editing Issues. Preserve unrelated labels, assignments and historical references. Use structured bodies or CLI `--body-file` for multiline text. Resolve bare numbers as Issues or PRs before acting.

Publishing a spec to this tracker means creating or updating an authorized Issue. It does not authorize a source push, tagged release, global installation, or migration of an existing run.

**PRs as a request surface: no.**

## Wayfinder

A map is an Issue labelled `wayfinder:map`; decision tickets use `wayfinder:research`, `wayfinder:prototype`, `wayfinder:grilling` or `wayfinder:task`. Link children using native sub-issues. If unavailable, use a task list in the map and `Part of #<map>` on each child.

Use native Issue dependencies for blockers; if unavailable, use explicit `Blocked by: #<number>` references. The frontier contains only this map's open, unassigned children with no open blockers, in map order. Never select work from an unscoped repository-wide queue.

Claim the selected child, record its answer, close the decision ticket when resolved, and link the result from the map. Closing a decision ticket does not complete an implementation ticket or a run's acceptance criteria.

## Existing work

UI-Test Issues and original run ledgers remain at their existing locations. Reference cross-repository evidence with full URLs or explicit local bindings. Moving a tracker does not reset budgets, change run IDs, reopen completed work, or manufacture new acceptance evidence.

The existing local optimization map remains authoritative until a separately recorded transition publishes it here. During that transition, retain old-to-new links and decision status, and mark the old map as a historical pointer so only one map receives updates.
