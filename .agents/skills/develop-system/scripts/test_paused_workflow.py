"""Public RunStore scenarios using synthetic specialist results, not model execution."""
import test_scoped_workflow as fixtures
from workflow_state import ready_steps
import unittest


class PausedWorkflowTests(unittest.TestCase):
    setUp = fixtures.ScopedWorkflowTests.setUp
    create = fixtures.ScopedWorkflowTests.create
    dispatch = fixtures.ScopedWorkflowTests.dispatch
    event = fixtures.ScopedWorkflowTests.event

    def test_paused_child_return_is_saved_once_without_resuming_parent(self):
        state=self.dispatch(self.create())
        parent=state['frames'][-1]
        state=self.dispatch(state,'tdd',parent=parent['frame_id'])
        child=state['frames'][-1]
        state=self.store.control(state['revision'],'paused','synthetic explicit user pause')
        event=self.event(child)
        state=self.store.receive(state['revision'],event)
        self.assertEqual(state['status'],'paused')
        self.assertEqual(state['control']['user_reason'],'synthetic explicit user pause')
        self.assertEqual(state['frames'][-1]['status'],'completed')
        self.assertEqual(state['frames'][0]['status'],'running')
        self.assertEqual(state['next_decision']['action'],'paused')
        self.assertEqual(ready_steps(state),[])
        before=self.store.path.read_bytes()
        duplicate=self.store.receive(state['revision'],event)
        self.assertEqual(duplicate['revision'],state['revision'])
        self.assertEqual(self.store.path.read_bytes(),before)
        with self.assertRaises(ValueError):self.dispatch(state,'code-review',parent=parent['frame_id'])
        with self.assertRaises(ValueError):self.store.complete(state['revision'],[])
        self.assertEqual(self.store.path.read_bytes(),before)

    def test_paused_resolution_and_acceptance_are_retained_without_being_applied(self):
        state=self.dispatch(self.create())
        parent=state['frames'][-1]
        state=self.dispatch(state,'code-review',parent=parent['frame_id'])
        original=state['frames'][-1]
        state=self.store.receive(state['revision'],self.event(original,findings=['synthetic defect']))
        state=self.dispatch(state,'domain-modeling',parent=parent['frame_id'])
        resolver=state['frames'][-1]
        state=self.store.control(state['revision'],'paused','synthetic explicit user pause')
        criterion=state['acceptance'][0]
        claim={'id':criterion['id'],'kind':'technical','status':'passed',
               'completion_checked':True,'evidence':[str(self.output)]}
        state=self.store.receive(state['revision'],self.event(resolver,
            resolved_findings=[original['frame_id']],acceptance_checks=[claim]))
        self.assertEqual(state['events'][-1]['resolved_findings'],[original['frame_id']])
        self.assertEqual(state['events'][-1]['acceptance_checks'][0]['status'],'passed')
        self.assertEqual(state['acceptance'][0]['status'],'pending')
        self.assertTrue(next(f for f in state['frames'] if f['frame_id']==original['frame_id'])['needs_assessment'])
        before=self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.assess(state['revision'],original['frame_id'],'synthetic repair',[str(self.output)],resolver['frame_id'])
        self.assertEqual(self.store.path.read_bytes(),before)
        with self.assertRaises(ValueError):self.store.complete(state['revision'],[])

    def test_only_explicit_user_resume_preserves_received_and_inflight_work(self):
        state=self.dispatch(self.create())
        parent=state['frames'][-1]
        state=self.dispatch(state,'tdd',parent=parent['frame_id'])
        received=state['frames'][-1]
        state=self.dispatch(state,'code-review',parent=parent['frame_id'])
        inflight=state['frames'][-1]
        state=self.store.control(state['revision'],'paused','synthetic explicit pause')
        event=self.event(received)
        state=self.store.receive(state['revision'],event)
        before=self.store.path.read_bytes()
        with self.assertRaises(ValueError):self.store.resume(state['revision'])
        self.assertEqual(before,self.store.path.read_bytes())
        frames=state['frames']
        events=state['events']
        state=self.store.resume(state['revision'],user_reference='synthetic user turn: continue')
        self.assertEqual(state['status'],'running')
        self.assertEqual(state['control']['resume_user_reference'],'synthetic user turn: continue')
        self.assertEqual(state['frames'],frames)
        self.assertEqual(state['events'],events)
        state=self.store.receive(state['revision'],self.event(inflight))
        self.assertEqual(len(state['frames']),3)
        self.assertEqual(state['next_decision']['action'],'resume_parent')
        state=self.store.receive(state['revision'],self.event(parent))
        self.assertEqual(state['frames'][0]['status'],'completed')

    def test_paused_repeat_stop_retains_user_pause_decision(self):
        state=self.dispatch(self.create())
        frame=state['frames'][-1]
        state=self.store.receive(state['revision'],self.event(frame,'blocked',blockers=['same synthetic blocker']))
        state=self.store.control(state['revision'],'paused','synthetic explicit pause')
        event=self.event(frame,'blocked',blockers=['same synthetic blocker'])
        state=self.store.receive(state['revision'],event)
        self.assertEqual(state['next_decision']['action'],'paused')
        self.assertEqual(state['control']['user_reason'],'synthetic explicit pause')
        self.assertTrue(any(r['kind']=='no_progress' for r in state['restrictions']))
        before=self.store.path.read_bytes()
        self.store.receive(state['revision'],event)
        self.assertEqual(self.store.path.read_bytes(),before)
        state=self.store.resume(state['revision'],user_reference='synthetic user continuation')
        self.assertTrue(any(r['kind']=='no_progress' for r in state['restrictions']))
        self.assertEqual(len(state['events']),2)

    def test_paused_protocol_identity_conflicts_and_stale_inputs_are_rejected_atomically(self):
        source=self.root/'input.txt'
        source.write_text('approved input',encoding='utf-8')
        state=self.dispatch(self.create(),inputs=[str(source)])
        frame=state['frames'][-1]
        state=self.store.control(state['revision'],'paused','synthetic explicit pause')
        event=self.event(frame)
        before=self.store.path.read_bytes()
        for changes in ({'run_id':'wrong-run'}, {'frame_id':'unknown'},
                        {'parent_frame_id':'wrong-parent'}, {'skill_id':'tdd'},
                        {'protocol_version':2}, {'protocol_version':None},
                        {'input_version':'old'}, {'completion_checked':False}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):self.store.receive(state['revision'],{**event,**changes})
                self.assertEqual(self.store.path.read_bytes(),before)
        state=self.store.receive(state['revision'],event)
        before=self.store.path.read_bytes()
        with self.assertRaises(ValueError):self.store.receive(state['revision'],{**event,'findings':['conflict']})
        self.assertEqual(self.store.path.read_bytes(),before)
        source.write_text('changed input',encoding='utf-8')
        with self.assertRaises(ValueError):self.store.receive(state['revision'],event)
        self.assertEqual(self.store.path.read_bytes(),before)

    def test_paused_superseded_frame_cannot_return(self):
        state=self.dispatch(self.create())
        old=state['frames'][-1]
        state=self.store.receive(state['revision'],self.event(old,'failed'))
        state=self.dispatch(state)
        current=state['frames'][-1]
        state=self.store.control(state['revision'],'paused','synthetic explicit pause')
        before=self.store.path.read_bytes()
        with self.assertRaises(ValueError):self.store.receive(state['revision'],self.event(old))
        self.assertEqual(self.store.path.read_bytes(),before)
        state=self.store.receive(state['revision'],self.event(current))
        self.assertEqual(state['status'],'paused')

    def test_paused_parent_still_cannot_complete_with_required_child(self):
        state=self.dispatch(self.create())
        parent=state['frames'][-1]
        state=self.dispatch(state,'tdd',parent=parent['frame_id'])
        state=self.store.control(state['revision'],'paused','synthetic explicit pause')
        before=self.store.path.read_bytes()
        with self.assertRaises(ValueError):self.store.receive(state['revision'],self.event(parent))
        self.assertEqual(self.store.path.read_bytes(),before)
