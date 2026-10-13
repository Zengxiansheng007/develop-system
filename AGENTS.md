# Develop System development

This repository owns the develop-system dispatcher and its bundled engineering skills. Before changing code or packaging, read `docs/development.md` for source ownership, verification and release rules. Preserve each imported skill's license and upstream provenance.

Run focused checks for changed behavior and direct dependants. Before releasing a bundle, run the complete develop-system verification entry and verify the exact packaged files. Passing repository checks does not prove a live run or a product's acceptance criteria are complete.

Existing runs retain their original identity, authorization, evidence and execution authority. Adopt a new runtime only through explicit, versioned migration. Keep global installed skills separate from source development.

## Agent skills

### Issue tracker

Track develop-system work in GitHub Issues in `Zengxiansheng007/develop-system`; always specify this repository. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the five canonical role mappings in `docs/agents/triage-labels.md`.

### Domain docs

Use one root `GLOSSARY.md` and `docs/adr/`. See `docs/agents/domain.md` before exploring terminology or design decisions.
