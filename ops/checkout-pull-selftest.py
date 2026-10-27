#!/usr/bin/env python3
"""Exercise checkout-pull against disposable Git repositories."""
import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parent / 'checkout-pull.py'

class CheckoutPullTest(unittest.TestCase):
    def test_dirty_tree_names_file(self):
        with tempfile.TemporaryDirectory() as d:
            repo = pathlib.Path(d)
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            subprocess.run(['git', '-C', str(repo), 'config', 'user.email', 'test@example.com'], check=True)
            subprocess.run(['git', '-C', str(repo), 'config', 'user.name', 'Test'], check=True)
            (repo / 'tracked').write_text('one')
            subprocess.run(['git', '-C', str(repo), 'add', 'tracked'], check=True)
            subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'initial'], check=True)
            (repo / 'tracked').write_text('two')
            result = subprocess.run([sys.executable, str(SCRIPT), str(repo)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('tracked', result.stderr)

    def test_fast_forwards_from_remote(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            remote, source, checkout = (root / name for name in ('remote.git', 'source', 'checkout'))
            def git(*args):
                subprocess.run(['git', *map(str, args)], check=True, capture_output=True)
            git('init', '--bare', remote)
            git('clone', remote, source)
            git('-C', source, 'config', 'user.email', 'test@example.com')
            git('-C', source, 'config', 'user.name', 'Test')
            (source / 'tracked').write_text('one')
            git('-C', source, 'add', 'tracked')
            git('-C', source, 'commit', '-m', 'initial')
            git('-C', source, 'push', '-u', 'origin', 'HEAD')
            git('clone', remote, checkout)
            (source / 'tracked').write_text('two')
            git('-C', source, 'commit', '-am', 'update')
            git('-C', source, 'push')
            result = subprocess.run([sys.executable, str(SCRIPT), str(checkout)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((checkout / 'tracked').read_text(), 'two')

    def test_ref_lock_is_retried(self):
        with tempfile.TemporaryDirectory() as d:
            repo = pathlib.Path(d)
            git = repo / 'git'
            git.write_text('#!/bin/sh\nif [ "$3" = status ]; then exit 0; fi\nif [ ! -f "$2/attempt" ]; then touch "$2/attempt"; echo "error: cannot lock ref refs/remotes/origin/main" >&2; exit 1; fi\nexit 0\n')
            git.chmod(0o755)
            result = subprocess.run([sys.executable, str(SCRIPT), str(repo), str(git)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('retry', result.stderr.lower())

if __name__ == '__main__':
    unittest.main()
