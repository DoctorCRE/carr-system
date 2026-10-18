#!/usr/bin/env python3
from pathlib import Path
import subprocess
import tempfile
import unittest

from git_env import fixture_env

ROOT = Path(__file__).resolve().parents[1]


class TypeCheckRouteTests(unittest.TestCase):
    def test_worktree_uses_canonical_tool_for_full_and_scoped_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            env = fixture_env()
            def git(*args):
                subprocess.run(['git', '-C', str(root), *args], env=env, check=True,
                               capture_output=True)
            git('init', '--quiet', '--initial-branch=main')
            (root / 'bin').mkdir()
            script = root / 'bin/type-check.sh'
            script.write_bytes((ROOT / 'bin/type-check.sh').read_bytes())
            message = root / 'message.txt'
            message.write_text('synthetic fixture\n')
            git('add', 'bin/type-check.sh')
            git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                'commit', '--quiet', '-F', str(message))
            tree = root / 'tree'
            git('worktree', 'add', '--quiet', '-b', 'fixture', str(tree))
            tool = root / '.venv/bin/mypy'
            tool.parent.mkdir(parents=True)
            tool.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\nexit "${FIXTURE_RESULT:-0}"\n')
            tool.chmod(0o755)
            for args, expected in [([], ['pipelines', 'tools', 'exporters', 'lib', 'generators',
                                         'shared', 'fill-engine', 'bin', 'hooks', 'ops']),
                                   (['--files', 'ops/example.py'], ['ops/example.py'])]:
                result = subprocess.run(['sh', str(tree / 'bin/type-check.sh'), *args],
                                        cwd=tree, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines(), expected)
            result = subprocess.run(['sh', str(tree / 'bin/type-check.sh'), '--files', 'ops/example.py'],
                                    cwd=tree, env={**env, 'FIXTURE_RESULT': '1'}, capture_output=True)
            self.assertEqual(result.returncode, 1)

    def test_empty_scoped_check_refuses(self):
        result = subprocess.run(['sh', str(ROOT / 'bin/type-check.sh'), '--files'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)


if __name__ == '__main__':
    unittest.main()
