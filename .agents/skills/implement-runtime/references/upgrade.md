# Reviewed implement restoration

Use this protocol only in an explicitly authorized isolated skills tree. The extension version is 1; upstream identity and MIT provenance live in `implement/source.json` and `implement/LICENSE`. The unique hook markers are `BEGIN LOCAL IMPLEMENT RUNTIME v1` and `END LOCAL IMPLEMENT RUNTIME v1`. Keep their existing three-line block intact. Source development and global installation are separate actions.

## Prepare before replacement

1. Stage the repository manifest into a new directory with repository `tools/release_bundle.stage_skills`, or use an already authorized isolated package. The shipped restore script uses only Python 3.11+ and Git; repository packaging tools are not installed-skill dependencies.
2. Obtain the exact pristine upstream `SKILL.md` named by `implement/source.json`, and an explicitly selected new upstream candidate. Preserve their provenance and licenses. Read the candidate's full workflow and the local adaptations: planning, mandatory tests, verification before final review, repair/review loop, completion and delegation semantics. Record the maintainer/professional review reference. This is a review record, not invented user approval or proof of compatibility. Stop for semantic conflicts even when text could merge cleanly.
3. Run the public prepare command, with an unused snapshot directory outside the skills tree:

```sh
python -B <skills>/implement-runtime/scripts/restore.py prepare --skills-root <skills> --base <pristine-SKILL.md> --candidate <reviewed-new-SKILL.md> --snapshot <new-snapshot> --review <review-reference>
```

Completion: `action=prepared`. Preparation checks the pinned base hash, unique exact hook, direct inline local Markdown references in the merged entry and extension entry, and required sibling sources/licenses. Reference-style Markdown is explicitly unsupported and blocks preparation; use explicit inline paths for review. All resolved direct local references are hash-bound, including files outside the six required skill directories. This is a bounded direct-reference check, not a recursive Markdown parser or remote-link verifier. It stores original local/base/candidate/merged bytes, source identity and dependency hashes. Git `merge-file --stdout` preserves nonconflicting changes on both sides; conflicts block preparation without changing the target. Keep failed inputs and the original command evidence. The snapshot is trusted local recovery data, not a signed authorization document.

## Replace and restore

The supported replacement boundary is **implement/SKILL.md**. The pinned upstream `agents/openai.yaml` may also be copied when byte-identical to the preserved local file. Keep `implement/LICENSE`, `source.json`, other metadata and all sibling directories intact. Whole-directory deletion or changes to those files require a separately reviewed migration; this script does not reconstruct them.

After the authorized upstream overwrite, run:

```sh
python -B <skills>/implement-runtime/scripts/restore.py restore --skills-root <skills> --snapshot <snapshot>
```

Completion: `action=restored` with before/after hashes, or `action=unchanged` for an identical repeated invocation. Restore accepts only the prepared upstream bytes or the exact merged bytes and checks all captured dependencies. Missing/changed assets, unknown target edits, damaged snapshots and unsafe paths fail before target mutation. Atomic replacement preserves the old target if replacement fails. Never resolve conflicts by choosing all ours/theirs or by silently restoring the old workflow over a new upstream. Backups contain the old full entry as recovery data; the live extension contains no duplicate upstream workflow.

## Qualify through the actual entry

Every successful command reports `runtime_status=unqualified`. Textual merging, hashes and existing files do not establish semantic compatibility. In a fresh context provide the restored implement entry, representative task and environment; let the entry load its extension naturally. Observe real loading, required tests, runtime evidence before final review, failed/blocked criteria remaining unaccepted, repair/review currentness and default-off security audit. Supply separate broken-reference and workflow-conflict scenarios. Retain original failure evidence. Only those observed behaviors qualify that exact candidate and environment; neither restoration nor an old run qualifies arbitrary future versions.

When preparing a subsequent upstream generation, provenance/base migration and adaptation review are explicit maintenance work. This command deliberately requires the currently pinned pristine base and does not advance provenance automatically.
