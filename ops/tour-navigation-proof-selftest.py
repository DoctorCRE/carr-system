"""Synthetic production-proof contract and refusal checks."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('proof', Path(__file__).resolve().parents[1] / 'tools/prove-tour-navigation.py')
assert spec is not None and spec.loader is not None
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)


def detail():
    # Recorded from read_tour_internal_detail in the disposable Tour delivery fixture.
    return json.loads((Path(__file__).parent / 'fixtures/tour-navigation-detail.json').read_text())


ROUTE_ID = detail()['routes'][0]['id']


class ProofTests(unittest.TestCase):
    def test_ready_route(self):
        self.assertEqual(proof.check_route(detail(), ROUTE_ID)['stops'], 2)

    def test_near_miss_route_version(self):
        row = detail()
        row['routes'][0]['navigation']['evidence']['promotion']['route_version'] = 11
        with self.assertRaises(AssertionError):
            proof.check_route(row, ROUTE_ID)

    def test_unknown_and_null_evidence(self):
        with self.assertRaises(proof.Unavailable):
            proof.check_route(detail(), 'unknown')
        row = detail()
        row['routes'][0]['navigation'] = dict(contract_version='tour-navigation-evidence.v1', status='unavailable', reason='promotion_missing', evidence=None)
        with self.assertRaises(proof.Unavailable):
            proof.check_route(row, ROUTE_ID)

    def test_release_classification(self):
        for name, code in [('equal', 0), ('descendant', 0), ('older', 1), ('unrelated', 1), ('unknown', 128)]:
            with self.subTest(name=name):
                release = dict(ok=True, env={'value': 'production'}, git_sha={'value': 'a'*40})
                reply = SimpleNamespace(status=200, body=json.dumps(release).encode())
                with patch.object(proof, 'read', return_value=reply), patch.object(proof.subprocess, 'run', return_value=SimpleNamespace(returncode=code)), patch('sys.argv', ['proof', '--merge-sha', 'b'*40, '--tour-id', 'c'*36, '--route-id', 'd'*36]), contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(proof.main(), 78)
                reason = json.loads(output.getvalue())['reason']
                self.assertEqual('cookie jar' in reason, code == 0)

    def test_cli_pass_and_failure(self):
        route_id = ROUTE_ID
        for broken in (False, True):
            row = detail()
            if broken:
                row['routes'][0]['navigation']['evidence']['promotion']['route_version'] = 11
            release = dict(ok=True, env={'value': 'production'}, git_sha={'value': 'a'*40})
            replies = [SimpleNamespace(status=200, body=json.dumps(value).encode()) for value in (release, {'data': row})]
            with patch.object(proof, 'read', side_effect=replies), patch.object(proof.subprocess, 'run', return_value=SimpleNamespace(returncode=0)), patch.object(proof.http.cookiejar.MozillaCookieJar, 'load'), patch('sys.argv', ['proof', '--merge-sha', 'b'*40, '--tour-id', route_id, '--route-id', route_id, '--cookie-jar', '/tmp/synthetic-proof-cookies']), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(proof.main(), 1 if broken else 0)
            self.assertEqual(json.loads(output.getvalue())['status'], 'failed' if broken else 'passed')


if __name__ == '__main__':
    unittest.main()
