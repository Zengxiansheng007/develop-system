from pathlib import Path
import tempfile
import unittest
from skill_format import validate_skill

class ExistingInvocationMetadataTests(unittest.TestCase):
    def test_supported_fields_keep_types_and_unknown_fields_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = '---\nname: example\ndescription: Example skill\n'
            for metadata, expected in [
                ('disable-model-invocation: true\nargument-hint: What next?\n', True),
                ('disable-model-invocation: "true"\n', False),
                ('argument-hint: 123\n', False),
                ('unexpected-field: true\n', False),
            ]:
                (root / 'SKILL.md').write_text(base + metadata + '---\nInstructions.\n', encoding='utf-8')
                self.assertEqual(validate_skill(root)[0], expected, metadata)
