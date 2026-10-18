#!/usr/bin/env python3
"""Required safety hooks cannot disappear from native settings unnoticed."""
import importlib.util
import json
from pathlib import Path
import unittest
import contextlib
import io
import tempfile
from unittest.mock import patch

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


class BaselineIntegrity(unittest.TestCase):
    def test_blessed_content_detects_changes_missing_and_unblessed_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hooks = root / 'hooks'
            hooks.mkdir()
            script = hooks / 'guard-unattended.py'
            script.write_text('original guard')
            contract = root / 'hooks.json'
            contract.write_text('{}')
            (root / 'ops/config').mkdir(parents=True)
            for name in ['hooks', 'codex-hooks']:
                (root / f'ops/config/{name}.json').write_text('{}')
            baseline = root / 'baseline.json'
            with patch.multiple(gate, REPO=root, HOOKS=hooks, BASELINE=baseline,
                                GATED=['guard-unattended.py'],
                                CONTRACTS={'hooks.json': contract}), \
                    patch.object(gate, 'dirty_gate_files', return_value=[]), \
                    patch.object(gate.sys, 'argv', ['gate-integrity.py', '--strict']), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(gate.bless(), 0)
                self.assertEqual(gate.check_baseline(), [])
                with patch.object(gate, 'check', return_value=[]):
                    self.assertEqual(gate.main(), 0)
                script.write_text('changed guard')
                with patch.object(gate, 'check', return_value=[]):
                    self.assertEqual(gate.main(), 1)
                self.assertTrue(any('CHANGED: hooks/guard-unattended.py' in error
                                    for error in gate.check_baseline()))
                script.unlink()
                self.assertTrue(any('MISSING: hooks/guard-unattended.py' in error
                                    for error in gate.check_baseline()))
                script.write_text('original guard')
                contract.write_text('{"changed": true}')
                self.assertTrue(any('CHANGED: ops/config/hooks.json' in error
                                    for error in gate.check_baseline()))
                data = json.loads(baseline.read_text())
                data['hashes'] = {}
                baseline.write_text(json.dumps(data))
                self.assertTrue(any('UNBLESSED: hooks/guard-unattended.py' in error
                                    for error in gate.check_baseline()))
                baseline.unlink()
                self.assertTrue(gate.check_baseline())


if __name__ == '__main__':
    unittest.main()
