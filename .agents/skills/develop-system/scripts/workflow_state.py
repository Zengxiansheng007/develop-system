"""Durable dispatch-only bookkeeping; specialist skills execute and verify work.

No model calls, no background workers, no installation or external publication.
"""
import argparse
import copy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from codex_runtime import accept_result, resolve_skill
from validate_registry import validate
from grill_routing import decide_entry, decide_root_route
from record_check import validate_record
from check_binding import bind_worktree, validate_managed, validate_reference_candidate, validate_worktree

SKILL_ROOT = Path(__file__).resolve().parents[1]


def entry_mode(entry):
    name = entry.lstrip("/$")
    if name == "ask-matt":
        return "navigate"
    if name == "develop-system":
        return "execute"
    raise ValueError("Unknown entrypoint")


def file_evidence(value):
    item = {"path": value} if isinstance(value, str) else dict(value)
    path = Path(item["path"]).resolve(strict=True)
    if not path.is_file():
        raise ValueError("Evidence must be a regular file")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if item.get("sha256") and item["sha256"] != digest:
        raise ValueError("Evidence changed since it was verified")
    return {**item, "path": str(path), "sha256": digest}


def evidence_is_current(item):
    try:
        file_evidence(item)
        return True
    except (OSError, ValueError, KeyError):
        return False


def load_registry(path):
    path = Path(path).resolve(strict=True)
    data = json.loads(path.read_text(encoding="utf-8"))
    errors = validate(data, path.parent)
    if errors:
        raise ValueError("; ".join(errors))
    return data, str(path), hashlib.sha256(path.read_bytes()).hexdigest()


def validate_plan(plan, registry):
    if not isinstance(plan, list) or not plan:
        raise ValueError("Explicit nonempty scoped plan required")
    capabilities = {item["id"] for item in registry["skills"] if item["enabled"]}
    steps = {}
    for step in plan:
        if not isinstance(step, dict) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", step.get("step_id", "")):
            raise ValueError("Invalid step id")
        if step["step_id"] in steps or step.get("skill_id") not in capabilities - {"develop-system", "ask-matt"}:
            raise ValueError("Duplicate step or unavailable execution capability")
        if not isinstance(step.get("depends_on", []), list):
            raise ValueError("depends_on must be an array")
        if not isinstance(step.get("required", True), bool):
            raise ValueError("required must be boolean")
        prerequisites = step.get("shared_preconditions", [])
        if not isinstance(prerequisites, list) or any(not isinstance(p, str) or not p.strip() for p in prerequisites):
            raise ValueError("shared_preconditions must contain named known premises")
        recovery = step.get("recovery_for", [])
        if not isinstance(recovery, list) or any(not isinstance(r, str) or not r.strip() for r in recovery):
            raise ValueError("Recovery must name restriction identities")
        if recovery and ("shared_preconditions" not in step or not isinstance(step.get("authorization_reference"), str)
                         or not step["authorization_reference"].strip()):
            raise ValueError("Recovery needs explicit premises and an existing authorization reference")
        steps[step["step_id"]] = step
    visited, active = set(), set()
    def visit(name):
        if name not in steps or name in active:
            raise ValueError("Unknown dependency or plan cycle")
        if name in visited:
            return
        active.add(name)
        for dependency in steps[name].get("depends_on", []):
            visit(dependency)
        active.remove(name)
        visited.add(name)
    for name in steps:
        visit(name)


def frame_resolved(frame):
    return (frame["status"] == "completed" or (frame["status"] == "skipped" and frame.get("required") is False and frame.get("skip_reason"))) and not frame.get("needs_assessment")


def frame_evidence(frame):
    return (frame.get("inputs", []) + frame.get("artifacts", [])
            + [c["record"] for c in frame.get("checks", []) if isinstance(c, dict) and "record" in c]
            + [e for c in frame.get("acceptance_checks", []) for e in c["evidence"]])


def validate_frame_check(state, frame, check, record, visiting=()):
    actual = validate_record(record)
    if check.get("status") != actual:
        raise ValueError("Claimed check status contradicts recorded execution")
    if "command" in check and check["command"] != record["command"]:
        raise ValueError("Claimed check command contradicts recorded execution")
    reference = check.get("reference")
    if reference is None:
        validate_managed(record, frame.get("worktree_binding"))
        return
    if frame["skill_id"] != "code-review" or not isinstance(reference, dict) or set(reference) != {"run_id", "frame_id", "record_id", "candidate_id"}:
        raise ValueError("Review references require explicit original execution identities")
    expected = {"run_id": state["run_id"], "frame_id": record.get("binding", {}).get("frame_id"),
                "record_id": record.get("record_id"), "candidate_id": record.get("candidate", {}).get("candidate_id")}
    if reference != expected or record.get("binding", {}).get("run_id") != state["run_id"] or actual != "passed":
        raise ValueError("Review reference identity or passing qualification does not match")
    source = next((f for f in state["frames"] if f["frame_id"] == reference["frame_id"]), None)
    if not source or source["frame_id"] == frame["frame_id"] or source["status"] != "completed":
        raise ValueError("Review requires a current completed original execution source")
    if not any(c.get("reference") is None and c.get("record") == check["record"] for c in source.get("checks", [])):
        raise ValueError("Reference must identify the original accepted execution record")
    if not frame_is_current(state, source, visiting):
        raise ValueError("Original execution evidence is no longer current")
    validate_reference_candidate(record, frame.get("worktree_binding"))


def frame_is_current(state, frame, visiting=()):
    """A receipt's unchanged bytes do not establish its candidate's currentness."""
    if frame["frame_id"] in visiting or not all(evidence_is_current(e) for e in frame_evidence(frame)):
        return False
    try:
        if frame.get("worktree_binding") and not frame.get("checks"):
            validate_worktree(frame["worktree_binding"])
        for check in frame.get("checks", []):
            record = json.loads(Path(check["record"]["path"]).read_text(encoding="utf-8"))
            validate_frame_check(state, frame, check, record, (*visiting, frame["frame_id"]))
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def require_current(state):
    relevant = [f for f in state["frames"] if f["status"] not in {"superseded", "stale", "interrupted"}]
    ids = {f["frame_id"] for f in relevant}
    evidence = [e for f in relevant for e in frame_evidence(f)]
    evidence += [e for a in state["assessments"] if a["frame_id"] in ids for e in a["evidence"]]
    if not all(evidence_is_current(e) for e in evidence) or not all(frame_is_current(state, f) for f in relevant):
        raise ValueError("Input or returned evidence changed; resume to invalidate dependencies before routing")


def descendant_frame_ids(frames, roots):
    replaced = set(roots)
    while True:
        expanded = replaced | {f["frame_id"] for f in frames if f.get("parent_frame_id") in replaced}
        if expanded == replaced:
            break
        replaced = expanded
    return replaced


def supersede_tree(frames, roots):
    replaced = descendant_frame_ids(frames, roots)
    for frame in frames:
        if frame["frame_id"] in replaced:
            frame["status"] = "superseded"


def root_step_id(state, frame_id):
    frames = {f["frame_id"]: f for f in state["frames"]}
    while frame_id is not None:
        frame = frames[frame_id]
        if frame.get("step_id"):
            return frame["step_id"]
        frame_id = frame.get("parent_frame_id")
    return None


def step_restricted(state, step):
    related = {step["step_id"]}
    while True:
        expanded = related | {d for s in state["plan"] if s["step_id"] in related for d in s.get("depends_on", [])}
        if expanded == related:
            break
        related = expanded
    for restriction in scoped_restrictions(state):
        if restriction["kind"] == "system":
            if not recovery_allowed(step, restriction):
                return True
            continue
        if restriction.get("frame_id") in step.get("resolves_frames", []) and restriction["kind"] == "findings":
            continue
        if (restriction.get("scope") == "unknown" or restriction.get("step_id") in related
                or set(restriction.get("shared_preconditions", [])) & set(step.get("shared_preconditions", []))):
            return True
    return False


def scoped_restrictions(state):
    """Derive active local facts; durable capability/retry stops survive frame replacement."""
    for restriction in state["restrictions"]:
        step = next((s for s in state["plan"] if s["step_id"] == restriction.get("step_id")), {})
        yield {"shared_preconditions": step.get("shared_preconditions", []), **restriction}
    for frame in state["frames"]:
        if frame["status"] == "superseded":
            continue
        if frame["status"] in {"blocked", "waiting_user", "cancelled"} or frame.get("needs_assessment"):
            yield {"kind": "findings" if frame["status"] == "completed" else frame["status"],
                   "frame_id": frame["frame_id"], "step_id": root_step_id(state, frame["frame_id"]),
                   "scope": frame.get("block_scope", "local"),
                   "shared_preconditions": frame.get("shared_preconditions", [])}


def child_restricted(state, parent, skill_id):
    ancestors = {parent["frame_id"]}
    ancestor = parent.get("parent_frame_id")
    while ancestor is not None:
        ancestors.add(ancestor)
        ancestor = next(f for f in state["frames"] if f["frame_id"] == ancestor).get("parent_frame_id")
    for restriction in scoped_restrictions(state):
        if restriction["kind"] == "system":
            if not recovery_allowed(parent, restriction):
                return True
            continue
        if (restriction.get("scope") == "unknown" or restriction.get("frame_id") in ancestors
                or set(restriction.get("shared_preconditions", [])) & set(parent.get("shared_preconditions", []))):
            return True
        if restriction["kind"] == "no_progress":
            source = next(f for f in state["frames"] if f["frame_id"] == restriction["frame_id"])
            if source.get("parent_frame_id") == parent["frame_id"] and source["skill_id"] == skill_id:
                return True
    return False



def active_root_work(state):
    """Waiting parents are not workers; an active independent child still consumes capacity."""
    relevant = [f for f in state["frames"] if f["status"] != "superseded"]
    stopped = {r.get("frame_id") for r in state["restrictions"]}
    return {root_step_id(state, f["frame_id"]) for f in relevant
            if f["status"] == "running" and f["frame_id"] not in stopped
            and not any(c.get("parent_frame_id") == f["frame_id"] and not frame_resolved(c) for c in relevant)}



def validate_control(state):
    if state.get("control", {}).get("status") not in {"running", "paused", "cancelled", "completed"} or state["status"] != state["control"]["status"]:
        raise ValueError("Displayed status must agree with run control")


def ready_steps(state):
    if state.get("schema_version") != 4 or state["mode"] != "execute" or state["status"] != "running" or state.get("control", {}).get("status") != "running":
        return []
    relevant = [f for f in state["frames"] if f["status"] != "superseded"]
    if active_root_work(state):
        return []
    def step_complete(step, resolving=()):
        owned = {f["frame_id"] for f in relevant if f.get("step_id") == step["step_id"]}
        owned = descendant_frame_ids(relevant, owned)
        return step["status"] in {"completed", "waived"} and all(
            frame_resolved(f) or (f["status"] == "completed" and f.get("needs_assessment") and f["frame_id"] in resolving)
            for f in relevant if f["frame_id"] in owned)
    def dependencies_ready(step):
        done = [s for s in state["plan"] if step_complete(s, step.get("resolves_frames", []))]
        return (set(step.get("depends_on", [])) <= {s["step_id"] for s in done}
                and set(index[step["skill_id"]]["requires"]) <= {s["skill_id"] for s in done if s["status"] == "completed"})
    index = {s["id"]: s for s in state["registry"]["skills"] + state["temporary_skills"]}
    return [s for s in state["plan"] if s["status"] in {"pending", "failed", "blocked", "interrupted"}
            and not step_restricted(state, s)
            and dependencies_ready(s)
            and all(not f.get("needs_assessment") or f["frame_id"] in s.get("resolves_frames", [])
                    for f in relevant if f.get("step_id") == s["step_id"])
            ]


def select_registered_skill(state, project, skill_id, skill_path=None):
    index = {s["id"]: s for s in state["registry"]["skills"] + state["temporary_skills"]}
    descriptor = index.get(skill_id)
    if not descriptor or not descriptor["enabled"] or skill_id in {"develop-system", "ask-matt"}:
        raise ValueError("Skill is not an enabled execution capability")
    selected_path = skill_path or descriptor.get("path")
    if selected_path and not Path(selected_path).is_absolute():
        selected_path = Path(state["registry_path"]).parent / selected_path
    roots = [project / ".agents/skills", project / ".codex/skills", Path.home() / ".agents/skills",
             Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "skills"]
    return resolve_skill(skill_id, roots=roots, explicit=selected_path)


def validate_system_conclusions(state, item):
    conclusions = item.get("system_conclusions", [])
    if not isinstance(conclusions, list):
        raise ValueError("System conclusions must be an array")
    if conclusions and (item["status"] != "completed" or item.get("completion_checked") is not True):
        raise ValueError("System facts require a completed professional conclusion")
    seen = set()
    for conclusion in conclusions:
        if not isinstance(conclusion, dict):
            raise ValueError("System conclusion must be an object")
        identity = conclusion.get("restriction_id")
        premises = conclusion.get("failed_preconditions")
        if (not isinstance(identity, str) or not identity.strip() or identity in seen
                or conclusion.get("scope") != "global" or conclusion.get("action") not in {"set", "clear"}
                or not isinstance(premises, list) or not premises
                or any(not isinstance(p, str) or not p.strip() for p in premises)):
            raise ValueError("System conclusion needs unique identity, explicit scope and failed premises")
        seen.add(identity)
        original = next((e for e in state["events"] if e["event_id"] == item["event_id"]), None)
        if conclusion["action"] == "set" and not original and any(c.get("restriction_id") == identity for e in state["events"] for c in e.get("system_conclusions", [])):
            raise ValueError("Restriction identity already belongs to another professional event")
        if conclusion["action"] == "clear":
            if any(c["status"] != "passed" for c in item["checks"]):
                raise ValueError("Failed checks cannot support a recovery conclusion")
            source = next((e for e in state["events"] if e["event_id"] == conclusion.get("blocked_event_id")), {})
            block = next((c for c in source.get("system_conclusions", []) if c["restriction_id"] == identity and c["action"] == "set"), None)
            if not block or block["scope"] != conclusion["scope"] or block["failed_preconditions"] != premises:
                raise ValueError("Recovery conclusion must identify the exact original restriction and scope")
        proof = [file_evidence(e) for e in conclusion.get("evidence", [])]
        if not proof or any(not any(e["path"] == a["path"] and e["sha256"] == a["sha256"] for a in item["artifacts"]) for e in proof):
            raise ValueError("System conclusion must cite its current professional artifacts")
        conclusion["evidence"] = proof


def apply_system_conclusions(state, event):
    if state["control"]["status"] != "running" or not event.get("system_conclusions"):
        return
    reconcile_system_conclusions(state)


def reconcile_system_conclusions(state):
    if state["control"]["status"] != "running":
        return
    active = {}
    for event in state["events"]:
        if not event.get("system_conclusions"):
            continue
        source = next(f for f in state["frames"] if f["frame_id"] == event["frame_id"])
        latest = next(e for e in reversed(state["events"]) if e["frame_id"] == source["frame_id"])
        current = (source["status"] == "completed" and latest["event_id"] == event["event_id"]
                   and frame_is_current(state, source))
        for conclusion in event["system_conclusions"]:
            identity = conclusion["restriction_id"]
            if conclusion["action"] == "set":
                # A stale source is an unresolved safety hold, not a new professional fact.
                active[identity] = {**copy.deepcopy(conclusion), "kind": "system", "source_current": current,
                    "source_event_id": event["event_id"], "source_frame_id": event["frame_id"]}
            elif current and identity in active and active[identity]["source_event_id"] == conclusion["blocked_event_id"]:
                del active[identity]
        if current and event["event_id"] not in state.get("system_applications", []):
            state.setdefault("system_applications", []).append(event["event_id"])
    state["restrictions"] = [r for r in state["restrictions"] if r["kind"] != "system"] + list(active.values())
    explain_system_restrictions(state)


def recovery_allowed(work, restriction):
    return (restriction["restriction_id"] in work.get("recovery_for", [])
            and isinstance(work.get("authorization_reference"), str) and bool(work["authorization_reference"].strip())
            and "shared_preconditions" in work
            and not set(work["shared_preconditions"]) & set(restriction["failed_preconditions"]))


def explain_system_restrictions(state):
    active = [r for r in state["restrictions"] if r["kind"] == "system"]
    if active and state["control"]["status"] == "running":
        state["next_decision"] = {"action": "system_restricted",
            "restriction_ids": sorted(r["restriction_id"] for r in active),
            "reason": "Professional common prerequisites remain unresolved; only authorized bound recovery may proceed"}



class RunStore:
    @staticmethod
    def inspect_grill_entry(context):
        """Scan only the specified project's runs; leave all candidates byte-for-byte intact."""
        project = Path(context['project_root']).resolve(strict=True)
        candidates = []
        for path in sorted((project / '.develop-system/runs').glob('*/state.json')):
            try:
                candidate = json.loads(path.read_text(encoding='utf-8'))
                if candidate.get('run_id') != path.parent.name:
                    continue
                try:
                    require_current(candidate)
                    candidate['evidence_current'] = True
                except ValueError:
                    candidate['evidence_current'] = False
                candidates.append(candidate)
            except (OSError, ValueError, KeyError, TypeError):
                # A corrupt explicitly bound run is reported as unavailable by the decision.
                continue
        return decide_entry(context, candidates)

    def __init__(self, project_root, run_id, entry="develop-system"):
        if entry_mode(entry) != "execute":
            raise ValueError("Navigation cannot create or mutate workflow state")
        self.project = Path(project_root).resolve(strict=True)
        if not self.project.is_dir() or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", run_id):
            raise ValueError("Invalid project or run id")
        self.root = self.project / ".develop-system" / "runs" / run_id
        if not self.root.resolve().is_relative_to(self.project):
            raise ValueError("Run path escapes project")
        self.path = self.root / "state.json"
        self.run_id = run_id

    @contextmanager
    def locked(self):
        lock = self.root / ".state.lock"
        owner = uuid.uuid4().hex.encode("ascii")
        with lock.open("xb") as output:
            output.write(owner)
        try:
            yield
        finally:
            if lock.is_file() and lock.read_bytes() == owner:
                lock.unlink()

    def read(self):
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("schema_version") not in {3, 4} or data.get("controller_policy") != "dispatch-only" or data.get("mode") != "execute" or data.get("entrypoint") != "develop-system":
            raise ValueError("Legacy or navigation state requires explicit migration")
        if data.get("run_id") != self.run_id or Path(data["project_root"]).resolve() != self.project:
            raise ValueError("State belongs to another run or project")
        if data["schema_version"] == 4:
            validate_control(data)
        return data

    def write_atomic(self, state):
        if state.get("schema_version") != 4:
            raise ValueError("Legacy state is read-only until explicit migration")
        validate_control(state)
        temp = self.root / (".state-" + uuid.uuid4().hex + ".tmp")
        try:
            with temp.open("x", encoding="utf-8", newline="\n") as output:
                json.dump(state, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp, self.path)
        finally:
            if temp.is_file():
                temp.unlink()

    def create(self, goal, plan, acceptance, registry_path=SKILL_ROOT / "registry.json"):
        registry, registry_path, digest = load_registry(registry_path)
        validate_plan(plan, registry)
        if not isinstance(goal, str) or not goal.strip() or not isinstance(acceptance, list) or not acceptance:
            raise ValueError("Explicit goal and acceptance criteria required")
        criteria = []
        capabilities = {s["id"] for s in registry["skills"] if s["enabled"]} - {"develop-system", "ask-matt"}
        for i, value in enumerate(acceptance):
            value = {"criterion": value, "kind": "technical"} if isinstance(value, str) else dict(value)
            if not isinstance(value.get("criterion"), str) or not value["criterion"].strip() or value.get("kind", "technical") not in {"technical", "human"}:
                raise ValueError("Acceptance needs a criterion and technical/human kind")
            owners = value.get("allowed_skill_ids", [])
            if not isinstance(owners, list) or any(not isinstance(owner, str) or owner not in capabilities for owner in owners):
                raise ValueError("Acceptance owners must be enabled specialist skills")
            criteria.append({"id": str(i + 1), "criterion": value["criterion"], "kind": value.get("kind", "technical"),
                             "allowed_skill_ids": owners, "status": "pending", "evidence": []})
        state = {"schema_version": 4, "controller_policy": "dispatch-only", "mode": "execute", "entrypoint": "develop-system", "run_id": self.run_id,
                 "control": {"status": "running", "user_reason": None}, "restrictions": [],
                 "project_root": str(self.project), "goal": goal, "scope": [goal], "revision": 1, "status": "running",
                 "registry_path": registry_path, "registry_sha256": digest, "registry": registry, "temporary_skills": [],
                 "plan": [{**s, "status": "pending", "required": s.get("required", True)} for s in plan],
                 "acceptance": criteria,
                 "frames": [], "events": [], "assessments": [], "next_decision": {"action": "select_ready"}}
        self.root.mkdir(parents=True, exist_ok=True)
        with self.locked():
            if self.path.exists():
                raise FileExistsError("Existing run must be resumed, not replaced")
            self.write_atomic(state)
        return state

    def block_unavailable(self, revision, skill_id, reason, step_id=None, parent_frame_id=None):
        def change(state):
            if state["status"] != "running" or not reason or not reason.strip() or (step_id is None) == (parent_frame_id is None):
                raise ValueError("Missing capability requires a scoped step or active parent and reason")
            index = {s["id"]: s for s in state["registry"]["skills"] + state["temporary_skills"]}
            if skill_id not in index or not index[skill_id]["enabled"] or skill_id in {"develop-system", "ask-matt"}:
                raise ValueError("Unknown specialist capability")
            if step_id is not None:
                step = next((s for s in ready_steps(state) if s["step_id"] == step_id and s["skill_id"] == skill_id), None)
                if not step:
                    raise ValueError("Unavailable step must be ready")
                step["status"] = "blocked"
            elif not any(f["frame_id"] == parent_frame_id and f["status"] == "running" for f in state["frames"]):
                raise ValueError("Missing parent")
            state["restrictions"].append({"kind": "missing_skill", "skill_id": skill_id,
                "step_id": step_id or root_step_id(state, parent_frame_id),
                "frame_id": parent_frame_id, "reason": reason})
            state["next_decision"] = {"action":"missing_skill", "skill_id":skill_id, "step_id":step_id,
                                      "parent_frame_id":parent_frame_id, "reason":reason}
            return state
        return self.mutate(revision, change)

    def mutate(self, expected_revision, operation):
        with self.locked():
            old = self.read()
            if old["schema_version"] != 4:
                raise ValueError("Legacy state is read-only until explicit migration")
            if old["revision"] != expected_revision:
                raise ValueError("Revision conflict; reread state before retrying")
            new = operation(copy.deepcopy(old))
            if new == old:
                return old
            new["revision"] = old["revision"] + 1
            self.write_atomic(new)
            return new

    def register(self, revision, descriptor):
        def change(state):
            if state["status"] != "running":
                raise ValueError("Resume before registration")
            merged = copy.deepcopy(state["registry"])
            merged["skills"] += state["temporary_skills"] + [descriptor]
            errors = validate(merged, Path(state["registry_path"]).parent)
            if errors:
                raise ValueError("; ".join(errors))
            state["temporary_skills"].append(copy.deepcopy(descriptor))
            return state
        return self.mutate(revision, change)

    def extend_plan(self, revision, steps, reason):
        def change(state):
            if state["status"] != "running" or not isinstance(steps, list) or not steps or not reason or not reason.strip():
                raise ValueError("In-scope follow-up steps and a reason are required")
            merged = copy.deepcopy(state["registry"])
            merged["skills"] += state["temporary_skills"]
            validate_plan(state["plan"] + steps, merged)
            known = {f["frame_id"] for f in state["frames"] if f["status"] != "superseded"}
            for step in steps:
                targets = step.get("resolves_frames", [])
                if not isinstance(targets, list) or any(not isinstance(t, str) or t not in known for t in targets):
                    raise ValueError("Follow-up must reference current frames")
            state["plan"].extend({**s, "status": "pending", "required": s.get("required", True)} for s in steps)
            state.setdefault("plan_changes", []).append({"reason": reason, "added_steps": [s["step_id"] for s in steps]})
            state["next_decision"] = {"action": "select_ready"}
            return state
        return self.mutate(revision, change)

    def dispatch(self, revision, skill_id, input_version, step_id=None, parent_frame_id=None, inputs=(), skill_path=None, require_original=False, required=True, worktree=None):
        return self.mutate(revision, lambda state: self._dispatch_state(state, skill_id, input_version,
            step_id, parent_frame_id, inputs, skill_path, require_original, required, worktree))

    def _dispatch_state(self, state, skill_id, input_version, step_id=None, parent_frame_id=None, inputs=(), skill_path=None, require_original=False, required=True, worktree=None):
        require_current(state)
        if state["status"] != "running" or not input_version or (step_id is None) == (parent_frame_id is None):
            raise ValueError("Running execution and exactly one step or parent are required")
        index = {s["id"]: s for s in state["registry"]["skills"] + state["temporary_skills"]}
        descriptor = index.get(skill_id)
        if not descriptor or not descriptor["enabled"] or skill_id in {"develop-system", "ask-matt"}:
            raise ValueError("Skill is not an enabled execution capability")
        if step_id is not None:
            step = next((s for s in ready_steps(state) if s["step_id"] == step_id and s["skill_id"] == skill_id), None)
            if step is None:
                raise ValueError("Step is not ready")
            previous = [f for f in state["frames"] if f.get("step_id") == step_id]
            supersede_tree(state["frames"], {f["frame_id"] for f in previous})
            step["status"] = "running"
        else:
            parent = next((f for f in state["frames"] if f["frame_id"] == parent_frame_id), None)
            if parent is None or parent["status"] != "running":
                raise ValueError("Parent is not active")
            if active_root_work(state) - {root_step_id(state, parent_frame_id)}:
                raise ValueError("Another root owns the available execution capacity")
            if child_restricted(state, parent, skill_id):
                raise ValueError("Child is affected by unresolved scoped work")
            supersede_tree(state["frames"], {prior["frame_id"] for prior in state["frames"]
                if prior.get("parent_frame_id") == parent_frame_id and prior["skill_id"] == skill_id
                and prior["status"] in {"failed", "blocked", "interrupted", "stale"} and not prior.get("needs_assessment")})
        try:
            selected = select_registered_skill(state, self.project, skill_id, skill_path)
            mode = "skill"
        except FileNotFoundError:
            raise FileNotFoundError("Missing specialist skill; dispatch-only controller cannot execute inline: " + skill_id)
        captured = [file_evidence(item) for item in inputs]
        prerequisites = (step if step_id is not None else parent).get("shared_preconditions", [])
        if selected["skill_path"]:
            captured.append(file_evidence(selected["skill_path"]))
        state["frames"].append({"frame_id": uuid.uuid4().hex, "parent_frame_id": parent_frame_id, "step_id": step_id,
                                "protocol_version": 1,
                                "shared_preconditions": copy.deepcopy(prerequisites),
                                "recovery_for": copy.deepcopy((step if step_id is not None else parent).get("recovery_for", [])),
                                "authorization_reference": (step if step_id is not None else parent).get("authorization_reference"),
                                "skill_id": skill_id, "skill_path": selected["skill_path"], "skill_sha256": selected["sha256"], "mode": mode,
                                "done_when": descriptor["done_when"], "status": "running", "required": step["required"] if step_id is not None else required, "input_version": input_version,
                                "inputs": captured, "artifacts": [], "needs_assessment": False})
        frame = state["frames"][-1]
        frame["worktree_binding"] = (bind_worktree(self.project, state["run_id"], frame["frame_id"],
            input_version, worktree) if worktree is not None else None)
        state["next_decision"] = {"action": "execute_frame", "frame_id": frame["frame_id"], "return_to": "develop-system"}
        return state

    def receive(self, revision, result):
        def change(state):
            require_current(state)
            item = copy.deepcopy(result)
            if item.get("mode") != "skill" or item.get("execution") not in {"sequential", "delegated"}:
                raise ValueError("Return must identify actual loading and execution modes")
            selected_frame = next((f for f in state["frames"] if f["frame_id"] == item.get("frame_id")), None)
            if selected_frame is None or item["mode"] != selected_frame["mode"]:
                raise ValueError("Return mode does not match dispatched frame")
            if selected_frame.get("completion_invalidated_by") and selected_frame.get("needs_assessment"):
                raise ValueError("Prior parent findings require professional assessment before requalification")
            if item.get("block_scope", "local") not in {"local", "unknown"}:
                raise ValueError("Block scope must be local or explicitly unknown")
            if item["status"] == "completed" and any(
                    r.get("frame_id") == selected_frame["frame_id"] or
                    (selected_frame.get("step_id") is not None and r.get("step_id") == selected_frame["step_id"])
                    for r in state["restrictions"]):
                raise ValueError("Necessary scoped restrictions remain unresolved")
            if item["status"] == "skipped" and (selected_frame["required"] or not item.get("skip_reason")):
                raise ValueError("Only optional frames with a reason can be skipped")
            item["artifacts"] = [file_evidence(a) for a in item["artifacts"]]
            if not isinstance(item.get("checks"), list):
                raise ValueError("checks must be an array")
            for check in item["checks"]:
                if not isinstance(check, dict) or "record" not in check:
                    raise ValueError("Command checks require a machine-generated record")
                check["record"] = file_evidence(check["record"])
                recorded = json.loads(Path(check["record"]["path"]).read_text(encoding="utf-8"))
                validate_frame_check(state, selected_frame, check, recorded)

            if item.get('skill_id') == 'grill-with-docs' and item.get('status') == 'completed':
                delivery = item.get('grill_delivery')
                if not isinstance(delivery, dict) or any(delivery.get(k) is not True
                    for k in ('confirmed','core_questions_resolved','helpers_resolved')):
                    raise ValueError('Grill completion needs current document confirmation and resolved questions/helpers')
                if any(not isinstance(delivery.get(k), str) or not delivery[k].strip()
                       for k in ('version','confirmation_reference')) or not isinstance(delivery.get('checklist'), list) or not delivery['checklist']:
                    raise ValueError('Grill completion needs version, real confirmation reference and delivery checklist')
                document = file_evidence(delivery['document'])
                if not any(a['path'] == document['path'] and a['sha256'] == document['sha256'] for a in item['artifacts']):
                    raise ValueError('Confirmed document must be a returned artifact')
                item['grill_delivery']['document'] = document
            if item["status"] == "completed" and not item["artifacts"] and not item["checks"]:
                raise ValueError("Completed return needs actual artifacts or checks")
            validate_system_conclusions(state, item)
            acceptance = item.get("acceptance_checks", [])
            resolutions = item.get("resolved_findings", [])
            if not isinstance(acceptance, list) or not isinstance(resolutions, list) or any(not isinstance(v, str) for v in resolutions):
                raise ValueError("Specialist conclusions must be arrays")
            if resolutions and item["status"] != "completed":
                raise ValueError("Only completed specialist work can resolve findings")
            current_findings = {f["frame_id"] for f in state["frames"] if f["status"] != "superseded" and f.get("needs_assessment")}
            if not set(resolutions) <= current_findings:
                # Repeated events may reference already resolved findings; compare their original event first.
                original = next((e for e in state["events"] if e["event_id"] == item["event_id"]), None)
                if original is None or original.get("resolved_findings", []) != resolutions:
                    raise ValueError("Resolution must name current unresolved frames")
            known_criteria = {c["id"]: c for c in state["acceptance"]}
            ids = set()
            for check in acceptance:
                if not isinstance(check, dict) or check.get("id") not in known_criteria or check["id"] in ids:
                    raise ValueError("Unknown or duplicate acceptance criterion")
                ids.add(check["id"])
                if check.get("kind") not in {"technical", "human"} or check.get("status") not in {"passed", "failed", "pending"}:
                    raise ValueError("Acceptance must identify kind and status")
                criterion = known_criteria[check["id"]]
                if check["kind"] != criterion["kind"] or (criterion["allowed_skill_ids"] and item["skill_id"] not in criterion["allowed_skill_ids"]):
                    raise ValueError("Acceptance must match the assigned kind and specialist owners")
                check["evidence"] = [file_evidence(e) for e in check.get("evidence", [])]
                if check["status"] == "passed":
                    if any(c["status"] != "passed" for c in item["checks"]):
                        raise ValueError("Failed command checks cannot support passed acceptance")
                    if item["status"] != "completed" or check.get("completion_checked") is not True or not check["evidence"]:
                        raise ValueError("Passed acceptance needs a completed specialist and evidence")
                    if check["kind"] == "human" and (check.get("human_confirmed") is not True or not isinstance(check.get("user_reference"), str) or not check["user_reference"].strip()):
                        raise ValueError("Human acceptance requires an actual user confirmation reference")
            updated, decision = accept_result(state, item)
            if decision["action"] == "duplicate":
                return state
            frame = next(f for f in updated["frames"] if f["frame_id"] == item["frame_id"])
            frame["artifacts"] = item["artifacts"]
            frame["checks"] = item["checks"]
            frame["acceptance_checks"] = acceptance
            frame["resolved_findings"] = resolutions
            frame["block_scope"] = item.get("block_scope", "local")
            if item["status"] == "completed":
                frame.pop("completion_invalidated_by", None)
            if item["status"] == "skipped":
                frame["skip_reason"] = item["skip_reason"]
            if frame.get("step_id"):
                step = next(s for s in updated["plan"] if s["step_id"] == frame["step_id"])
                step["status"] = item["status"]
            # A specialist's local outcome does not change user run control.
            if item["status"] in {"failed", "blocked"}:
                def signature(event):
                    owner = next(f for f in updated["frames"] if f["frame_id"] == event["frame_id"])
                    return (owner.get("step_id"), owner.get("parent_frame_id"), event["skill_id"],
                            event.get("input_version"), event["status"], event["blockers"], event["artifacts"], event["checks"])
                current_signature = signature(item)
                matching = [e for e in updated["events"] if signature(e) == current_signature]
                if len(matching) >= 2:
                    restriction = {"kind": "no_progress", "step_id": root_step_id(updated, frame["frame_id"]),
                                   "frame_id": frame["frame_id"], "signature": list(current_signature)}
                    if restriction not in updated["restrictions"]:
                        updated["restrictions"].append(restriction)
                    if updated["control"]["status"] == "running":
                        updated["next_decision"] = {"action": "stop_repeated_no_progress", "signature": list(current_signature),
                                                    "frame_id": frame["frame_id"], "scope": "local"}
            apply_system_conclusions(updated, item)
            explain_system_restrictions(updated)
            return updated
        return self.mutate(revision, change)

    def attach_grill(self, revision, context, inputs=(), skill_path=None):
        """Register/reuse an interview within one locked revision; never init or guess a parent."""
        if Path(context['project_root']).resolve() != self.project:
            raise ValueError('Entry context belongs to another project')
        def change(state):
            require_current(state)
            decision = self.inspect_grill_entry(context)
            if decision['action'] != 'managed' or decision['run_id'] != self.run_id:
                raise ValueError('Resolve entry ownership/recovery before registration: ' + decision['reason'])
            version = context.get('input_version')
            if not version:
                raise ValueError('Current input version is required')
            bound_id = decision.get('frame_id')
            bound = next((f for f in state['frames'] if f['frame_id'] == bound_id), None)
            parent_id = context.get('parent_frame_id')
            if bound and bound['skill_id'] != 'grill-with-docs':
                if parent_id and parent_id != bound_id:
                    raise ValueError('Parent disagrees with frame binding')
                parent_id = bound_id
            if parent_id:
                parent = next((f for f in state['frames'] if f['frame_id'] == parent_id), None)
                if not parent or parent['status'] != 'running':
                    raise ValueError('Professional parent must be active')
            if bound and bound['skill_id'] == 'grill-with-docs':
                if parent_id and parent_id != bound.get('parent_frame_id'):
                    raise ValueError('Interview belongs to another parent')
                parent_id = bound.get('parent_frame_id')
            active_grills = [f for f in state['frames'] if f['skill_id'] == 'grill-with-docs'
                and f['status'] == 'running' and f.get('parent_frame_id') == parent_id]
            if bound and bound['skill_id'] == 'grill-with-docs':
                active_grills = [f for f in active_grills if f['frame_id'] == bound_id]
            if len(active_grills) > 1:
                raise ValueError('Multiple active interview frames require explicit ownership')
            if active_grills:
                existing = active_grills[0]
                supplied = [file_evidence(p) for p in inputs]
                if existing['input_version'] != version or any(e not in existing['inputs'] for e in supplied):
                    raise ValueError('Interview inputs changed; resume and redispatch before reuse')
                if skill_path and str(Path(skill_path).resolve()) != existing['skill_path']:
                    raise ValueError('Interview skill selection changed')
                return state
            if parent_id:
                return self._dispatch_state(state, 'grill-with-docs', version, parent_frame_id=parent_id,
                                            inputs=inputs, skill_path=skill_path)
            if any(f['status'] == 'running' for f in state['frames']):
                raise ValueError('Active professional work requires an explicit parent frame')
            requested = context.get('step_id')
            ready = [s for s in ready_steps(state) if s['skill_id'] == 'grill-with-docs'
                     and (requested is None or s['step_id'] == requested)]
            if len(ready) != 1:
                raise ValueError('Register one dependency-valid root grill plan step before attachment')
            return self._dispatch_state(state, 'grill-with-docs', version, step_id=ready[0]['step_id'],
                                        inputs=inputs, skill_path=skill_path)
        return self.mutate(revision, change)

    def route_grill(self, revision, event_id):
        """Append necessary root stages only after a current specialist return is persisted."""
        def change(state):
            require_current(state)
            if state['status'] != 'running':
                raise ValueError('Resume the run before routing')
            event = next((e for e in state['events'] if e['event_id'] == event_id), None)
            frame = next((f for f in state['frames'] if event and f['frame_id'] == event['frame_id']), None)
            if (not event or not frame or event['skill_id'] != 'grill-with-docs' or frame['status'] != 'completed'
                    or event['status'] != 'completed' or frame.get('parent_frame_id') is not None
                    or frame.get('needs_assessment') or not frame.get('step_id')):
                raise ValueError('Routing requires a completed, resolved root grill return')
            previous = next((r for r in state.get('grill_routes', []) if r['source_event_id'] == event_id), None)
            if previous:
                return state
            decision = decide_root_route(event['grill_delivery'])
            if decision['action'] != 'route':
                raise ValueError('Interview must complete before choosing a root route')
            dependency = frame['step_id']
            steps = []
            route_ids, reused = [], []
            finished = {s['step_id'] for s in state['plan'] if s['status'] == 'completed'}
            for skill in decision['skills']:
                reusable = next((s for s in state['plan'] if s['skill_id'] == skill
                    and s.get('requirement_version') == decision['requirement_version'] and s.get('scope') == state['scope']
                    and (s['status'] == 'pending' or
                         (s['status'] == 'completed' and set(s.get('depends_on', [])) <= finished
                          and any(f.get('step_id') == s['step_id'] and f['skill_id'] == skill and frame_resolved(f)
                                  and f.get('artifacts') and frame_is_current(state, f)
                                  for f in state['frames'])))), None)
                if reusable:
                    if reusable['status'] == 'pending':
                        reusable['depends_on'] = list(dict.fromkeys(reusable.get('depends_on', []) + [frame['step_id'],dependency]))
                        reusable['source_event_id'] = event_id
                    dependency = reusable['step_id']; reused.append(dependency); route_ids.append(dependency)
                    continue
                step_id = 'grill-' + frame['frame_id'][:12] + '-' + skill
                steps.append({'step_id':step_id, 'skill_id':skill, 'depends_on':list(dict.fromkeys([frame['step_id'],dependency])),
                              'required':True, 'status':'pending', 'requirement_version':decision['requirement_version'],
                              'scope':copy.deepcopy(state['scope']), 'source_event_id':event_id})
                route_ids.append(step_id)
                dependency = step_id
            replaced_steps = set()
            for old_route in state.get('grill_routes', []):
                old_frame = next((f for f in state['frames'] if f['frame_id'] == old_route['source_frame_id']), None)
                if old_frame and old_frame.get('step_id') == frame['step_id']:
                    # Reused external artifacts remain owned by their original plan, not this branch.
                    replaced_steps.update(set(old_route['step_ids']) - set(old_route.get('reused_step_ids', [])) - set(route_ids))
                    old_route['superseded_by'] = event_id
            replaced_frames = descendant_frame_ids(state['frames'],
                {f['frame_id'] for f in state['frames'] if f.get('step_id') in replaced_steps})
            if any(f.get('needs_assessment') for f in state['frames'] if f['frame_id'] in replaced_frames):
                raise ValueError('Resolve previous branch findings before retiring its tasks')
            for step in state['plan']:
                if step['step_id'] in replaced_steps:
                    step.update(required=False, status='waived', superseded_by=event_id)
            supersede_tree(state['frames'], replaced_frames)
            merged = copy.deepcopy(state['registry']); merged['skills'] += state['temporary_skills']
            validate_plan(state['plan'] + steps, merged)
            state['plan'].extend(steps)
            state.setdefault('grill_routes', []).append({**decision, 'source_event_id':event_id,
                'source_frame_id':frame['frame_id'], 'input_version':event['input_version'],
                'step_ids':route_ids, 'reused_step_ids':reused,
                'document':copy.deepcopy(event['grill_delivery']['document'])})
            state['next_decision'] = {'action':'select_ready', 'return_to':'develop-system'}
            return state
        return self.mutate(revision, change)

    def assess(self, revision, frame_id, resolution, evidence, source_frame_id=None):
        def change(state):
            if state["control"]["status"] != "running":
                raise ValueError("Finding assessment requires explicit user continuation")
            frame = next(f for f in state["frames"] if f["frame_id"] == frame_id)
            if not frame.get("needs_assessment") or not resolution.strip() or not evidence:
                raise ValueError("Assessment needs unresolved findings, a resolution and evidence")
            source = next((f for f in state["frames"] if f["frame_id"] == source_frame_id), None)
            event = next((e for e in reversed(state["events"]) if e["frame_id"] == source_frame_id), None)
            if not source or source["status"] != "completed" or not frame_resolved(source) or not event or frame_id not in event.get("resolved_findings", []):
                raise ValueError("Findings require a completed specialist resolution source")
            captured = [file_evidence(e) for e in evidence]
            declared = event.get("artifacts", []) + [e for c in event.get("acceptance_checks", []) for e in c["evidence"]]
            if any(not any(e["path"] == d["path"] and e["sha256"] == d["sha256"] for d in declared) for e in captured):
                raise ValueError("Resolution evidence must belong to the specialist result")
            if not frame_is_current(state, source):
                raise ValueError("Resolution source is stale")
            state["assessments"].append({"frame_id": frame_id, "source_frame_id": source_frame_id,
                                         "resolution": resolution, "evidence": captured})
            frame["needs_assessment"] = False
            state["next_decision"] = {"action": "reassess_parent_or_next"}
            return state
        return self.mutate(revision, change)

    def control(self, revision, status, reason):
        if status not in {"paused", "cancelled"} or not reason or not reason.strip():
            raise ValueError("Explicit user pause/cancel reason required")
        def change(state):
            if state["status"] in {"completed", "cancelled"}:
                raise ValueError("Run is already closed")
            state["status"] = status
            state["control"] = {"status": status, "user_reason": reason}
            state["next_decision"] = {"action": status, "user_reason": reason}
            return state
        return self.mutate(revision, change)

    def resume(self, revision, user_reference=None):
        def change(state):
            if state["status"] in {"completed", "cancelled"}:
                raise ValueError("Closed run needs a new explicit goal")
            user_paused = state["control"]["status"] == "paused"
            if user_paused and (not isinstance(user_reference, str) or not user_reference.strip()):
                raise ValueError("User pause requires a real user continuation reference")
            remaining = []
            for restriction in state["restrictions"]:
                if restriction["kind"] == "missing_skill":
                    try:
                        select_registered_skill(state, self.project, restriction["skill_id"])
                    except (OSError, ValueError):
                        remaining.append(restriction)
                else:
                    remaining.append(restriction)
            state["restrictions"] = remaining
            stale = set()
            affected = set()
            def invalidate(frame):
                newly_stale = frame["status"] != "stale"
                frame["status"] = "stale"
                affected.add(root_step_id(state, frame["frame_id"]))
                if frame.get("step_id"):
                    stale.add(frame["step_id"])
                parent = frame.get("parent_frame_id")
                while parent:
                    ancestor = next(f for f in state["frames"] if f["frame_id"] == parent)
                    # Child changes invalidate completion, not independent sibling inputs.
                    if ancestor["status"] == "completed" or (
                            ancestor["status"] == "running" and ancestor.get("completion_invalidated_by")):
                        ancestor["status"] = "running"
                        causes = ancestor.setdefault("completion_invalidated_by", [])
                        if frame["frame_id"] not in causes:
                            causes.append(frame["frame_id"])
                        if ancestor.get("step_id"):
                            next(s for s in state["plan"] if s["step_id"] == ancestor["step_id"])["status"] = "running"
                    parent = ancestor.get("parent_frame_id")
                return newly_stale
            for frame in state["frames"]:
                if frame["status"] == "superseded":
                    continue
                evidence = frame_evidence(frame) + [e for a in state["assessments"] if a["frame_id"] == frame["frame_id"] for e in a["evidence"]]
                if not all(evidence_is_current(e) for e in evidence) or not frame_is_current(state, frame):
                    invalidate(frame)
            changed = True
            capabilities = {s["id"]: s for s in state["registry"]["skills"] + state["temporary_skills"]}
            while changed:
                changed = False
                affected_skills = {s["skill_id"] for s in state["plan"] if s["step_id"] in affected}
                for step in state["plan"]:
                    dependency_changed = (set(step.get("depends_on", [])) & affected
                        or set(capabilities[step["skill_id"]]["requires"]) & affected_skills)
                    if dependency_changed and step["step_id"] not in stale:
                        stale.add(step["step_id"]); affected.add(step["step_id"]); changed = True
                invalidated = descendant_frame_ids(state["frames"],
                    {f["frame_id"] for f in state["frames"] if f["status"] == "stale" or f.get("step_id") in stale})
                frames = {f["frame_id"]: f for f in state["frames"]}
                for frame in state["frames"]:
                    if frame["status"] in {"stale", "superseded"}:
                        continue
                    # References add dependency edges without relabeling execution.
                    # Close these edges together with plan and descendant changes.
                    source_changed = any(frames[check["reference"]["frame_id"]]["status"] != "completed"
                        for check in frame.get("checks", []) if check.get("reference"))
                    if frame["frame_id"] in invalidated or source_changed:
                        changed = invalidate(frame) or changed
            for step in state["plan"]:
                if step["step_id"] in stale:
                    step["status"] = "pending"
            for assessment in state["assessments"]:
                source = next(f for f in state["frames"] if f["frame_id"] == assessment["source_frame_id"])
                if source["status"] in {"stale", "superseded", "interrupted"}:
                    target = next(f for f in state["frames"] if f["frame_id"] == assessment["frame_id"])
                    if target["status"] != "superseded":
                        target["needs_assessment"] = True
            if affected:
                for criterion in state["acceptance"]:
                    source = next((f for f in state["frames"] if f["frame_id"] == criterion.get("source_frame_id")), None)
                    if source and (source["status"] in {"stale", "superseded", "interrupted"} or source.get("completion_invalidated_by")):
                        criterion.update(status="pending", evidence=[])
            state["status"] = "running"
            state["control"] = {"status": "running", "user_reason": None}
            if user_paused:
                state["control"]["resume_user_reference"] = user_reference
            state["next_decision"] = {"action": "revalidate_and_select", "invalidated_steps": sorted(stale),
                                      "note": "File hashes do not prove live runtime health; recheck environmental blockers and actual verification."}
            reconcile_system_conclusions(state)
            explain_system_restrictions(state)
            return state
        return self.mutate(revision, change)

    def complete(self, revision, checks):
        def change(state):
            if any(r["kind"] == "system" for r in state["restrictions"]):
                raise ValueError("Professional system restrictions remain unresolved")
            if state["status"] != "running" or any(s["required"] and s["status"] != "completed" for s in state["plan"]):
                raise ValueError("Necessary scoped steps are unfinished")
            relevant = [f for f in state["frames"] if f["status"] != "superseded"]
            if any(not frame_resolved(f) for f in relevant):
                raise ValueError("Unresolved skill work or findings remain")
            if any(not frame_is_current(state, f) for f in relevant):
                raise ValueError("Revalidate changed artifacts before completion")
            current_ids = {f["frame_id"] for f in relevant}
            if any(not evidence_is_current(e) for a in state["assessments"] if a["frame_id"] in current_ids for e in a["evidence"]):
                raise ValueError("Revalidate changed assessment evidence before completion")
            index = {c["id"]: c for c in checks}
            if len(index) != len(checks) or set(index) != {c["id"] for c in state["acceptance"]}:
                raise ValueError("Every acceptance criterion needs one checked result")
            for criterion in state["acceptance"]:
                reference = index[criterion["id"]]
                if set(reference) != {"id", "source_frame_id"}:
                    raise ValueError("Completion only accepts specialist conclusion references, not controller assertions")
                source = next((f for f in relevant if f["frame_id"] == reference["source_frame_id"]), None)
                event = next((e for e in reversed(state["events"]) if e["frame_id"] == reference["source_frame_id"]), None)
                if not source or source["status"] != "completed" or not event or source["skill_id"] in {"develop-system", "ask-matt"}:
                    raise ValueError("Acceptance source must be a current completed specialist frame")
                check = next((c for c in event.get("acceptance_checks", []) if c["id"] == criterion["id"]), None)
                if not check or check.get("status") != "passed" or check.get("completion_checked") is not True or not check.get("evidence"):
                    raise ValueError("Acceptance lacks a passed specialist conclusion")
                criterion.update(status="passed", evidence=[file_evidence(e) for e in check["evidence"]],
                                 source_frame_id=source["frame_id"], source_skill_id=source["skill_id"], kind=check["kind"])
            state["status"] = "completed"
            state["control"]["status"] = "completed"
            state["next_decision"] = {"action": "complete", "return_to": "develop-system"}
            return state
        return self.mutate(revision, change)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "show", "next", "inspect-grill", "attach-grill", "route-grill", "dispatch", "block", "return", "register", "extend-plan", "assess", "resume", "complete", "pause", "cancel"])
    parser.add_argument("--entry", required=True, choices=["develop-system", "ask-matt"])
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--revision", type=int)
    parser.add_argument("--goal")
    parser.add_argument("--plan")
    parser.add_argument("--acceptance")
    parser.add_argument("--registry", default=str(SKILL_ROOT / "registry.json"))
    parser.add_argument("--skill-id")
    parser.add_argument("--step-id")
    parser.add_argument("--parent-frame-id")
    parser.add_argument("--frame-id")
    parser.add_argument("--source-frame-id")
    parser.add_argument("--input-version")
    parser.add_argument("--skill-path")
    parser.add_argument("--require-original", action="store_true")
    parser.add_argument("--optional", action="store_true")
    parser.add_argument("--worktree", help="Explicit authorized worktree scope JSON for this dispatch")
    parser.add_argument("--reason")
    parser.add_argument("--user-reference")
    parser.add_argument("--input", action="append", default=[])
    parser.add_argument("--evidence", action="append", default=[])
    parser.add_argument("--resolution")
    parser.add_argument("--payload")
    parser.add_argument('--event-id')
    args = parser.parse_args()
    def read(path):
        return json.loads(Path(path).read_text(encoding="utf-8"))
    try:
        store = RunStore(args.project_root, args.run_id, args.entry)
        if args.command == 'inspect-grill':
            context = read(args.payload)
            context['project_root'] = str(store.project)
            value = store.inspect_grill_entry(context)
        elif args.command == "init":
            value = store.create(args.goal, read(args.plan), read(args.acceptance), args.registry)
        elif args.command in {"show", "next"}:
            state = store.read();value = state if args.command == "show" else {"ready_steps": ready_steps(state), "next_decision": state["next_decision"]}
        else:
            if args.revision is None:
                raise ValueError("Mutation requires the revision returned by show")
            if args.command == "dispatch":
                value = store.dispatch(args.revision,args.skill_id,args.input_version,args.step_id,args.parent_frame_id,args.input,args.skill_path,args.require_original,not args.optional,
                    worktree=read(args.worktree) if args.worktree else None)
            elif args.command == 'attach-grill':value = store.attach_grill(args.revision,read(args.payload),args.input,args.skill_path)
            elif args.command == 'route-grill':value = store.route_grill(args.revision,args.event_id)
            elif args.command == "block":value = store.block_unavailable(args.revision,args.skill_id,args.reason,args.step_id,args.parent_frame_id)
            elif args.command == "return":value = store.receive(args.revision,read(args.payload))
            elif args.command == "register":value = store.register(args.revision,read(args.payload))
            elif args.command == "extend-plan":value = store.extend_plan(args.revision,read(args.payload),args.reason)
            elif args.command == "assess":value = store.assess(args.revision,args.frame_id,args.resolution,args.evidence,args.source_frame_id)
            elif args.command == "resume":value = store.resume(args.revision,args.user_reference)
            elif args.command in {"pause", "cancel"}:value = store.control(args.revision,"paused" if args.command == "pause" else "cancelled",args.reason)
            else:value = store.complete(args.revision,read(args.payload))
        print(json.dumps(value,ensure_ascii=False,indent=2))
        return 0
    except (OSError,ValueError,KeyError,TypeError,StopIteration) as exc:
        print(json.dumps({"ok":False,"error":str(exc)},ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
