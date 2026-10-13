"""Codex protocol helpers. No model calls or automatic delegation."""

import copy
import hashlib
import re
from pathlib import Path


def resolve_skill(name, roots=(), explicit=None, catalog=None):
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        raise ValueError("Invalid skill name")
    catalog = catalog or {}
    if explicit is not None:
        candidates = [Path(explicit)]
    elif name in catalog:
        candidates = [Path(catalog[name])]
    else:
        candidates = [Path(root) / name / "SKILL.md" for root in roots]
        existing = {candidate.resolve() for candidate in candidates if candidate.is_file()}
        if len(existing) > 1:
            raise ValueError('Ambiguous skill sources require an explicit selection: ' + name)
    for candidate in candidates:
        if candidate.is_dir():
            candidate = candidate / "SKILL.md"
        if not candidate.is_file():
            continue
        content = candidate.read_bytes()
        text = content.decode("utf-8-sig")
        match = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
        if not match or not re.search(r"^name:\s*[\"']?" + re.escape(name) + r"[\"']?\s*$", match.group(1), re.M):
            raise ValueError("Selected skill name does not match its file")
        def boolean(body, field, default):
            values = re.findall(r'^\s*' + re.escape(field) + r':\s*([^\n#]+)', body, re.M)
            if not values:
                return default
            if len(values) != 1 or values[0].strip() not in {'true', 'false'}:
                raise ValueError('Invalid invocation policy: ' + field)
            return values[0].strip() == 'true'
        callable_skill = not boolean(match.group(1), 'disable-model-invocation', False)
        policy = candidate.parent / 'agents/openai.yaml'
        policy_evidence = None
        if policy.is_file():
            policy_bytes = policy.read_bytes()
            callable_skill = callable_skill and boolean(policy_bytes.decode('utf-8-sig'), 'allow_implicit_invocation', True)
            policy_evidence = {'path': str(policy.resolve()), 'sha256': hashlib.sha256(policy_bytes).hexdigest()}
        return {"skill_id": name, "skill_path": str(candidate.resolve()), "sha256": hashlib.sha256(content).hexdigest(),
                "content": text, 'callable': callable_skill, 'policy': policy_evidence}
    # Explicit/catalog paths are authoritative; do not silently choose another version.
    raise FileNotFoundError(f"Skill file unavailable: {name}")


def delegation_mode(authorized, available, free_slots, requested_workers=1, in_child=False):
    if requested_workers < 1 or free_slots < 0:
        raise ValueError("Invalid concurrency limits")
    if in_child or not authorized or not available or free_slots == 0:
        return {"execution": "sequential", "workers": 0}
    return {"execution": "delegated", "workers": min(free_slots, requested_workers)}


RETURN_STATUSES = {"completed", "failed", "blocked", "waiting_user", "skipped", "cancelled"}


def children_resolved(frame, frames):
    """Current-parent duties include helpers that have not been dispatched yet."""
    obligations = frame.get("completion_obligations")
    if not isinstance(obligations, list):
        return False
    children = [child for child in frames if child.get("parent_frame_id") == frame["frame_id"]
                and child["status"] != "superseded"]
    def resolved(child):
        return (child["status"] == "completed" or (child["status"] == "skipped" and child.get("required") is False
            and child.get("skip_reason"))) and not child.get("needs_assessment")
    return all(resolved(child) for child in children) and all(any(
        child["status"] == "completed" and child.get("required") is True and resolved(child)
        and all(child.get(key) == duty[key] for key in ("skill_id", "skill_path", "skill_sha256"))
        for child in children) for duty in obligations)


def accept_result(state, result):
    """Merge a specialist's declared result, checking protocol rather than business behavior."""
    if state.get("schema_version") != 4:
        raise ValueError("Legacy state requires explicit migration before accepting results")
    if state.get("mode", "execute") != "execute" or result.get("skill_id") in {"ask-matt", "develop-system"}:
        raise ValueError("Navigation cannot write execution state")
    control = state.get("control", {}).get("status")
    if control not in {"running", "paused"} or state.get("status") != control:
        raise ValueError("Only active or user-paused runs can accept results")
    required = {"event_id", "run_id", "frame_id", "parent_frame_id", "skill_id", "status", "return_to", "completion_checked", "artifacts", "checks", "findings", "blockers"}
    if not required <= result.keys():
        raise ValueError("Incomplete return event")
    if result["run_id"] != state["run_id"] or result["return_to"] != "develop-system":
        raise ValueError("Wrong run or return target")
    if result["status"] not in RETURN_STATUSES:
        raise ValueError("Invalid return status")
    if result["status"] == "completed" and result["completion_checked"] is not True:
        raise ValueError("Completion has not been declared checked by the executing specialist")
    for field in ("artifacts", "checks", "findings", "blockers"):
        if not isinstance(result[field], list):
            raise ValueError(f"{field} must be an array")
    for event in state.get("events", []):
        if event["event_id"] == result["event_id"]:
            if event != result:
                raise ValueError("Conflicting duplicate event")
            return copy.deepcopy(state), {"action": "duplicate", "return_to": "develop-system"}
    updated = copy.deepcopy(state)
    frames = {frame["frame_id"]: frame for frame in updated["frames"]}
    frame = frames.get(result["frame_id"])
    if frame is None or frame["skill_id"] != result["skill_id"] or frame.get("parent_frame_id") != result["parent_frame_id"]:
        raise ValueError("Unknown or mismatched frame")
    if frame.get("protocol_version") != 1 or type(result.get("protocol_version")) is not int or result["protocol_version"] != frame["protocol_version"]:
        raise ValueError("Return protocol version does not match the dispatched frame")
    if "input_version" in frame and result.get("input_version") != frame["input_version"]:
        raise ValueError("Result belongs to an obsolete input version")
    if frame["status"] not in {"running", "waiting_user", "blocked"}:
        raise ValueError("Stale frame return")
    parent_id = frame.get("parent_frame_id")
    if parent_id is not None and (parent_id not in frames or frames[parent_id]["status"] != "running"):
        raise ValueError("Parent frame is missing or no longer active")
    if result["status"] == "completed" and not children_resolved(frame, list(frames.values())):
        raise ValueError("Parent cannot complete with unresolved child work")
    frame["status"] = result["status"]
    frame["needs_assessment"] = bool(result["findings"] or result["blockers"])
    updated.setdefault("events", []).append(copy.deepcopy(result))
    updated["revision"] = updated.get("revision", 0) + 1
    if result["status"] != "completed":
        decision = {"action": "handle_return", "status": result["status"], "frame_id": frame["frame_id"]}
    elif result["findings"] or result["blockers"]:
        decision = {"action": "assess_findings", "frame_id": frame["frame_id"], "parent_frame_id": parent_id}
    elif parent_id is not None:
        if children_resolved(frames[parent_id], list(frames.values())):
            decision = {"action": "resume_parent", "frame_id": parent_id}
        else:
            decision = {"action": "collect_children", "frame_id": parent_id}
    else:
        decision = {"action": "assess_next", "frame_id": frame["frame_id"]}
    decision["return_to"] = "develop-system"
    if control == "paused":
        decision = {"action": "paused", "user_reason": state["control"]["user_reason"],
                    "return_to": "develop-system"}
    updated["next_decision"] = decision
    return updated, decision
