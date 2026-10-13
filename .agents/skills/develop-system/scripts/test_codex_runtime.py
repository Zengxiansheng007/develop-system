import tempfile
import unittest
from pathlib import Path

from codex_runtime import accept_result, delegation_mode, resolve_skill


def frame(name, skill, parent=None):
    return {"frame_id": name, "skill_id": skill, "parent_frame_id": parent, "status": "running"}


def event(item, status="completed"):
    return {"event_id": item["frame_id"] + "-return", "run_id": "test", "frame_id": item["frame_id"], "parent_frame_id": item["parent_frame_id"], "skill_id": item["skill_id"], "status": status, "return_to": "develop-system", "completion_checked": True, "artifacts": [], "checks": [], "findings": [], "blockers": []}


class RuntimeTests(unittest.TestCase):
    def state(self, *items):
        return {"run_id": "test", "status": "running", "revision": 0, "frames": list(items), "events": []}

    def test_nested_return_resumes_parent_without_completing_it(self):
        parent, child = frame("design", "grill-with-docs"), frame("terms", "domain-modeling", "design")
        state, decision = accept_result(self.state(parent, child), event(child))
        self.assertEqual(decision["action"], "resume_parent")
        self.assertEqual(state["frames"][0]["status"], "running")
        self.assertEqual(state["status"], "running")

    def test_parallel_child_does_not_skip_sibling(self):
        parent = frame("design", "codebase-design")
        children = [frame("a", "research", "design"), frame("b", "research", "design")]
        state, decision = accept_result(self.state(parent, *children), event(children[0]))
        self.assertEqual(decision["action"], "collect_children")
        state, decision = accept_result(state, event(children[1]))
        self.assertEqual(decision["action"], "resume_parent")

    def test_parent_cannot_complete_unfinished_children(self):
        parent, child = frame("design", "codebase-design"), frame("a", "research", "design")
        with self.assertRaises(ValueError):
            accept_result(self.state(parent, child), event(parent))

    def test_failed_child_is_recovery_work(self):
        parent, child = frame("design", "codebase-design"), frame("a", "research", "design")
        state, decision = accept_result(self.state(parent, child), event(child, "failed"))
        self.assertEqual(decision["action"], "handle_return")
        self.assertEqual(state["frames"][0]["status"], "running")

    def test_root_completion_requires_next_assessment(self):
        item = frame("spec", "to-spec")
        state, decision = accept_result(self.state(item), event(item))
        self.assertEqual(decision["action"], "assess_next")
        self.assertEqual(state["status"], "running")

    def test_sibling_completion_does_not_hide_unresolved_findings(self):
        parent = frame("design", "codebase-design")
        a, b = frame("a", "research", "design"), frame("b", "research", "design")
        result = event(a)
        result["findings"] = ["open decision"]
        state, _ = accept_result(self.state(parent, a, b), result)
        state, decision = accept_result(state, event(b))
        self.assertEqual(decision["action"], "collect_children")
        with self.assertRaises(ValueError):
            accept_result(state, event(parent))

    def test_duplicate_is_idempotent_but_conflict_rejected(self):
        item = frame("spec", "to-spec")
        state, _ = accept_result(self.state(item), event(item))
        repeated, decision = accept_result(state, event(item))
        self.assertEqual(repeated["revision"], state["revision"])
        self.assertEqual(decision["action"], "duplicate")
        changed = event(item)
        changed["findings"] = ["new"]
        with self.assertRaises(ValueError):
            accept_result(state, changed)

    def test_wrong_run_and_unchecked_completion_rejected(self):
        item = frame("spec", "to-spec")
        for field, value in [("run_id", "other"), ("return_to", "to-tickets"), ("completion_checked", False)]:
            result = event(item)
            result[field] = value
            with self.assertRaises(ValueError):
                accept_result(self.state(item), result)

    def test_controller_cannot_return_as_specialist(self):
        item = frame("controller", "develop-system")
        with self.assertRaises(ValueError):
            accept_result(self.state(item), event(item))

    def test_completed_review_with_findings_needs_assessment(self):
        item = frame("review", "code-review")
        result = event(item)
        result["findings"] = ["in-scope issue"]
        _, decision = accept_result(self.state(item), result)
        self.assertEqual(decision["action"], "assess_findings")

    def test_delegation_falls_back_and_respects_capacity(self):
        for args in [(False, True, 3), (True, False, 3), (True, True, 0)]:
            self.assertEqual(delegation_mode(*args)["execution"], "sequential")
        self.assertEqual(delegation_mode(True, True, 2, requested_workers=4)["workers"], 2)
        self.assertEqual(delegation_mode(True, True, 3, in_child=True)["execution"], "sequential")

    def test_skill_resolution_and_authoritative_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for label in ["project", "global"]:
                folder = root / label / "research"
                folder.mkdir(parents=True)
                (folder / "SKILL.md").write_text("---\nname: research\ndescription: test\n---\n" + label, encoding="utf-8")
            resolved = resolve_skill("research", [root / "project", root / "global"])
            self.assertIn("project", resolved["skill_path"])
            explicit = root / "global" / "research" / "SKILL.md"
            self.assertEqual(resolve_skill("research", explicit=explicit)["skill_path"], str(explicit.resolve()))
            with self.assertRaises(FileNotFoundError):
                resolve_skill("research", [root / "global"], explicit=root / "missing")
            with self.assertRaises(ValueError):
                resolve_skill("../research", [root])


if __name__ == "__main__":
    unittest.main(verbosity=2)
