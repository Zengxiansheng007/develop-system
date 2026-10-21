# First-batch candidate integration

For the first-batch specification, review the complete branch from the imported baseline, together with each accepted ticket and the final AC-01–AC-38 evidence map. Ticket #12's small integration diff does not describe the whole behavioral change. Independent Spec and Standards conclusions decide acceptance; command counts and hashes bind evidence to a candidate.

## Source and adaptation

The direction is diagnostic baseline → isolated candidate → canonical `.agents/skills` → generated distribution. Author only canonical source. The project-local research Skill, registry ID and navigation command use `research`. The user's installed `matt-research` remains a separate host adaptation; deployment must separately preserve the unrelated global research router. Existing run registry snapshots and archived v3 fixtures keep their original names and versions.

Imported `source.json`, licenses and `docs/provenance/import-manifest.json` describe historical provenance, not the current implementation. Preserve those originals; Git history records subsequent adaptations.

## Bytes and evidence

`.gitattributes` defines LF for source text. Issue #7 recorded CRLF registry working bytes while Git stored LF; Issue #8 checked out LF. Keep both original receipts and distinguish their raw hashes. A normalized-content comparison can explain a line-ending-only difference but does not make raw identities equal. Freeze and verify the final checkout's actual bytes and generate distribution from those exact bytes.

Use the existing `tools/checks.py` entry to produce fresh workflow, verification, format, registry and generated-copy records. Preserve original failures with their tested versions. When replaying F01/F02, point the copied diagnosis at the final candidate and explicitly adapt only current protocol/source/worktree bindings. Record actual imports and execution sources. The default retro → writing chain needs real document application and helper return before parent completion, separately identified from synthetic state fixtures.

## Delivery boundary

Local candidate verification does not publish, install globally or adopt any real old run. Keep the parent specification open until the user chooses its closure. The delivery packet names the fixed commit, AC evidence, independent conclusions and remaining limitations; historical UI-Test product acceptance and execution authority are unchanged.
