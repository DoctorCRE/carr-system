#!/usr/bin/env python3
"""Required safety hooks cannot disappear from native settings unnoticed."""
import importlib.util
import hashlib
import json
from pathlib import Path
import unittest
import contextlib
import io
import subprocess
import sys
import tempfile
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'ops'))
from git_env import fixture_env  # noqa: E402

spec =importlib.util.spec_from_file_location('safety_hooks', REPO / 'hooks/gate-integrity.py')
assert spec and spec.loader
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class SafetyHooks(unittest.TestCase):
    def test_strict_cli_rejects_removed_or_tampered_required_hooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hooks = root / 'hooks'
            config = root / 'ops/config'
            hooks.mkdir()
            config.mkdir(parents=True)
            (hooks / 'gate-integrity.py').write_bytes((REPO / 'hooks/gate-integrity.py').read_bytes())
            required = {'guard-unattended.py', 'record-home-gate.py'}
            for name in required | {'lint-gate.py'}:
                (hooks / name).write_text('# fixture hook\n')
            baseline = {'hashes': {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in hooks.iterdir()
            }, 'contracts': {}}
            document = {'PreToolUse': [{'hooks': [
                {'command': f'python {{{{REPO}}}}/hooks/{name}'} for name in sorted(required)
            ]}]}
            for name in ('hooks', 'codex-hooks'):
                (config / f'{name}.json').write_text(json.dumps(document))
            baseline['contracts'] = {
                f'{name}.json': hashlib.sha256((config / f'{name}.json').read_bytes()).hexdigest()
                for name in ('hooks', 'codex-hooks')
            }
            (config / 'gate-baseline.json').write_text(json.dumps(baseline))

            def run():
                return subprocess.run([sys.executable, hooks / 'gate-integrity.py', '--strict'],
                                      capture_output=True, text=True, env=fixture_env())

            healthy = run()
            self.assertEqual(healthy.returncode, 0, healthy.stderr)
            for name in sorted(required):
                script = hooks / name
                original = script.read_bytes()
                with self.subTest(hook=name, mutation='missing script'):
                    script.unlink()
                    result = run()
                    self.assertEqual(result.returncode, 1)
                    self.assertIn(f'MISSING: hooks/{name}', result.stderr)
                    script.write_bytes(original)
                with self.subTest(hook=name, mutation='tampered script'):
                    script.write_bytes(original + b'# tampered\n')
                    result = run()
                    self.assertEqual(result.returncode, 1)
                    self.assertIn(f'CHANGED: hooks/{name}', result.stderr)
                    script.write_bytes(original)
                for client, config_name in [('claude', 'hooks'), ('codex', 'codex-hooks')]:
                    with self.subTest(hook=name, client=client, mutation='removed registration'):
                        path = config / f'{config_name}.json'
                        removed = {'PreToolUse': [{'hooks': [
                            hook for hook in document['PreToolUse'][0]['hooks']
                            if not hook['command'].endswith('/' + name)
                        ]}]}
                        path.write_text(json.dumps(removed))
                        result = run()
                        self.assertEqual(result.returncode, 1)
                        self.assertIn(f'{client}: required safety hook is not installed: {name}', result.stderr)
                        path.write_text(json.dumps(document))
            self.assertEqual(run().returncode, 0)

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
    def test_scoped_bless_removes_only_the_named_deleted_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / 'baseline.json'
            baseline.write_text(json.dumps({'hashes': {'retired.py': 'old', 'other.py': 'keep'},
                                            'contracts': {}}))
            with patch.multiple(gate, REPO=root, HOOKS=root, BASELINE=baseline,
                                GATED=[], CONTRACTS={}), \
                    patch.object(gate, 'dirty_gate_files', return_value=['retired.py']), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(gate.bless(['retired.py']), 0)
                self.assertEqual(json.loads(baseline.read_text())['hashes'], {'other.py': 'keep'})

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

    def test_unchanged_gates_bless_to_identical_bytes_from_any_commit_or_committer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hooks = root / 'hooks'
            hooks.mkdir()
            (hooks / 'guard-unattended.py').write_text('guard')
            baseline = root / 'baseline.json'

            def commit(email):
                for args in (['config', 'user.email', email], ['config', 'user.name', 'n'],
                             ['commit', '-q', '--allow-empty', '-m', email]):
                    subprocess.run(['git', '-C', tmp, *args], check=True, capture_output=True, env=fixture_env())

            subprocess.run(['git', 'init', '-q', tmp], check=True, capture_output=True, env=fixture_env())
            with patch.multiple(gate, REPO=root, HOOKS=hooks, BASELINE=baseline,
                                GATED=['guard-unattended.py'], CONTRACTS={}), \
                    patch.object(gate, 'dirty_gate_files', return_value=[]), \
                    patch.object(gate.sys, 'argv', ['gate-integrity.py', '--bless']), \
                    contextlib.redirect_stdout(io.StringIO()):
                commit('first@example.invalid')
                self.assertEqual(gate.bless(), 0)
                first = baseline.read_bytes()
                commit('second@example.invalid')
                self.assertEqual(gate.bless(), 0)
                self.assertEqual(baseline.read_bytes(), first)


if __name__ == '__main__':
    unittest.main()
