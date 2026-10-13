# Releases

Version is declared in `VERSION`; release notes live in `CHANGELOG.md`. Bump them together with approved changes. Version 0.1.0 is the independent repository bootstrap, not the F01/F02/F09 repair release.

## Prepare a candidate

1. Run `python -B tools/checks.py`; inspect its summary and any failing receipts.
2. Review the exact changes and commit the source. Keep the worktree clean; ignored local receipts and credential metadata are excluded.
3. Run `python -B tools/release_bundle.py --output-dir dist/<new-candidate-directory>`.
4. Inspect the archive's `MANIFEST.json` and adjacent `SHA256SUMS`. The manifest binds the commit, version, skill names and each payload file's hash. Packaging uses an allowlist, requires skill licenses and rejects credential-like patterns; it is not a substitute for reviewing all files before publication.

The ZIP is deterministic for the same committed source on supported platforms. It contains `skills/<name>/`, license, changelog and manifest. It uses the source skill names; see `development.md` before installing into an existing global skill directory.

## GitHub workflows

`Check skills` runs Windows and Linux validation for pushes and PRs. `Prepare release` is manually dispatched with a version matching `VERSION`. It reruns validation on both operating systems and uploads the exact verified archives and receipts. By default it creates no Release or tag.

The `create_draft_release` input must be explicitly enabled to create a GitHub draft Release from the verified candidate. That job alone has contents-write permission. It verifies uploaded SHA256SUMS and creates the version tag at the workflow's fixed commit. Existing Releases are not overwritten. Publishing the draft is a separate explicit action; ordinary commits do not publish.

Use the actual CI results to report hosted verification. Writing the YAML or passing local checks does not establish a successful hosted run.

## Installation and old runs

Installation is separate from artifact publication. Review name adaptation, target paths, backups and installed-file readback before deploying. Migrate old runs individually only after new-run acceptance and explicit migration authorization; preserve user pauses, identities, history, inflight results and original authorization.

Workflow mechanics follow [GitHub manual triggers](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow) and [gh release create](https://cli.github.com/manual/gh_release_create).
