"""Exercise extension acceptance and rejection of unsafe routing configs."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from validate_registry import validate


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.data = json.loads((self.root / "registry.json").read_text(encoding="utf-8"))

    def skill(self, name):
        return next(item for item in self.data["skills"] if item["id"] == name)

    def test_current_registry(self):
        self.assertEqual(validate(self.data, self.root), [])

    def test_new_skill_and_new_stage(self):
        self.data["stages"].append("release-validation")
        item = copy.deepcopy(self.skill("research"))
        item.update(id="release-check", stage="release-validation", requires=["code-review"], next_candidates=["pr"])
        self.data["skills"].append(item)
        self.data["default_flows"]["release"] = ["code-review", "release-check", "pr"]
        self.assertEqual(validate(self.data, self.root), [])

    def test_wrong_return_target(self):
        self.skill("tdd")["return_to"] = "implement"
        self.assertTrue(any("invalid return target" in error for error in validate(self.data, self.root)))

    def test_cyclic_dependencies(self):
        self.skill("implement")["requires"] = ["code-review"]
        self.skill("code-review")["requires"] = ["implement"]
        self.assertTrue(any("Dependency cycle" in error for error in validate(self.data, self.root)))

    def test_missing_route(self):
        self.skill("research")["next_candidates"] = ["not-installed-or-registered"]
        self.assertTrue(any("unknown next_candidates" in error for error in validate(self.data, self.root)))

    def test_disabled_route(self):
        self.skill("pr")["enabled"] = False
        self.assertTrue(any("disabled target" in error for error in validate(self.data, self.root)))

    def test_duplicate_id(self):
        self.data["skills"].append(copy.deepcopy(self.skill("implement")))
        self.assertTrue(any("Duplicate skill" in error for error in validate(self.data, self.root)))

    def test_inline_fallback_and_executable_body_are_rejected(self):
        self.skill("research")["fallback"] = "inline"
        self.skill("research")["inline"] = "controller does research"
        errors = validate(self.data, self.root)
        self.assertTrue(any("invalid fallback" in e for e in errors))
        self.assertTrue(any("inline execution is forbidden" in e for e in errors))

    def test_controller_policy_is_required(self):
        del self.data["controller_policy"]
        self.assertTrue(any("dispatch-only" in e for e in validate(self.data, self.root)))

    def test_explicit_extension_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "SKILL.md"
            self.skill("research")["path"] = str(path)
            self.assertTrue(any("does not exist" in error for error in validate(self.data, self.root)))
            path.write_text("---\nname: research\ndescription: research\n---\n", encoding="utf-8")
            self.assertEqual(validate(self.data, self.root), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
