#!/usr/bin/env python3
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

from git_env import fixture_env

ROOT = Path(__file__).resolve().parents[1]


class WorktreeMigrationOrder(unittest.TestCase):
    def test_peer_delivery_requires_a_forward_filename(self):
        with tempfile.TemporaryDirectory(prefix='migration-worktree-order-') as temp:
            repo = Path(temp) / 'primary'
            peer = Path(temp) / 'peer'
            repo.mkdir()
            env = fixture_env({**os.environ, 'GITHUB_BASE_REF': 'some/stacked-branch'})

            def git(tree, *args):
                return subprocess.run(['git', '-C', str(tree), *args], check=True,
                                      capture_output=True, text=True, env=env).stdout

            git(repo, 'init', '-b', 'main')
            git(repo, 'config', 'user.name', 'Fixture')
            git(repo, 'config', 'user.email', 'fixture@example.invalid')
            git(repo, 'config', 'core.hooksPath', '/dev/null')
            (repo / 'migrations').mkdir()
            (repo / 'migrations/0889_base.sql').write_text('select 1;\n')
            git(repo, 'add', 'migrations/0889_base.sql')
            git(repo, 'commit', '-m', 'Base')
            git(repo, 'worktree', 'add', '-b', 'peer', str(peer))
            (peer / 'migrations/0890_peer.sql').write_text('select 2;\n')
            git(peer, 'add', 'migrations/0890_peer.sql')
            git(peer, 'commit', '-m', 'Peer')
            (repo / 'migrations/0890_main.sql').write_text('select 3;\n')
            git(repo, 'add', 'migrations/0890_main.sql')
            git(repo, 'commit', '-m', 'Main advances')
            git(repo, 'update-ref', 'refs/remotes/origin/main', 'HEAD')

            def gate():
                return subprocess.run([sys.executable, str(ROOT / 'ops/migration-order-gate.py'),
                                       '--repo', str(peer)], env=env, capture_output=True,
                                      text=True, timeout=30)

            refused = gate()
            self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
            self.assertIn('migrations/0890_peer.sql', refused.stderr)
            git(peer, 'mv', 'migrations/0890_peer.sql', 'migrations/0891_peer.sql')
            git(peer, 'commit', '-m', 'Use forward filename')
            accepted = gate()
            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)


if __name__ == '__main__':
    unittest.main()
