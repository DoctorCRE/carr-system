#!/usr/bin/env python3
"""Repository scoping and exact, repeatable user-config cleanup."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import runpy
import shutil
from unittest.mock import patch
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ops'))
from git_env import fixture_env  # noqa: E402
MIGRATION = runpy.run_path(str(ROOT / 'ops/scope-agent-config.py'))


class Scope(unittest.TestCase):
    def test_installer_default_and_project_template_have_one_scope(self):
        installer = runpy.run_path(str(ROOT / 'ops/config-as-code.py'))
        self.assertEqual(Path(installer['SETTINGS']), Path(installer['REPO']) / '.claude/settings.json')
        project = json.loads((ROOT / '.claude/settings.json').read_text())
        template = json.loads((ROOT / 'ops/config/hooks.json').read_text().replace(
            '{{REPO}}', '${CLAUDE_PROJECT_DIR}'))
        self.assertEqual(project['hooks'], template)


    def test_cleanup_is_exact_dry_by_default_backed_up_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            settings = home / '.claude/settings.json'
            settings.parent.mkdir()
            legacy = json.loads((ROOT / 'ops/config/claude-legacy-user-hooks.json').read_text().replace(
                '{{REPO}}', str(home / 'carr-system')))
            personal = {'hooks': [{'type': 'command', 'command': 'echo personal'}]}
            legacy['Stop'].append(personal)
            altered = copy.deepcopy(legacy['PreToolUse'][0])
            altered['hooks'][0]['timeout'] += 1
            legacy['PreToolUse'].append(altered)
            settings.write_text(json.dumps({'hooks': legacy, 'theme': 'dark'}))
            mcp = home / '.claude.json'
            server = json.loads((ROOT / '.mcp.json').read_text())['mcpServers']['carr']
            mcp.write_text(json.dumps({'mcpServers': {'carr': server, 'rea': {'url': 'other'}},
                                       'oauthAccount': {'fixture': True}}))
            before = {p: p.read_bytes() for p in (settings, mcp)}
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(MIGRATION['migrate'](home), 2)
                self.assertEqual(before, {p: p.read_bytes() for p in before})
                self.assertFalse(list(home.rglob('*before-carr-scope*')))
                self.assertEqual(MIGRATION['migrate'](home, True), 2)
                self.assertEqual(MIGRATION['migrate'](home, True), 0)
            self.assertEqual(json.loads(settings.read_text())['hooks']['Stop'], [personal])
            self.assertEqual(json.loads(settings.read_text())['hooks']['PreToolUse'], [altered])
            self.assertEqual(json.loads(mcp.read_text())['mcpServers'], {'rea': {'url': 'other'}})
            self.assertEqual(json.loads(mcp.read_text())['oauthAccount'], {'fixture': True})
            for path, raw in before.items():
                backups = list(path.parent.glob(path.name + '.before-carr-scope-*'))
                self.assertEqual(len(backups), 1)
                self.assertEqual(backups[0].read_bytes(), raw)
                self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)

    def test_codex_cleanup_preserves_modified_and_unrelated_hooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            path = home / '.codex/hooks.json'
            path.parent.mkdir()
            legacy = json.loads((ROOT / 'ops/config/codex-legacy-user-hooks.json').read_text().replace(
                '{{REPO}}', str(home / 'carr-system')))
            personal = {'type': 'command', 'command': 'echo personal'}
            changed = copy.deepcopy(legacy['hooks']['PreToolUse'][0]['hooks'][0])
            changed['timeout'] += 1
            legacy['hooks']['PreToolUse'][0]['hooks'] += [personal, changed]
            path.write_text(json.dumps(legacy))
            before = path.read_bytes()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(MIGRATION['migrate'](home), 1)
                self.assertEqual(path.read_bytes(), before)
                self.assertFalse(list(path.parent.glob('*before-carr-scope*')))
                self.assertEqual(MIGRATION['migrate'](home, True), 1)
                self.assertEqual(MIGRATION['migrate'](home, True), 0)
            self.assertEqual(json.loads(path.read_text())['hooks']['PreToolUse'],
                             [{'matcher': '.*', 'hooks': [personal, changed]}])
            backup, = path.parent.glob('*before-carr-scope*')
            self.assertEqual(backup.read_bytes(), before)
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)

    @unittest.skipUnless(shutil.which('codex'), 'native Codex CLI is not installed')
    def test_native_codex_discovers_only_the_trusted_project(self):
        installer = runpy.run_path(str(ROOT / 'ops/config-as-code.py'))
        with tempfile.TemporaryDirectory(prefix='codex discovery ') as tmp:
            base = Path(tmp)
            home, project, outside = (base / name for name in ('home', 'carr-system', 'life-ai'))
            for directory in (home, project, outside):
                directory.mkdir()
            for directory in (project, outside):
                subprocess.run(['git', 'init', '-q', str(directory)], check=True, env=fixture_env())
            (project / '.codex').mkdir()
            (project / '.codex/hooks.json').write_bytes((ROOT / '.codex/hooks.json').read_bytes())
            subprocess.run(['git', '-C', str(project), 'add', '.codex/hooks.json'], check=True, env=fixture_env())
            subprocess.run(['git', '-C', str(project), '-c', 'core.hooksPath=/dev/null',
                            '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
                            'commit', '-qm', 'fixture'], check=True, env=fixture_env())
            worktree = base / 'worktree'
            subprocess.run(['git', '-C', str(project), 'worktree', 'add', '-q',
                            str(worktree)], check=True, env=fixture_env())
            (project / 'nested').mkdir()
            config = '[features]\nhooks = true\n'
            for directory in (project, worktree):
                config += '[projects.' + json.dumps(str(directory.resolve())) + ']\ntrust_level = "trusted"\n'
            (home / 'config.toml').write_text(config)
            request = installer['codex_app_server_request']
            request.__globals__['REPO'] = str(project)
            with patch.dict(os.environ, {'CODEX_HOME': str(home)}):
                result = request('hooks/list', {'cwds': [str(p) for p in
                                 (outside, project, project / 'nested', worktree)]})
            outside_entry, *inside = result['data']
            self.assertEqual(outside_entry['hooks'], [])
            for entry in inside:
                self.assertEqual(entry['errors'], [])
                command = next(h['command'] for h in entry['hooks']
                               if 'guard-unattended.py' in h.get('command', ''))
                checkout = worktree if Path(entry['cwd']).name == 'worktree' else project
                (checkout / 'hooks').mkdir(exist_ok=True)
                (checkout / 'hooks/guard-unattended.py').write_text('print("NATIVE GUARD FIRED")')
                fired = subprocess.run(['/bin/sh', '-c', command], cwd=entry['cwd'],
                                       capture_output=True, text=True, timeout=5,
                                       env=fixture_env())
                self.assertEqual(fired.returncode, 0, fired.stderr)
                self.assertEqual(fired.stdout.strip(), 'NATIVE GUARD FIRED')

    def test_codex_installer_and_guard_use_checkout_scope(self):
        installer = runpy.run_path(str(ROOT / 'ops/config-as-code.py'))
        self.assertEqual(Path(installer['CODEX_HOOKS_SRC']),
                         Path(installer['REPO']) / '.codex/hooks.json')
        state = installer['codex_configuration_state']
        with tempfile.TemporaryDirectory() as home, patch.dict(state.__globals__,
                {'HOME': home, 'CODEX_CONFIG': str(Path(home) / '.codex/config.toml'),
                 'CODEX_HOOKS_SRC': str(ROOT / '.codex/hooks.json')}):
            self.assertEqual(state(), 'absent')
        doc = json.loads((ROOT / '.codex/hooks.json').read_text())
        self.assertEqual(doc, json.loads((ROOT / 'ops/config/codex-hooks.json').read_text()))
        command = next(h['command'] for g in doc['hooks']['PreToolUse'] for h in g['hooks']
                       if 'guard-unattended.py' in h['command'])
        with tempfile.TemporaryDirectory(prefix='carr codex scope ') as tmp:
            project = Path(tmp) / 'checkout'
            project.mkdir()
            subprocess.run(['git', 'init', '-q', str(project)], check=True, env=fixture_env())
            (project / 'hooks').mkdir()
            (project / 'hooks/guard-unattended.py').write_text('print("CHECKOUT GUARD FIRED")')
            (project / 'nested').mkdir()
            for cwd in (project, project / 'nested'):
                result = subprocess.run(['/bin/sh', '-c', command], cwd=cwd,
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), 'CHECKOUT GUARD FIRED')

    def test_changed_server_and_invalid_json_refuse_before_any_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            p = home / '.claude.json'
            for body in ('{', '{"mcpServers":{"carr":{"url":"different"}}}'):
                p.write_text(body)
                with self.assertRaises(ValueError):
                    MIGRATION['migrate'](home, True)
                self.assertEqual(p.read_text(), body)

    def test_project_hook_runs_from_worktree_even_with_canonical_checkout(self):
        doc = json.loads((ROOT / '.claude/settings.json').read_text())
        self.assertEqual(doc['enabledMcpjsonServers'], ['carr'])
        with tempfile.TemporaryDirectory(prefix='carr scope ') as tmp:
            project = Path(tmp) / 'worktree'
            (project / 'hooks').mkdir(parents=True)
            (project / '.venv/bin').mkdir(parents=True)
            (project / '.venv/bin/python').symlink_to(sys.executable)
            command = next(h['command'] for g in doc['hooks']['PreToolUse'] for h in g['hooks']
                           if 'agent-card-gate.py' in h['command'])
            (project / 'hooks/agent-card-gate.py').write_text('print("WORKTREE HOOK FIRED")')
            result = subprocess.run(['/bin/sh', '-c', command], env={**os.environ,
                'CLAUDE_PROJECT_DIR': str(project)}, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'WORKTREE HOOK FIRED')


if __name__ == '__main__':
    unittest.main()
