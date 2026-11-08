"""Exercise the release-aware proof CLI with recorded registered responses."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'mcp-server/contracts/fixtures/action-card-proof.v2.json'
CLI = ROOT / 'tools/prove-action-card-targets.py'


class ExactCardProofTests(unittest.TestCase):
    def invoke(self, change=None, expected='a' * 40):
        fixture = json.loads(FIXTURE.read_text())
        if change:
            change(fixture)
        with tempfile.TemporaryDirectory(prefix='action-card-proof-') as folder:
            path = Path(folder) / 'fixture.json'
            path.write_text(json.dumps(fixture))
            result = subprocess.run([sys.executable, str(CLI), '--expected-merge', expected,
                                     '--fixture', str(path)], capture_output=True, text=True, timeout=15)
            return result.returncode, json.loads(result.stdout)

    def test_containing_equal_release_passes_recorded_real_contracts(self):
        code, receipt = self.invoke()
        self.assertEqual(code, 0)
        self.assertTrue(receipt['fixture'])

    def test_containing_descendant_release_passes(self):
        def descendant(value):
            value['release']['git_sha']['value'] = 'b' * 40
            value['ancestors'] = ['a' * 40]
        self.assertEqual(self.invoke(descendant)[0], 0)

    def test_older_unrelated_and_unknown_releases_are_insufficient(self):
        for observed in ['b' * 40, 'c' * 40, None, 'unknown']:
            with self.subTest(observed=observed):
                self.assertEqual(self.invoke(lambda v: v['release']['git_sha'].update(value=observed))[0], 78)

    def test_exact_source_id_near_miss_is_assertion_failure(self):
        def mismatch(value):
            value['targets'][0]['id'] = value['targets'][0]['id'][:-1] + '2'
        self.assertEqual(self.invoke(mismatch)[0], 1)

    def test_exact_version_near_miss_is_assertion_failure(self):
        self.assertEqual(self.invoke(lambda v: v['targets'][0].update(source_version=999))[0], 1)

    def test_foreign_and_unknown_disclosure_difference_is_failure(self):
        self.assertEqual(self.invoke(lambda v: v['foreign'].update(detail='private'))[0], 1)

    def test_invalid_registered_response_is_failure(self):
        self.assertEqual(self.invoke(lambda v: v['responses'][0].update(schema_version='unknown'))[0], 1)

    def test_short_total_deadline_exits_insufficient(self):
        result = subprocess.run([sys.executable, str(CLI), '--expected-merge', 'a' * 40,
            '--fixture', str(FIXTURE), '--deadline-seconds', '.1'], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 78)


if __name__ == '__main__':
    unittest.main()
