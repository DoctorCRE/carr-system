#!/usr/bin/env python3
"""Repository scoping and exact, repeatable user-config cleanup."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = runpy.run_path(str(ROOT / 'ops/scope-claude-config.py'))


class Scope(unittest.TestCase):
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
