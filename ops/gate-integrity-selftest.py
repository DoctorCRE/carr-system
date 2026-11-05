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


class BaselineIntegrity(unittest.TestCase):
    def test_linked_worktree_keeps_installed_codex_root_and_rejects_missing_hooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = root / 'worker'
            canonical = root / 'canonical'
            codex_home = root / 'codex'
            codex_home.mkdir()
            project_settings = worktree / '.claude/settings.json'
            project_settings.parent.mkdir(parents=True)
            project = json.loads((REPO / '.claude/settings.json').read_text())
            codex_template = gate.REGISTRATIONS['codex'].read_text()
            codex = json.loads(codex_template.replace('{{REPO}}', str(canonical)))
            for repo in (worktree, canonical):
                for script in (REPO / 'hooks').glob('*.py'):
                    target = repo / 'hooks' / script.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.touch()
            project_settings.write_text(json.dumps(project))
            installed_codex = codex_home / 'hooks.json'
            installed_codex.write_text(json.dumps(codex))
            with patch.object(gate, 'REPO', worktree), \
                    patch.object(gate, 'installation_repo', return_value=canonical), \
                    patch.object(gate, 'check_baseline', return_value=[]), \
                    patch.dict(gate.os.environ, {'CODEX_HOME': str(codex_home)}), \
                    patch.object(gate.sys, 'argv', ['gate-integrity.py']), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(gate.main(), 0, errors.getvalue())
                for client, path, document in (
                        ('claude', project_settings, project),
                        ('codex', installed_codex, codex)):
                    with self.subTest(client=client):
                        broken = json.loads(json.dumps(document))
                        broken['hooks']['PreToolUse'] = [
                            group for group in broken['hooks']['PreToolUse']
                            if not any('guard-unattended.py' in hook['command']
                                       for hook in group['hooks'])]
                        path.write_text(json.dumps(broken))
                        errors.seek(0)
                        errors.truncate()
                        self.assertEqual(gate.main(), 1)
                        self.assertIn(f'{client}: required safety hook is not installed: '
                                      'guard-unattended.py', errors.getvalue())
                        path.write_text(json.dumps(document))
                with patch.object(gate.sys, 'argv', ['gate-integrity.py', '--strict']):
                    self.assertEqual(gate.main(), 0, errors.getvalue())

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
