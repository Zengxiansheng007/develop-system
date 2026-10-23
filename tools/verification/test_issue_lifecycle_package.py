"""Load the actual shipped lifecycle/resources in an isolated child process."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))  # Use the source-owned production staging entry.
from release_bundle import stage_skills


class IssueLifecyclePackageTests(unittest.TestCase):
    def test_shipped_public_lifecycle_and_operational_resources_are_reachable(self):
        with tempfile.TemporaryDirectory(prefix='issue-lifecycle-package-') as temporary:
            stage = Path(temporary) / 'skills'
            stage_skills(ROOT, stage)  # Stage the same explicit manifest consumed by actual distribution.
            skill = stage / 'develop-system'
            for name in ('SKILL.md', 'references/state-operations.md', 'references/codex-runtime.md',
                    'references/issue-sync-lifecycle.md', 'references/issue-sync-project.disabled.json'):
                self.assertEqual((skill / name).read_bytes(), (ROOT / '.agents/skills/develop-system' / name).read_bytes())
            for name in ('SKILL.md', 'references/state-operations.md', 'references/codex-runtime.md'):
                self.assertIn('issue-sync-lifecycle.md', (skill / name).read_text(encoding='utf-8'))  # Verify the agent can reach the new operational branch from its real entry pointers.
            self.assertIn('issue-sync-project.disabled.json', (skill / 'references/issue-sync-lifecycle.md').read_text(encoding='utf-8'))
            protected = json.loads((ROOT / 'docs/issue-sync/protected-skills-v1.json').read_text(encoding='utf-8'))
            for item in protected['files']:
                relative = Path(item['path']).relative_to('.agents/skills')
                self.assertEqual((stage / relative).read_bytes(), (ROOT / item['path']).read_bytes())  # Staging cannot change a protected imported skill.
            child = Path(temporary) / 'run-shipped-lifecycle.py'
            child.write_text('''import json
from pathlib import Path
import sys
import unittest
scripts = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(scripts))
import workflow_state, issue_sync, issue_lifecycle
loaded = {name: str(Path(module.__file__).resolve()) for name, module in [('workflow_state', workflow_state), ('issue_sync', issue_sync), ('issue_lifecycle', issue_lifecycle)]}
if any(not Path(path).is_relative_to(scripts) for path in loaded.values()):
    raise ValueError('A generated package imported a different source candidate')
names = ['test_accepted_return_refreshes_initial_binding_and_synchronizes_automatically',
 'test_final_professional_assessment_synchronizes_its_ticket_before_unrelated_work',
 'test_whole_run_completion_uses_the_same_sync_entry_for_its_existing_binding',
 'test_completed_explicit_continue_only_synchronizes_and_keeps_authority_bytes',
 'test_result_received_while_paused_is_saved_then_applied_only_after_explicit_resume',
 'test_owned_pr_handoff_rejects_native_early_close_and_keeps_a_safe_draft',
 'test_shipped_first_project_template_is_valid_disabled_and_does_not_adopt_this_project']
suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName('test_issue_lifecycle.IssueLifecycleTests.' + name) for name in names)
result = unittest.TextTestRunner(verbosity=1).run(suite)
print(json.dumps({'loaded_shipped_sources': loaded, 'tests': result.testsRun, 'successful': result.wasSuccessful()}))
raise SystemExit(0 if result.wasSuccessful() else 1)
''', encoding='utf-8')  # Child isolation prevents source-module caches from claiming package execution.
            completed = subprocess.run([sys.executable, '-B', str(child), str(skill / 'scripts')],
                cwd=stage, capture_output=True, text=True, encoding='utf-8', timeout=120)
            print(completed.stdout, end='')  # Preserve actual child module paths in the outer command receipt.
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            observation = json.loads(completed.stdout.strip().splitlines()[-1])
            self.assertTrue(observation['successful'])
            self.assertEqual(observation['tests'], 7)
            self.assertEqual(set(observation['loaded_shipped_sources']), {'workflow_state', 'issue_sync', 'issue_lifecycle'})


if __name__ == '__main__':
    unittest.main()  # Run from the source-bound managed entry used by this ticket.
