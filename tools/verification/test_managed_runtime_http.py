"""Real HTTP public receipt consistency; specialist returns are synthetic fixtures."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.agents/skills/develop-system/scripts'))
import test_runtime_managed as managed_tests
from workflow_state import file_evidence
import test_runtime_http as http_tests


class ManagedHTTPConsistencyTests(unittest.TestCase):
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
