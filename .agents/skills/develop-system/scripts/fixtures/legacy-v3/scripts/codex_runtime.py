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
        return {"skill_id": name, "skill_path": str(candidate.resolve()), "sha256": hashlib.sha256(content).hexdigest(), "content": text}
    # Explicit/catalog paths are authoritative; do not silently choose another version.
    raise FileNotFoundError(f"Skill file unavailable: {name}")


def delegation_mode(authorized, available, free_slots, requested_workers=1, in_child=False):
    if requested_workers < 1 or free_slots < 0:
        raise ValueError("Invalid concurrency limits")
    if in_child or not authorized or not available or free_slots == 0:
        return {"execution": "sequential", "workers": 0}
    return {"execution": "delegated", "workers": min(free_slots, requested_workers)}


RETURN_STATUSES = {"completed", "failed", "blocked", "waiting_user", "skipped", "cancelled"}


def accept_result(state, result):
    """Merge a specialist's declared result, checking protocol rather than business behavior."""
    if state.get("mode", "execute") != "execute" or result.get("skill_id") in {"ask-matt", "develop-system"}:
        raise ValueError("Navigation cannot write execution state")
    if state.get("status") != "running":
        raise ValueError("Run must be explicitly resumed before accepting results")
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
    if "input_version" in frame and result.get("input_version") != frame["input_version"]:
        raise ValueError("Result belongs to an obsolete input version")
    if frame["status"] not in {"running", "waiting_user", "blocked"}:
        raise ValueError("Stale frame return")
    parent_id = frame.get("parent_frame_id")
    if parent_id is not None and (parent_id not in frames or frames[parent_id]["status"] != "running"):
        raise ValueError("Parent frame is missing or no longer active")
    children = [item for item in frames.values() if item.get("parent_frame_id") == frame["frame_id"] and item["status"] != "superseded"]
    def resolved(item):
        return (item["status"] == "completed" or (item["status"] == "skipped" and item.get("required") is False and item.get("skip_reason"))) and not item.get("needs_assessment")
    if result["status"] == "completed" and any(not resolved(item) for item in children):
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
        siblings = [item for item in frames.values() if item.get("parent_frame_id") == parent_id and item["status"] != "superseded"]
        if all(resolved(item) for item in siblings):
            decision = {"action": "resume_parent", "frame_id": parent_id}
        else:
            decision = {"action": "collect_children", "frame_id": parent_id}
    else:
        decision = {"action": "assess_next", "frame_id": frame["frame_id"]}
    decision["return_to"] = "develop-system"
    updated["next_decision"] = decision
    return updated, decision
