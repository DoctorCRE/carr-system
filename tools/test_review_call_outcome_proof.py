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

UNSET = object()
RUNNER = ROOT / 'tools/prove-review-call-outcome.py'


class ProofCLI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.path = Path(cls.folder.name)
        output = cls.path / 'observations.json'
        result = subprocess.run(['node', '--test', '--test-name-pattern=proof observations',
                                 'mcp-server/test/review-call-outcome-postgres.test.ts'], cwd=ROOT,
                                env={**os.environ, 'REVIEW_PROOF_OBSERVATIONS': str(output)},
                                capture_output=True, text=True, timeout=240)
        if result.returncode:
            cls.folder.cleanup()
            raise AssertionError(result.stdout + result.stderr)
        cls.observation = json.loads(output.read_text())
        source = (ROOT / 'mcp-server/src/release.js').as_uri()
        script = f"import {{buildRelease}} from {json.dumps(source)};" + (
            "console.log(JSON.stringify(await buildRelease({env:{CARR_ENV:'production',"
            "GIT_SHA:'a'.repeat(40),CF_VERSION_METADATA:{id:'fixture-worker'}},"
            "sql:async()=>[],verbCount:1})));"
        )
        cls.release = json.loads(subprocess.check_output(
            ['node', '--input-type=module', '-e', script], cwd=ROOT, env=fixture_env(), text=True))
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

    def run_cli(self, revision, mutation=None, changed_worker=False, bundle=UNSET):
        observations = copy.deepcopy(self.observation)
        observations['source_commit'] = revision
        if mutation:
            mutation(observations)
        release = copy.deepcopy(self.release)
        release['git_sha']['value'] = revision
        later = copy.deepcopy(release)
        later['ts'] = '2026-11-08T00:00:01.000Z'
        later['doctrine_generation'] = {'value': 2, 'reason': None}
        if changed_worker:
            later['worker_version']['id'] = 'different-worker'
        (self.path / 'release.json').write_text(json.dumps([release, later]))
        (self.path / 'observations.json').write_text(json.dumps(observations if bundle is UNSET else bundle))
        result = subprocess.run([sys.executable, str(RUNNER), '--merge-sha', self.merge,
                                 '--repo', str(self.repo), '--observations', str(self.path / 'observations.json'),
                                 '--release-fixture', str(self.path / 'release.json')],
                                env=fixture_env(), capture_output=True, text=True)
        self.assertNotIn("Traceback", result.stderr)
        return result.returncode, result.stdout

    def test_malformed_bundles_are_insufficient_evidence(self):
        for bundle in [[], None, 'scalar', 7, True, {}]:
            with self.subTest(bundle=bundle):
                code, output = self.run_cli(self.merge, bundle=bundle)
                self.assertEqual(code, 78)
                self.assertEqual(json.loads(output), {'status': 'insufficient_evidence'})
        code, output = self.run_cli(self.merge, lambda o: o.clear())
        self.assertEqual(code, 78)
        self.assertEqual(json.loads(output), {'status': 'insufficient_evidence'})

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
        self.assertEqual(self.run_cli(self.merge, changed_worker=True)[0], 78)


if __name__ == '__main__':
    unittest.main()
