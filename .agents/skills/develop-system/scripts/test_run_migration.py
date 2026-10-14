"""Public cutover/restore behavior; all authorization and professional facts are synthetic."""
import hashlib
import json
import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from workflow_state import RunStore, SKILL_ROOT, file_evidence, ready_steps


class RunMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.store = RunStore(self.project, 'synthetic-adoption')
        self.legacy = SKILL_ROOT / 'scripts/fixtures/legacy-v3'
        registry = json.loads((self.legacy / 'registry.json').read_text(encoding='utf-8'))
        self.original_files = [file_evidence(str(self.legacy / item['path'])) for item in
            json.loads((self.legacy / 'provenance.json').read_text(encoding='utf-8'))['files']]
        for descriptor in registry['skills']:
            path = self.project / (descriptor['id'] + '.md')
            path.write_text('---\nname: ' + descriptor['id'] + '\ndescription: Synthetic Skill fixture\n---\n', encoding='utf-8')
            descriptor['path'] = str(path)
            self.original_files.append(file_evidence(str(path)))
        registry_path = self.save('original-registry', registry)
        self.original_files.append(file_evidence(registry_path))
        self.state = self.old('init', '--goal', 'Synthetic cutover', '--plan', self.save('plan',
            [{'step_id': 'a', 'skill_id': 'codebase-design'}, {'step_id': 'b', 'skill_id': 'domain-modeling'}]),
            '--acceptance', self.save('acceptance', ['Synthetic AC']), '--registry', registry_path)
        self.authority = self.project / 'synthetic-authorization.md'
        self.authority.write_text('Synthetic explicit single-run adoption authorization; no real run.', encoding='utf-8')
        self.basis = self.project / 'synthetic-original-versions.md'
        self.basis.write_text('Synthetic professional manifest of the actual fixture engine and resources.', encoding='utf-8')

    def save(self, name, value):
        path = self.project / (name + '.json')
        path.write_text(json.dumps(value), encoding='utf-8')
        return str(path)

    def old(self, action, *args):
        result = subprocess.run([sys.executable, '-X', 'utf8', '-B',
            str(self.legacy / 'scripts/workflow_state.py'), action, '--entry', 'develop-system',
            '--project-root', str(self.project), '--run-id', self.store.run_id, *args],
            capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def preview(self, target_registry=SKILL_ROOT / 'registry.json'):
        plain = self.store.preview_migration(target_registry)
        adoption = {'authorization': {'run_id': self.store.run_id,
            'source_sha256': plain['source']['sha256'], 'target_sha256': plain['target']['sha256'],
            'reference': file_evidence(str(self.authority))},
            'historical': {'engine': file_evidence(str(self.legacy / 'scripts/workflow_state.py')),
                'files': self.original_files, 'basis': file_evidence(str(self.basis))}}
        return self.store.preview_migration(target_registry, adoption=adoption)

    def target_registry(self):
        registry = json.loads((SKILL_ROOT / 'registry.json').read_text(encoding='utf-8'))
        for descriptor in registry['skills']:
            descriptor['path'] = str((SKILL_ROOT / descriptor['path']).resolve())
        return Path(self.save('target-registry', registry))

    def finish_legacy(self, close=True):
        artifact = self.project / 'synthetic-historical-conclusion.md'
        artifact.write_text('Synthetic historical professional conclusion.', encoding='utf-8')
        for step, skill in [('a', 'codebase-design'), ('b', 'domain-modeling')]:
            self.state = self.old('dispatch', '--revision', str(self.state['revision']),
                '--step-id', step, '--skill-id', skill, '--input-version', 'synthetic-old-v1')
            frame = self.state['frames'][-1]
            event = {**{key: frame[key] for key in ('frame_id', 'parent_frame_id', 'skill_id', 'input_version', 'mode')},
                'event_id': 'synthetic-' + step, 'run_id': self.store.run_id, 'execution': 'sequential',
                'status': 'completed', 'return_to': 'develop-system', 'completion_checked': True,
                'artifacts': [str(artifact)], 'checks': [], 'findings': [], 'blockers': [],
                'acceptance_checks': [{'id': '1', 'kind': 'technical', 'status': 'passed',
                    'completion_checked': True, 'evidence': [str(artifact)]}], 'resolved_findings': []}
            self.state = self.old('return', '--revision', str(self.state['revision']),
                '--payload', self.save('event-' + step, event))
        if close:
            self.state = self.old('complete', '--revision', str(self.state['revision']),
                '--payload', self.save('completion', [{'id': '1', 'source_frame_id': frame['frame_id']}]))

    def old_fields(self, fields):
        # Explicit synthetic historical extensions, written by the actual old public mutation Interface.
        script = "import json,sys;sys.path.insert(0,sys.argv[1]);from workflow_state import RunStore;s=RunStore(sys.argv[2],sys.argv[3]);v=json.load(open(sys.argv[4],encoding='utf-8'));print(json.dumps(s.mutate(s.read()['revision'],lambda x:{**x,**v})))"
        result = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c', script,
            str(self.legacy / 'scripts'), str(self.project), self.store.run_id, self.save('extensions', fields)],
            capture_output=True, text=True, encoding='utf-8', timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.state = json.loads(result.stdout)

    def test_missing_recorded_frame_engine_refuses_adoption_without_changing_source(self):
        self.finish_legacy()
        frames = copy.deepcopy(self.state['frames'])
        frames[-1]['engine_binding'] = {
            'path': str(self.project / 'missing-original-frame-engine.py'),
            'sha256': file_evidence(str(self.legacy / 'scripts/workflow_state.py'))['sha256']}
        self.old_fields({'frames': frames})
        before = self.store.path.read_bytes()
        plain = self.store.preview_migration()
        self.assertTrue(any(item['kind'] == 'historical_engine' and
            item['owner'] == frames[-1]['frame_id'] and item['reason'] == 'unavailable'
            for item in plain['pending']))
        self.assertEqual(self.store.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.store.apply_migration(self.preview())
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_recorded_frame_engine_requires_its_original_hash_and_manifest_entry(self):
        self.finish_legacy()
        engine = self.project / 'synthetic-recorded-frame-engine.py'
        original = (self.legacy / 'scripts/workflow_state.py').read_bytes()
        engine.write_bytes(original)
        recorded = file_evidence(str(engine))
        frames = copy.deepcopy(self.state['frames'])
        frames[-1]['engine_binding'] = recorded
        self.old_fields({'frames': frames})
        before = self.store.path.read_bytes()
        # Available bytes at another path are not a substitute for this recorded version.
        with self.assertRaises(ValueError):
            self.store.apply_migration(self.preview())
        self.assertEqual(self.store.path.read_bytes(), before)
        engine.write_bytes(original + b'\n# synthetic changed version\n')
        self.original_files.append(file_evidence(str(engine)))
        with self.assertRaises(ValueError):
            self.store.apply_migration(self.preview())
        self.assertEqual(self.store.path.read_bytes(), before)
        engine.write_bytes(original)
        self.original_files[-1] = recorded
        preview = self.preview()
        # Revalidate inside apply even after a valid reviewed preview.
        engine.unlink()
        with self.assertRaises(ValueError):
            self.store.apply_migration(preview)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_every_recorded_frame_engine_is_saved_and_required_for_restoration(self):
        self.finish_legacy()
        frames = copy.deepcopy(self.state['frames'])
        versions = []
        for index, frame in enumerate(frames):
            engine = self.project / ('synthetic-frame-engine-' + str(index) + '.py')
            raw = (self.legacy / 'scripts/workflow_state.py').read_bytes()
            engine.write_bytes(raw)
            reference = file_evidence(str(engine))
            frame['engine_binding'] = reference
            self.original_files.append(reference)
            versions.append((engine, raw, reference))
        self.old_fields({'frames': frames})
        before = self.store.path.read_bytes()
        applied = self.store.apply_migration(self.preview())
        self.assertEqual(applied['state']['frames'], frames)
        adopted = self.store.path.read_bytes()
        for engine, raw, reference in versions:
            saved = next(item['saved'] for item in applied['recovery']['versions']
                if item['original'] == reference)
            self.assertEqual(Path(saved['path']).read_bytes(), raw)
            for damage in ('changed', 'missing'):
                with self.subTest(frame_engine=engine.name, damage=damage):
                    if damage == 'changed':
                        engine.write_bytes(raw + b'\n# synthetic change\n')
                    else:
                        engine.unlink()
                    with self.assertRaises(ValueError):
                        self.store.rollback_migration(applied['state']['revision'], applied['receipt'])
                    self.assertEqual(self.store.path.read_bytes(), adopted)
                    engine.write_bytes(raw)
        self.store.rollback_migration(applied['state']['revision'], applied['receipt'])
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(self.old('show')['frames'], frames)

    def test_unsupported_control_shape_cannot_be_committed_before_validation(self):
        self.old_fields({'status': 'synthetic-unrecognized-control'})
        preview = self.preview()
        before = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.apply_migration(preview)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_completed_history_is_preserved_without_manufacturing_modern_frame_qualification(self):
        self.finish_legacy()
        original = self.store.read()
        self.assertEqual(original['status'], 'completed')
        self.assertEqual(original['acceptance'][0]['status'], 'passed')
        adopted = self.store.apply_migration(self.preview())['state']
        self.assertEqual(adopted['status'], 'completed')
        for key in ('frames', 'events', 'acceptance', 'plan', 'assessments'):
            self.assertEqual(adopted[key], original[key], key)
        self.assertNotIn('completion_obligations', adopted['frames'][0])
        self.assertEqual(ready_steps(adopted), [])
        before = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.resume(adopted['revision'], 'synthetic-continue')
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_explicit_cutover_preserves_identity_and_has_one_authoritative_run(self):
        before = self.store.path.read_bytes()
        original = self.store.read()
        preview = self.preview()
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(preview['adoption']['authorization']['run_id'], self.store.run_id)
        applied = self.store.apply_migration(preview)
        current = self.store.read()
        self.assertEqual(current['schema_version'], 4)
        self.assertEqual(current['revision'], original['revision'] + 1)
        for key in ('run_id', 'project_root', 'goal', 'plan', 'frames', 'events', 'acceptance', 'scope'):
            self.assertEqual(current[key], original[key], key)
        self.assertEqual(applied['state'], current)
        self.assertEqual(Path(applied['recovery']['original_state']['path']).read_bytes(), before)
        self.assertEqual(list((self.project / '.develop-system/runs').glob('*/state.json')), [self.store.path])
        self.assertEqual({s['step_id'] for s in ready_steps(current)}, {'a', 'b'})
        current = self.store.dispatch(current['revision'], 'codebase-design', 'new-v1', step_id='a')
        self.assertEqual(current['frames'][-1]['protocol_version'], 1)
        self.assertEqual(current['migration']['source']['sha256'], hashlib.sha256(before).hexdigest())

    def test_known_local_old_block_maps_without_releasing_the_affected_work(self):
        self.state = self.old('block', '--revision', str(self.state['revision']), '--skill-id',
            'codebase-design', '--step-id', 'a', '--reason', 'Synthetic missing local capability')
        self.assertEqual(self.state['status'], 'blocked')
        adopted = self.store.apply_migration(self.preview())['state']
        self.assertEqual(adopted['status'], 'running')
        self.assertEqual({s['step_id'] for s in ready_steps(adopted)}, {'b'})
        self.assertEqual(adopted['restrictions'][0]['scope'], 'local')
        self.assertEqual(adopted['restrictions'][0]['step_id'], 'a')
        self.assertEqual(adopted['plan'], self.state['plan'])

    def test_rollback_restores_exact_original_bytes_and_matching_original_engine(self):
        self.state = self.old('pause', '--revision', str(self.state['revision']), '--reason', 'Synthetic user pause')
        original = self.store.path.read_bytes()
        applied = self.store.apply_migration(self.preview())
        restored = self.store.rollback_migration(applied['state']['revision'], applied['receipt'])
        self.assertEqual(self.store.path.read_bytes(), original)
        self.assertEqual(restored['state'], self.state)
        self.assertEqual(restored['engine'], file_evidence(str(self.legacy / 'scripts/workflow_state.py')))
        self.assertTrue(Path(restored['restoration']['path']).is_file())
        observed = self.old('show')
        self.assertEqual(observed, self.state)

    def test_cli_adoption_and_rollback_share_the_public_version_bound_operations(self):
        authorized = self.preview()
        before = self.store.path.read_bytes()
        def command(action, *args):
            result = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(SKILL_ROOT / 'scripts/workflow_state.py'),
                action, '--entry', 'develop-system', '--project-root', str(self.project), '--run-id', self.store.run_id,
                *args], capture_output=True, text=True, encoding='utf-8', timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads(result.stdout)
        preview = command('preview-migration', '--adoption', self.save('adoption', authorized['adoption']))
        self.assertEqual(preview, authorized)
        applied = command('apply-migration', '--revision', str(self.state['revision']),
            '--payload', self.save('preview', preview))
        restored = command('rollback-migration', '--revision', str(applied['state']['revision']),
            '--payload', self.save('receipt', applied['receipt']))
        self.assertEqual(restored['state'], self.state)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_unknown_hold_authority_ticket_and_unknown_budget_are_not_erased(self):
        self.old_fields({'status': 'blocked', 'next_decision': {'action': 'synthetic-unknown-stop'},
            'authorization': {'reference': 'synthetic-existing-scope', 'parallel': False},
            'budget': {'used': 2, 'reserved': 1, 'remaining': None}, 'ticket_id': 'synthetic-ticket',
            'unresolved_items': [{'id': 'synthetic-unknown', 'status': 'unknown'}]})
        before = self.store.path.read_bytes()
        applied = self.store.apply_migration(self.preview())
        state = applied['state']
        for key in ('authorization', 'budget', 'ticket_id', 'unresolved_items', 'events', 'acceptance'):
            self.assertEqual(state[key], self.state[key], key)
        self.assertEqual(ready_steps(state), [])
        self.assertTrue(any(r['scope'] == 'unknown' for r in state['restrictions']))
        resumed = self.store.resume(state['revision'])
        self.assertEqual(ready_steps(resumed), [])
        self.assertEqual(resumed['budget']['remaining'], None)
        self.assertEqual(Path(applied['recovery']['original_state']['path']).read_bytes(), before)

    def test_user_pause_survives_cutover_and_requires_explicit_continuation(self):
        self.state = self.old('pause', '--revision', str(self.state['revision']), '--reason', 'Synthetic user stop')
        state = self.store.apply_migration(self.preview())['state']
        self.assertEqual(state['control'], {'status': 'paused', 'user_reason': 'Synthetic user stop'})
        self.assertEqual(ready_steps(state), [])
        before = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.resume(state['revision'])
        self.assertEqual(self.store.path.read_bytes(), before)
        resumed = self.store.resume(state['revision'], 'Synthetic explicit user continuation')
        self.assertEqual({s['step_id'] for s in ready_steps(resumed)}, {'a', 'b'})

    def test_cancelled_run_stays_terminal(self):
        self.state = self.old('cancel', '--revision', str(self.state['revision']), '--reason', 'Synthetic cancellation')
        state = self.store.apply_migration(self.preview())['state']
        self.assertEqual(state['status'], 'cancelled')
        self.assertEqual(state['next_decision'], self.state['next_decision'])
        self.assertEqual(ready_steps(state), [])

    def test_inflight_and_continuing_legacy_evidence_require_explicit_compatibility(self):
        self.state = self.old('dispatch', '--revision', str(self.state['revision']), '--step-id', 'a',
            '--skill-id', 'codebase-design', '--input-version', 'synthetic-inflight')
        before = self.store.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'explicit compatibility inventory'):
            self.store.apply_migration(self.preview())
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_quiescent_open_legacy_frames_are_not_given_empty_modern_obligations(self):
        self.finish_legacy(close=False)
        self.assertEqual(self.state['status'], 'running')
        self.assertTrue(all(f['status'] == 'completed' for f in self.state['frames']))
        before = self.store.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'explicit compatibility inventory'):
            self.store.apply_migration(self.preview())
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_missing_wrong_or_changed_authority_and_historical_versions_refuse(self):
        preview = self.preview()
        before = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.apply_migration(self.store.preview_migration())
        for key in ('run_id', 'source_sha256', 'target_sha256'):
            changed = copy.deepcopy(preview)
            changed['adoption']['authorization'][key] = 'synthetic-wrong'
            with self.assertRaises(ValueError):
                self.store.apply_migration(changed)
        incomplete = copy.deepcopy(preview)
        incomplete['adoption']['historical']['files'] = []
        with self.assertRaises(ValueError):
            self.store.apply_migration(incomplete)
        self.authority.write_text('Synthetic changed authority', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.store.apply_migration(preview)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_source_revision_and_same_revision_bytes_conflicts_preserve_current_authority(self):
        preview = self.preview()
        self.state = self.old('pause', '--revision', str(self.state['revision']), '--reason', 'Synthetic concurrent change')
        current = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.apply_migration(preview)
        self.assertEqual(self.store.path.read_bytes(), current)
        preview = self.preview()
        # Simulate a non-cooperating writer without manufacturing a newer revision.
        changed = self.store.read()
        changed['synthetic_external_fact'] = 'must be retained'
        self.store.path.write_text(json.dumps(changed), encoding='utf-8')
        current = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.apply_migration(preview)
        self.assertEqual(self.store.path.read_bytes(), current)

    def test_lock_conflict_and_window_dispatch_leave_authority_and_pause_intact(self):
        self.state = self.old('pause', '--revision', str(self.state['revision']), '--reason', 'Synthetic pause')
        preview = self.preview()
        before = self.store.path.read_bytes()
        with self.store.locked():
            with self.assertRaises(FileExistsError):
                self.store.apply_migration(preview)
        self.assertEqual(self.store.path.read_bytes(), before)
        replace = os.replace
        observed = []
        def during_window(source, destination):
            observed.append(self.store.read()['status'])
            with self.assertRaises(FileExistsError):
                self.store.dispatch(self.state['revision'], 'codebase-design', 'synthetic-new', step_id='a')
            return replace(source, destination)
        with patch('os.replace', side_effect=during_window):
            state = self.store.apply_migration(preview)['state']
        self.assertEqual(observed, ['paused'])
        self.assertEqual(state['control']['user_reason'], 'Synthetic pause')

    def test_precommit_write_and_replace_failures_preserve_original_bytes(self):
        preview = self.preview()
        before = self.store.path.read_bytes()
        for function in ('os.fsync', 'os.replace'):
            with self.subTest(function=function), patch(function, side_effect=OSError('Synthetic filesystem failure')):
                with self.assertRaises(OSError):
                    self.store.apply_migration(preview)
            self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(list((self.project / '.develop-system/runs').glob('*/state.json')), [self.store.path])

    def test_rollback_refuses_dispatch_result_and_other_new_facts(self):
        applied = self.store.apply_migration(self.preview())
        state = self.store.dispatch(applied['state']['revision'], 'codebase-design', 'synthetic-new', step_id='a')
        def refuses():
            before = self.store.path.read_bytes()
            with self.assertRaises(ValueError):
                self.store.rollback_migration(self.store.read()['revision'], applied['receipt'])
            self.assertEqual(self.store.path.read_bytes(), before)
        refuses()
        artifact = self.project / 'synthetic-new-result.md'
        artifact.write_text('Synthetic new specialist fact', encoding='utf-8')
        frame = state['frames'][-1]
        event = {**{key: frame[key] for key in ('frame_id', 'parent_frame_id', 'skill_id', 'input_version', 'mode', 'protocol_version')},
            'event_id': 'synthetic-new-event', 'run_id': self.store.run_id, 'execution': 'sequential',
            'status': 'completed', 'return_to': 'develop-system', 'completion_checked': True,
            'artifacts': [str(artifact)], 'checks': [], 'findings': [], 'blockers': [],
            'acceptance_checks': [], 'resolved_findings': []}
        state = self.store.receive(state['revision'], event)
        refuses()
        state = self.store.control(state['revision'], 'paused', 'Synthetic new user fact')
        refuses()

    def test_rollback_refuses_same_revision_tampering_receipt_and_old_version_conflicts(self):
        applied = self.store.apply_migration(self.preview())
        revision = applied['state']['revision']
        before = self.store.path.read_bytes()
        wrong = {**applied['receipt'], 'sha256': '0' * 64}
        with self.assertRaises(ValueError):
            self.store.rollback_migration(revision, wrong)
        self.assertEqual(self.store.path.read_bytes(), before)
        original_skill = self.project / 'codebase-design.md'
        original_skill.write_text('Synthetic changed old Skill', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.store.rollback_migration(revision, applied['receipt'])
        self.assertEqual(self.store.path.read_bytes(), before)
        changed = self.store.read()
        changed['synthetic_new_fact'] = True
        self.store.path.write_text(json.dumps(changed), encoding='utf-8')
        before = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.rollback_migration(revision, applied['receipt'])
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_rollback_lock_and_write_failure_preserve_adopted_state(self):
        applied = self.store.apply_migration(self.preview())
        before = self.store.path.read_bytes()
        with self.store.locked(), self.assertRaises(FileExistsError):
            self.store.rollback_migration(applied['state']['revision'], applied['receipt'])
        with patch('os.replace', side_effect=OSError('Synthetic rollback storage failure')):
            with self.assertRaises(OSError):
                self.store.rollback_migration(applied['state']['revision'], applied['receipt'])
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_target_version_drift_refuses_apply_and_rollback_without_state_writes(self):
        target = self.target_registry()
        preview = self.preview(target)
        before = self.store.path.read_bytes()
        raw = target.read_bytes()
        target.write_bytes(raw + b'\n')
        with self.assertRaises(ValueError):
            self.store.apply_migration(preview)
        self.assertEqual(self.store.path.read_bytes(), before)
        applied = self.store.apply_migration(self.preview(target))
        before = self.store.path.read_bytes()
        target.write_bytes(raw)
        with self.assertRaises(ValueError):
            self.store.rollback_migration(applied['state']['revision'], applied['receipt'])
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_each_retained_original_version_has_exact_saved_bytes(self):
        applied = self.store.apply_migration(self.preview())
        versions = applied['recovery']['versions']
        self.assertEqual([item['original'] for item in versions], self.original_files)
        for item in versions:
            raw = Path(item['saved']['path']).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), item['original']['sha256'])
            self.assertEqual(raw, Path(item['original']['path']).read_bytes())

    def test_existing_unknown_modern_named_extensions_are_not_overwritten_by_conversion(self):
        self.old_fields({'restrictions': [{'kind': 'synthetic-old-extension', 'unresolved': True}]})
        before = self.store.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.apply_migration(self.preview())
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_rebound_recovery_receipt_cannot_redeclare_new_facts_as_the_adoption_point(self):
        applied = self.store.apply_migration(self.preview())
        state = self.store.control(applied['state']['revision'], 'paused', 'Synthetic new user fact')
        for revision in (state['revision'], applied['state']['revision']):
            # Also model an external writer changing facts without advancing revision.
            state['revision'] = revision
            self.store.path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
            before = self.store.path.read_bytes()
            payload = copy.deepcopy(state)
            del payload['migration']['recovery']
            forged = copy.deepcopy(applied['recovery'])
            forged['adopted'] = {'revision': revision, 'payload_sha256': hashlib.sha256(
                json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')).hexdigest()}
            path = self.save('synthetic-rebound-receipt', forged)
            with self.assertRaises(ValueError):
                self.store.rollback_migration(revision, file_evidence(path))
            self.assertEqual(self.store.path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
