# Development

This repository owns the develop-system dispatcher, navigation entry, and the 25 bundled engineering skills listed in `release-manifest.json`. Author source under `.agents/skills/`. Generated verification packages and release archives are ignored outputs, not another editable source tree.

## Baseline and boundaries

The import preserves the engineering source from the exact coedx-home commit and hashes in `docs/provenance/import-manifest.json`. Per-skill `source.json` and LICENSE retain upstream attribution. UI-Test product source, its Issues, active run state and historical evidence remain in their original locations. Future develop-system development belongs here; old runs remain bound to the version under which they started until an explicit migration.

The registry now includes writing-for-agents as an auxiliary execution capability. ask-matt remains navigation; handoff and wait-what remain standalone companions. The required-call declarations, selected-source rules and package/runtime qualification scopes are owned by [the extension contract](../.agents/skills/develop-system/references/extensions.md). Bootstrap checks alone do not establish F01/F02/F09 acceptance; current candidate capabilities and deferred work are listed in [scoped state](../.agents/skills/develop-system/references/scoped-state.md).

Source names retain `research`. The user's global installation uses `matt-research` to coexist with a separate research router. This bundle is suitable for project-local use; before global deployment, review that explicit rename and registry/link adaptation. Do not replace the user's unrelated global research skill. Setup's source already contains its authentication helper; no global credential files are imported.

## Local checks

Use Python 3.11 or newer with `requirements-checks.txt` available. Install dependencies only when the environment's authorization permits it.

```sh
python -B tools/checks.py
```

The entry records workflow unit tests, verification/packaging tests, all bundled skill formats, the routing registry, and generated source-copy parity. Every invocation uses a fresh receipt directory under `.scratch/verification/`. It stages packages from the explicit manifest; checks never rely on global installed skills unless `--installed` is explicitly supplied.

For changes, start with the affected tests and direct dependants. The release boundary requires the full workflow verification entry. These checks do not execute business UI tests, call models, perform live deployments or prove whole-product acceptance.

## Maintenance

Command receipts from tools/checks.py are independent check-record v2 and require no run. Managed checks separately use dispatched worktree bindings and actual execution-time source observation; see [check records](../.agents/skills/develop-system/references/check-records.md). Repository success is not automatically managed-frame proof. Existing v1 receipts remain historical originals.

The verification entry accepts `--timeout <seconds>` per command (default 300). Use a bounded larger value when observed real Git worktree checks exceed that duration; preserve the original timeout receipt.

Update source and related tests/documentation together. Add or remove shipped skills through `release-manifest.json`, with license and dependency review. Keep source provenance immutable as a historical baseline; version control records later edits. A schema change needs an explicit compatibility and migration contract.

Use GitHub Issues in this repository for new develop-system work. The previous local optimization map retains its authority until an explicit map publication records old-to-new links; repository setup does not duplicate its tickets.

For first-batch source naming, historical byte reconciliation and whole-branch acceptance, see [first-batch integration](first-batch.md). See `releases.md` for candidate creation, verification and publication.
