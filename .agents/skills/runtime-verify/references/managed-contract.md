# Managed professional runtime contract v1

This opt-in contract applies to new task steps. Old schema-v4 runs, protocol-v1 returns and check-record semantics remain unchanged. No migration or evidence relabeling occurs. Runtime evidence is a professional artifact; the dispatcher checks identity, coverage, currentness and internal consistency. The specialist owns the independent business expectations and actual acceptance judgment. This is neither arbitrary-language loaded-source observation nor authenticated user consent.

Task contract v1 and professional envelope versions are separate. New runtime dispatches freeze `runtime_evidence_version: 3`; v3 retains v2's actual invocation interval and adds explicit direct-original reuse references under [the reuse contract](reuse-contract.md). Original frames retain their frozen v1/v2 semantics; frames without a declaration retain v1. Use their matching producer/runtime or an explicitly authorized transition; this adapter does not migrate them. Existing producer-currentness checks still apply. A new v3 frame cannot accept a downgraded envelope.

Use the real selected implement entry and its local extension. Create the step with `runtime_contract` before dispatch:

```python
from workflow_state import RunStore, SKILL_ROOT
store = RunStore(project, 'explicit-new-run')
contract = {'version': 1, 'criteria': {'1': ['cli-success', 'cli-invalid'], '2': ['http-crud']}, 'scope_adjustments': []}
state = store.create('authorized task', [{'step_id': 'implement', 'skill_id': 'implement', 'runtime_contract': contract}],
    [{'criterion': 'CLI business behavior', 'allowed_skill_ids': ['runtime-verify']},
     {'criterion': 'HTTP business behavior', 'allowed_skill_ids': ['runtime-verify']}])
worktree = {'authorization_reference': 'actual user task reference', 'worktree': str(candidate_root),
            'execution_roots': [str(project), str(candidate_root)], 'source_ranges': ['src', 'tests', 'config']}
state = store.dispatch(state['revision'], 'implement', 'task-v1', 'implement', worktree=worktree)
parent = state['frames'][-1]
# Actual implement professional performs the work under this parent.
state = store.dispatch(state['revision'], 'runtime-verify', 'task-v1', parent_frame_id=parent['frame_id'], worktree=worktree)
runtime = state['frames'][-1]
```

Use actual paths, authorized source ranges and task criteria. Keep registry `implement.required_calls` unchanged: code-review stays necessary. The task contract durably adds runtime-verify; omitting its dispatch cannot complete the parent. Security audit stays explicit-only and absent by default.

The runtime professional reads its selected SKILL.md, builds the ordinary CLI/HTTP plan, and executes:

```text
python -B <runtime-verify>/scripts/managed.py --project <project> --run <run-id> --frame <runtime-frame-id> --plan <plan.json> --output <new-output-directory>
```

Python API: add the selected runtime scripts and develop-system scripts to import search paths, then call `managed.execute(store, runtime_frame_id, plan, output)`. It executes the existing verifier and returns an event. The controller receives `return.json` using `store.receive(state['revision'], event)` (or the existing `workflow_state.py return --payload` CLI). Execution does not itself mutate the run. Each output directory is new; failures remain intact.

The plan uses the child input version, the exact bound worktree root and declared candidate roles. Its required IDs cover the contract. `result.json` and original command receipts retain **independent** context. `managed-envelope.json` adds original run/frame/parent/input/binding, contract, selected producer inventory and evidence hashes. The producer inventory includes the runtime skill and its direct dispatcher dependencies; it conservatively invalidates changed verification assets. App candidate files use the existing authorization-aware `source_inventory`. Runtime process identity and service startup identity come from the original CLI/HTTP evidence; the envelope makes no claim that a cleaned service remains live.

The event's initially empty `checks` belongs to this newly executed professional helper; it is not a replacement for any existing frame or command checks. Additional managed Python checks keep their original validator and binding. Never copy independent receipts into `checks`. A raw CLI exit 1 may satisfy an explicitly expected invalid-input case; it is still a failed command receipt and a passed business assertion, without rewriting either.

CLI qualification links both recorded argv fields and canonical cwd to the persisted plan, and links its entry argument to the declared candidate file. Compare persisted redacted argv with persisted redacted argv; omitted output stays omitted. Envelope v2 additionally bounds command timestamps and the runtime result timestamp by the invocation interval. Required record references are exact and distinct. HTTP qualification compares the emitted owned-service launch argv/cwd and outer business method/path with the plan; dynamic poll and cleanup templates retain their own runtime contracts. These are consistency checks on emitted facts, not authenticated producer signatures or runtime-loaded-source observation.

After accepted runtime evidence, actually execute the code-review child and receive its Standards/Spec result with runtime artifacts. Receive the implement professional's real result. Complete with `store.complete(revision, [{'id':'1','source_frame_id':runtime_frame_id}, ...])`. Every runtime criterion must reference this task's runtime child. Failed, blocked, missing, stale or contradictory required evidence prevents completion even if the professional frame says completed. Artifacts, candidate contents, producer and binding are checked again on currentness/completion. These checks are evidence integrity, not an OS security boundary or proof a model loaded the skill.

Scope adjustment uses `action: defer`, named `check_ids`, `reason`, actual `user_reference`, and `decision_evidence: {path, sha256}`. The plan must carry exactly the same entries. Verify the reference against the actual user instruction; the dispatcher cannot authenticate authorship. The report must call acceptance adjusted and retain original deferred criteria; it must not claim their behavior passed.

An explicit later decision after a blocked professional result stays in the same authoritative run: `state = store.adjust_runtime_scope(state['revision'], parent_frame_id, adjustment)`. This applies only to an active implement parent already enrolled in contract v1. It appends a versioned declaration and immutable original/decision/retired-frame records inside the existing state; original plan, events, artifacts and requirement text remain unchanged. Prior runtime and review children are superseded, with their full previous frame state retained in the amendment. Dispatch and execute fresh runtime and review children; use the updated parent contract's `scope_adjustments` in the new runtime plan. Only named checks become deferred, and remaining required checks must actually pass. Completion labels affected criteria `passed_with_adjustment` and original runtime acceptance `deferred`. This operation neither migrates old runs nor resolves unrelated/system restrictions; active child work must first return, and scoped execution restrictions require their normal resolution.

To reproduce protocol negatives, run `python -B -m unittest discover -s .agents/skills/develop-system/scripts -p test_runtime_managed.py`. Those synthetic professional returns test public seams, not actual agent entry. Release qualification additionally requires a fresh actual managed implement invocation and actual professional review.
