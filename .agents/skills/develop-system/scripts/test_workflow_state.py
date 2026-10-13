import copy
import json
import tempfile
import unittest
from pathlib import Path

from workflow_state import RunStore, SKILL_ROOT, ready_steps


class DurableWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = self.root / "registry.json"
        data = json.loads((SKILL_ROOT / "registry.json").read_text(encoding="utf-8"))
        for item in data["skills"]:
            path = self.root / (item["id"] + ".md")
            path.write_text("---\nname: " + item["id"] + "\ndescription: fixture\n---\n", encoding="utf-8")
            item["path"] = str(path)
        self.registry.write_text(json.dumps(data), encoding="utf-8")
        self.output = self.root / "output.txt"
        self.output.write_text("verified output", encoding="utf-8")
        self.store = RunStore(self.root, "test-run")
        self.plan = [{"step_id": "research-step", "skill_id": "research"},
                     {"step_id": "spec-step", "skill_id": "to-spec", "depends_on": ["research-step"]}]

    def create(self, plan=None):
        return self.store.create("fixture goal", plan or self.plan, ["deliver verified output"], self.registry)

    def dispatch(self, state, skill="research", step="research-step", parent=None, **kwargs):
        return self.store.dispatch(state["revision"], skill, "input-v1", step if parent is None else None, parent, **kwargs)

    def event(self, state, status="completed", **kwargs):
        frame = state["frames"][-1]
        return {"event_id": frame["frame_id"] + "-return", "run_id": "test-run", "frame_id": frame["frame_id"],
                "parent_frame_id": frame["parent_frame_id"], "skill_id": frame["skill_id"], "input_version": frame["input_version"],
                "mode": frame["mode"], "execution": "sequential", "status": status, "return_to": "develop-system",
                "completion_checked": True, "artifacts": [str(self.output)] if status == "completed" else [],
                "checks": [], "findings": [], "blockers": [],
                "acceptance_checks": [{"id": "1", "status": "passed", "completion_checked": True,
                                       "kind": "technical", "evidence": [str(self.output)]}] if status == "completed" else [],
                "resolved_findings": [], **kwargs}

    def receive(self, state, **kwargs):
        return self.store.receive(state["revision"], self.event(state, **kwargs))

    def complete(self, state):
        source = state["frames"][-1]["frame_id"] if state["frames"] else "missing"
        return self.store.complete(state["revision"], [{"id": "1", "source_frame_id": source}])

    def test_navigation_cannot_even_create_state_directory(self):
        for entry in ("ask-matt", "/ask-matt", "$ask-matt"):
            with self.assertRaises(ValueError):
                RunStore(self.root, "test-run", entry)
        self.assertFalse((self.root / ".develop-system").exists())

    def test_dependencies_and_acceptance_control_whole_run_completion(self):
        state = self.create()
        self.assertEqual([s["step_id"] for s in ready_steps(state)], ["research-step"])
        with self.assertRaises(ValueError):
            self.complete(state)
        state = self.receive(self.dispatch(state))
        self.assertEqual(state["status"], "running")
        self.assertEqual([s["step_id"] for s in ready_steps(state)], ["spec-step"])
        state = self.receive(self.dispatch(state, "to-spec", "spec-step"))
        state = self.complete(state)
        self.assertEqual(self.store.read()["status"], "completed")
        with self.assertRaises(ValueError):
            self.store.resume(state["revision"])

    def test_child_completion_returns_to_parent_and_never_completes_parent(self):
        state = self.dispatch(self.create())
        parent = state["frames"][-1]["frame_id"]
        parent_event = self.event(state)
        state = self.dispatch(state, "domain-modeling", parent=parent)
        with self.assertRaises(ValueError):
            self.store.receive(state["revision"], parent_event)
        state = self.receive(state)
        self.assertEqual(state["next_decision"]["action"], "resume_parent")
        self.assertEqual(state["frames"][0]["status"], "running")
        state = self.store.receive(state["revision"], parent_event)
        self.assertEqual(state["plan"][0]["status"], "completed")

    def test_duplicate_no_write_and_conflicts_rejected(self):
        state = self.dispatch(self.create())
        event = self.event(state)
        state = self.store.receive(state["revision"], event)
        before = self.store.path.read_bytes()
        repeated = self.store.receive(state["revision"], event)
        self.assertEqual(repeated["revision"], state["revision"])
        self.assertEqual(before, self.store.path.read_bytes())
        with self.assertRaises(ValueError):
            self.store.receive(state["revision"], {**event, "findings": ["different"]})

    def test_obsolete_input_version_wrong_return_and_unchecked_success_rejected(self):
        state = self.dispatch(self.create())
        for changes in ({"input_version": "obsolete"}, {"return_to": "to-spec"}, {"completion_checked": False}):
            with self.assertRaises(ValueError):
                self.store.receive(state["revision"], {**self.event(state), **changes})
        self.assertEqual(self.store.read()["revision"], state["revision"])

    def test_revision_and_exclusive_lock_prevent_overwrite(self):
        state = self.create()
        with self.assertRaises(ValueError):
            self.store.dispatch(0, "research", "v", "research-step")
        with self.store.locked():
            with self.assertRaises(FileExistsError):
                self.dispatch(state)
        self.assertEqual(self.store.read()["revision"], 1)
        with self.assertRaises(FileExistsError):
            self.create()

    def test_changed_upstream_invalidates_completed_downstream(self):
        state = self.receive(self.dispatch(self.create()))
        state = self.receive(self.dispatch(state, "to-spec", "spec-step"))
        self.output.write_text("changed after checking", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.complete(state)
        state = self.store.resume(state["revision"])
        self.assertEqual([s["status"] for s in state["plan"]], ["pending", "pending"])
        self.assertEqual([s["step_id"] for s in ready_steps(state)], ["research-step"])
        state = self.receive(self.dispatch(state))
        state = self.receive(self.dispatch(state, "to-spec", "spec-step"))
        self.assertEqual(self.complete(state)["status"], "completed")

    def test_resume_interrupts_old_frames_and_supersedes_all_descendants(self):
        state = self.dispatch(self.create())
        root = state["frames"][-1]["frame_id"]
        old_event = self.event(state)
        state = self.dispatch(state, "domain-modeling", parent=root)
        child = state["frames"][-1]["frame_id"]
        state = self.dispatch(state, "codebase-design", parent=child)
        state = self.store.resume(state["revision"])
        with self.assertRaises(ValueError):
            self.store.receive(state["revision"], old_event)
        state = self.dispatch(state)
        self.assertTrue(all(f["status"] == "superseded" for f in state["frames"][:-1]))

    def test_findings_require_resolution_with_current_evidence(self):
        state = self.dispatch(self.create([self.plan[0]]))
        state = self.receive(state, findings=["unresolved design decision"])
        self.assertEqual(ready_steps(state), [])
        with self.assertRaises(ValueError):
            self.complete(state)
        proof = self.root / "resolution.txt"
        proof.write_text("decision and verification", encoding="utf-8")
        original_frame = state["frames"][0]["frame_id"]
        state = self.store.extend_plan(state["revision"], [{"step_id": "resolve", "skill_id": "to-spec",
                 "depends_on": ["research-step"], "resolves_frames": [original_frame]}], "resolve design decision")
        state = self.dispatch(state, "to-spec", "resolve")
        state = self.receive(state, artifacts=[str(proof)], resolved_findings=[original_frame])
        state = self.store.assess(state["revision"], original_frame, "decision resolved", [str(proof)], state["frames"][-1]["frame_id"])
        proof.write_text("changed resolution", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.complete(state)
        state = self.store.resume(state["revision"])
        self.assertEqual(state["plan"][0]["status"], "pending")

    def test_required_step_cannot_be_skipped_using_optional_flag(self):
        state = self.dispatch(self.create(), required=False)
        with self.assertRaises(ValueError):
            self.receive(state, status="skipped", skip_reason="skip necessary work")

    def test_optional_child_skip_can_return_to_parent(self):
        state = self.dispatch(self.create())
        parent_event = self.event(state)
        state = self.dispatch(state, "domain-modeling", parent=state["frames"][0]["frame_id"], required=False)
        state = self.receive(state, status="skipped", skip_reason="terminology already documented")
        state = self.store.receive(state["revision"], parent_event)
        self.assertEqual(state["plan"][0]["status"], "completed")

    def test_repeated_identical_failures_stop_automatic_retry(self):
        state = self.create()
        for _ in range(2):
            state = self.dispatch(state)
            state = self.receive(state, status="failed")
        self.assertEqual(state["status"], "blocked")
        self.assertEqual(state["next_decision"]["action"], "stop_repeated_no_progress")
        self.assertEqual(ready_steps(state), [])

    def test_temporary_extension_does_not_change_shared_registry(self):
        state = self.create()
        original = self.registry.read_bytes()
        path = self.root / "custom.md"
        path.write_text("---\nname: future-skill\ndescription: fixture\n---\n", encoding="utf-8")
        descriptor = copy.deepcopy(next(s for s in state["registry"]["skills"] if s["id"] == "research"))
        descriptor.update(id="future-skill", path=str(path), requires=[], next_candidates=[])
        state = self.store.register(state["revision"], descriptor)
        state = self.dispatch(state)
        state = self.dispatch(state, "future-skill", parent=state["frames"][0]["frame_id"])
        state = self.receive(state)
        self.assertEqual(state["next_decision"]["action"], "resume_parent")
        self.assertEqual(original, self.registry.read_bytes())

    def test_findings_route_to_in_scope_repair_before_assessment_and_completion(self):
        state = self.receive(self.dispatch(self.create([self.plan[0]])), findings=["in-scope defect"])
        review_frame = state["frames"][0]["frame_id"]
        state = self.store.extend_plan(state["revision"], [{"step_id": "repair", "skill_id": "research",
             "depends_on": ["research-step"], "resolves_frames": [review_frame]}], "repair verified in-scope defect")
        self.assertEqual([s["step_id"] for s in ready_steps(state)], ["repair"])
        state = self.receive(self.dispatch(state, step="repair"), resolved_findings=[review_frame])
        with self.assertRaises(ValueError):
            self.complete(state)
        state = self.store.assess(state["revision"], review_frame, "repair verified", [str(self.output)], state["frames"][-1]["frame_id"])
        self.assertEqual(self.complete(state)["status"], "completed")

    def test_navigation_alias_and_leaf_injections_are_rejected(self):
        state = self.create()
        descriptor = copy.deepcopy(state["registry"]["skills"][1])
        descriptor["id"] = "ask-matt"
        with self.assertRaises(ValueError):
            self.store.register(state["revision"], descriptor)
        with self.assertRaises(ValueError):
            self.store.dispatch(state["revision"], "ask-matt", "v", "research-step")

    def test_explicit_missing_original_does_not_fall_back(self):
        state = self.create()
        with self.assertRaises(FileNotFoundError):
            self.dispatch(state, skill_path=str(self.root / "missing.md"), require_original=True)
        self.assertEqual(self.store.read()["revision"], state["revision"])

    def test_pause_cancel_and_legacy_state_fail_closed(self):
        state = self.create()
        state = self.store.control(state["revision"], "paused", "user requested pause")
        self.assertEqual(ready_steps(state), [])
        state = self.store.resume(state["revision"])
        state = self.store.control(state["revision"], "cancelled", "user requested cancellation")
        with self.assertRaises(ValueError):
            self.store.resume(state["revision"])
        state["schema_version"] = 1
        self.store.write_atomic(state)
        with self.assertRaises(ValueError):
            self.store.read()

    def test_controller_cannot_invent_acceptance_on_completion(self):
        state = self.receive(self.dispatch(self.create([self.plan[0]])))
        before = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.complete(state["revision"], [{"id":"1", "status":"passed", "completion_checked":True,
                                                   "evidence":[str(self.output)]}])
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(self.complete(state)["acceptance"][0]["source_skill_id"], "research")

    def test_missing_or_failed_specialist_conclusion_cannot_close_run(self):
        for conclusions in ([], [{"id":"1", "status":"failed", "completion_checked":True, "kind":"technical", "evidence":[str(self.output)]}]):
            state = self.dispatch(self.create([self.plan[0]]))
            state = self.receive(state, acceptance_checks=conclusions)
            with self.assertRaises(ValueError):
                self.complete(state)
            self.store.path.unlink()

    def test_human_acceptance_needs_true_confirmation(self):
        state = self.store.create("manual acceptance", [self.plan[0]], [{"criterion":"user confirms outcome", "kind":"human"}], self.registry)
        state = self.dispatch(state)
        claim = {"id":"1", "status":"passed", "completion_checked":True, "kind":"human", "evidence":[str(self.output)]}
        with self.assertRaises(ValueError):
            self.receive(state, acceptance_checks=[claim])
        claim.update(human_confirmed=True, user_reference="actual user turn reference")
        state = self.receive(state, acceptance_checks=[claim])
        self.assertEqual(self.complete(state)["acceptance"][0]["kind"], "human")

    def test_technical_check_cannot_replace_manual_acceptance(self):
        state = self.store.create("manual acceptance", [self.plan[0]], [{"criterion":"user confirms outcome", "kind":"human"}], self.registry)
        state = self.dispatch(state)
        with self.assertRaises(ValueError):
            self.receive(state)

    def test_configured_reviewer_cannot_be_replaced_by_implementation(self):
        state = self.store.create("review outcome", [self.plan[0]], [{"criterion":"review passed", "kind":"technical", "allowed_skill_ids":["code-review"]}], self.registry)
        state = self.dispatch(state)
        with self.assertRaises(ValueError):
            self.receive(state)

    def test_missing_skill_blocks_without_creating_inline_frame(self):
        data = json.loads(self.registry.read_text(encoding="utf-8"))
        item = next(s for s in data["skills"] if s["id"] == "research")
        path = Path(item["path"])
        state = self.create([self.plan[0]])
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            self.dispatch(state)
        self.assertEqual(self.store.read()["frames"], [])
        state = self.store.block_unavailable(state["revision"], "research", "file missing", "research-step")
        self.assertEqual(state["status"], "blocked")
        self.assertEqual(state["next_decision"]["action"], "missing_skill")
        path.write_text("---\nname: research\ndescription: restored\n---\n", encoding="utf-8")
        state = self.store.resume(state["revision"])
        self.assertEqual(self.dispatch(state)["frames"][-1]["mode"], "skill")

    def test_controller_cannot_clear_findings_with_untracked_proof(self):
        state = self.receive(self.dispatch(self.create([self.plan[0]])), findings=["defect"])
        with self.assertRaises(ValueError):
            self.store.assess(state["revision"], state["frames"][0]["frame_id"], "controller says fixed", [str(self.output)])
        self.assertTrue(self.store.read()["frames"][0]["needs_assessment"])

    def test_acceptance_evidence_change_invalidates_source_on_resume(self):
        proof = self.root / "acceptance.log"
        proof.write_text("actual check passed", encoding="utf-8")
        state = self.dispatch(self.create([self.plan[0]]))
        state = self.receive(state, acceptance_checks=[{"id":"1", "status":"passed", "completion_checked":True,
                            "kind":"technical", "evidence":[str(proof)]}])
        proof.write_text("changed", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.complete(state)
        state = self.store.resume(state["revision"])
        self.assertEqual(state["plan"][0]["status"], "pending")

    def test_legacy_v2_cannot_restore_controller_owned_acceptance(self):
        state = self.create()
        state["schema_version"] = 2
        self.store.write_atomic(state)
        with self.assertRaises(ValueError):
            self.store.read()

    def test_changed_upstream_blocks_same_session_dispatch_without_resume(self):
        state = self.receive(self.dispatch(self.create()))
        self.output.write_text("changed upstream", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.dispatch(state, "to-spec", "spec-step")
        self.assertEqual(self.store.read()["plan"][1]["status"], "pending")

    def test_changed_frame_input_cannot_return_old_success(self):
        spec = self.root / "input.md"
        spec.write_text("old input", encoding="utf-8")
        state = self.dispatch(self.create(), inputs=[str(spec)])
        spec.write_text("new input", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.receive(state)
        self.assertEqual(self.store.read()["frames"][0]["status"], "running")

    def test_skipped_helper_cannot_declare_findings_resolved(self):
        state = self.receive(self.dispatch(self.create([self.plan[0]])), findings=["defect"])
        target = state["frames"][0]["frame_id"]
        state = self.store.extend_plan(state["revision"], [{"step_id":"repair", "skill_id":"implement",
                     "depends_on":["research-step"], "resolves_frames":[target]}], "resolve defect")
        state = self.dispatch(state, "implement", "repair")
        state = self.dispatch(state, "code-review", parent=state["frames"][-1]["frame_id"], required=False)
        with self.assertRaises(ValueError):
            self.receive(state, status="skipped", skip_reason="not needed", artifacts=[str(self.output)], resolved_findings=[target])
        self.assertTrue(self.store.read()["frames"][0]["needs_assessment"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
