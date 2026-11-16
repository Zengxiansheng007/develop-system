# Audit report contract

Adapted from Trail of Bits differential-review reporting.md; see [source](../SOURCE.md). CC BY-SA 4.0.

Return a Markdown report with these sections. Use explicit unknown/not run values when evidence is absent.

1. **Request and comparison:** explicit request reference; repository/snapshot identity; resolved base and target; dirty-content identity if applicable; requested paths, security focus, data boundary, and report scope.
2. **Conclusion:** findings by evidence-backed severity, overall confidence and limitations. Missing required comparison input makes the review blocked/partial; no claim of full safety, product acceptance or permission to merge follows.
3. **Findings:** each includes an ID, concrete behavior, affected path/line and revision, baseline versus target evidence, reachable attacker/interface, prerequisites, attack sequence, impact, severity rationale and confidence. Link actual supporting evidence. Label static reasoning, unverified assumptions and executed proofs distinctly. Recommendations are proposals; no repair is performed by this skill.
4. **Coverage ledger:** every requested path (changed or unchanged), reviewed revision/range, examined caller/dependency depth, focus addressed, status reviewed/partial/skipped/unavailable, and exact reason for any gap. Record out-of-scope paths needed to strengthen conclusions without expanding the audit. List relevant tests inspected and actual commands/outcomes separately. Never invent test execution or coverage percentages.
5. **History and residual gaps:** prior defenses/regressions supported by available history, missing inputs, dependencies and deployment assumptions; scans/exploits not run; unresolved questions and the next evidence needed.

If there are no findings, retain the coverage ledger and state “No findings identified within the reviewed scope,” qualified by actual limitations. If no comparison was possible, state that no differential conclusion was reached. Save only within the authorized destination; a write failure is reported, not worked around by writing to a global skill directory or Desktop. Redact secrets before persistence. Returning a report does not transmit it to third parties.
