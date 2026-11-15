---
name: security-differential-audit
description: Independent security differential audit, only when the user explicitly requests it, with a comparison baseline, target, scope and security focus.
disable-model-invocation: true
license: CC-BY-SA-4.0
---

# Security differential audit

Use only for an explicit user request for a security differential audit (including a direct skill invocation). Ordinary implementation, security terminology, authentication changes and generic code review do not activate this skill. If no explicit request exists, return to the caller without an audit. This is an independent professional review; preserve normal TDD, type checks, mandatory project checks, runtime verification and Standards/Spec review.

This method adapts Trail of Bits differential-review. Read [source and modifications](SOURCE.md) for attribution and the [license](LICENSE). Invocation policy is also declared in [Codex metadata](agents/openai.yaml); these declarations are not an OS security boundary.

1. **Bind the comparison.** Record the user's request, repository or supplied snapshot, explicit base and target identities, requested paths (including named unchanged callers), security focus, allowed data boundary and report destination. Resolve supplied Git revisions to immutable commits; identify dirty target files by content when relevant. A missing or ambiguous base, target, scope or focus is a gap: ask for that input and return a limited/blocked report. Never infer a base from HEAD's parent, a default branch or hidden task material. Do not turn missing input into a whole-repository audit.
2. **Read the bounded evidence.** Follow [methodology](references/methodology.md) for baseline invariants, change/history analysis, callers and tests. Read both revisions without checkout/reset. Every requested path must appear in the coverage ledger, even if unchanged, missing or skipped. For an out-of-scope dependency, state the limitation and request scope expansion when necessary; do not silently expand.
3. **Analyze plausible attack paths.** Use [patterns](references/patterns.md) as prompts where relevant, then [adversarial analysis](references/adversarial.md) for candidate findings. Use available read/search/Git tools directly; no assumed Claude agent, audit-context-building or issue-writer dependency. Distinguish a proven source path from a hypothetical exploit. Risk prioritizes investigation; keywords, caller counts and absent tests alone never establish a vulnerability or its severity.
4. **Return findings and coverage.** Use [reporting](references/reporting.md). Complete when each requested path and focus has an observed outcome or explicit gap, and each finding has source/revision location, evidence, attacker prerequisites, impact and confidence. Save to the authorized workspace destination if available; otherwise return the complete report in chat with the write limitation. Zero findings means only none identified within actual coverage.

Read-only source analysis is the default. Audit does not authorize repairs, exploitation, installation, network transmission or publication. Do not install or invoke OCR, Semgrep, CodeQL, external models, performance tools or other specialist scans as part of this capability. Dynamic execution needs separately established task authorization and owned cleanup; report it as not run otherwise. Redact credentials and unrelated private content before persisting evidence.

Outside a develop-system run, return directly without creating a run. When explicitly selected as a managed root task, use the existing [return protocol](../develop-system/references/codex-runtime.md), retain actual sources/artifacts and gaps, and return all end states to the controller. Registration does not create a default helper obligation or qualify a managed child audit. A professional report is not proof that the product is secure or that implementation/runtime acceptance has passed.
