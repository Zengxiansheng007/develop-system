"""Managed reuse protocol; professional conclusions are synthetic fixtures."""
import json
from pathlib import Path
import unittest
import uuid
import test_runtime_managed as fixtures
from managed import execute


class ManagedReuseTests(unittest.TestCase):
    setUp = fixtures.RuntimeEvidenceTests.setUp
    git_run = fixtures.RuntimeEvidenceTests.git_run
    authorization = fixtures.RuntimeEvidenceTests.authorization
    prepare = fixtures.RuntimeEvidenceTests.prepare
    run_runtime = fixtures.RuntimeEvidenceTests.run_runtime
    finish_professionals = fixtures.RuntimeEvidenceTests.finish_professionals

    def test_same_run_admitted_original_reuse_and_final_completion(self):
        self.prepare()
        self.plan['checks'][0]['candidate_paths'] = ['src/main.js']
        self.plan['checks'][0]['requalification'] = {
            'argv': [self.plan['checks'][0]['argv'][0], '-e', "console.log('ready')"],
            'cwd': '.', 'timeout': 10, 'stdout': 'ready\n', 'scope': 'real Node process execution'}
        first = self.run_runtime()
        self.state = self.store.receive(self.state['revision'], first)
        original = Path(first['runtime_evidence']['path']).read_bytes()
        self.state = self.store.dispatch(self.state['revision'], 'runtime-verify', 'v1',
            parent_frame_id=self.parent['frame_id'], worktree=self.authorization())
        self.frame = self.state['frames'][-1]
        second = execute(self.store, self.frame['frame_id'], self.plan, self.root / uuid.uuid4().hex,
                         previous=first['runtime_evidence']['path'])
        envelope = json.loads(Path(second['runtime_evidence']['path']).read_text())
        result = json.loads(Path(envelope['result']['path']).read_text())
        self.assertEqual(result['checks'][0]['execution_status'], 'reused')
        self.assertEqual(envelope['records'], [])
        self.finish_professionals(second)
        self.assertEqual(self.store.complete(self.state['revision'],
            [{'id': '1', 'source_frame_id': self.frame['frame_id']}])['status'], 'completed')
        self.assertEqual(Path(first['runtime_evidence']['path']).read_bytes(), original)

    def test_unadmitted_original_cannot_be_promoted_to_managed_reuse(self):
        self.prepare()
        first = self.run_runtime()
        with self.assertRaises(ValueError):
            execute(self.store, self.frame['frame_id'], self.plan, self.root / uuid.uuid4().hex,
                    previous=first['runtime_evidence']['path'])


if __name__ == '__main__':
    unittest.main()
