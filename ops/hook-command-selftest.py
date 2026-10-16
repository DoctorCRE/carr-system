#!/usr/bin/env python3
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parent.parent
SOURCES = ('ops/config/hooks.json', 'ops/config/codex-hooks.json',
           'ops/config/claude-continuity-hooks.json', '.claude/settings.json')


def commands():
    for source in SOURCES:
        document = json.loads((REPO / source).read_text())
        for event, groups in document.get('hooks', document).items():
            if not isinstance(groups, list):
                continue
            for group in groups:
                for hook in group.get('hooks', []):
                    yield source, event, hook['command']


class HookCommands(unittest.TestCase):
    def test_missing_scripts_warn_and_present_scripts_keep_their_exit_status(self):
        for source, event, template in commands():
            with self.subTest(source=source, event=event, command=template), tempfile.TemporaryDirectory() as raw:
                home = Path(raw)
                repo = home / 'carr-system'
                (repo / 'hooks').mkdir(parents=True)
                (repo / 'ops').mkdir()
                (repo / '.venv/bin').mkdir(parents=True)
                (repo / '.venv/bin/python').symlink_to(sys.executable)
                command = template.replace('{{REPO}}', str(repo))
                relative = re.search(r'/(hooks|ops)/([\w-]+\.py)', command)
                self.assertIsNotNone(relative)
                script = repo / relative.group(1) / relative.group(2)
                env = {**os.environ, 'HOME': str(home), 'CLAUDE_PROJECT_DIR': str(home / 'branch')}
                for _ in range(2):
                    result = subprocess.run(['/bin/sh', '-c', command], input='payload',
                                            text=True, capture_output=True, env=env, timeout=5)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('missing', result.stderr.lower())
                    self.assertIn(script.name, result.stderr)
                script.write_text('import os, sys\nprint(sys.stdin.read())\nprint(sys.argv[1:])\n'
                                  'print(os.environ.get("CARR_MCP_CLIENT_PROFILE", ""))\nsys.exit(2)\n')
                result = subprocess.run(['/bin/sh', '-c', command], input='payload',
                                        text=True, capture_output=True, env=env, timeout=5)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('payload', result.stdout)
                if 'continuity' in script.name:
                    self.assertIn('continuity', result.stdout)
                if 'session-presence' in script.name:
                    self.assertIn('codex' if 'codex' in source else 'claude', result.stdout)
                script.unlink()
                script.mkdir()
                result = subprocess.run(['/bin/sh', '-c', command], input='', text=True,
                                        capture_output=True, env=env, timeout=5)
                self.assertNotEqual(result.returncode, 0, 'a present invalid script must fail')


if __name__ == '__main__':
    unittest.main()
