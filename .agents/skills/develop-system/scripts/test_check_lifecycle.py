"""Public evidence lifecycle scenarios with real commands and synthetic professional conclusions."""
import copy
import json
import shutil
import sys
import unittest
from pathlib import Path
import test_worktree_checks as fixtures
from record_check import run_record


class CheckLifecycleTests(unittest.TestCase):
    setUp=fixtures.WorktreeCheckTests.setUp
    git_run=fixtures.WorktreeCheckTests.git_run
    dispatch=fixtures.WorktreeCheckTests.dispatch
    check=fixtures.WorktreeCheckTests.check

    def authorization(self):
        value=fixtures.WorktreeCheckTests.authorization(self)
        value['source_ranges']+=getattr(self,'extra_ranges',[])
        return value

    def candidate(self):
        return copy.deepcopy(self.selection) if hasattr(self,'selection') else fixtures.WorktreeCheckTests.candidate(self)

    def event(self,frame,record):
        event=fixtures.WorktreeCheckTests.event(self,frame,record)
        event.update(skill_id=frame['skill_id'],input_version=frame['input_version'],parent_frame_id=frame['parent_frame_id'])
        return event

    def review(self,source,path,record,authorization=None):
        self.state=self.store.extend_plan(self.state['revision'],
            [{'step_id':'review','skill_id':'code-review'}],'synthetic authorized review')
        self.state=self.store.dispatch(self.state['revision'],'code-review','review-v1','review',worktree=authorization or self.authorization())
        frame=self.state['frames'][-1]
        event=self.event(frame,path)
        event['checks'][0]['reference']={'run_id':record['binding']['run_id'],'frame_id':source['frame_id'],
            'record_id':record['record_id'],'candidate_id':record['candidate']['candidate_id']}
        return frame,event

    def test_review_references_original_execution_without_relabeling(self):
        source=self.dispatch()
        path,record=self.check(source)
        original=path.read_bytes()
        self.state=self.store.receive(self.state['revision'],self.event(source,path))
        reviewer,event=self.review(source,path,record)
        self.state=self.store.control(self.state['revision'],'paused','synthetic explicit pause')
        state=self.store.receive(self.state['revision'],event)
        self.assertEqual(state['control']['status'],'paused')
        self.assertEqual(state['frames'][-1]['status'],'completed')
        self.assertEqual(state['frames'][-1]['checks'][0]['reference']['frame_id'],source['frame_id'])
        self.assertNotEqual(source['frame_id'],reviewer['frame_id'])
        self.assertEqual(path.read_bytes(),original)
        self.assertEqual(self.store.receive(state['revision'],event)['revision'],state['revision'])
        state=self.store.resume(state['revision'],user_reference='synthetic explicit continuation')
        (self.worktree/'config/check.json').write_text('{"changed":true}\n',encoding='utf-8')
        state=self.store.resume(state['revision'])
        # The explicit check reference carries this relation without a plan dependency.
        self.assertEqual([f['status'] for f in state['frames']],['stale','stale'])
        self.assertEqual(path.read_bytes(),original)

    def test_accepted_candidate_mutation_blocks_completion_and_preserves_history(self):
        frame=self.dispatch()
        path,record=self.check(frame)
        event=self.event(frame,path)
        event['acceptance_checks']=[{'id':'1','kind':'technical','status':'passed',
            'completion_checked':True,'evidence':[str(path)]}]
        state=self.store.receive(self.state['revision'],event)
        original=path.read_bytes()
        (self.worktree/'config/check.json').write_text('{"changed":true}\n',encoding='utf-8')
        before=self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.complete(state['revision'],[{'id':'1','source_frame_id':frame['frame_id']}])
        self.assertEqual(self.store.path.read_bytes(),before)
        state=self.store.resume(state['revision'])
        self.assertEqual(state['frames'][0]['status'],'stale')
        self.assertEqual(state['events'][0]['event_id'],event['event_id'])
        self.assertEqual(path.read_bytes(),original)

    def test_identical_replacement_requires_new_binding_and_execution_then_move_invalidates(self):
        source=self.dispatch()
        path,record=self.check(source)
        event=self.event(source,path)
        self.state=self.store.receive(self.state['revision'],event)
        original=path.read_bytes()
        preserved=self.root/'preserved'
        self.assertTrue(self.worktree.resolve().is_relative_to(self.root.resolve()))
        self.assertTrue(preserved.resolve().is_relative_to(self.root.resolve()))
        self.worktree.rename(preserved)
        shutil.copytree(preserved,self.worktree)
        before=self.store.path.read_bytes()
        with self.assertRaises(ValueError):self.store.receive(self.state['revision'],event)
        self.assertEqual(self.store.path.read_bytes(),before)
        self.state=self.store.resume(self.state['revision'])
        self.assertEqual(self.state['frames'][0]['status'],'stale')
        replacement=self.dispatch()
        self.assertNotEqual(replacement['worktree_binding']['binding_id'],source['worktree_binding']['binding_id'])
        with self.assertRaises(ValueError):self.store.receive(self.state['revision'],self.event(replacement,path))
        fresh,fresh_record=self.check(replacement)
        self.assertEqual(fresh_record['status'],'passed',fresh_record['error'])
        self.state=self.store.receive(self.state['revision'],self.event(replacement,fresh))
        self.assertEqual(path.read_bytes(),original)
        moved=self.root/'moved'
        self.assertTrue(moved.resolve().is_relative_to(self.root.resolve()))
        self.git_run('-C',str(self.project),'worktree','move',str(self.worktree),str(moved))
        self.state=self.store.resume(self.state['revision'])
        self.assertEqual(self.state['frames'][-1]['status'],'stale')

    def test_related_roles_and_shared_dependency_invalidate_only_their_consumers(self):
        self.extra_ranges=['shared']
        shared=self.worktree/'shared';shared.mkdir()
        dependency=shared/'dependency.txt';dependency.write_text('synthetic dependency 1',encoding='utf-8')
        other=self.worktree/'tests/other.py'
        other.write_text("import sys\nfrom pathlib import Path\nsys.path.insert(0,str(Path(__file__).parents[1]/'src'))\nimport new\nassert new.NEW\n",encoding='utf-8')
        self.selection={'ranges':[{'path':'src/value.py','role':'source'},{'path':'tests/check.py','role':'test'},
            {'path':'config/check.json','role':'config'},{'path':'shared','role':'dependency'}],
            'entry':'tests/check.py','loaded':['src/value.py','tests/check.py']}
        source=self.dispatch();path,record=self.check(source)
        event=self.event(source,path)
        self.state=self.store.receive(self.state['revision'],event)
        self.state=self.store.extend_plan(self.state['revision'],[{'step_id':'b','skill_id':'domain-modeling'}],'synthetic independent verification')
        self.state=self.store.dispatch(self.state['revision'],'domain-modeling','b-v1','b',worktree=self.authorization())
        second=self.state['frames'][-1]
        selection={'ranges':[{'path':'src/new.py','role':'source'},{'path':'tests/other.py','role':'test'},
            {'path':'shared','role':'dependency'}],'entry':'tests/other.py','loaded':['src/new.py','tests/other.py']}
        second_path=self.root/'second.json'
        second_record=run_record([sys.executable,'-B',str(other)],self.worktree,second_path,
            binding=second['worktree_binding'],candidate=selection)
        self.assertEqual(second_record['status'],'passed',second_record['error'])
        self.state=self.store.receive(self.state['revision'],self.event(second,second_path))
        (self.worktree/'unrelated.md').write_text('unrelated synthetic note',encoding='utf-8')
        self.state=self.store.resume(self.state['revision'])
        self.assertEqual([f['status'] for f in self.state['frames']],['completed','completed'])
        for name in ('src/value.py','tests/check.py','config/check.json'):
            with self.subTest(role_file=name):
                file=self.worktree/name;original=file.read_text(encoding='utf-8')
                file.write_text(original+'\n',encoding='utf-8')
                before=self.store.path.read_bytes()
                with self.assertRaises(ValueError):self.store.receive(self.state['revision'],event)
                self.assertEqual(self.store.path.read_bytes(),before)
                file.write_text(original,encoding='utf-8')
        value=self.worktree/'src/value.py';value.write_text(value.read_text(encoding='utf-8')+'\n',encoding='utf-8')
        self.state=self.store.resume(self.state['revision'])
        self.assertEqual([f['status'] for f in self.state['frames']],['stale','completed'])
        dependency.write_text('synthetic dependency 2',encoding='utf-8')
        self.state=self.store.resume(self.state['revision'])
        self.assertEqual([f['status'] for f in self.state['frames']],['stale','stale'])
        self.assertEqual(len(self.state['events']),2)

    def test_wrong_review_references_and_other_worktree_reject_without_mutation(self):
        source=self.dispatch();path,record=self.check(source)
        self.state=self.store.receive(self.state['revision'],self.event(source,path))
        other=self.root/'another-worktree'
        self.git_run('-C',str(self.project),'worktree','add','-b','other-review',str(other))
        authorization={**self.authorization(),'worktree':str(other),'execution_roots':[str(other)]}
        _,event=self.review(source,path,record,authorization)
        before=self.store.path.read_bytes()
        for field in ('run_id','frame_id','record_id','candidate_id'):
            wrong=copy.deepcopy(event);wrong['checks'][0]['reference'][field]='wrong'
            with self.assertRaises(ValueError):self.store.receive(self.state['revision'],wrong)
            self.assertEqual(self.store.path.read_bytes(),before)
        copy_path=self.root/'unaccepted-copy.json';copy_path.write_bytes(path.read_bytes())
        wrong=copy.deepcopy(event);wrong['checks'][0]['record']=str(copy_path)
        with self.assertRaises(ValueError):self.store.receive(self.state['revision'],wrong)
        with self.assertRaisesRegex(ValueError,'another authorized candidate'):
            self.store.receive(self.state['revision'],event)
        self.assertEqual(self.store.path.read_bytes(),before)

    def test_actual_failed_execution_cannot_be_reused_as_review_qualification(self):
        script=self.worktree/'tests/check.py'
        script.write_text(script.read_text(encoding='utf-8')+'raise SystemExit(7)\n',encoding='utf-8')
        source=self.dispatch();path,record=self.check(source)
        self.assertEqual(record['exit_code'],7)
        event=self.event(source,path);event['checks'][0]['status']='failed'
        self.state=self.store.receive(self.state['revision'],event)
        _,review=self.review(source,path,record)
        review['checks'][0]['status']='failed'
        before=self.store.path.read_bytes()
        with self.assertRaisesRegex(ValueError,'passing qualification'):
            self.store.receive(self.state['revision'],review)
        self.assertEqual(self.store.path.read_bytes(),before)
