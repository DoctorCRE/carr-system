#!/usr/bin/env python3
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from git_env import fixture_env

REPO = Path(__file__).resolve().parent.parent
CHECK = REPO / 'ops/hook-deletion-check.py'


class HookDeletion(unittest.TestCase):
    def test_target_spellings_cannot_bypass_deletion_check(self):
        for target in ('{{REPO}}/hooks/safety/old.py', 'hooks/old.py', './hooks/old.py',
                       'hooks/safety/../old.py', '{{REPO}}/hooks/../hooks/old.py',
                       '"{{ABS}}/hooks/old.py"',
                       '"{{REPO}}/hooks/safety/old.py"',
                       '"${HOME}/carr-system/hooks/safety/old.py"',
                       '"$r/hooks/safety/old.py"', '"${CLAUDE_PROJECT_DIR}/hooks/old.py"',
                       '"$UNKNOWN/old.py"', '"$(pwd)/hooks/old.py"',
                       '"$HOOK"', 'hooks/../../outside.py',
                       'hooks/old.py; python hooks/another.py',
                       'hooks/old.py; if true; then :; fi; python hooks/another.py'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as raw:
                repo = Path(raw)
                def git(*args):
                    return subprocess.run(['git', '-C', str(repo), *args], check=True,
                                          capture_output=True, text=True, env=fixture_env()).stdout.strip()
                git('init', '-q')
                git('config', 'user.name', 'Synthetic test')
                git('config', 'user.email', 'fixture@example.invalid')
                relative = 'hooks/safety/old.py' if 'safety/old.py' in target else 'hooks/old.py'
                script = repo / relative
                script.parent.mkdir(parents=True)
                script.write_text('print("fixture")\n')
                (repo / 'ops/config').mkdir(parents=True)
                config = repo / 'ops/config/hooks.json'
                command = 'python ' + target.replace('{{ABS}}', str(repo))
                config.write_text(json.dumps({'Stop': [{'hooks': [{'command': command}]}]}))
                git('add', relative, 'ops/config/hooks.json')
                git('commit', '-qm', 'Wired hook')
                base = git('rev-parse', 'HEAD')
                unsupported = target in {'"$UNKNOWN/old.py"', '"$(pwd)/hooks/old.py"',
                                         '"$HOOK"', 'hooks/../../outside.py',
                                         'hooks/old.py; python hooks/another.py',
                                         'hooks/old.py; if true; then :; fi; python hooks/another.py'}
                result = subprocess.run(['python3', str(CHECK), '--repo', str(repo), '--base', base],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 1 if unsupported else 0, result.stdout + result.stderr)
                config.write_text('{}')
                git('rm', relative)
                git('add', 'ops/config/hooks.json')
                git('commit', '-qm', 'Delete and unwire')
                result = subprocess.run(['python3', str(CHECK), '--repo', str(repo), '--base', base],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn('REFUSED', result.stdout)

    def test_unwire_and_rename_need_separate_prs(self):
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw)
            def git(*args):
                return subprocess.run(['git', '-C', str(repo), *args], check=True,
                                      capture_output=True, text=True, env=fixture_env()).stdout.strip()
            git('init', '-q')
            git('config', 'user.name', 'Synthetic test')
            git('config', 'user.email', 'fixture@example.invalid')
            (repo / 'hooks').mkdir()
            script = repo / 'hooks/old.py'
            script.write_text('print("fixture")\n')
            (repo / 'ops/config').mkdir(parents=True)
            config = repo / 'ops/config/hooks.json'
            config.write_text(json.dumps({'Stop': [{'hooks': [{'command': 'python {{REPO}}/hooks/old.py'}]}]}))
            git('add', 'hooks/old.py', 'ops/config/hooks.json')
            git('commit', '-qm', 'Wired hook')
            base = git('rev-parse', 'HEAD')
            config.write_text('{}')
            shutil.move(script, repo / 'hooks/new.py')
            git('add', 'hooks/old.py', 'hooks/new.py', 'ops/config/hooks.json')
            git('commit', '-qm', 'Unsafe rename and unwire')
            result = subprocess.run(['python3', str(CHECK), '--repo', str(repo), '--base', base],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn('hooks/old.py', result.stdout)
            git('checkout', base, '--', 'hooks/old.py')
            git('commit', '-qm', 'Keep script through unwire')
            unwired = git('rev-parse', 'HEAD')
            result = subprocess.run(['python3', str(CHECK), '--repo', str(repo), '--base', base],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            git('rm', 'hooks/old.py')
            git('commit', '-qm', 'Delete after unwire')
            result = subprocess.run(['python3', str(CHECK), '--repo', str(repo), '--base', unwired],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
