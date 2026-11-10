"""Exercise the proof CLI with recorded release contracts and isolated Git history."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / 'tools/prove-chromium-safety.py'


class ChromiumProofTest(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.repo = Path(self.scratch.name)
        self.git('init', '-q')
        self.git('config', 'user.email', 'fixture@example.test')
        self.git('config', 'user.name', 'Fixture')
        self.suite = self.repo / 'mcp-server/test/chromium-safety-args.test.ts'
        self.suite.parent.mkdir(parents=True)
        self.suite.write_text("import assert from 'node:assert/strict'; assert.equal(2, 2);\n")
        self.git('add', 'mcp-server/test/chromium-safety-args.test.ts')
        self.git('commit', '-qm', 'older')
        self.older = self.git('rev-parse', 'HEAD')
        self.git('commit', '--allow-empty', '-qm', 'merge')
        self.merge = self.git('rev-parse', 'HEAD')
        self.git('commit', '--allow-empty', '-qm', 'descendant')
        self.descendant = self.git('rev-parse', 'HEAD')
        self.git('checkout', '--orphan', 'unrelated', '-q')
        self.git('commit', '--allow-empty', '-qm', 'unrelated')
        self.unrelated = self.git('rev-parse', 'HEAD')
        self.release = self.repo / 'release.json'
        self.recorded = json.loads((ROOT / 'tools/fixtures/proof-release-production.json').read_text())

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.repo, stderr=subprocess.DEVNULL, text=True).strip()

    def prove(self, revision, expected, *, payload=None):
        release = json.loads(json.dumps(self.recorded)) if payload is None else payload
        if payload is None:
            release['git_sha']['value'] = revision
        self.release.write_text(json.dumps(release))
        result = subprocess.run([sys.executable, str(RUNNER), '--expected-merge', self.merge,
                                 '--repository', str(self.repo), '--release', str(self.release)],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def test_descendant(self):
        self.prove(self.descendant, 0)

    def test_equal(self):
        self.prove(self.merge, 0)

    def test_older(self):
        self.prove(self.older, 78)

    def test_unrelated(self):
        self.prove(self.unrelated, 78)

    def test_unknown(self):
        self.prove('f' * 40, 78)

    def test_recorded_real_release_through_parser(self):
        self.prove(None, 78, payload=self.recorded)

    def test_invalid_identity(self):
        for revision in [None, 'unknown', {'value': self.merge}]:
            with self.subTest(revision=revision):
                self.prove(revision, 78)

    def test_assertion_failure_only_on_containing_release(self):
        self.suite.write_text("import assert from 'node:assert/strict'; assert.equal(1, 2);\n")
        self.prove(self.merge, 1)
        self.prove(self.older, 78)


if __name__ == '__main__':
    unittest.main()
