# Differential investigation prompts

Adapted from Trail of Bits differential-review patterns.md; see [source](../SOURCE.md). CC BY-SA 4.0.

Apply only patterns relevant to the requested security focus and platform. They prompt examination, not automatic findings.

| Change pattern | Evidence to seek |
| --- | --- |
| A defense removed or earlier code reintroduced | Scoped history showing why it existed; equivalent protection elsewhere; target path that bypasses it |
| Relaxed access checks | Actual attacker privileges, caller validation, reachable operation and affected authority |
| Missing input/state validation | Accepted boundary values, state invariants and downstream checks |
| Repeated accounting/state transitions | Same logical event reaching both updates and concrete inconsistent outcome |
| Arithmetic/unchecked blocks | Language/version overflow rules, attacker-controlled values, reachable impact |
| External calls or unchecked return values | Trust assumptions, failure handling, state ordering and caller behavior |
| Reentrancy | Callback reachability, state before/after external calls, effective reentrancy guards |
| Timing-dependent authorization or value transfer | Platform timing/control assumptions and an achievable attack sequence |
| Unbounded work or blocking dependencies | Attacker-controlled growth/input, resource bounds, and actual denial of service |

Solidity-specific overflow, front-running and timestamp examples do not establish analogous bugs on other platforms. No external pattern databases, detector installs or additional audits are prerequisites.
