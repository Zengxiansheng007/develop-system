"""Public registration repair contracts; professional event fixtures are synthetic."""
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from workflow_state import RunStore,SKILL_ROOT,ready_steps


class RegistrationRepairTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.registry=self.root/'registry.json'
        data=json.loads((SKILL_ROOT/'registry.json').read_text(encoding='utf-8'))
        for item in data['skills']:
            item['path']=str((SKILL_ROOT/item['path']).resolve())
        self.descriptor=copy.deepcopy(next(i for i in data['skills'] if i['id']=='writing-for-agents'))
        data['skills']=[i for i in data['skills'] if i['id']!='writing-for-agents']
        self.registry.write_text(json.dumps(data),encoding='utf-8')
        path=Path(self.descriptor['path'])
        self.authorization={'skill_id':'writing-for-agents','skill_path':str(path),
            'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'authorization_reference':'synthetic user authorization for this exact helper and work'}
        self.plan=[{'step_id':'a','skill_id':'retro','capability_authorizations':[self.authorization]},
            {'step_id':'b','skill_id':'code-review'}]
        self.store=RunStore(self.root,'repair')
        self.report=self.root/'synthetic.md'
        self.report.write_text('Synthetic professional result; not actual skill application.\n',encoding='utf-8')

    def create(self):
        return self.store.create('registration repair',self.plan,['synthetic acceptance'],self.registry)

    def block(self,state,**kwargs):
        return self.store.block_unavailable(state['revision'],'writing-for-agents',
            'helper file exists but is absent from this run',category='registration_missing',**kwargs)

    def event(self,frame):
        return {'protocol_version':1,'event_id':uuid.uuid4().hex,'run_id':self.store.run_id,
            'frame_id':frame['frame_id'],'parent_frame_id':frame['parent_frame_id'],
            'skill_id':frame['skill_id'],'input_version':frame['input_version'],
            'mode':'skill','execution':'sequential','status':'completed','return_to':'develop-system',
            'completion_checked':True,'artifacts':[str(self.report)],'checks':[],
            'findings':[],'blockers':[],'acceptance_checks':[],'resolved_findings':[]}

    def test_missing_registration_has_an_owner_and_independent_work_continues(self):
        state=self.create()
        before=self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.dispatch(state['revision'],'retro','v1','a')
        self.assertEqual(self.store.path.read_bytes(),before)
        state=self.block(state,step_id='a')
        self.assertEqual(state['frames'],[])
        self.assertEqual(state['temporary_skills'],[])
        hold=state['restrictions'][-1]
        self.assertEqual(hold['category'],'registration_missing')
        self.assertEqual(hold['step_id'],'a')
        self.assertEqual(hold['source']['skill_path'],self.authorization['skill_path'])
        self.assertEqual([s['step_id'] for s in ready_steps(state)],['b'])
        state=self.store.dispatch(state['revision'],'code-review','v1','b')
        state=self.store.receive(state['revision'],self.event(state['frames'][-1]))
        self.assertEqual(state['status'],'running')
        self.assertEqual(state['frames'][-1]['status'],'completed')
        self.assertEqual(ready_steps(state),[])

    def local_helper(self):
        folder=self.root/'helper';folder.mkdir()
        path=folder/'SKILL.md'
        path.write_text('---\nname: writing-for-agents\ndescription: synthetic source\n---\n',encoding='utf-8')
        self.descriptor['path']=str(path)
        self.authorization.update(skill_path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        return path

    def test_correction_preserves_pause_other_holds_and_selected_parent_duty(self):
        self.plan[0]['skill_id']='code-review'
        self.plan[1]['skill_id']='domain-modeling'
        data=json.loads(self.registry.read_text(encoding='utf-8'))
        next(i for i in data['skills'] if i['id']=='domain-modeling')['path']=str(self.root/'absent.md')
        self.registry.write_text(json.dumps(data),encoding='utf-8')
        state=self.create()
        state=self.store.dispatch(state['revision'],'code-review','v1','a')
        parent=state['frames'][-1]
        state=self.block(state,parent_frame_id=parent['frame_id'])
        hold=state['restrictions'][-1]
        state=self.store.block_unavailable(state['revision'],'domain-modeling','synthetic missing file',step_id='b')
        other=copy.deepcopy(state['restrictions'][-1])
        state=self.store.control(state['revision'],'paused','synthetic explicit user pause')
        state=self.store.register(state['revision'],self.descriptor,restriction_id=hold['restriction_id'])
        self.assertEqual(state['control'],{'status':'paused','user_reason':'synthetic explicit user pause'})
        self.assertEqual(state['restrictions'],[other])
        self.assertEqual(len(state['frames']),1)
        with self.assertRaises(ValueError):
            self.store.dispatch(state['revision'],'writing-for-agents','v1',parent_frame_id=parent['frame_id'])
        with self.assertRaises(ValueError):
            self.store.receive(state['revision'],self.event(parent))
        state=self.store.resume(state['revision'],user_reference='synthetic explicit continue')
        self.assertEqual(state['restrictions'],[other])
        state=self.store.dispatch(state['revision'],'writing-for-agents','v1',parent_frame_id=parent['frame_id'],required=False)
        self.assertTrue(state['frames'][-1]['required'])
        state=self.store.receive(state['revision'],self.event(state['frames'][-1]))
        state=self.store.receive(state['revision'],self.event(parent))
        self.assertEqual(state['frames'][0]['status'],'completed')
        self.assertEqual(state['restrictions'],[other])

    def test_foreign_source_and_changed_content_are_rejected_without_mutation(self):
        path=self.local_helper()
        state=self.block(self.create(),step_id='a')
        hold=state['restrictions'][-1]
        before=self.store.path.read_bytes()
        alternate=self.root/'same-name.md'
        alternate.write_bytes(path.read_bytes())
        descriptor={**self.descriptor,'path':str(alternate)}
        with self.assertRaises(ValueError):
            self.store.register(state['revision'],descriptor,restriction_id=hold['restriction_id'])
        self.assertEqual(self.store.path.read_bytes(),before)
        path.write_text(path.read_text(encoding='utf-8')+'changed version\n',encoding='utf-8')
        with self.assertRaises(ValueError):
            self.store.register(state['revision'],self.descriptor,restriction_id=hold['restriction_id'])
        self.assertEqual(self.store.path.read_bytes(),before)

    def test_changed_invocation_policy_does_not_expand_authority(self):
        path=self.local_helper()
        state=self.block(self.create(),step_id='a')
        hold=state['restrictions'][-1]
        before=self.store.path.read_bytes()
        agents=path.parent/'agents';agents.mkdir()
        (agents/'openai.yaml').write_text('policy:\n  allow_implicit_invocation: false\n',encoding='utf-8')
        with self.assertRaises(ValueError):
            self.store.register(state['revision'],self.descriptor,restriction_id=hold['restriction_id'])
        self.assertEqual(self.store.path.read_bytes(),before)

    def test_missing_authority_disabled_and_duplicate_entries_cannot_be_repaired(self):
        self.plan[0]['capability_authorizations']=[]
        state=self.create()
        before=self.store.path.read_bytes()
        with self.assertRaises(ValueError):self.block(state,step_id='a')
        self.assertEqual(self.store.path.read_bytes(),before)
        data=json.loads(self.registry.read_text(encoding='utf-8'))
        data['skills'].append({**self.descriptor,'enabled':False})
        disabled=self.root/'disabled.json';disabled.write_text(json.dumps(data),encoding='utf-8')
        self.store=RunStore(self.root,'disabled')
        state=self.store.create('synthetic disabled source',self.plan,['synthetic'],disabled)
        before=self.store.path.read_bytes()
        with self.assertRaises(ValueError):self.block(state,step_id='a')
        with self.assertRaises(ValueError):self.store.register(state['revision'],self.descriptor)
        self.assertEqual(self.store.path.read_bytes(),before)

    def test_explicit_correction_is_versioned_scoped_and_keeps_parent_obligations(self):
        state=self.block(self.create(),step_id='a')
        hold=state['restrictions'][-1]
        original_registry=self.registry.read_bytes()
        other=RunStore(self.root,'other')
        other.create('unaffected run',self.plan,['synthetic'],self.registry)
        other_bytes=other.path.read_bytes()
        before=self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.register(state['revision'],self.descriptor)
        self.assertEqual(self.store.path.read_bytes(),before)
        with self.assertRaises(ValueError):
            self.store.register(state['revision']-1,self.descriptor,restriction_id=hold['restriction_id'])
        self.assertEqual(self.store.path.read_bytes(),before)
        state=self.store.register(state['revision'],self.descriptor,restriction_id=hold['restriction_id'])
        self.assertEqual(self.registry.read_bytes(),original_registry)
        self.assertEqual(other.path.read_bytes(),other_bytes)
        self.assertEqual(state['restrictions'],[])
        self.assertEqual(state['frames'],[])
        selected=self.store.preflight('retro')
        state=self.store.dispatch(state['revision'],'retro','v1','a')
        parent=state['frames'][-1]
        with self.assertRaises(ValueError):
            self.store.receive(state['revision'],self.event(parent))
        state=self.store.dispatch(state['revision'],'writing-for-agents','v1',parent_frame_id=parent['frame_id'])
        child=state['frames'][-1]
        self.assertEqual(child['skill_path'],selected['writing-for-agents']['skill_path'])
        self.assertEqual(child['skill_sha256'],selected['writing-for-agents']['sha256'])
        state=self.store.receive(state['revision'],self.event(child))
        state=self.store.receive(state['revision'],self.event(parent))
        self.assertEqual(state['frames'][0]['status'],'completed')

    def test_cli_exposes_explicit_block_and_correction(self):
        state=self.create()
        def cli(command,*args):
            result=subprocess.run([sys.executable,'-B',str(SKILL_ROOT/'scripts/workflow_state.py'),
                command,'--entry','develop-system','--project-root',str(self.root),'--run-id','repair',
                '--revision',str(self.store.read()['revision']),*args],
                capture_output=True,text=True,encoding='utf-8',timeout=30)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            return json.loads(result.stdout)
        state=cli('block','--skill-id','writing-for-agents','--step-id','a',
            '--category','registration_missing','--reason','synthetic CLI selected-helper discovery')
        hold=state['restrictions'][-1]
        payload=self.root/'descriptor.json'
        payload.write_text(json.dumps(self.descriptor),encoding='utf-8')
        state=cli('register','--payload',str(payload),'--restriction-id',hold['restriction_id'])
        self.assertEqual(state['restrictions'],[])
        self.assertEqual(state['frames'],[])
