#!/usr/bin/env python3
"""Every project hook resolves inside its own checkout on Mac and cloud shells."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
SHELLS = [s for s in ('/bin/sh', '/bin/bash', '/bin/zsh') if os.access(s, os.X_OK)]


class ProjectPaths(unittest.TestCase):
    def test_every_hook_uses_the_project_even_when_home_has_a_checkout(self):
        document = json.loads((REPO / '.claude/settings.json').read_text())
        with tempfile.TemporaryDirectory(prefix='carr project ') as tmp:
            home = Path(tmp) / 'home'
            project = Path(tmp) / 'worktree'
            canonical = home / 'carr-system/hooks'
            canonical.mkdir(parents=True)
            (project / 'hooks').mkdir(parents=True)
            (project / '.venv/bin').mkdir(parents=True)
            (project / '.venv/bin/python').symlink_to(sys.executable)
            for event, groups in document['hooks'].items():
                for group in groups:
                    for hook in group['hooks']:
                        command = hook['command']
                        name = re.search(r'/hooks/([\w-]+\.py)', command)[1]
                        (canonical / name).write_text('raise SystemExit(2)')
                        script = project / 'hooks' / name
                        script.write_text('import sys\nprint("PROJECT:" + sys.stdin.read())\n')
                        for shell in SHELLS:
                            with self.subTest(event=event, name=name, shell=shell):
                                env = {**os.environ, 'HOME': str(home), 'CLAUDE_PROJECT_DIR': str(project)}
                                result = subprocess.run([shell, '-c', command], input='payload',
                                    env=env, cwd=project, capture_output=True, text=True, timeout=5)
                                self.assertEqual(result.returncode, 0, result.stderr)
                                self.assertEqual(result.stdout.strip(), 'PROJECT:payload')
                                self.assertEqual(result.stderr, '')
                        script.unlink()


if __name__ == '__main__':
    unittest.main()
