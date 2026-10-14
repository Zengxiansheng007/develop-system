"""Public migration-preview scenarios; historical business facts below are synthetic."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from workflow_state import RunStore, SKILL_ROOT


class MigrationPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.store = RunStore(self.project, 'legacy')
        self.store.root.mkdir(parents=True)
        self.state = {
            'schema_version': 3, 'controller_policy': 'dispatch-only', 'mode': 'execute',
            'entrypoint': 'develop-system', 'run_id': 'legacy', 'project_root': str(self.project),
            'revision': 17, 'goal': 'preserve original delivery', 'scope': ['original scope'],
            'status': 'running', 'registry': {'schema_version': 2, 'skills': []},
            'registry_path': str(self.project / 'missing-old-registry.json'),
            'registry_sha256': '0' * 64, 'temporary_skills': [], 'selected_skills': {},
            'plan': [{'step_id': 'a', 'skill_id': 'retro', 'status': 'running', 'required': True}],
            'frames': [{'frame_id': 'old-a', 'parent_frame_id': None, 'step_id': 'a',
                        'skill_id': 'retro', 'status': 'running', 'input_version': 'original-v1',
                        'skill_path': str(self.project / 'old-retro.md'), 'skill_sha256': '1' * 64,
                        'inputs': [], 'artifacts': []}],
            'acceptance': [{'id': 'original-ac', 'status': 'passed', 'evidence': []}],
            'events': [{'event_id': 'original-event', 'frame_id': 'prior', 'status': 'completed'}],
            'assessments': [], 'next_decision': {'action': 'execute_frame', 'frame_id': 'old-a'},
            'authorization': {'reference': 'synthetic-user-scope', 'allowed': ['a']},
            'budget': {'used': 2, 'remaining': None}, 'extension_owned_by_old_engine': {'keep': True}}
        self.save()

    def save(self):
        self.store.path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding='utf-8')

    def test_historical_dependencies_need_original_identity_and_do_not_gain_new_qualification(self):
        skill = self.project / 'old-retro.md'
        skill.write_text('original synthetic Skill bytes', encoding='utf-8')
        resource = self.project / 'old-resource.md'
        resource.write_text('changed synthetic resource', encoding='utf-8')
        record = self.project / 'old-check.json'
        record.write_text(json.dumps({'schema_version': 1, 'record_id': 'original-check',
            'status': 'passed', 'run_id': 'original-executor', 'frame_id': 'original-worker'}), encoding='utf-8')
        frame = self.state['frames'][0]
        frame['skill_sha256'] = hashlib.sha256(skill.read_bytes()).hexdigest()
        frame['resources'] = [{'path': str(resource), 'sha256': '2' * 64}]
        frame['checks'] = [{'record': {'path': str(record),
                            'sha256': hashlib.sha256(record.read_bytes()).hexdigest()}, 'status': 'passed'}]
        self.save()
        before = self.store.path.read_bytes()
        preview = self.store.preview_migration()
        historical = preview['historical']['frames'][0]
        self.assertEqual(historical['skill']['observation'], 'matches_recorded_bytes')
        self.assertEqual(historical['resources'][0]['observation'], 'changed')
        check = historical['checks'][0]
        self.assertEqual(check['original_record']['record_id'], 'original-check')
        self.assertEqual(check['original_record']['run_id'], 'original-executor')
        self.assertEqual(check['qualification'], 'pending')
        kinds = {p['kind'] for p in preview['pending']}
        self.assertTrue({'historical_engine', 'completion_obligations', 'worktree',
                         'legacy_check', 'historical_resource', 'budget'} <= kinds)
        self.assertNotIn('completion_obligations', preview['inventory']['snapshot']['frames'][0])
        self.assertIsNone(preview['inventory']['budget']['remaining'])
        self.assertEqual(preview['inventory']['snapshot']['acceptance'][0]['status'], 'passed')
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_mapping_requires_scope_evidence_and_preserves_pause_unknowns_and_terminal_states(self):
        self.state['status'] = 'blocked'
        self.state['plan'][0]['status'] = 'blocked'
        self.state['next_decision'] = {'action': 'missing_skill', 'skill_id': 'retro',
                                       'step_id': 'a', 'parent_frame_id': None, 'reason': 'original missing file'}
        self.save()
        preview = self.store.preview_migration()
        restriction = preview['mapping']['restrictions'][0]
        self.assertEqual((restriction['kind'], restriction['step_id']), ('missing_skill', 'a'))
        self.assertEqual(restriction['basis'], self.state['next_decision'])
        for status in ('paused', 'completed', 'cancelled'):
            with self.subTest(status=status):
                self.state['status'] = status
                self.state['next_decision'] = {'action': status, 'user_reason': 'original user request'}
                self.state['frames'][0].update(status='blocked', block_scope='local',
                                               blockers=['synthetic local environment missing'])
                self.save()
                preview = self.store.preview_migration()
                self.assertEqual(preview['mapping']['control']['status'], status)
                self.assertEqual(preview['mapping']['original_status'], status)
                self.assertEqual(preview['inventory']['snapshot'], self.state)
                if status == 'paused':
                    self.assertEqual(preview['mapping']['control']['user_reason'], 'original user request')
        self.state['status'] = 'blocked'
        self.state['next_decision'] = {'action': 'unexplained', 'reason': 'original unknown cause'}
        self.state['frames'][0].pop('block_scope')
        self.save()
        preview = self.store.preview_migration()
        self.assertTrue(any(r['scope'] == 'unknown' for r in preview['mapping']['restrictions']))
        self.assertTrue(any(p['kind'] == 'restriction_scope' for p in preview['pending']))

    def test_cli_previews_without_revision_or_adoption(self):
        before = self.store.path.read_bytes()
        command = [sys.executable, '-B', str(SKILL_ROOT / 'scripts/workflow_state.py'),
                   'preview-migration', '--entry', 'develop-system', '--project-root', str(self.project),
                   '--run-id', 'legacy']
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        preview = json.loads(result.stdout)
        self.assertEqual(preview['source']['revision'], 17)
        self.assertEqual(preview['action'], 'preview_only')
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_versions_identity_and_revision_guards_preserve_source(self):
        before = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.control(17, 'paused', 'new semantics forbidden')
        self.assertEqual(self.store.path.read_bytes(), before)
        for field, value in [('schema_version', 1), ('schema_version', 2),
                             ('run_id', 'another-run'), ('project_root', str(self.project.parent)),
                             ('revision', None)]:
            with self.subTest(field=field, value=value):
                previous = self.state[field]
                self.state[field] = value
                self.save()
                original = self.store.path.read_bytes()
                with self.assertRaises(ValueError):
                    self.store.preview_migration()
                self.assertEqual(self.store.path.read_bytes(), original)
                self.state[field] = previous
        self.save()
        self.assertFalse((self.store.root / '.state.lock').exists())

    def test_real_historical_engine_constructs_v3_and_refuses_v4(self):
        legacy = SKILL_ROOT / 'scripts/fixtures/legacy-v3'
        provenance = json.loads((legacy / 'provenance.json').read_text(encoding='utf-8'))
        self.assertEqual(provenance['commit'], 'e6b98caa8c183902a15f83f57fd174e0783660a5')
        for file in provenance['files']:
            self.assertEqual(hashlib.sha256((legacy / file['path']).read_bytes()).hexdigest(), file['sha256'])
        registry = json.loads((legacy / 'registry.json').read_text(encoding='utf-8'))
        for descriptor in registry['skills']:
            skill = self.project / (descriptor['id'] + '.md')
            skill.write_text('---\nname: ' + descriptor['id'] + '\ndescription: synthetic historical capability\n---\n',
                             encoding='utf-8')
            descriptor['path'] = str(skill)
        registry_path = self.project / 'fixture-old-registry.json'
        registry_path.write_text(json.dumps(registry), encoding='utf-8')
        plan = self.project / 'plan.json'
        plan.write_text(json.dumps([{'step_id': 'old-step', 'skill_id': 'codebase-design'}]), encoding='utf-8')
        acceptance = self.project / 'acceptance.json'
        acceptance.write_text(json.dumps(['synthetic original AC']), encoding='utf-8')
        command = [sys.executable, '-B', str(legacy / 'scripts/workflow_state.py')]
        common = ['--entry', 'develop-system', '--project-root', str(self.project), '--run-id', 'old-created']
        result = subprocess.run([*command, 'init', *common, '--goal', 'synthetic legacy goal',
            '--plan', str(plan), '--acceptance', str(acceptance), '--registry', str(registry_path)],
            capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = subprocess.run([*command, 'dispatch', *common, '--revision', '1',
            '--skill-id', 'codebase-design', '--step-id', 'old-step', '--input-version', 'original-v1'],
            capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        old_store = RunStore(self.project, 'old-created')
        before = old_store.path.read_bytes()
        preview = old_store.preview_migration()
        self.assertEqual(preview['source']['versions'], {'state': 3, 'registry': 2})
        self.assertEqual(preview['historical']['frames'][0]['skill']['observation'], 'matches_recorded_bytes')
        self.assertEqual(old_store.path.read_bytes(), before)
        new_store = RunStore(self.project, 'new-v4')
        state = new_store.create('current contract', [{'step_id': 'design', 'skill_id': 'codebase-design'}], ['AC'])
        self.assertEqual((state['schema_version'], state['registry']['schema_version']), (4, 3))
        new_before = new_store.path.read_bytes()
        result = subprocess.run([*command, 'pause', '--entry', 'develop-system',
            '--project-root', str(self.project), '--run-id', 'new-v4', '--revision', '1', '--reason', 'must refuse'],
            capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('requires explicit migration', result.stdout + result.stderr)
        self.assertEqual(new_store.path.read_bytes(), new_before)

    def test_unbound_skill_and_target_resource_versions_are_not_assumed(self):
        path = Path(self.state['frames'][0]['skill_path'])
        path.write_text('current visible bytes are not historical proof', encoding='utf-8')
        self.state['frames'][0].pop('skill_sha256')
        self.save()
        first = self.store.preview_migration()
        self.assertEqual(first['historical']['frames'][0]['skill']['observation'], 'unbound')
        registry = json.loads((SKILL_ROOT / 'registry.json').read_text(encoding='utf-8'))
        for descriptor in registry['skills']:
            descriptor['path'] = str((SKILL_ROOT / descriptor['path']).resolve())
        descriptor = next(d for d in registry['skills'] if d['id'] == 'tdd')
        descriptor['path'] = str(self.project / 'missing-selected-tdd.md')
        target = self.project / 'target-registry.json'
        target.write_text(json.dumps(registry), encoding='utf-8')
        second = self.store.preview_migration(target)
        self.assertNotEqual(first['target']['sha256'], second['target']['sha256'])
        self.assertTrue(any(p['kind'] == 'target_skill' and p['skill_id'] == 'tdd' for p in second['pending']))
        self.assertEqual(second['inventory']['snapshot'], self.state)

    def test_all_evidence_locations_and_per_frame_engine_identity_remain_visible(self):
        evidence = self.project / 'original-evidence.txt'
        evidence.write_text('synthetic historical evidence', encoding='utf-8')
        reference = {'path': str(evidence), 'sha256': hashlib.sha256(evidence.read_bytes()).hexdigest()}
        self.state['frames'][0]['inputs'] = [reference]
        self.state['frames'][0]['engine_binding'] = reference
        self.state['acceptance'][0]['evidence'] = [reference]
        self.state['events'][0]['artifacts'] = [reference]
        self.state['assessments'] = [{'frame_id': 'prior', 'evidence': [reference]}]
        self.save()
        preview = self.store.preview_migration()
        locations = {item['location'] for item in preview['inventory']['evidence']}
        self.assertEqual(locations, {'frames/0/inputs/0', 'acceptance/0/evidence/0',
                                     'events/0/artifacts/0', 'assessments/0/evidence/0'})
        self.assertEqual(preview['historical']['frames'][0]['engine']['recorded'], reference)
        self.assertEqual(preview['historical']['frames'][0]['engine']['observation'], 'matches_recorded_bytes')
        self.assertTrue(any(p['kind'] == 'migration_environment' for p in preview['pending']))
        evidence.unlink()
        preview = self.store.preview_migration()
        self.assertTrue(all(i['observation'] == 'unavailable' for i in preview['inventory']['evidence']))
        self.assertEqual(preview['inventory']['snapshot']['acceptance'][0]['status'], 'passed')

    def test_preview_binds_actual_source_and_target_without_writing(self):
        before = self.store.path.read_bytes()
        names = sorted(p.name for p in self.store.root.iterdir())
        preview = self.store.preview_migration()
        self.assertEqual(preview['source']['run_id'], 'legacy')
        self.assertEqual(preview['source']['revision'], 17)
        self.assertEqual(preview['source']['sha256'], hashlib.sha256(before).hexdigest())
        self.assertEqual(preview['source']['versions'], {'state': 3, 'registry': 2})
        self.assertEqual(preview['inventory']['snapshot'], self.state)
        self.assertEqual(preview['inventory']['in_flight'][0]['frame_id'], 'old-a')
        self.assertEqual(preview['target']['versions'],
                         {'state': 4, 'registry': 3, 'result': 1, 'check': 2})
        files = {f['path']: f['sha256'] for f in preview['target']['files']}
        engine = (SKILL_ROOT / 'scripts/workflow_state.py').resolve()
        self.assertEqual(files[str(engine)], hashlib.sha256(engine.read_bytes()).hexdigest())
        resource = (SKILL_ROOT.parent / 'tdd/tests.md').resolve()
        self.assertIn(str(resource), files)
        self.assertEqual(preview['action'], 'preview_only')
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.store.root.iterdir()), names)


class LegacyRestrictionPreviewTests(unittest.TestCase):
    """Real archived v3 public commands; all professional conclusions are synthetic."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.store = RunStore(self.project, 'legacy-restrictions')
        legacy = SKILL_ROOT / 'scripts/fixtures/legacy-v3'
        self.command = [sys.executable, '-X', 'utf8', '-B', str(legacy / 'scripts/workflow_state.py')]
        registry = json.loads((legacy / 'registry.json').read_text(encoding='utf-8'))
        for descriptor in registry['skills']:
            skill = self.project / (descriptor['id'] + '.md')
            skill.write_text('---\nname: ' + descriptor['id'] +
                             '\ndescription: Synthetic legacy restriction fixture\n---\n', encoding='utf-8')
            descriptor['path'] = str(skill)
        self.artifact = self.project / 'synthetic-conclusion.md'
        self.artifact.write_text('Synthetic professional result for migration preview only.', encoding='utf-8')
        self.state = self.old('init', '--goal', 'Synthetic legacy restrictions',
            '--plan', self.save('plan', [{'step_id': 'design', 'skill_id': 'codebase-design'}]),
            '--acceptance', self.save('acceptance', ['synthetic AC']),
            '--registry', self.save('registry', registry))
        self.state = self.old('dispatch', '--revision', str(self.state['revision']),
            '--skill-id', 'codebase-design', '--step-id', 'design', '--input-version', 'synthetic-v1')

    def save(self, name, value):
        path = self.project / (name + '.json')
        path.write_text(json.dumps(value), encoding='utf-8')
        return str(path)

    def old(self, action, *args):
        result = subprocess.run([*self.command, action, '--entry', 'develop-system',
            '--project-root', str(self.project), '--run-id', self.store.run_id, *args],
            capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def result(self, status='completed', findings=(), blockers=(), resolved=(), **extra):
        frame = self.state['frames'][-1]
        event = {'event_id': 'synthetic-' + str(self.state['revision']),
            'run_id': self.store.run_id, 'frame_id': frame['frame_id'],
            'parent_frame_id': frame['parent_frame_id'], 'skill_id': frame['skill_id'],
            'input_version': frame['input_version'], 'mode': frame['mode'], 'execution': 'sequential',
            'status': status, 'return_to': 'develop-system', 'completion_checked': True,
            'artifacts': [str(self.artifact)], 'checks': [], 'findings': list(findings),
            'blockers': list(blockers), 'acceptance_checks': [], 'resolved_findings': list(resolved), **extra}
        self.state = self.old('return', '--revision', str(self.state['revision']),
                              '--payload', self.save('event', event))
        return self.state['events'][-1]

    def test_genuine_completed_findings_are_pending_restrictions_with_original_event_basis(self):
        event = self.result(findings=['Synthetic unresolved requirement'])
        frame = self.state['frames'][0]
        self.assertTrue(frame['needs_assessment'])
        self.assertNotIn('findings', frame)
        before = self.store.path.read_bytes()
        preview = self.store.preview_migration()
        restrictions = preview['mapping']['restrictions']
        self.assertEqual(len(restrictions), 1)
        self.assertEqual(restrictions[0]['frame_id'], frame['frame_id'])
        self.assertEqual(restrictions[0]['event_basis'], event)
        self.assertEqual(restrictions[0]['scope'], 'unknown')
        self.assertTrue(any(p['kind'] == 'restriction_scope' and p['owner'] == frame['frame_id']
                            for p in preview['pending']))
        self.assertEqual(preview['inventory']['snapshot'], self.state)
        self.assertEqual(self.store.path.read_bytes(), before)

    def resolution(self, target):
        self.state = self.old('extend-plan', '--revision', str(self.state['revision']),
            '--payload', self.save('repair-plan', [{'step_id': 'resolve', 'skill_id': 'codebase-design',
                                                   'resolves_frames': [target]}]),
            '--reason', 'Synthetic scoped resolution')
        self.state = self.old('dispatch', '--revision', str(self.state['revision']), '--step-id', 'resolve',
            '--skill-id', 'codebase-design', '--input-version', 'synthetic-resolution-v1')
        event = self.result(resolved=[target])
        return event['frame_id']

    def assess(self, target, source):
        self.state = self.old('assess', '--revision', str(self.state['revision']), '--frame-id', target,
            '--source-frame-id', source, '--resolution', 'Synthetic finding assessed',
            '--evidence', str(self.artifact))

    def test_completed_blocker_uses_original_event_and_explicit_scope(self):
        event = self.result(blockers=['Synthetic current blocker'], block_scope='local')
        self.assertNotIn('blockers', self.state['frames'][0])
        self.assertNotIn('block_scope', self.state['frames'][0])
        preview = self.store.preview_migration()
        restriction = preview['mapping']['restrictions'][0]
        self.assertEqual(restriction['event_basis'], event)
        self.assertEqual((restriction['scope'], restriction['step_id']), ('local', 'design'))
        self.assertEqual(preview['inventory']['snapshot']['events'], [event])

    def test_completed_blocker_requires_assess_and_does_not_reappear_after_resolution(self):
        original = self.result(blockers=['Synthetic unresolved blocker'])
        target = original['frame_id']
        source = self.resolution(target)
        self.assertTrue(any(r.get('frame_id') == target for r in self.store.preview_migration()['mapping']['restrictions']))
        self.assess(target, source)
        before = self.store.path.read_bytes()
        preview = self.store.preview_migration()
        self.assertFalse(self.state['frames'][0]['needs_assessment'])
        self.assertEqual(preview['mapping']['restrictions'], [])
        self.assertIn(original, preview['inventory']['snapshot']['events'])
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_unresolved_stale_work_is_retained_and_superseded_history_is_not_reactivated(self):
        original = self.result(status='blocked', findings=['Synthetic finding'], blockers=['Synthetic blocker'])
        target = original['frame_id']
        self.state = self.old('resume', '--revision', str(self.state['revision']))
        self.assertEqual(self.state['frames'][0]['status'], 'stale')
        preview = self.store.preview_migration()
        self.assertEqual(preview['mapping']['restrictions'][0]['event_basis'], original)
        self.assess(target, self.resolution(target))
        self.state = self.old('dispatch', '--revision', str(self.state['revision']), '--step-id', 'design',
            '--skill-id', 'codebase-design', '--input-version', 'synthetic-replacement-v1')
        self.result()
        before = self.store.path.read_bytes()
        preview = self.store.preview_migration()
        self.assertEqual(self.state['frames'][0]['status'], 'superseded')
        self.assertEqual(preview['mapping']['restrictions'], [])
        self.assertIn(original, preview['inventory']['snapshot']['events'])
        self.assertEqual(self.store.path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
