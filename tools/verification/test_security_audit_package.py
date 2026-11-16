"""Public package/selection contracts; no claim of model audit execution."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / '.agents/skills/develop-system/scripts'))
from release_bundle import stage_skills
from workflow_state import RunStore
from validate_registry import validate


class AuditPackageTests(unittest.TestCase):
    def test_packaged_manual_entry_selects_explicitly_without_default_obligation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skills = root / 'skills'
            stage_skills(ROOT, skills)
            audit = skills / 'security-differential-audit'
            self.assertTrue((audit / 'SKILL.md').is_file())
            source = ROOT / '.agents/skills/security-differential-audit'
            for path in source.rglob('*'):
                if path.is_file():
                    self.assertEqual(path.read_bytes(), (audit / path.relative_to(source)).read_bytes())
            registry = skills / 'develop-system/registry.json'
            data = json.loads(registry.read_text(encoding='utf-8'))
            self.assertEqual(validate(data, registry.parent), [])
            for flow in data['default_flows'].values():
                self.assertNotIn('security-differential-audit', flow)
            for item in data['skills']:
                self.assertNotIn('security-differential-audit', item['required_calls'])
            ordinary = RunStore(root, 'normal')
            ordinary.create('normal implementation', [{'step_id': 'work', 'skill_id': 'implement'}], ['checks'], registry)
            self.assertEqual(set(ordinary.preflight('implement')), {'implement', 'code-review'})
            store = RunStore(root, 'explicit')
            state = store.create('explicit audit request', [{'step_id': 'audit', 'skill_id': 'security-differential-audit'}], ['report'], registry)
            selected = store.preflight('security-differential-audit')['security-differential-audit']
            state = store.dispatch(state['revision'], 'security-differential-audit', 'scope-v1', 'audit')
            self.assertEqual(state['frames'][-1]['skill_path'], str((audit / 'SKILL.md').resolve()))
            self.assertEqual(state['frames'][-1]['skill_sha256'], selected['sha256'])
            next(item for item in data['skills'] if item['id'] == 'implement')['required_calls'].append('security-differential-audit')
            self.assertTrue(validate(data, registry.parent))


if __name__ == '__main__':
    unittest.main()
