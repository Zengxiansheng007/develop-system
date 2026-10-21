"""Public mandatory-helper contracts; real files, explicitly synthetic professional results."""
import copy
import hashlib
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from workflow_state import RunStore, SKILL_ROOT
from validate_registry import validate
from codex_runtime import resolve_skill


class RequiredHelperTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.store=RunStore(self.root,'helpers')
        self.report=self.root/'application.md'
        self.report.write_text('Synthetic professional protocol result; no external model execution.\n',encoding='utf-8')

    def event(self,frame,**changes):
        return {'protocol_version':1,'event_id':uuid.uuid4().hex,'run_id':'helpers',
            'frame_id':frame['frame_id'],'parent_frame_id':frame['parent_frame_id'],
            'skill_id':frame['skill_id'],'input_version':frame['input_version'],
            'mode':'skill','execution':'sequential','status':'completed','return_to':'develop-system',
            'completion_checked':True,'artifacts':[str(self.report)],'checks':[],
            'findings':[],'blockers':[],'acceptance_checks':[],'resolved_findings':[],**changes}

    def create(self,skill='retro',registry=None):
        return self.store.create('synthetic helper duty',[{'step_id':'a','skill_id':skill}],['verified'],registry or SKILL_ROOT/'registry.json')

    def registry_data(self):
        data=json.loads((SKILL_ROOT/'registry.json').read_text(encoding='utf-8'))
        for item in data['skills']:
            item['path']=str((SKILL_ROOT/item['path']).resolve())
        return data

    def test_package_reports_direct_and_indirect_required_gaps(self):
        data=self.registry_data()
        data['skills']=[item for item in data['skills'] if item['id']!='writing-for-agents']
        errors=validate(data,SKILL_ROOT)
        self.assertTrue(any('retro' in e and 'writing-for-agents' in e for e in errors),errors)
        data=self.registry_data()
        next(s for s in data['skills'] if s['id']=='writing-for-agents')['required_calls']=['missing-helper']
        self.assertTrue(any('missing-helper' in e for e in validate(data,SKILL_ROOT)))

    def test_parent_cannot_complete_before_uncreated_default_helper(self):
        state=self.create()
        state=self.store.dispatch(state['revision'],'retro','v1','a',skill_path=str(SKILL_ROOT.parent/'retro/SKILL.md'))
        parent=state['frames'][-1]
        before=self.store.path.read_bytes()
        with self.assertRaises(ValueError):self.store.receive(state['revision'],self.event(parent))
        self.assertEqual(self.store.path.read_bytes(),before)

    def test_runtime_preflight_limits_missing_sources_to_selected_closure(self):
        data=self.registry_data()
        next(s for s in data['skills'] if s['id']=='writing-for-agents')['path']=str(self.root/'missing.md')
        registry=self.root/'registry.json';registry.write_text(json.dumps(data),encoding='utf-8')
        state=self.create('code-review',registry)
        before=self.store.path.read_bytes()
        selected=self.store.preflight('code-review')
        self.assertEqual(self.store.path.read_bytes(),before)
        with self.assertRaises((ValueError,OSError)):self.store.preflight('retro')
        self.assertEqual(self.store.path.read_bytes(),before)
        state=self.store.dispatch(state['revision'],'code-review','v1','a')
        frame=state['frames'][-1]
        self.assertEqual(frame['skill_path'],selected['code-review']['skill_path'])
        self.assertEqual(frame['skill_sha256'],selected['code-review']['sha256'])

    def test_default_sources_load_and_synthetic_parent_child_returns_keep_identity(self):
        state=self.create()
        selected=self.store.preflight('retro')
        self.assertEqual(set(selected),{'retro','writing-for-agents'})
        for name,source in selected.items():
            path=SKILL_ROOT.parent/name/'SKILL.md'
            self.assertEqual(source['skill_path'],str(path.resolve()))
            self.assertEqual(source['sha256'],hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertIn('name: '+name,source['content'])
        state=self.store.dispatch(state['revision'],'retro','v1','a')
        parent=state['frames'][-1]
        state=self.store.dispatch(state['revision'],'writing-for-agents','writing-v1',parent_frame_id=parent['frame_id'],required=False)
        child=state['frames'][-1]
        self.assertTrue(child['required'])
        with self.assertRaises(ValueError):self.store.receive(state['revision'],self.event(parent))
        state=self.store.receive(state['revision'],self.event(child))
        self.assertEqual(state['next_decision']['action'],'resume_parent')
        self.assertEqual(state['frames'][0]['status'],'running')
        state=self.store.receive(state['revision'],self.event(parent))
        self.assertEqual(state['frames'][0]['status'],'completed')
        state=self.store.extend_plan(state['revision'],[{'step_id':'b','skill_id':'retro'}],'synthetic second ticket')
        state=self.store.dispatch(state['revision'],'retro','other-ticket','b')
        before=self.store.path.read_bytes()
        with self.assertRaises(ValueError):self.store.receive(state['revision'],self.event(state['frames'][-1]))
        self.assertEqual(self.store.path.read_bytes(),before)

    def test_unresolved_and_stale_helper_do_not_clear_parent(self):
        state=self.create();state=self.store.dispatch(state['revision'],'retro','v1','a')
        parent=state['frames'][-1]
        state=self.store.dispatch(state['revision'],'writing-for-agents','v1',parent_frame_id=parent['frame_id'])
        child=state['frames'][-1]
        state=self.store.receive(state['revision'],self.event(child,findings=['synthetic unresolved defect']))
        with self.assertRaises(ValueError):self.store.receive(state['revision'],self.event(parent))
        self.report.write_text('changed synthetic evidence',encoding='utf-8')
        with self.assertRaises(ValueError):self.store.receive(state['revision'],self.event(parent))
        state=self.store.resume(state['revision'])
        self.assertEqual(state['frames'][-1]['status'],'stale')
        with self.assertRaises(ValueError):self.store.receive(state['revision'],self.event(parent))

    def test_conditions_only_apply_after_selection_and_references_do_not_create_runs(self):
        standalone=self.root/'standalone';standalone.mkdir()
        resolve_skill('writing-for-agents',explicit=SKILL_ROOT.parent/'writing-for-agents/SKILL.md')
        self.assertFalse((standalone/'.develop-system').exists())
        state=self.create('code-review');state=self.store.dispatch(state['revision'],'code-review','v1','a')
        parent=state['frames'][-1]
        self.assertEqual(parent['completion_obligations'],[])
        state=self.store.dispatch(state['revision'],'domain-modeling','v1',parent_frame_id=parent['frame_id'],required=False)
        state=self.store.receive(state['revision'],self.event(state['frames'][-1],status='skipped',skip_reason='synthetic condition not needed'))
        state=self.store.dispatch(state['revision'],'tdd','v1',parent_frame_id=parent['frame_id'],required=True)
        child=state['frames'][-1]
        with self.assertRaises(ValueError):self.store.receive(state['revision'],self.event(child,status='skipped',skip_reason='cannot skip required'))
        with self.assertRaises(ValueError):self.store.receive(state['revision'],self.event(parent))
        state=self.store.receive(state['revision'],self.event(child))
        state=self.store.receive(state['revision'],self.event(parent))
        self.assertEqual(state['frames'][0]['status'],'completed')

    def test_package_qualifies_files_names_policy_disabled_and_cycles(self):
        for case in ('disabled','file','name','policy','yaml-policy','cycle'):
            with self.subTest(case=case):
                data=self.registry_data();target=next(s for s in data['skills'] if s['id']=='writing-for-agents')
                folder=self.root/case;folder.mkdir();path=folder/'SKILL.md'
                path.write_text('---\nname: writing-for-agents\ndescription: synthetic\n---\n',encoding='utf-8')
                target['path']=str(path)
                if case=='disabled':target['enabled']=False
                if case=='file':target['path']=str(folder/'missing.md')
                if case=='name':path.write_text('---\nname: wrong\ndescription: synthetic\n---\n',encoding='utf-8')
                if case=='policy':path.write_text('---\nname: writing-for-agents\ndescription: synthetic\ndisable-model-invocation: true\n---\n',encoding='utf-8')
                if case=='yaml-policy':
                    (folder/'agents').mkdir();(folder/'agents/openai.yaml').write_text('policy:\n  allow_implicit_invocation: false\n',encoding='utf-8')
                if case=='cycle':target['required_calls']=['retro']
                self.assertTrue(validate(data,SKILL_ROOT),case)
        data=self.registry_data()
        next(s for s in data['skills'] if s['id']=='code-review')['next_candidates']=['implement']
        self.assertEqual(validate(data,SKILL_ROOT),[])  # Advice/conditional repair is not a required-call edge.

    def test_selected_version_and_explicit_missing_path_cannot_fall_back(self):
        state=self.create();state=self.store.dispatch(state['revision'],'retro','v1','a')
        parent=state['frames'][-1]
        other=self.root/'other.md';other.write_bytes((SKILL_ROOT.parent/'writing-for-agents/SKILL.md').read_bytes())
        before=self.store.path.read_bytes()
        for path in (self.root/'missing.md',other):
            with self.assertRaises((ValueError,OSError)):
                self.store.dispatch(state['revision'],'writing-for-agents','v1',parent_frame_id=parent['frame_id'],skill_path=str(path))
        self.assertEqual(self.store.path.read_bytes(),before)
        for name in ('one','two'):
            directory=self.root/name/'writing-for-agents';directory.mkdir(parents=True)
            (directory/'SKILL.md').write_bytes(other.read_bytes())
        with self.assertRaisesRegex(ValueError,'Ambiguous'):
            resolve_skill('writing-for-agents',[self.root/'one',self.root/'two'])

    def test_registry_v2_or_missing_declaration_is_not_empty_obligations(self):
        data=self.registry_data();data['schema_version']=2
        self.assertTrue(validate(data,SKILL_ROOT))
        data['schema_version']=3;del data['skills'][0]['required_calls']
        self.assertTrue(any('required_calls' in e for e in validate(data,SKILL_ROOT)))
        state=self.create();state['registry']['schema_version']=2
        self.store.path.write_text(json.dumps(state),encoding='utf-8')  # Explicit historical snapshot fixture.
        original=self.store.path.read_bytes()
        with self.assertRaises(ValueError):self.store.control(state['revision'],'paused','synthetic pause')
        self.assertEqual(self.store.path.read_bytes(),original)
