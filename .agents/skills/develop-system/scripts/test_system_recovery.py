"""Public RunStore tests; all professional conclusions and user sources are synthetic fixtures."""
import unittest
import sys
import json
import test_scoped_workflow as fixtures
from workflow_state import ready_steps
from record_check import run_record


class SystemRecoveryTests(unittest.TestCase):
    setUp=fixtures.ScopedWorkflowTests.setUp
    create=fixtures.ScopedWorkflowTests.create
    dispatch=fixtures.ScopedWorkflowTests.dispatch
    event=fixtures.ScopedWorkflowTests.event

    def conclusion(self,name,action='set',blocked_event_id=None):
        item={'restriction_id':name,'action':action,'scope':'global',
              'failed_preconditions':[name],'evidence':[str(self.output)]}
        if blocked_event_id:item['blocked_event_id']=blocked_event_id
        return item

    def test_registry_prerequisite_is_part_of_selective_invalidation(self):
        registry=json.loads(self.registry.read_text(encoding='utf-8'))
        next(s for s in registry['skills'] if s['id']=='to-spec')['requires']=['implement']
        self.registry.write_text(json.dumps(registry),encoding='utf-8')
        source=self.root/'required-input.txt'
        source.write_text('original',encoding='utf-8')
        state=self.dispatch(self.create([{'step_id':'a','skill_id':'implement'},
            {'step_id':'dependent','skill_id':'to-spec'}]),inputs=[str(source)])
        state=self.store.receive(state['revision'],self.event(state['frames'][-1]))
        state=self.dispatch(state,'to-spec','dependent')
        state=self.store.receive(state['revision'],self.event(state['frames'][-1]))
        source.write_text('changed',encoding='utf-8')
        state=self.store.resume(state['revision'])
        self.assertEqual([f['status'] for f in state['frames']],['stale','stale'])

    def test_professional_global_restrictions_are_independent_and_block_completion(self):
        state=self.dispatch(self.create())
        event=self.event(state['frames'][-1],system_conclusions=[self.conclusion('database'),self.conclusion('credentials')])
        state=self.store.receive(state['revision'],event)
        self.assertEqual(ready_steps(state),[])
        self.assertEqual({r['restriction_id'] for r in state['restrictions']},{'database','credentials'})
        self.assertEqual(state['control']['status'],'running')
        self.assertEqual(state['next_decision']['action'],'system_restricted')
        self.assertEqual(state['next_decision']['restriction_ids'],['credentials','database'])
        before=self.store.path.read_bytes()
        with self.assertRaises(ValueError):self.dispatch(state,'domain-modeling','b')
        with self.assertRaises(ValueError):self.store.complete(state['revision'],[])
        self.assertEqual(self.store.path.read_bytes(),before)

    def test_invalid_system_claims_and_unproven_recovery_plans_are_rejected(self):
        state=self.dispatch(self.create())
        frame=state['frames'][-1]
        before=self.store.path.read_bytes()
        for changes in ({'evidence':[]},{'scope':'unknown'},{'failed_preconditions':[]},
                        {'action':'clear','blocked_event_id':'nonexistent'}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    self.store.receive(state['revision'],self.event(frame,system_conclusions=[{**self.conclusion('database'),**changes}]))
                self.assertEqual(self.store.path.read_bytes(),before)
        with self.assertRaises(ValueError):
            self.store.extend_plan(state['revision'],[{'step_id':'unapproved','skill_id':'code-review',
                'recovery_for':['database'],'shared_preconditions':[]}],'synthetic recovery request')
        self.assertEqual(self.store.path.read_bytes(),before)

    def test_expired_paused_clear_preserves_system_and_unknown_local_restrictions(self):
        state=self.dispatch(self.create())
        parent=state['frames'][-1]
        state=self.dispatch(state,'tdd',parent=parent['frame_id'])
        local=state['frames'][-1]
        state=self.dispatch(state,'code-review',parent=parent['frame_id'])
        setter=state['frames'][-1]
        state=self.dispatch(state,'domain-modeling',parent=parent['frame_id'])
        resolver=state['frames'][-1]
        state=self.store.receive(state['revision'],self.event(local,'blocked',block_scope='unknown',blockers=['unknown impact']))
        blocked=self.event(setter,system_conclusions=[self.conclusion('database')])
        state=self.store.receive(state['revision'],blocked)
        state=self.store.control(state['revision'],'paused','synthetic user pause')
        proof=self.root/'paused-clear.txt'
        proof.write_text('valid recovery',encoding='utf-8')
        clear={**self.conclusion('database','clear',blocked['event_id']),'evidence':[str(proof)]}
        state=self.store.receive(state['revision'],self.event(resolver,artifacts=[str(proof)],system_conclusions=[clear]))
        proof.write_text('obsolete recovery',encoding='utf-8')
        state=self.store.resume(state['revision'],user_reference='synthetic explicit continuation')
        self.assertEqual([r['restriction_id'] for r in state['restrictions']],['database'])
        self.assertEqual(state['frames'][1]['block_scope'],'unknown')
        self.assertEqual(state['frames'][1]['status'],'blocked')
        self.assertEqual(ready_steps(state),[])
        self.assertEqual(len(state['events']),3)
        before=self.store.path.read_bytes()
        self.assertEqual(ready_steps(self.store.read()),[])
        self.assertEqual(self.store.path.read_bytes(),before)

    def test_only_bound_authorized_independent_recovery_can_release_one_restriction(self):
        plan=[{'step_id':'a','skill_id':'implement'},
              {'step_id':'b','skill_id':'domain-modeling'},
              {'step_id':'repair','skill_id':'code-review','recovery_for':['database','credentials'],
               'authorization_reference':'synthetic approved recovery','shared_preconditions':['local-files']},
              {'step_id':'unsafe','skill_id':'code-review','recovery_for':['database','credentials'],
               'authorization_reference':'synthetic approved recovery','shared_preconditions':['database']}]
        state=self.dispatch(self.create(plan))
        blocked=self.event(state['frames'][-1],system_conclusions=[self.conclusion('database'),self.conclusion('credentials')])
        state=self.store.receive(state['revision'],blocked)
        self.assertEqual([s['step_id'] for s in ready_steps(state)],['repair'])
        state=self.store.control(state['revision'],'paused','synthetic pause overrides recovery')
        with self.assertRaises(ValueError):self.dispatch(state,'code-review','repair')
        state=self.store.resume(state['revision'],user_reference='synthetic user continuation')
        state=self.dispatch(state,'code-review','repair')
        recovery=state['frames'][-1]
        state=self.store.receive(state['revision'],self.event(recovery,
            system_conclusions=[self.conclusion('database','clear',blocked['event_id'])]))
        self.assertEqual([r['restriction_id'] for r in state['restrictions']],['credentials'])
        self.assertNotIn('b',[s['step_id'] for s in ready_steps(state)])
        self.assertEqual(state['next_decision']['restriction_ids'],['credentials'])

    def test_paused_professional_recovery_is_applied_only_after_explicit_continue(self):
        state=self.dispatch(self.create())
        parent=state['frames'][-1]
        state=self.dispatch(state,'code-review',parent=parent['frame_id'])
        setter=state['frames'][-1]
        state=self.dispatch(state,'domain-modeling',parent=parent['frame_id'])
        resolver=state['frames'][-1]
        blocked=self.event(setter,system_conclusions=[self.conclusion('database')])
        state=self.store.receive(state['revision'],blocked)
        state=self.store.control(state['revision'],'paused','synthetic pause')
        cleared=self.event(resolver,system_conclusions=[self.conclusion('database','clear',blocked['event_id'])])
        state=self.store.receive(state['revision'],cleared)
        self.assertEqual([r['restriction_id'] for r in state['restrictions']],['database'])
        self.assertEqual(state['next_decision']['action'],'paused')
        events=state['events']
        state=self.store.resume(state['revision'],user_reference='synthetic explicit continue')
        self.assertEqual(state['restrictions'],[])
        self.assertEqual(state['events'],events)
        self.assertEqual(len(state['frames']),3)
        self.assertEqual(state['frames'][0]['status'],'running')
        state=self.store.receive(state['revision'],self.event(parent))
        self.assertEqual(state['frames'][0]['status'],'completed')

    def test_input_revalidation_preserves_independent_results_and_inflight_frames(self):
        plan=[{'step_id':'a','skill_id':'implement'},
              {'step_id':'a-dependent','skill_id':'to-spec','depends_on':['a']},
              {'step_id':'b','skill_id':'domain-modeling'},
              {'step_id':'c','skill_id':'code-review'}]
        source=self.root/'a-input.txt'
        source.write_text('approved a',encoding='utf-8')
        state=self.dispatch(self.create(plan),'code-review','c')
        independent=state['frames'][-1]
        state=self.store.receive(state['revision'],self.event(independent))
        state=self.dispatch(state,inputs=[str(source)])
        a=state['frames'][-1]
        state=self.store.receive(state['revision'],self.event(a))
        state=self.dispatch(state,'to-spec','a-dependent')
        dependent=state['frames'][-1]
        state=self.store.receive(state['revision'],self.event(dependent))
        state=self.dispatch(state,'domain-modeling','b')
        inflight=state['frames'][-1]
        events=state['events']
        source.write_text('changed a',encoding='utf-8')
        state=self.store.resume(state['revision'])
        statuses={f['frame_id']:f['status'] for f in state['frames']}
        self.assertEqual(statuses[independent['frame_id']],'completed')
        self.assertEqual(statuses[inflight['frame_id']],'running')
        self.assertEqual(statuses[a['frame_id']],'stale')
        self.assertEqual(statuses[dependent['frame_id']],'stale')
        self.assertEqual(state['events'],events)
        state=self.store.receive(state['revision'],self.event(inflight))
        self.assertEqual(len(state['frames']),4)
        self.assertEqual([s['step_id'] for s in ready_steps(state)],['a'])

    def test_changed_child_does_not_discard_independent_inflight_sibling(self):
        source=self.root/'child-input.txt'
        source.write_text('old',encoding='utf-8')
        state=self.dispatch(self.create())
        parent=state['frames'][-1]
        state=self.dispatch(state,'tdd',parent=parent['frame_id'],inputs=[str(source)])
        changed=state['frames'][-1]
        state=self.store.receive(state['revision'],self.event(changed))
        state=self.dispatch(state,'domain-modeling',parent=parent['frame_id'])
        sibling=state['frames'][-1]
        source.write_text('new',encoding='utf-8')
        state=self.store.resume(state['revision'])
        self.assertEqual(state['frames'][0]['status'],'running')
        self.assertEqual(state['frames'][1]['status'],'stale')
        self.assertEqual(state['frames'][2]['status'],'running')
        state=self.store.receive(state['revision'],self.event(sibling))
        with self.assertRaises(ValueError):self.store.receive(state['revision'],self.event(parent))
        state=self.dispatch(state,'tdd',parent=parent['frame_id'],inputs=[str(source)])
        self.assertEqual(state['frames'][1]['status'],'superseded')
        self.assertEqual(state['frames'][2]['status'],'completed')

    def test_expired_recovery_evidence_restores_only_its_system_restriction(self):
        plan=[{'step_id':'a','skill_id':'implement'},
              {'step_id':'repair','skill_id':'code-review','recovery_for':['database'],
               'authorization_reference':'synthetic approved repair','shared_preconditions':[]},
              {'step_id':'b','skill_id':'domain-modeling'}]
        state=self.dispatch(self.create(plan))
        blocked=self.event(state['frames'][-1],system_conclusions=[self.conclusion('database')])
        state=self.store.receive(state['revision'],blocked)
        state=self.dispatch(state,'code-review','repair')
        proof=self.root/'recovery.txt'
        proof.write_text('verified recovery',encoding='utf-8')
        clear={**self.conclusion('database','clear',blocked['event_id']),'evidence':[str(proof)]}
        state=self.store.receive(state['revision'],self.event(state['frames'][-1],artifacts=[str(proof)],system_conclusions=[clear]))
        self.assertEqual(state['restrictions'],[])
        proof.write_text('changed recovery evidence',encoding='utf-8')
        state=self.store.resume(state['revision'])
        self.assertEqual([r['restriction_id'] for r in state['restrictions']],['database'])
        self.assertNotIn('b',[s['step_id'] for s in ready_steps(state)])
        self.assertEqual(len(state['events']),2)

    def test_failed_check_cannot_support_a_system_recovery_conclusion(self):
        plan=[{'step_id':'a','skill_id':'implement'},
              {'step_id':'repair','skill_id':'code-review','recovery_for':['database'],
               'authorization_reference':'synthetic approved repair','shared_preconditions':[]}]
        state=self.dispatch(self.create(plan))
        blocked=self.event(state['frames'][-1],system_conclusions=[self.conclusion('database')])
        state=self.store.receive(state['revision'],blocked)
        state=self.dispatch(state,'code-review','repair')
        record=self.root/'expected-failed-check.json'
        run_record([sys.executable,'-c','raise SystemExit(1)'],self.root,record)
        before=self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.receive(state['revision'],self.event(state['frames'][-1],
                checks=[{'record':str(record),'status':'failed'}],
                system_conclusions=[self.conclusion('database','clear',blocked['event_id'])]))
        self.assertEqual(self.store.path.read_bytes(),before)
