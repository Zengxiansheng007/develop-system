"""Public workflow scenarios; results are synthetic specialist fixtures."""
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from workflow_state import RunStore, SKILL_ROOT, ready_steps

class ScopedWorkflowTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        registry=json.loads((SKILL_ROOT/"registry.json").read_text(encoding="utf-8"))
        for item in registry["skills"]:
            path=self.root/(item["id"]+".md")
            path.write_text("---\nname: "+item["id"]+"\ndescription: synthetic fixture\n---\n",encoding="utf-8")
            item["path"]=str(path)
        self.registry=self.root/"registry.json"
        self.registry.write_text(json.dumps(registry),encoding="utf-8")
        self.output=self.root/"synthetic.txt"
        self.output.write_text("Synthetic specialist output; not product acceptance.",encoding="utf-8")
        self.store=RunStore(self.root,"scoped-test")

    def create(self,plan=None,**kw):
        return self.store.create("scope behavior",plan or [
            {"step_id":"a","skill_id":"implement"},
            {"step_id":"a-dependent","skill_id":"to-spec","depends_on":["a"]},
            {"step_id":"b","skill_id":"domain-modeling"}],["synthetic acceptance"],self.registry,**kw)

    def dispatch(self,state,skill="implement",step="a",parent=None,**kw):
        return self.store.dispatch(state["revision"],skill,"input-v1",
            step_id=step if parent is None else None,parent_frame_id=parent,**kw)

    def event(self,frame,status="completed",**kw):
        return {"protocol_version":1,"event_id":uuid.uuid4().hex,"run_id":"scoped-test","frame_id":frame["frame_id"],
            "parent_frame_id":frame["parent_frame_id"],"skill_id":frame["skill_id"],
            "input_version":frame["input_version"],"mode":"skill","execution":"sequential",
            "status":status,"return_to":"develop-system","completion_checked":True,
            "artifacts":[str(self.output)] if status=="completed" else [],
            "checks":[],"findings":[],"blockers":[],"acceptance_checks":[],"resolved_findings":[],**kw}

    def test_original_f01_independent_child_return_survives_local_block(self):
        state=self.dispatch(self.create())
        parent=state["frames"][-1]
        state=self.dispatch(state,"code-review",parent=parent["frame_id"])
        blocked=state["frames"][-1]
        state=self.dispatch(state,"domain-modeling",parent=parent["frame_id"])
        independent=state["frames"][-1]
        state=self.store.receive(state["revision"],self.event(blocked,"blocked",blockers=["local review needs repair"]))
        state=self.store.receive(state["revision"],self.event(independent))
        self.assertEqual(state["status"],"running")
        self.assertEqual(state["frames"][-1]["status"],"completed")
        self.assertNotIn("a-dependent",[x["step_id"] for x in ready_steps(state)])
        with self.assertRaises(ValueError):
            self.store.receive(state["revision"],self.event(parent))

    def test_waiting_or_findings_allow_only_independent_root_work(self):
        state=self.dispatch(self.create())
        frame=state["frames"][-1]
        state=self.store.receive(state["revision"],self.event(frame,"waiting_user",blockers=["answer needed"]))
        self.assertEqual([s["step_id"] for s in ready_steps(state)],["b"])
        state=self.dispatch(state,"domain-modeling","b")
        state=self.store.receive(state["revision"],self.event(state["frames"][-1]))
        # Same active waiting frame may submit its professional findings; no invented success.
        state=self.store.receive(state["revision"],self.event(frame,findings=["unresolved defect"]))
        self.assertEqual(ready_steps(state),[])
        with self.assertRaises(ValueError):
            self.store.complete(state["revision"],[])

    def test_new_contract_rejects_unversioned_returns_and_legacy_mutation(self):
        state=self.dispatch(self.create())
        self.assertEqual(state["schema_version"],4)
        self.assertEqual(state["frames"][-1]["protocol_version"],1)
        before=self.store.path.read_bytes()
        event=self.event(state["frames"][-1])
        event.pop("protocol_version",None)
        with self.assertRaises(ValueError):
            self.store.receive(state["revision"],event)
        self.assertEqual(self.store.path.read_bytes(),before)
        legacy={**state,"schema_version":3}
        self.store.path.write_text(json.dumps(legacy),encoding="utf-8")
        original=self.store.path.read_bytes()
        self.assertEqual(self.store.read()["schema_version"],3)
        for operation in (
            lambda:self.store.write_atomic(legacy),
            lambda:self.store.resume(state["revision"]),
            lambda:self.store.control(state["revision"],"paused","user pause"),
            lambda:self.store.receive(state["revision"],self.event(state["frames"][-1]))):
            with self.assertRaises(ValueError):operation()
        self.assertEqual(self.store.path.read_bytes(),original)

    def test_missing_skill_is_local_and_never_creates_an_execution_frame(self):
        state=self.create()
        registry=json.loads(self.registry.read_text(encoding="utf-8"))
        Path(next(x for x in registry["skills"] if x["id"]=="implement")["path"]).unlink()
        before=self.store.path.read_bytes()
        with self.assertRaises(FileNotFoundError):
            self.dispatch(state)
        self.assertEqual(self.store.path.read_bytes(),before)
        state=self.store.block_unavailable(state["revision"],"implement","selected file missing",step_id="a")
        self.assertEqual(state["status"],"running")
        self.assertEqual(state["frames"],[])
        self.assertEqual([x["step_id"] for x in ready_steps(state)],["b"])
        state=self.dispatch(state,"domain-modeling","b")
        state=self.store.receive(state["revision"],self.event(state["frames"][-1]))
        with self.assertRaises(ValueError):self.store.complete(state["revision"],[])

    def test_repeated_no_progress_does_not_stop_independent_work_or_reset_on_resume(self):
        state=self.create()
        for _ in range(2):
            state=self.dispatch(state)
            state=self.store.receive(state["revision"],self.event(state["frames"][-1],"failed"))
        self.assertEqual(state["status"],"running")
        self.assertEqual([x["step_id"] for x in ready_steps(state)],["b"])
        count=len(state["events"])
        state=self.store.resume(state["revision"])
        self.assertEqual(len(state["events"]),count)
        self.assertEqual([x["step_id"] for x in ready_steps(state)],["b"])
        with self.assertRaises(ValueError):self.dispatch(state)

    def test_shared_or_unknown_scope_cannot_be_assumed_independent(self):
        plan=[{"step_id":"a","skill_id":"implement","shared_preconditions":["database"]},
              {"step_id":"b","skill_id":"domain-modeling","shared_preconditions":["database"]},
              {"step_id":"c","skill_id":"to-spec"}]
        state=self.dispatch(self.create(plan))
        owner=state["frames"][-1]
        state=self.store.receive(state["revision"],self.event(owner,"blocked",blockers=["database unavailable"]))
        self.assertEqual([x["step_id"] for x in ready_steps(state)],["c"])
        with self.assertRaises(ValueError):self.dispatch(state,"domain-modeling","b")
        state=self.store.receive(state["revision"],self.event(owner,"blocked",blockers=["scope needs verification"],block_scope="unknown"))
        self.assertEqual(ready_steps(state),[])
        with self.assertRaises(ValueError):self.dispatch(state,"to-spec","c")

    def test_blocked_child_does_not_hold_independent_root_capacity(self):
        state=self.dispatch(self.create())
        parent=state["frames"][-1]
        state=self.dispatch(state,"code-review",parent=parent["frame_id"])
        child=state["frames"][-1]
        state=self.store.receive(state["revision"],self.event(child,"blocked",blockers=["local repair"]))
        self.assertEqual([x["step_id"] for x in ready_steps(state)],["b"])
        state=self.dispatch(state,"domain-modeling","b")
        self.assertEqual(state["frames"][-1]["skill_id"],"domain-modeling")
        with self.assertRaises(ValueError):self.store.receive(state["revision"],self.event(parent))

    def test_unknown_child_scope_blocks_new_siblings_but_saves_valid_inflight_return(self):
        state=self.dispatch(self.create())
        parent=state["frames"][-1]
        state=self.dispatch(state,"code-review",parent=parent["frame_id"])
        child=state["frames"][-1]
        state=self.dispatch(state,"domain-modeling",parent=parent["frame_id"])
        inflight=state["frames"][-1]
        state=self.store.receive(state["revision"],self.event(child,"blocked",blockers=["unknown reach"],block_scope="unknown"))
        with self.assertRaises(ValueError):
            self.dispatch(state,"tdd",parent=parent["frame_id"])
        state=self.store.receive(state["revision"],self.event(inflight))
        self.assertEqual(state["frames"][-1]["status"],"completed")

    def test_local_cancellation_preserves_independent_work_and_root_terminal_is_final(self):
        state=self.dispatch(self.create())
        state=self.store.receive(state["revision"],self.event(state["frames"][-1],"cancelled"))
        self.assertEqual(state["status"],"running")
        self.assertEqual([x["step_id"] for x in ready_steps(state)],["b"])
        state=self.dispatch(state,"domain-modeling","b")
        late=self.event(state["frames"][-1])
        state=self.store.control(state["revision"],"cancelled","explicit synthetic user cancellation")
        before=self.store.path.read_bytes()
        with self.assertRaises(ValueError):self.store.receive(state["revision"],late)
        with self.assertRaises(ValueError):self.store.resume(state["revision"])
        self.assertEqual(before,self.store.path.read_bytes())

    def test_display_status_cannot_override_user_control(self):
        state=self.dispatch(self.create())
        frame=state["frames"][-1]
        state=self.store.control(state["revision"],"paused","synthetic explicit pause")
        state["status"]="running"
        self.store.path.write_text(json.dumps(state),encoding="utf-8")
        before=self.store.path.read_bytes()
        self.assertEqual(ready_steps(state),[])
        with self.assertRaises(ValueError):self.store.receive(state["revision"],self.event(frame))
        self.assertEqual(before,self.store.path.read_bytes())

    def test_new_child_cannot_reclaim_capacity_while_another_root_is_running(self):
        state=self.dispatch(self.create())
        parent=state["frames"][-1]
        state=self.dispatch(state,"code-review",parent=parent["frame_id"])
        blocked=state["frames"][-1]
        state=self.store.receive(state["revision"],self.event(blocked,"blocked"))
        state=self.dispatch(state,"domain-modeling","b")
        independent=state["frames"][-1]
        before=self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.dispatch(state,"tdd",parent=parent["frame_id"])
        self.assertEqual(before,self.store.path.read_bytes())
        # In-flight returns remain valid; capacity becomes available after B returns.
        state=self.store.receive(state["revision"],self.event(independent))
        state=self.dispatch(state,"tdd",parent=parent["frame_id"])
        self.assertEqual(state["frames"][-1]["skill_id"],"tdd")




