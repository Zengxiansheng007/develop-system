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


def require_current(state):
    relevant = [f for f in state["frames"] if f["status"] not in {"superseded", "stale", "interrupted"}]
    ids = {f["frame_id"] for f in relevant}
    evidence = [e for f in relevant for e in frame_evidence(f)]
    evidence += [e for a in state["assessments"] if a["frame_id"] in ids for e in a["evidence"]]
    if not all(evidence_is_current(e) for e in evidence):
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


def ready_steps(state):
    if state["mode"] != "execute" or state["status"] != "running":
        return []
    relevant = [f for f in state["frames"] if f["status"] != "superseded"]
    if any(f["status"] == "running" for f in relevant):
        return []
    unresolved = {f["frame_id"] for f in relevant if f.get("needs_assessment")}
    done = {s["step_id"] for s in state["plan"] if s["status"] in {"completed", "waived"}}
    done_skills = {s["skill_id"] for s in state["plan"] if s["status"] == "completed"}
    index = {s["id"]: s for s in state["registry"]["skills"] + state["temporary_skills"]}
    return [s for s in state["plan"] if s["status"] in {"pending", "failed", "blocked", "interrupted"}
            and set(s.get("depends_on", [])) <= done
            and unresolved <= set(s.get("resolves_frames", []))
            and set(index[s["skill_id"]]["requires"]) <= done_skills]


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
        if data.get("schema_version") != 3 or data.get("controller_policy") != "dispatch-only" or data.get("mode") != "execute" or data.get("entrypoint") != "develop-system":
            raise ValueError("Legacy or navigation state requires explicit migration")
        if data.get("run_id") != self.run_id or Path(data["project_root"]).resolve() != self.project:
            raise ValueError("State belongs to another run or project")
        return data

    def write_atomic(self, state):
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
        state = {"schema_version": 3, "controller_policy": "dispatch-only", "mode": "execute", "entrypoint": "develop-system", "run_id": self.run_id,
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
            state["status"] = "blocked"
            state["next_decision"] = {"action":"missing_skill", "skill_id":skill_id, "step_id":step_id,
                                      "parent_frame_id":parent_frame_id, "reason":reason}
            return state
        return self.mutate(revision, change)

    def mutate(self, expected_revision, operation):
        with self.locked():
            old = self.read()
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

    def dispatch(self, revision, skill_id, input_version, step_id=None, parent_frame_id=None, inputs=(), skill_path=None, require_original=False, required=True):
        return self.mutate(revision, lambda state: self._dispatch_state(state, skill_id, input_version,
            step_id, parent_frame_id, inputs, skill_path, require_original, required))

    def _dispatch_state(self, state, skill_id, input_version, step_id=None, parent_frame_id=None, inputs=(), skill_path=None, require_original=False, required=True):
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
            supersede_tree(state["frames"], {prior["frame_id"] for prior in state["frames"]
                if prior.get("parent_frame_id") == parent_frame_id and prior["skill_id"] == skill_id
                and prior["status"] in {"failed", "blocked", "interrupted", "stale"} and not prior.get("needs_assessment")})
        selected_path = skill_path or descriptor.get("path")
        if selected_path and not Path(selected_path).is_absolute():
            selected_path = Path(state["registry_path"]).parent / selected_path
        roots = [self.project / ".agents/skills", self.project / ".codex/skills", Path.home() / ".agents/skills",
                 Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "skills"]
        try:
            selected = resolve_skill(skill_id, roots=roots, explicit=selected_path)
            mode = "skill"
        except FileNotFoundError:
            raise FileNotFoundError("Missing specialist skill; dispatch-only controller cannot execute inline: " + skill_id)
        captured = [file_evidence(item) for item in inputs]
        if selected["skill_path"]:
            captured.append(file_evidence(selected["skill_path"]))
        state["frames"].append({"frame_id": uuid.uuid4().hex, "parent_frame_id": parent_frame_id, "step_id": step_id,
                                "skill_id": skill_id, "skill_path": selected["skill_path"], "skill_sha256": selected["sha256"], "mode": mode,
                                "done_when": descriptor["done_when"], "status": "running", "required": step["required"] if step_id is not None else required, "input_version": input_version,
                                "inputs": captured, "artifacts": [], "needs_assessment": False})
        state["next_decision"] = {"action": "execute_frame", "frame_id": state["frames"][-1]["frame_id"], "return_to": "develop-system"}
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
                actual = validate_record(recorded)
                if not Path(recorded["cwd"]).resolve().is_relative_to(self.project):
                    raise ValueError("Check record belongs to another project")
                if check.get("status") != actual:
                    raise ValueError("Claimed check status contradicts recorded execution")
                if "command" in check and check["command"] != recorded["command"]:
                    raise ValueError("Claimed check command contradicts recorded execution")

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
            if item["status"] == "skipped":
                frame["skip_reason"] = item["skip_reason"]
            if frame.get("step_id"):
                step = next(s for s in updated["plan"] if s["step_id"] == frame["step_id"])
                step["status"] = item["status"]
            if item["status"] in {"waiting_user", "blocked", "cancelled"}:
                updated["status"] = item["status"]
            if item["status"] in {"failed", "blocked"}:
                signature = (item["skill_id"], item.get("input_version"), item["blockers"], item["artifacts"], item["checks"])
                matching = [e for e in updated["events"] if (e["skill_id"], e.get("input_version"), e["blockers"], e["artifacts"], e["checks"]) == signature]
                if len(matching) >= 2:
                    updated["status"] = "blocked"
                    updated["next_decision"] = {"action": "stop_repeated_no_progress", "signature": list(signature)}
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
                                  and f.get('artifacts') and all(evidence_is_current(e) for e in frame_evidence(f))
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
            source_evidence = frame_evidence(source)
            if not all(evidence_is_current(e) for e in source_evidence):
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
            state["next_decision"] = {"action": status, "user_reason": reason}
            return state
        return self.mutate(revision, change)

    def resume(self, revision):
        def change(state):
            if state["status"] in {"completed", "cancelled"}:
                raise ValueError("Closed run needs a new explicit goal")
            stale = set()
            for frame in state["frames"]:
                if frame["status"] == "superseded":
                    continue
                evidence = frame_evidence(frame) + [e for a in state["assessments"] if a["frame_id"] == frame["frame_id"] for e in a["evidence"]]
                if not all(evidence_is_current(e) for e in evidence):
                    frame["status"] = "stale"
                    if frame.get("step_id"):
                        stale.add(frame["step_id"])
                    parent = frame.get("parent_frame_id")
                    while parent:
                        ancestor = next(f for f in state["frames"] if f["frame_id"] == parent)
                        ancestor["status"] = "stale"
                        if ancestor.get("step_id"):
                            stale.add(ancestor["step_id"])
                        parent = ancestor.get("parent_frame_id")
                elif frame["status"] in {"running", "waiting_user", "blocked"}:
                    frame["status"] = "interrupted"
                    if frame.get("step_id"):
                        stale.add(frame["step_id"])
            changed = True
            while changed:
                changed = False
                for step in state["plan"]:
                    if set(step.get("depends_on", [])) & stale and step["step_id"] not in stale:
                        stale.add(step["step_id"]); changed = True
            for step in state["plan"]:
                if step["step_id"] in stale:
                    step["status"] = "pending"
            for frame in state["frames"]:
                if frame.get("step_id") in stale and frame["status"] != "superseded":
                    frame["status"] = "stale"
            for assessment in state["assessments"]:
                source = next(f for f in state["frames"] if f["frame_id"] == assessment["source_frame_id"])
                if source["status"] in {"stale", "superseded", "interrupted"}:
                    target = next(f for f in state["frames"] if f["frame_id"] == assessment["frame_id"])
                    if target["status"] != "superseded":
                        target["needs_assessment"] = True
            if stale:
                for criterion in state["acceptance"]:
                    criterion.update(status="pending", evidence=[])
            state["status"] = "running"
            state["next_decision"] = {"action": "revalidate_and_select", "invalidated_steps": sorted(stale),
                                      "note": "File hashes do not prove live runtime health; recheck environmental blockers and actual verification."}
            return state
        return self.mutate(revision, change)

    def complete(self, revision, checks):
        def change(state):
            if state["status"] != "running" or any(s["required"] and s["status"] != "completed" for s in state["plan"]):
                raise ValueError("Necessary scoped steps are unfinished")
            relevant = [f for f in state["frames"] if f["status"] != "superseded"]
            if any(not frame_resolved(f) for f in relevant):
                raise ValueError("Unresolved skill work or findings remain")
            if any(not evidence_is_current(e) for f in relevant for e in frame_evidence(f)):
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
    parser.add_argument("--reason")
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
                value = store.dispatch(args.revision,args.skill_id,args.input_version,args.step_id,args.parent_frame_id,args.input,args.skill_path,args.require_original,not args.optional)
            elif args.command == 'attach-grill':value = store.attach_grill(args.revision,read(args.payload),args.input,args.skill_path)
            elif args.command == 'route-grill':value = store.route_grill(args.revision,args.event_id)
            elif args.command == "block":value = store.block_unavailable(args.revision,args.skill_id,args.reason,args.step_id,args.parent_frame_id)
            elif args.command == "return":value = store.receive(args.revision,read(args.payload))
            elif args.command == "register":value = store.register(args.revision,read(args.payload))
            elif args.command == "extend-plan":value = store.extend_plan(args.revision,read(args.payload),args.reason)
            elif args.command == "assess":value = store.assess(args.revision,args.frame_id,args.resolution,args.evidence,args.source_frame_id)
            elif args.command == "resume":value = store.resume(args.revision)
            elif args.command in {"pause", "cancel"}:value = store.control(args.revision,"paused" if args.command == "pause" else "cancelled",args.reason)
            else:value = store.complete(args.revision,read(args.payload))
        print(json.dumps(value,ensure_ascii=False,indent=2))
        return 0
    except (OSError,ValueError,KeyError,TypeError,StopIteration) as exc:
        print(json.dumps({"ok":False,"error":str(exc)},ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
