"""Staged source fixtures retain merge ancestry without copying unstaged edits."""
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'ops'))
from git_env import clone_index_fixture, fixture_env


class IndexFixtureTests(unittest.TestCase):
    def test_pending_merge_keeps_both_parents_and_exact_staged_tree(self):
        with tempfile.TemporaryDirectory() as scratch:
            source = pathlib.Path(scratch) / 'source'
            source.mkdir()
            env = fixture_env()
            for key in ('GIT_AUTHOR_NAME', 'GIT_AUTHOR_EMAIL',
                        'GIT_COMMITTER_NAME', 'GIT_COMMITTER_EMAIL', 'EMAIL'):
                env.pop(key, None)
            def git(*args):
                return subprocess.check_output(['git', '-C', str(source), *args],
                                               env=env, stderr=subprocess.PIPE, text=True).strip()
            def commit(path):
                git('add', path)
                message = source / '.git/message'
                message.write_text('Fixture source\n')
                git('commit', '-q', '-F', str(message))
            git('init', '-q', '-b', 'main')
            git('config', 'user.useConfigOnly', 'true')
            git('config', 'user.name', 'Fixture')
            git('config', 'user.email', 'fixture@example.invalid')
            (source / 'seed.txt').write_text('seed\n')
            commit('seed.txt')
            git('switch', '-qc', 'feature')
            (source / 'feature.txt').write_text('feature\n')
            commit('feature.txt')
            feature = git('rev-parse', 'HEAD')
            git('switch', '-q', 'main')
            (source / 'main.txt').write_text('main\n')
            commit('main.txt')
            main = git('rev-parse', 'HEAD')
            git('switch', '-q', 'feature')
            git('merge', '--no-commit', 'main')
            tree = git('write-tree')
            (source / 'feature.txt').write_text('unstaged\n')
            clone = pathlib.Path(scratch) / 'clone'
            clone_index_fixture(source, clone)
            def cloned(*args):
                return subprocess.check_output(['git', '-C', str(clone), *args], env=env, text=True).strip()
            self.assertEqual(cloned('rev-parse', 'HEAD^{tree}'), tree)
            self.assertEqual((clone / 'feature.txt').read_text(), 'feature\n')
            self.assertEqual(cloned('show', '-s', '--format=%P', 'HEAD').split(), [feature, main])
            self.assertEqual(git('rev-parse', 'HEAD'), feature)
            self.assertEqual(git('rev-parse', 'MERGE_HEAD'), main)


if __name__ == '__main__':
    unittest.main()
