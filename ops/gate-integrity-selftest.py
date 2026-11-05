#!/usr/bin/env python3
"""Required safety hooks cannot disappear from native settings unnoticed."""
import importlib.util
import hashlib
import json
from pathlib import Path
import unittest
import copy
import os
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
    def documents(self):
        return {client: json.loads((REPO / f'ops/config/{name}.json').read_text())
                for client, name in [('claude', 'hooks'), ('codex', 'codex-hooks')]}

    def test_every_registration_is_required_and_cannot_be_altered(self):
        counts = {}
        for client, original in self.documents().items():
            block = original.get('hooks', original)
            self.assertEqual(gate.check(original, client), [])
            counts[client] = 0
            for event, groups in block.items():
                for gi, group in enumerate(groups):
                    for hi, hook in enumerate(group.get('hooks', [])):
                        counts[client] += 1
                        name = hook['command'].split('/')[-1]
                        for mutation in ('delete', 'matcher', 'command', 'timeout', 'type', 'async', 'event'):
                            with self.subTest(client=client, event=event, hook=name, mutation=mutation):
                                document = copy.deepcopy(original)
                                hooks = document.get('hooks', document)
                                changed = hooks[event][gi]
                                if mutation == 'delete':
                                    del changed['hooks'][hi]
                                elif mutation == 'matcher':
                                    changed['matcher'] = 'NeverMatches'
                                elif mutation == 'event':
                                    hooks['WrongEvent'] = [hooks[event].pop(gi)]
                                elif mutation == 'async':
                                    changed['hooks'][hi]['async'] = True
                                else:
                                    changed['hooks'][hi][mutation] = 'altered'
                                errors = '\n'.join(gate.check(document, client))
                                self.assertIn(f'{client}: required safety hook is not installed:', errors)
                                self.assertIn(name, errors)
                                self.assertIn(event, errors)
        self.assertEqual(counts, {'claude': 12, 'codex': 7})

    def test_effective_config_homes(self):
        (REPO / 'scratchpad').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=REPO / 'scratchpad') as tmp:
            home = Path(tmp)
            sources = {'claude': home / '.claude/settings.json',
                       'codex': home / '.codex/hooks.json'}
            for client, document in self.documents().items():
                sources[client].parent.mkdir()
                sources[client].write_text(json.dumps(document).replace('{{REPO}}', str(REPO)))
            with patch.dict(os.environ, {'HOME': str(home)}, clear=True), \
                    patch.object(gate, 'check_baseline', side_effect=lambda: []), \
                    patch.object(gate, 'installation_repo', return_value=REPO), \
                    patch.object(gate.sys, 'argv', ['gate-integrity.py']), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(gate.main(), 0, errors.getvalue())
                for client, variable in [('claude', 'CLAUDE_CONFIG_DIR'), ('codex', 'CODEX_HOME')]:
                    for missing in (False, True):
                        with self.subTest(client=client, missing=missing):
                            selected = home / f'{client}-selected-{missing}'
                            if not missing:
                                selected.mkdir()
                            with patch.dict(os.environ, {variable: str(selected)}):
                                self.assertEqual(gate.main(), 1)
                                self.assertIn(str(selected), errors.getvalue())
                    selected = home / f'{client}-valid'
                    selected.mkdir()
                    (selected / sources[client].name).write_text(sources[client].read_text())
                    sources[client].write_text('{}')
                    with patch.dict(os.environ, {variable: str(selected)}):
                        self.assertEqual(gate.main(), 0, errors.getvalue())
                    sources[client].write_text((selected / sources[client].name).read_text())
                for source in sources.values():
                    source.unlink()
                self.assertEqual(gate.main(), 1)

    def test_unrelated_hooks_and_group_order_are_preserved(self):
        for client, document in self.documents().items():
            hooks = document.get('hooks', document)
            for groups in hooks.values():
                groups.reverse()
            hooks.setdefault('Stop', []).append({'hooks': [{'command': '/usr/bin/true'}]})
            self.assertEqual(gate.check(document, client), [])

    def test_disabled_hooks_are_rejected(self):
        for client, document in self.documents().items():
            if 'hooks' not in document:
                document = {'hooks': document}
            document['disableAllHooks'] = True
            self.assertIn(f'{client}: hooks are disabled by disableAllHooks', gate.check(document, client))

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
