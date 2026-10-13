import copy
import json
import tempfile
import unittest
from pathlib import Path

from workflow_state import RunStore, SKILL_ROOT


class GrillRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        registry = json.loads((SKILL_ROOT / 'registry.json').read_text(encoding='utf-8'))
        for skill in registry['skills']:
            path = self.root / (skill['id'] + '.md')
            path.write_text('---\nname: ' + skill['id'] + '\ndescription: fixture\n---\n', encoding='utf-8')
            skill['path'] = str(path)
        self.registry = self.root / 'registry.json'
        self.registry.write_text(json.dumps(registry), encoding='utf-8')
        self.store = RunStore(self.root, 'checkout')
        self.state = self.store.create('checkout change', [{'step_id':'grill', 'skill_id':'grill-with-docs'}], ['verified change'], self.registry)
        self.context = {'project_root':str(self.root), 'goal':'checkout change', 'scope':['checkout change'], 'input_version':'v1'}

    def delivery(self, direct=True):
        document = self.root / 'requirements.md'
        document.write_text('fixture requirements v1', encoding='utf-8')
        return {'document':str(document), 'version':'v1', 'confirmed':True,
                'confirmation_reference':'fixture confirmation (not production)',
                'core_questions_resolved':True, 'helpers_resolved':True, 'checklist':['saved','reviewed'],
                'direct_conditions':{key:{'satisfied':direct, 'evidence':'fixture scope decision'}
                    for key in ('single_task','scope_clear','constraints_clear','verification_sufficient','single_session')}}

    def grill_event(self, state, delivery=None):
        frame = state['frames'][-1]
        delivery = delivery or self.delivery()
        return {'event_id':frame['frame_id'] + '-return', 'run_id':state['run_id'],
                'frame_id':frame['frame_id'], 'parent_frame_id':frame['parent_frame_id'],
                'skill_id':frame['skill_id'], 'input_version':frame['input_version'],
                'mode':'skill', 'execution':'sequential', 'status':'completed', 'return_to':'develop-system',
                'completion_checked':True, 'artifacts':[delivery['document']], 'checks':[],
                'findings':[], 'blockers':[], 'acceptance_checks':[], 'resolved_findings':[],
                'grill_delivery':delivery}

    def completed_grill(self, direct=True):
        state = self.store.dispatch(self.state['revision'], 'grill-with-docs', 'v1', step_id='grill')
        event = self.grill_event(state, self.delivery(direct))
        return self.store.receive(state['revision'], event), event

    def test_completed_root_return_routes_once_and_persists_reasons(self):
        state, event = self.completed_grill()
        self.assertEqual(len(state['plan']), 1)
        state = self.store.route_grill(state['revision'], event['event_id'])
        self.assertEqual(state['grill_routes'][0]['skills'], ['implement'])
        self.assertEqual(state['grill_routes'][0]['source_event_id'], event['event_id'])
        from workflow_state import ready_steps
        self.assertEqual([s['skill_id'] for s in ready_steps(state)], ['implement'])
        before = self.store.path.read_bytes()
        repeated = self.store.route_grill(state['revision'], event['event_id'])
        self.assertEqual(repeated['revision'], state['revision'])
        self.assertEqual(before, self.store.path.read_bytes())

    def test_grill_cannot_claim_completion_before_current_document_and_questions(self):
        state = self.store.dispatch(self.state['revision'], 'grill-with-docs', 'v1', step_id='grill')
        event = self.grill_event(state)
        for key in ('confirmed','core_questions_resolved','helpers_resolved'):
            invalid = copy.deepcopy(event); invalid['grill_delivery'][key] = False
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.store.receive(state['revision'], invalid)
        for changes in ({'confirmation_reference':''}, {'version':''}, {'document':str(self.root/'absent.md')}):
            invalid = copy.deepcopy(event); invalid['grill_delivery'].update(changes)
            with self.assertRaises((ValueError, OSError)):
                self.store.receive(state['revision'], invalid)
        self.assertEqual(self.store.read()['revision'], state['revision'])

    def test_complex_route_reuses_a_valid_same_version_spec(self):
        store = RunStore(self.root, 'reuse')
        state = store.create('checkout change', [
            {'step_id':'existing-spec','skill_id':'to-spec','requirement_version':'v1','scope':['checkout change']},
            {'step_id':'grill','skill_id':'grill-with-docs','depends_on':['existing-spec']}], ['verified'], self.registry)
        state = store.dispatch(state['revision'], 'to-spec', 'v1', step_id='existing-spec')
        state = store.receive(state['revision'], self.grill_event(state))
        state = store.dispatch(state['revision'], 'grill-with-docs', 'v1', step_id='grill')
        event = self.grill_event(state, self.delivery(False))
        state = store.receive(state['revision'], event)
        state = store.route_grill(state['revision'], event['event_id'])
        self.assertEqual([s['skill_id'] for s in state['plan']].count('to-spec'), 1)
        self.assertEqual(state['grill_routes'][0]['reused_step_ids'], ['existing-spec'])
        from workflow_state import ready_steps
        self.assertEqual([s['skill_id'] for s in ready_steps(state)], ['to-tickets'])

    def test_complex_route_adds_spec_before_tickets(self):
        state, event = self.completed_grill(False)
        state = self.store.route_grill(state['revision'], event['event_id'])
        self.assertEqual(state['grill_routes'][0]['skills'], ['to-spec','to-tickets'])
        self.assertIn(state['plan'][1]['step_id'], state['plan'][2]['depends_on'])

    def test_route_reuses_a_valid_pending_step_instead_of_duplicate_implementation(self):
        store = RunStore(self.root, 'pending')
        state = store.create('checkout change', [
            {'step_id':'grill','skill_id':'grill-with-docs'},
            {'step_id':'planned-implement','skill_id':'implement','depends_on':['grill'],
             'requirement_version':'v1','scope':['checkout change']}], ['verified'], self.registry)
        state = store.dispatch(state['revision'], 'grill-with-docs', 'v1', step_id='grill')
        event = self.grill_event(state)
        state = store.receive(state['revision'], event)
        state = store.route_grill(state['revision'], event['event_id'])
        self.assertEqual([s['skill_id'] for s in state['plan']].count('implement'), 1)
        self.assertEqual(state['grill_routes'][0]['reused_step_ids'], ['planned-implement'])

    def test_attach_reuses_root_frame_and_auxiliary_return_resumes_parent_only(self):
        state = self.store.attach_grill(self.state['revision'], self.context)
        root_id = state['frames'][-1]['frame_id']
        before = self.store.path.read_bytes()
        repeated = self.store.attach_grill(state['revision'], self.context)
        self.assertEqual(repeated['frames'][-1]['frame_id'], root_id)
        self.assertEqual(before, self.store.path.read_bytes())
        other = RunStore(self.root, 'helper')
        state = other.create('checkout change', [{'step_id':'implementation','skill_id':'implement'}], ['verified'], self.registry)
        state = other.dispatch(state['revision'], 'implement', 'v1', step_id='implementation')
        parent_id = state['frames'][-1]['frame_id']
        context = {**self.context, 'run_id':'helper', 'parent_frame_id':parent_id}
        state = other.attach_grill(state['revision'], context)
        before = other.path.read_bytes()
        repeated = other.attach_grill(state['revision'], {**self.context,'run_id':'helper'})
        self.assertEqual(repeated['frames'][-1]['frame_id'], state['frames'][-1]['frame_id'])
        self.assertEqual(before, other.path.read_bytes())
        event = self.grill_event(state)
        state = other.receive(state['revision'], event)
        self.assertEqual(state['next_decision']['action'], 'resume_parent')
        self.assertEqual(state['frames'][0]['status'], 'running')
        self.assertEqual(len(state['plan']), 1)
        with self.assertRaises(ValueError):
            other.route_grill(state['revision'], event['event_id'])

    def test_attachment_revalidates_revision_inputs_and_interrupted_frames(self):
        state = self.store.attach_grill(self.state['revision'], self.context)
        old_event = self.grill_event(state)
        before = self.store.path.read_bytes()
        for revision, context in ((0,self.context), (state['revision'],{**self.context,'input_version':'v2'}),
                                  (state['revision'],{**self.context,'independent':True})):
            with self.assertRaises(ValueError):
                self.store.attach_grill(revision, context)
            self.assertEqual(before, self.store.path.read_bytes())
        state = self.store.resume(state['revision'])
        state = self.store.attach_grill(state['revision'], {**self.context,'input_version':'v2'})
        self.assertEqual(state['frames'][0]['status'], 'superseded')
        self.assertEqual(state['frames'][-1]['input_version'], 'v2')
        with self.assertRaises(ValueError):
            self.store.receive(state['revision'], old_event)

    def test_changed_requirements_reroute_supersedes_previous_branch(self):
        state, event = self.completed_grill(False)
        state = self.store.route_grill(state['revision'], event['event_id'])
        old_steps = state['grill_routes'][0]['step_ids']
        Path(event['grill_delivery']['document']).write_text('changed requirements v2', encoding='utf-8')
        state = self.store.resume(state['revision'])
        state = self.store.attach_grill(state['revision'], {**self.context,'input_version':'v2'})
        delivery = self.delivery(); delivery['version'] = 'v2'
        event = self.grill_event(state, delivery)
        state = self.store.receive(state['revision'], event)
        state = self.store.route_grill(state['revision'], event['event_id'])
        from workflow_state import ready_steps
        self.assertEqual([s['skill_id'] for s in ready_steps(state)], ['implement'])
        self.assertTrue(all(not s['required'] for s in state['plan'] if s['step_id'] in old_steps))

    def test_retiring_a_branch_cannot_hide_nested_helper_findings(self):
        state, event = self.completed_grill(False)
        state = self.store.route_grill(state['revision'], event['event_id'])
        spec_step = state['grill_routes'][0]['step_ids'][0]
        state = self.store.dispatch(state['revision'], 'to-spec', 'v1', step_id=spec_step)
        parent = state['frames'][-1]['frame_id']
        state = self.store.dispatch(state['revision'], 'domain-modeling', 'v1', parent_frame_id=parent)
        child = state['frames'][-1]['frame_id']
        finding = self.grill_event(state); finding['findings'] = ['fixture unresolved domain conflict']
        state = self.store.receive(state['revision'], finding)
        Path(event['grill_delivery']['document']).write_text('changed requirements', encoding='utf-8')
        state = self.store.resume(state['revision'])
        # Incoming plan fixture: root clarification is allowed to address this finding.
        state['plan'][0]['resolves_frames'] = [child]
        self.store.write_atomic(state)
        state = self.store.attach_grill(state['revision'], {**self.context,'input_version':'v2'})
        delivery = self.delivery(); delivery['version'] = 'v2'
        returned = self.grill_event(state, delivery)
        state = self.store.receive(state['revision'], returned)
        before = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.route_grill(state['revision'], returned['event_id'])
        self.assertEqual(before, self.store.path.read_bytes())
        self.assertTrue(next(f for f in self.store.read()['frames'] if f['frame_id'] == child)['needs_assessment'])

    def inspect(self, **changes):
        return RunStore.inspect_grill_entry({**self.context, **changes})

    def test_entry_inspection_inherits_exact_goal_without_writing(self):
        before = self.store.path.read_bytes()
        decision = self.inspect()
        self.assertEqual(decision['action'], 'managed')
        self.assertEqual(decision['run_id'], 'checkout')
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_independent_override_and_no_candidate_leave_state_untouched(self):
        before = self.store.path.read_bytes()
        self.assertEqual(self.inspect(independent=True)['action'], 'independent')
        self.assertEqual(self.inspect(goal='unrelated', scope=['unrelated'])['action'], 'independent')
        self.assertEqual(self.store.path.read_bytes(), before)
        new_root = self.root / 'empty'; new_root.mkdir()
        self.assertEqual(self.inspect(project_root=str(new_root))['action'], 'independent')
        self.assertFalse((new_root / '.develop-system').exists())

    def test_ambiguous_and_weak_matches_require_clarification(self):
        other = RunStore(self.root, 'checkout-other')
        other.create('checkout change', [{'step_id':'grill', 'skill_id':'grill-with-docs'}], ['verified'], self.registry)
        before = [s.path.read_bytes() for s in (self.store, other)]
        self.assertEqual(self.inspect()['action'], 'clarify')
        self.assertEqual(self.inspect(goal='checkout', scope=['checkout'])['action'], 'clarify')
        self.assertEqual([s.path.read_bytes() for s in (self.store, other)], before)

    def test_explicit_binding_is_validated_and_can_disambiguate(self):
        decision = self.inspect(run_id='checkout')
        self.assertEqual(decision['action'], 'managed')
        self.assertEqual(self.inspect(run_id='missing')['action'], 'clarify')
        self.assertEqual(self.inspect(run_id='checkout', frame_id='missing')['action'], 'clarify')

    def test_closed_legacy_paused_waiting_and_cross_project_boundaries(self):
        from grill_routing import decide_entry
        for status in ('completed', 'cancelled'):
            closed = {**self.state, 'status':status}
            self.assertEqual(decide_entry(self.context, [closed])['action'], 'independent')
        for override in ({'schema_version':1}, {'status':'paused'}, {'status':'waiting_user'}, {'status':'blocked'}):
            self.assertEqual(decide_entry(self.context, [{**self.state, **override}])['action'], 'clarify')
        self.assertEqual(decide_entry({**self.context, 'resume_paused':True}, [{**self.state,'status':'paused'}])['action'], 'resume')
        self.assertEqual(decide_entry({**self.context, 'recovery_ready':True}, [{**self.state,'status':'blocked'}])['action'], 'resume')
        foreign = {**self.state, 'project_root':str(self.root / 'other')}
        self.assertEqual(decide_entry(self.context, [foreign])['action'], 'independent')
        self.assertEqual(decide_entry({**self.context,'run_id':'checkout'}, [foreign])['action'], 'clarify')

    def test_independent_confirmation_never_becomes_route_selection(self):
        from grill_routing import independent_finish
        delivery = {'document':str(self.registry), 'version':'v1', 'confirmation_reference':'fixture answer', 'checklist':['saved','confirmed']}
        for answer in ('all adopted', 'confirmed', None):
            result = independent_finish(delivery, confirmed=True, choice=answer)
            self.assertEqual(result['action'], 'await_route_choice')
            self.assertEqual(result['candidates'], ['to-spec','implement'])
            self.assertEqual(result['delivery'], delivery)
        self.assertEqual(independent_finish(delivery, confirmed=False)['action'], 'await_document_confirmation')
        self.assertEqual(independent_finish(delivery, confirmed=True, choice='to-spec')['action'], 'selected')

    def test_entry_requires_revalidation_of_stale_assessment_evidence(self):
        # Imported-state fixture: an old resolution can outlive its superseded source.
        state = self.store.dispatch(self.state['revision'], 'grill-with-docs', 'v1', step_id='grill')
        state['assessments'] = [{'frame_id':state['frames'][0]['frame_id'], 'evidence':[
            {'path':str(self.root / 'missing-resolution.md'), 'sha256':'0' * 64}]}]
        self.store.write_atomic(state)
        before = self.store.path.read_bytes()
        self.assertEqual(self.inspect()['action'], 'resume')
        self.assertEqual(before, self.store.path.read_bytes())


if __name__ == '__main__':
    unittest.main()
