"""Real HTTP public receipt consistency; specialist returns are synthetic fixtures."""
import copy
import json
from pathlib import Path
import sys
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.agents/skills/develop-system/scripts'))
import test_runtime_managed as managed_tests
from workflow_state import file_evidence
import test_runtime_http as http_tests
from managed import execute


class ManagedHTTPConsistencyTests(unittest.TestCase):
    def test_existing_http_reuse_rejects_incomplete_or_tampered_current_observations(self):
        fixture = managed_tests.RuntimeEvidenceTests('test_public_completion_and_changed_candidate_gate')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.prepare()
        http = http_tests.HTTPRuntimeTests()
        root = fixture.worktree / 'src'
        plan = http.plan(root)
        check = plan['checks'][0]
        check['requalification'] = {'scope': 'real readiness and immutable startup identity'}
        check['cleanup']['external_owner'] = 'fixture owns rows'
        with http.existing(root, plan):
            plan.update(root=str(fixture.worktree), input_version='v1', candidate=[{'path': 'src/service.py', 'role': 'source'}])
            check['id'] = 'business'
            check['service']['identity']['source'] = 'src/service.py'
            plan['coverage']['success'] = ['business']
            fixture.plan = plan
            first = fixture.run_runtime()
            self.assertFalse(first['blockers'])
            fixture.state = fixture.store.receive(fixture.state['revision'], first)
            original_bytes = Path(first['runtime_evidence']['path']).read_bytes()
            fixture.state = fixture.store.dispatch(fixture.state['revision'], 'runtime-verify', 'v1',
                parent_frame_id=fixture.parent['frame_id'], worktree=fixture.authorization())
            fixture.frame = fixture.state['frames'][-1]
            event = execute(fixture.store, fixture.frame['frame_id'], plan, fixture.root / uuid.uuid4().hex,
                            previous=first['runtime_evidence']['path'])
            envelope = json.loads(Path(event['runtime_evidence']['path']).read_text())
            result_path = Path(envelope['result']['path'])
            result = json.loads(result_path.read_text())
            self.assertEqual(result['checks'][0]['execution_status'], 'reused')
            observation_path = Path(result['checks'][0]['environment_observation']['record']['path'])
            observation = json.loads(observation_path.read_text())
            before = fixture.store.path.read_bytes()
            for variant in ('hash', 'version', 'missing-ready', 'missing-match', 'false-match', 'truthy-match',
                            'numeric-match', 'missing-field', 'wrong-identity', 'origin', 'status', 'legacy'):
                with self.subTest(variant=variant):
                    changed = copy.deepcopy(observation)
                    if variant == 'version':
                        changed['schema_version'] = 1
                    elif variant == 'missing-ready':
                        changed.pop('ready')
                    elif variant == 'missing-match':
                        changed['ready'].pop('json_matches')
                    elif variant in ('false-match', 'truthy-match', 'numeric-match'):
                        changed['ready']['json_matches'] = {'false-match': False, 'truthy-match': 'true', 'numeric-match': 1}[variant]
                    elif variant == 'missing-field':
                        changed['identity']['fields'].pop('pid')
                    elif variant in ('wrong-identity', 'hash'):
                        changed['identity']['fields']['instance_id'] = 'foreign'
                    elif variant == 'origin':
                        changed['origin'] = 'http://127.0.0.1:1'
                    elif variant == 'status':
                        changed['ready']['status'] = 503
                    elif variant == 'legacy':
                        changed.pop('schema_version')
                        changed['ready'] = {'status': 200, 'body': {'status': 'ready'}}
                        changed['identity'] = {'status': 200, 'body': changed['identity']['fields']}
                    observation_path.write_text(json.dumps(changed), encoding='utf-8')
                    changed_result = copy.deepcopy(result)
                    if variant != 'hash':
                        changed_result['checks'][0]['environment_observation']['record'] = file_evidence(str(observation_path))
                    result_path.write_text(json.dumps(changed_result), encoding='utf-8')
                    changed_envelope = copy.deepcopy(envelope)
                    changed_envelope['result'] = file_evidence(str(result_path))
                    changed_event = copy.deepcopy(event)
                    fixture.refresh_runtime_event(changed_event, changed_envelope)
                    with self.assertRaises(ValueError):
                        fixture.store.receive(fixture.state['revision'], changed_event)
                    self.assertEqual(before, fixture.store.path.read_bytes())
            observation_path.write_text(json.dumps(observation), encoding='utf-8')
            result['checks'][0]['environment_observation']['record'] = file_evidence(str(observation_path))
            result_path.write_text(json.dumps(result), encoding='utf-8')
            envelope['result'] = file_evidence(str(result_path))
            fixture.refresh_runtime_event(event, envelope)
            fixture.finish_professionals(event)
            self.assertEqual(fixture.store.complete(fixture.state['revision'],
                [{'id': '1', 'source_frame_id': fixture.frame['frame_id']}])['status'], 'completed')
            self.assertEqual(Path(first['runtime_evidence']['path']).read_bytes(), original_bytes)

    def test_owned_launch_and_business_operation_linkage(self):
        fixture = managed_tests.RuntimeEvidenceTests('test_public_completion_and_changed_candidate_gate')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.prepare()
        plan = http_tests.HTTPRuntimeTests().plan(fixture.worktree / 'src')
        plan.update(root=str(fixture.worktree), input_version='v1', candidate=[{'path': 'src/service.py', 'role': 'source'}])
        plan['checks'][0]['id'] = 'business'
        plan['coverage']['success'] = ['business']
        service = plan['checks'][0]['service']
        service.update(cwd='src', entry='src/service.py')
        service['identity']['source'] = 'src/service.py'
        fixture.plan = plan
        event = fixture.run_runtime()
        self.assertFalse(event['blockers'])
        envelope_path = Path(event['runtime_evidence']['path'])
        original_envelope = json.loads(envelope_path.read_text(encoding='utf-8'))
        result_path = Path(original_envelope['result']['path'])
        original_result = json.loads(result_path.read_text(encoding='utf-8'))
        record_path = Path(original_envelope['records'][0]['path'])
        original_record = json.loads(record_path.read_text(encoding='utf-8'))
        before = fixture.store.path.read_bytes()
        for variant in ('argv', 'cwd', 'method', 'path'):
            with self.subTest(variant=variant):
                record = copy.deepcopy(original_record)
                if variant == 'argv':
                    record['launch']['argv'] = [sys.executable, '-c', 'pass']
                elif variant == 'cwd':
                    record['launch']['cwd'] = str(fixture.project)
                else:
                    record['requests'][0][variant] = 'GET' if variant == 'method' else '/different'
                result = copy.deepcopy(original_result)
                result['checks'][0] = {**record, 'record': original_result['checks'][0]['record']}
                record_path.write_text(json.dumps(record), encoding='utf-8')
                result_path.write_text(json.dumps(result), encoding='utf-8')
                envelope = copy.deepcopy(original_envelope)
                envelope['result'] = file_evidence(str(result_path))
                envelope['records'][0] = dict(file_evidence(str(record_path)), name=envelope['records'][0]['name'])
                changed = copy.deepcopy(event)
                fixture.refresh_runtime_event(changed, envelope)
                with self.assertRaises(ValueError):
                    fixture.store.receive(fixture.state['revision'], changed)
                self.assertEqual(before, fixture.store.path.read_bytes())
        record_path.write_text(json.dumps(original_record), encoding='utf-8')
        result_path.write_text(json.dumps(original_result), encoding='utf-8')
        original_envelope['result'] = file_evidence(str(result_path))
        original_envelope['records'][0] = dict(file_evidence(str(record_path)), name=original_envelope['records'][0]['name'])
        fixture.refresh_runtime_event(event, original_envelope)
        fixture.finish_professionals(event)
        self.assertEqual(fixture.store.complete(fixture.state['revision'], [{'id': '1', 'source_frame_id': fixture.frame['frame_id']}])['status'], 'completed')
