# Attack-path analysis

Adapted from Trail of Bits differential-review adversarial.md; see [source](../SOURCE.md). CC BY-SA 4.0.

For each candidate finding:

- Define the attacker: unauthenticated user, ordinary account, privileged account or compromised component. State the exact permissions, reachable interface and required starting state.
- Trace a concrete sequence from the public entry through the changed code to the violated invariant. Reference both baseline and target when claiming a regression. Inspect requested unchanged callers; a caller's validation may invalidate the alleged exploit.
- Identify the resulting harm and affected data/authority. Distinguish demonstrated reachability from assumptions about deployment, dependencies, race timing or attacker control.
- Assess practical exploitability from those prerequisites. A missing test or many callers is an investigation priority, not a severity formula.
- Attach evidence for every material claim. A source-derived example is illustrative and not an executed proof of concept. Label actual execution separately, including environment, authorization and outcome. No exploit execution follows merely from reading this reference.
- Assign confidence based on evidence completeness. If the critical path cannot be established, return a qualified hypothesis or coverage gap rather than a confirmed vulnerability.

Use the report contract for exact file/line/revision, evidence, impact, confidence, and remaining verification needs.
