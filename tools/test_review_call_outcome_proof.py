"""Exercise the proof with PostgreSQL-recorded registered responses."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ops'))
from git_env import fixture_env

RUNNER = ROOT / 'tools/prove-review-call-outcome.py'


class ProofCLI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.path = Path(cls.folder.name)
        output = cls.path / 'observations.json'
        result = subprocess.run(['node', '--test', '--test-name-pattern=proof observations',
                                 'mcp-server/test/review-call-outcome-postgres.test.mjs'], cwd=ROOT,
                                env={**os.environ, 'REVIEW_PROOF_OBSERVATIONS': str(output)},
                                capture_output=True, text=True, timeout=240)
        if result.returncode:
            cls.folder.cleanup()
            raise AssertionError(result.stdout + result.stderr)
        cls.observation = json.loads(output.read_text())
        cls.repo = cls.path / 'repo'
        cls.repo.mkdir()
        def git(*args):
            return subprocess.check_output(['git', '-C', str(cls.repo), *args],
                                           env=fixture_env(), text=True).strip()
        git('init', '-q')
        git('config', 'user.name', 'Fixture')
        git('config', 'user.email', 'fixture@example.test')
        git('commit', '-q', '--allow-empty', '-m', 'older')
        cls.older = git('rev-parse', 'HEAD')
        git('commit', '-q', '--allow-empty', '-m', 'merge')
        cls.merge = git('rev-parse', 'HEAD')
        git('commit', '-q', '--allow-empty', '-m', 'descendant')
        cls.descendant = git('rev-parse', 'HEAD')
        git('checkout', '-q', '--orphan', 'unrelated')
        git('commit', '-q', '--allow-empty', '-m', 'unrelated')
        cls.unrelated = git('rev-parse', 'HEAD')

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def run_cli(self, revision, mutation=None):
        observations = copy.deepcopy(self.observation)
        observations['source_commit'] = revision
        if mutation:
            mutation(observations)
        release = {'ok': True, 'env': {'value': 'production'}, 'git_sha': {'value': revision}}
        (self.path / 'release.json').write_text(json.dumps(release))
        (self.path / 'observations.json').write_text(json.dumps(observations))
        result = subprocess.run([sys.executable, str(RUNNER), '--merge-sha', self.merge,
                                 '--repo', str(self.repo), '--observations', str(self.path / 'observations.json'),
                                 '--release-fixture', str(self.path / 'release.json')],
                                env=fixture_env(), capture_output=True, text=True)
        return result.returncode, result.stdout

    def test_release_and_assertion_exits(self):
        for revision in [self.merge, self.descendant]:
            with self.subTest(revision=revision):
                self.assertEqual(self.run_cli(revision)[0], 0)
        for revision in [self.older, self.unrelated, 'f' * 40]:
            with self.subTest(revision=revision):
                self.assertEqual(self.run_cli(revision)[0], 78)
        self.assertEqual(self.run_cli(self.merge, lambda o: o['counts'].update(events=2))[0], 1)
        self.assertEqual(self.run_cli(self.merge, lambda o: o.pop('refusal'))[0], 78)
        self.assertEqual(self.run_cli(self.merge, lambda o: o.update(source_commit=self.older))[0], 78)


if __name__ == '__main__':
    unittest.main()
