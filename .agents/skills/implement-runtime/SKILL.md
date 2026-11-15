---
name: implement-runtime
description: Local runtime verification policy loaded by implement throughout planning, candidate validation, final review and completion.
---

# Local implement runtime extension v1

This sibling directory is local source, outside the upstream `implement/` replacement boundary. Preserve the upstream workflow, existing local adaptations, TDD, type checks, project mandatory checks, and Standards/Spec review. The only upstream addition is the marked loading block. Skill loading alone proves no runtime outcome.

1. **Plan before implementation.** Read [runtime-verify](../runtime-verify/SKILL.md) and its request contract. Map task acceptance IDs to real entry, working directory, independent expected business results, success and affected abnormal behavior, real/simulated dependencies, authorization, evidence location and owned cleanup. Reuse approved seams; ask only for genuinely missing requirements/permissions. Record gaps; never derive expected values from the candidate's output. For non-runnable changes, record why runtime is inapplicable and retain every previously required check.
2. **Verify the candidate before final review.** Invoke the independent runtime capability against actual changed content (including relevant uncommitted source, tests, configuration and dependencies). Use project execution tools and explicit argv/cwd. Keep original failures, then create new evidence for repairs. Required failure, unavailable dependency or missing execution means **运行未验收**; code may still be delivered with that limitation. A zero command exit is not business acceptance.
3. **Review current evidence.** Give code-review the actual runtime result and original command receipt paths, candidate identity, project check evidence and unresolved gaps. Runtime conclusions remain separate from Standards and Spec. Review repairs return to implementation and affected verification before review resumes. T01 establishes the first-run path; broader automatic evidence reuse is not claimed.
4. **Complete honestly.** Report code delivery, professional responsibilities and runtime acceptance separately. Missing runtime invocation/evidence cannot pass a required acceptance item. Only a real user decision can defer or change requirements: preserve original requirements and the exact decision reference, and report adjusted scope explicitly. Record nonapplicability without manufacturing a run. Standalone use returns directly to implement; no synthetic develop-system run or managed check is created. Managed integration needs its separate acceptance contract.

Security differential audit is **explicit-only**. Security-related task wording does not select it, create an audit obligation, or mark it done. Runtime verification grants no installation, deployment, production write, global modification, data export or publication authority.

For a replaced upstream skill, first verify the new implementation sequence and sibling references, then restore exactly one marked loading block under existing authorization. Missing/conflicting references block runtime qualification. Preservation of files does not prove upgrade compatibility; fresh implement-entry execution is required. Automated restoration and upgrade qualification are a later ticket.
