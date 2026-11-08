"""Exercise recording admission through the same check CI runs."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('recording_check', ROOT / 'ops/recording-provenance-check.py')
assert spec is not None and spec.loader is not None
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class RecordingScopeTests(unittest.TestCase):
    def test_repository_recordings_are_scoped(self):
        self.assertEqual(checker.check(ROOT), [])

    def test_whole_registry_pin_is_rejected_even_with_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / 'mcp-server/src/tools.js'
            registry.parent.mkdir(parents=True)
            registry.write_text('export const TOOLS = {};')
            fixture = root / 'mcp-server/test/recording.json'
            fixture.parent.mkdir(parents=True)
            for scope in ({}, {'scheme': checker.SCHEME, 'verbs': ['lead-hot']}):
                fixture.write_text(json.dumps({'provenance': {
                    'producer_scope': scope,
                    'producer_sha256': checker.hashlib.sha256(registry.read_bytes()).hexdigest()}}))
                self.assertEqual(len(checker.check(root)), 1)

    def test_whole_family_registry_pin_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = root / 'mcp-server/src/family.ts'
            registry.parent.mkdir(parents=True)
            registry.write_text('export const family = { a: {handler: () => 1}, b: {handler: () => 2} };')
            fixture = root / 'tools/fixtures/recording.json'
            fixture.parent.mkdir(parents=True)
            fixture.write_text(json.dumps({'provenance': {
                'producer_scope': {'scheme': checker.SCHEME, 'verbs': ['a']},
                'producer_sha256': checker.hashlib.sha256(registry.read_bytes()).hexdigest()}}))
            self.assertEqual(len(checker.check(root)), 1)

    def test_unscoped_leaf_pin_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / 'tools/fixtures/recording.json'
            fixture.parent.mkdir(parents=True)
            fixture.write_text(json.dumps({'provenance': {'producer_sha256': '0' * 64}}))
            self.assertEqual(len(checker.check(root)), 1)


if __name__ == '__main__':
    unittest.main()
