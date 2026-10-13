"""Actual Git/command checks; professional results and authorization are synthetic fixtures."""
import copy
import json
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from workflow_state import RunStore, SKILL_ROOT
from record_check import run_record, validate_record


class WorktreeCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.project=self.root/'main'
        self.project.mkdir()
        self.git=shutil.which('git')
        if not self.git:self.skipTest('Git required for real worktree checks')
        self.git_run('init',str(self.project))
        self.git_run('-C',str(self.project),'config','user.email','fixture@example.invalid')
        self.git_run('-C',str(self.project),'config','user.name','Synthetic fixture')
        for folder in ('src','tests','config'):(self.project/folder).mkdir()
        (self.project/'src/value.py').write_text('VALUE=1\n',encoding='utf-8')
        (self.project/'config/check.json').write_text('{}\n',encoding='utf-8')
        (self.project/'tests/check.py').write_text(
            "import sys\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\nimport value\nassert value.VALUE == 2\nprint(value.__file__)\n",encoding='utf-8')
        self.git_run('-C',str(self.project),'add','.')
        self.git_run('-C',str(self.project),'commit','-m','fixture')
        self.worktree=self.root/'outside'
        self.git_run('-C',str(self.project),'worktree','add','-b','candidate',str(self.worktree))
        (self.worktree/'src/value.py').write_text('VALUE=2\n',encoding='utf-8')
        (self.worktree/'src/new.py').write_text('NEW=True\n',encoding='utf-8')
        self.registry=self.root/'registry.json'
        data=json.loads((SKILL_ROOT/'registry.json').read_text(encoding='utf-8'))
        for item in data['skills']:
            p=self.root/(item['id']+'.md')
            p.write_text('---\nname: '+item['id']+'\ndescription: fixture\n---\n',encoding='utf-8')
            item['path']=str(p)
        self.registry.write_text(json.dumps(data),encoding='utf-8')
        self.store=RunStore(self.project,'managed-test')
        self.state=self.store.create('synthetic check goal',[{'step_id':'a','skill_id':'implement'}],['verified'],self.registry)

    def git_run(self,*args):
        return subprocess.check_output([self.git,*args],stderr=subprocess.STDOUT,text=True,encoding='utf-8').strip()

    def authorization(self):
        return {'authorization_reference':'synthetic current task authorization','worktree':str(self.worktree),
                'execution_roots':[str(self.project),str(self.worktree)],'source_ranges':['src','tests','config']}

    def dispatch(self):
        self.state=self.store.dispatch(self.state['revision'],'implement','candidate-v1','a',
                                       worktree=self.authorization())
        return self.state['frames'][-1]

    def candidate(self):
        return {'ranges':[{'path':'src','role':'source'},{'path':'tests','role':'test'},
                          {'path':'config','role':'config'}],
                'entry':'tests/check.py','loaded':['src/value.py','tests/check.py']}

    def check(self,frame,cwd=None,**kwargs):
        output=self.root/(uuid.uuid4().hex+'.json')
        record=run_record([sys.executable,'-B',str(self.worktree/'tests/check.py'),str(self.worktree/'src')],
            cwd or self.worktree,output,binding=frame['worktree_binding'],candidate=self.candidate(),**kwargs)
        return output,record

    def event(self,frame,record):
        return {'protocol_version':1,'event_id':uuid.uuid4().hex,'run_id':'managed-test','frame_id':frame['frame_id'],
            'parent_frame_id':None,'skill_id':'implement','input_version':'candidate-v1','mode':'skill',
            'execution':'sequential','status':'completed','return_to':'develop-system','completion_checked':True,
            'artifacts':[],'checks':[{'record':str(record),'status':'passed'}],'findings':[],'blockers':[],
            'acceptance_checks':[],'resolved_findings':[]}

    def directory_link(self,link,target):
        # Real synthetic directory junctions need no Windows privilege changes.
        if os.name=='nt':
            quote=lambda p: "'"+str(p).replace("'","''")+"'"
            subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',
                'New-Item -ItemType Junction -Path '+quote(link)+' -Target '+quote(target)+' -ErrorAction Stop | Out-Null'],
                check=True,capture_output=True,text=True,encoding='utf-8')
        else:
            link.symlink_to(target,target_is_directory=True)
        self.addCleanup(lambda: link.rmdir() if os.name=='nt' else link.unlink())

    def test_linked_descendant_cannot_collect_unauthorized_source(self):
        frame=self.dispatch()
        target=self.worktree/'private';target.mkdir()
        (target/'note.py').write_text('SYNTHETIC=True\n',encoding='utf-8')
        self.directory_link(self.worktree/'src/linked',target)
        before=self.store.path.read_bytes()
        path,record=self.check(frame)
        self.assertEqual(record['status'],'failed')
        self.assertIn('source scope',record['error'])
        self.assertIsNone(record['execution_command'])
        self.assertNotIn('candidate',record)
        with self.assertRaises(ValueError):self.store.receive(self.state['revision'],self.event(frame,path))
        self.assertEqual(self.store.path.read_bytes(),before)

    def test_observer_excludes_sensitive_targets_before_collecting_hashes(self):
        target=self.worktree/'src/credentials';target.mkdir()
        note=target/'note.py';note.write_text('SYNTHETIC=True\n',encoding='utf-8')
        script=self.worktree/'tests/check.py'
        script.write_text(script.read_text(encoding='utf-8')+
            "import runpy\nrunpy.run_path(str(Path(__file__).parents[1]/'src/credentials/note.py'))\n",encoding='utf-8')
        selection=self.candidate();selection['ranges'][0]['path']='src/value.py'
        frame=self.dispatch()
        record=run_record([sys.executable,'-B',str(script),str(self.worktree/'src')],self.worktree,
            self.root/'observed-sensitive.json',binding=frame['worktree_binding'],candidate=selection)
        self.assertEqual(record['status'],'failed')
        self.assertNotIn(str(note),[p['path'] for p in record['observation']['loaded']])
        self.assertIn('Sensitive',record['stderr'])

    def test_resolved_link_targets_keep_sensitive_and_metadata_exclusions(self):
        frame=self.dispatch()
        for index,name in enumerate(('ordinary','.git','credentials','.env.fixture')):
            with self.subTest(target=name):
                target=self.worktree/'src'/name;target.mkdir()
                (target/'note.py').write_text('SYNTHETIC=True\n',encoding='utf-8')
                link=self.worktree/'src'/('linked-'+str(index))
                self.directory_link(link,target)
                selection=self.candidate();selection['ranges'][0]['path']='src/value.py'
                selection['ranges'].append({'path':link.relative_to(self.worktree).as_posix(),'role':'source'})
                record=run_record([sys.executable,'-B',str(self.worktree/'tests/check.py'),str(self.worktree/'src')],
                    self.worktree,self.root/('linked-'+str(index)+'.json'),binding=frame['worktree_binding'],candidate=selection)
                if name=='ordinary':
                    self.assertEqual(record['status'],'passed',record['error'])
                else:
                    self.assertEqual(record['status'],'failed')
                    self.assertIn('Sensitive',record['error'])
                    self.assertIsNone(record['execution_command'])
                    self.assertNotIn('candidate',record)

    def test_replacing_authorized_directory_cannot_move_its_scope(self):
        frame=self.dispatch()
        target=self.worktree/'private'
        (self.worktree/'src').rename(target)
        self.directory_link(self.worktree/'src',target)
        _,record=self.check(frame)
        self.assertEqual(record['status'],'failed')
        self.assertIn('source scope',record['error'])
        self.assertIsNone(record['execution_command'])

    def test_authorized_external_worktree_executes_dirty_candidate_and_returns(self):
        frame=self.dispatch()
        path,record=self.check(frame)
        self.assertEqual(validate_record(record),'passed')
        self.assertEqual(record['schema_version'],2)
        self.assertEqual(record['context'],'managed')
        self.assertEqual(record['cwd'],str(self.worktree))
        self.assertIn('src/new.py',[f['path'] for f in record['candidate']['files']])
        self.assertEqual(record['binding']['frame_id'],frame['frame_id'])
        state=self.store.receive(self.state['revision'],self.event(frame,path))
        self.assertEqual(state['frames'][0]['status'],'completed')

    def test_main_cwd_distinguishes_actual_import_and_preserves_wrong_import_execution(self):
        frame=self.dispatch()
        path,record=self.check(frame,self.project)
        self.assertEqual(validate_record(record),'passed')
        self.assertEqual(record['cwd'],str(self.project))
        self.assertIn(str(self.worktree/'src/value.py'),[p['path'] for p in record['observation']['loaded']])
        # A successful command loading the canonical copy is still wrong candidate evidence.
        (self.project/'src/value.py').write_text('VALUE=2\n',encoding='utf-8')
        wrong=self.root/'wrong-import.json'
        result=run_record([sys.executable,'-B',str(self.worktree/'tests/check.py'),str(self.project/'src')],
            self.project,wrong,binding=frame['worktree_binding'],candidate=self.candidate())
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['exit_code'],0)
        self.assertIn(str(self.project/'src/value.py'),result['stdout'])
        before=self.store.path.read_bytes()
        with self.assertRaises(ValueError):self.store.receive(self.store.read()['revision'],self.event(frame,wrong))
        self.assertEqual(self.store.path.read_bytes(),before)
        self.store.receive(self.state['revision'],self.event(frame,path))

    def test_recorder_cli_and_standalone_consumers_have_explicit_distinct_contexts(self):
        frame=self.dispatch()
        binding=self.root/'binding.json';binding.write_text(json.dumps(frame['worktree_binding']),encoding='utf-8')
        selection=self.root/'selection.json';selection.write_text(json.dumps(self.candidate()),encoding='utf-8')
        output=self.root/'cli.json'
        result=subprocess.run([sys.executable,'-B',str(SKILL_ROOT/'scripts/record_check.py'),
            '--cwd',str(self.project),'--output',str(output),'--binding',str(binding),'--candidate',str(selection),
            '--',sys.executable,'-B',str(self.worktree/'tests/check.py'),str(self.worktree/'src')],
            capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(output.read_text(encoding='utf-8'))['context'],'managed')
        standalone=self.root/'standalone.json'
        record=run_record([sys.executable,'-c',"print('independent')"],self.project,standalone)
        self.assertEqual(record['context'],'independent')
        self.assertEqual(validate_record(record),'passed')
        before=self.store.path.read_bytes()
        with self.assertRaises(ValueError):self.store.receive(self.state['revision'],self.event(frame,standalone))
        self.assertEqual(before,self.store.path.read_bytes())
        legacy={k:v for k,v in record.items() if k not in {'context','execution_command'}}
        legacy['schema_version']=1
        old=self.root/'v1.json';old.write_text(json.dumps(legacy),encoding='utf-8')
        original=old.read_bytes()
        self.assertEqual(validate_record(legacy),'passed')
        with self.assertRaises(ValueError):self.store.receive(self.state['revision'],self.event(frame,old))
        self.assertEqual(old.read_bytes(),original)
        self.assertEqual(before,self.store.path.read_bytes())
        self.store.receive(self.state['revision'],self.event(frame,output))

    def test_unsupported_managed_runtime_fails_closed_but_independent_command_runs(self):
        frame=self.dispatch()
        managed=run_record([self.git,'--version'],self.worktree,self.root/'unsupported.json',
            binding=frame['worktree_binding'],candidate=self.candidate())
        self.assertEqual(managed['status'],'failed')
        self.assertIsNone(managed['exit_code'])
        self.assertIn('Python',managed['error'])
        independent=run_record([self.git,'--version'],self.root,self.root/'git-version.json')
        self.assertEqual(independent['status'],'passed')
        self.assertEqual(independent['context'],'independent')
        self.assertFalse((self.root/'.develop-system').exists())

    def test_drift_and_omitted_loaded_source_fail_while_unrelated_change_paused_return_is_valid(self):
        frame=self.dispatch()
        selection=self.candidate()
        script=self.worktree/'tests/check.py'
        original=script.read_text(encoding='utf-8')
        script.write_text(original+"Path(__file__).parents[1].joinpath('src/new.py').write_text('NEW=False')\n",encoding='utf-8')
        path,drift=self.check(frame)
        self.assertEqual(drift['status'],'failed')
        self.assertEqual(drift['exit_code'],0)
        before=self.store.path.read_bytes()
        with self.assertRaises(ValueError):self.store.receive(self.state['revision'],self.event(frame,path))
        self.assertEqual(before,self.store.path.read_bytes())
        script.write_text(original+"import new\n",encoding='utf-8')
        selection['ranges'][0]['path']='src/value.py'
        omitted=self.root/'omitted.json'
        record=run_record([sys.executable,'-B',str(script),str(self.worktree/'src')],self.worktree,omitted,
            binding=frame['worktree_binding'],candidate=selection)
        self.assertEqual(record['status'],'failed')
        self.assertIn('omitted',record['error'])
        path,record=self.check(frame)
        self.assertEqual(record['status'],'passed')
        (self.worktree/'unrelated.md').write_text('unrelated',encoding='utf-8')
        self.state=self.store.control(self.state['revision'],'paused','synthetic user pause')
        event=self.event(frame,path)
        state=self.store.receive(self.state['revision'],event)
        self.assertEqual(state['control']['status'],'paused')
        self.assertEqual(self.store.receive(state['revision'],event)['revision'],state['revision'])

    def test_actual_failed_managed_command_cannot_support_passed_acceptance(self):
        script=self.worktree/'tests/check.py'
        script.write_text(script.read_text(encoding='utf-8')+"raise SystemExit(7)\n",encoding='utf-8')
        frame=self.dispatch()
        path,record=self.check(frame)
        self.assertEqual(record['status'],'failed')
        self.assertEqual(record['exit_code'],7)
        event=self.event(frame,path);event['checks'][0]['status']='failed'
        event['acceptance_checks']=[{'id':'1','status':'passed','kind':'technical','completion_checked':True,
                                    'evidence':[str(path)]}]
        before=self.store.path.read_bytes()
        with self.assertRaises(ValueError):self.store.receive(self.state['revision'],event)
        self.assertEqual(before,self.store.path.read_bytes())
        event['acceptance_checks']=[]
        self.assertEqual(self.store.receive(self.state['revision'],event)['frames'][0]['status'],'completed')

    def test_observation_does_not_attest_stale_bytecode_as_current_source(self):
        source=self.worktree/'src/value.py'
        original=source.stat()
        py_compile.compile(str(source),doraise=True,invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
        source.write_text('VALUE=3\n',encoding='utf-8')
        os.utime(source,ns=(original.st_atime_ns,original.st_mtime_ns))
        frame=self.dispatch()
        _,record=self.check(frame)
        # Actual current source is 3, so the fixture's assertion of 2 must fail.
        # A passed old bytecode body cannot be labeled with the current source hash.
        self.assertEqual(record['status'],'failed')
        self.assertNotEqual(record['exit_code'],0)

    def test_declared_python_unbuffered_option_reaches_the_real_interpreter(self):
        script=self.worktree/'tests/check.py'
        script.write_text(script.read_text(encoding='utf-8')+"assert sys.stdout.write_through\n",encoding='utf-8')
        frame=self.dispatch()
        record=run_record([sys.executable,'-u','-X','utf8','-B',str(script),str(self.worktree/'src')],
            self.worktree,self.root/'unbuffered.json',binding=frame['worktree_binding'],candidate=self.candidate())
        self.assertEqual(record['status'],'passed',record['stderr'])

    def test_nested_unassigned_worktree_cwd_is_not_authorized_by_parent_directory(self):
        frame=self.dispatch()
        nested=self.project/'unassigned'
        self.git_run('-C',str(self.project),'worktree','add','-b','unassigned',str(nested))
        _,record=self.check(frame,nested)
        self.assertEqual(record['status'],'failed')
        self.assertIn('scope',record['error'])

    def test_wrong_run_frame_version_candidate_and_unassigned_sources_reject_without_mutation(self):
        frame=self.dispatch()
        original,record=self.check(frame)
        before=self.store.path.read_bytes()
        variants=[]
        for field in ('run_id','frame_id','input_version','binding_id'):
            forged=copy.deepcopy(record);forged['binding'][field]='wrong'
            variants.append(forged)
        forged=copy.deepcopy(record);forged['candidate']['candidate_id']='wrong';variants.append(forged)
        forged=copy.deepcopy(record);forged['candidate']['files']=[f for f in forged['candidate']['files'] if f['path']!='src/new.py'];variants.append(forged)
        for forged in variants:
            path=self.root/(uuid.uuid4().hex+'.json')
            path.write_text(json.dumps(forged),encoding='utf-8')
            with self.assertRaises(ValueError):self.store.receive(self.state['revision'],self.event(frame,path))
            self.assertEqual(before,self.store.path.read_bytes())
        # Same repository but no frame assignment cannot substitute its binding.
        other=self.root/'unassigned'
        self.git_run('-C',str(self.project),'worktree','add','-b','other',str(other))
        forged=copy.deepcopy(record)
        forged['binding']['worktree']['root']=str(other)
        path=self.root/'other-worktree.json';path.write_text(json.dumps(forged),encoding='utf-8')
        with self.assertRaises(ValueError):self.store.receive(self.state['revision'],self.event(frame,path))
        self.assertEqual(before,self.store.path.read_bytes())
        foreign=self.root/'foreign';foreign.mkdir();self.git_run('init',str(foreign))
        declaration={**self.authorization(),'worktree':str(foreign)}
        with self.assertRaises(ValueError):
            self.store.dispatch(self.state['revision'],'tdd','v',parent_frame_id=frame['frame_id'],worktree=declaration)
        self.assertEqual(before,self.store.path.read_bytes())

    def test_python_module_test_entry_is_observed_from_actual_discovery(self):
        (self.worktree/'tests/test_value.py').write_text(
            "import unittest,sys\nfrom pathlib import Path\nsys.path.insert(0,str(Path(__file__).parents[1]/'src'))\nimport value\nclass ValueTest(unittest.TestCase):\n def test_value(self): self.assertEqual(value.VALUE,2)\n",encoding='utf-8')
        frame=self.dispatch()
        selection=self.candidate()
        selection.update(entry='tests/test_value.py',loaded=['src/value.py','tests/test_value.py'])
        path=self.root/'module.json'
        record=run_record([sys.executable,'-B','-m','unittest','discover','-s','tests','-v'],self.worktree,path,
            binding=frame['worktree_binding'],candidate=selection)
        self.assertEqual(record['status'],'passed',record['error'])
        self.assertIn('Ran 1 test',record['stderr'])
        self.store.receive(self.state['revision'],self.event(frame,path))
