#!/usr/bin/env python3
"""Required safety hooks cannot disappear from native settings unnoticed."""
import importlib.util
import json
from pathlib import Path
import unittest

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('safety_hooks', REPO / 'hooks/gate-integrity.py')
assert spec and spec.loader
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class SafetyHooks(unittest.TestCase):
    def test_native_configuration(self):
        for client, name in [('claude', 'hooks'), ('codex', 'codex-hooks')]:
            document = json.loads((REPO / f'ops/config/{name}.json').read_text())
            self.assertEqual(gate.check(document, client), [])

    def test_removed_guard(self):
        self.assertIn('codex: required safety hook is not installed: guard-unattended.py',
                      gate.check({'PreToolUse': []}, 'codex'))

    def test_stop_guard_is_not_a_pre_action_safeguard(self):
        document = {'Stop': [{'hooks': [{'command': 'python {{REPO}}/hooks/guard-unattended.py'}]}]}
        self.assertIn('codex: required safety hook is not installed: guard-unattended.py',
                      gate.check(document, 'codex'))

    def test_missing_command(self):
        document = {'PreToolUse': [{'hooks': [{'command': 'python {{REPO}}/hooks/absent.py'}]}]}
        self.assertTrue(any('missing hook script:' in error for error in gate.check(document, 'codex')))


if __name__ == '__main__':
    unittest.main()
