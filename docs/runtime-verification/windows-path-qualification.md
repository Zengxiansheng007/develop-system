# Windows CI path qualification

PR #33's first Windows CI run at `c751d4d` completed the workflow suite but failed the 77-test verification suite (30 failures and 11 errors). This was not a timeout. The original run and artifact remain historical evidence.

The runner's temporary root used the Windows `RUNNER~1` alias while Python's resolved candidate and the HTTP fixture's advertised database used `runneradmin`. CLI requalification passed the unresolved plan root to a containment helper whose caller must supply a canonical root. Both probe execution and subsequent evidence validation now resolve that independent root, matching candidate snapshots. Frozen managed authorization scopes are unchanged; an actual `..` escape still blocks before execution.

The HTTP fixture now declares the canonical database identity that its service actually returns. Production HTTP identity comparison remains exact: an external service's database identifier must not be resolved against the verifier's local filesystem. Wrong instance, process, source or database still fails. No evidence schema, ownership policy, retry or timeout changes.

Regression tests use a noncanonical root through real Node probe/execution/reuse and real HTTP/SQLite existing-service reuse, including original execution identity, no duplicate write and rejected probe escape. They reproduce both failures before the fix on the local Windows runtime. The full Windows CI suite also exercises its actual temporary-directory aliases. Tests remain protocol evidence, not UI or existing-run migration acceptance.

Reuse: Python standard library and the existing receipt/HTTP adapters; no added dependencies. Python documents Windows 8.3 expansion in [os.path.realpath](https://docs.python.org/3.13/library/os.path.html#os.path.realpath).

The subsequent Ubuntu run on `c2556b9` exposed an independent fixture timing race: the failure/wrong-total cases allowed only two polls 0.25 seconds apart, while the real worker sleeps 0.4 seconds before committing its terminal state. Fast request processes could exhaust attempts before that state existed. Those two cases now retain the existing four-second deadline and allow the normal 20-attempt budget. Stuck, wrong-correlation and slow-transport cases keep two attempts. Exact expected terminal outcomes, one submitted job and preserved existing service assertions remain unchanged. This modifies only test scheduling, not production polling or a passing/failing business expectation.
