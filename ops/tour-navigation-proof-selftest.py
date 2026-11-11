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
    source = dict(coordinate_role='entrance', review_state='reviewed', retrieval_status='read',
                  native_navigation_proof={'status': 'passed'})
    for field in ('coordinate_candidate_id', 'entrance_verification_receipt_id', 'source_evidence_id', 'rights_receipt_id', 'evidence_reference'):
        source[field] = 'synthetic-source'
    evidence = dict(route_version_id='route', route_version=1, projection_id='projection', projection_digest='digest',
                    promotion=dict(route_version=1, receipt_id='receipt', receipt_digest='digest', component_registry_version='component', canonical_dataset_version='dataset'),
                    stops=[dict(route_stop_id='stop', property_id='property', position=dict(latitude=30.421, longitude=-87.216), source=source)])
    return dict(route_version=1, projections=[dict(id='projection', route_version=1, projection_digest='digest')],
                routes=[dict(id='route', route_version=1, accepted=True, stops=[dict(id='stop', property_id='property', stop_state='active')],
                             navigation=dict(contract_version='tour-navigation-evidence.v1', status='ready', reason=None, evidence=evidence))])


class ProofTests(unittest.TestCase):
    def test_ready_route(self):
        self.assertEqual(proof.check_route(detail(), 'route')['stops'], 1)

    def test_near_miss_route_version(self):
        row = detail()
        row['routes'][0]['navigation']['evidence']['promotion']['route_version'] = 11
        with self.assertRaises(AssertionError):
            proof.check_route(row, 'route')

    def test_unknown_and_null_evidence(self):
        with self.assertRaises(proof.Unavailable):
            proof.check_route(detail(), 'unknown')
        row = detail()
        row['routes'][0]['navigation'] = dict(contract_version='tour-navigation-evidence.v1', status='unavailable', reason='promotion_missing', evidence=None)
        with self.assertRaises(proof.Unavailable):
            proof.check_route(row, 'route')

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
        route_id = '00000000-0000-4000-8000-000000000001'
        for broken in (False, True):
            row = json.loads(json.dumps(detail()).replace('"route"', json.dumps(route_id)))
            if broken:
                row['routes'][0]['navigation']['evidence']['promotion']['route_version'] = 11
            release = dict(ok=True, env={'value': 'production'}, git_sha={'value': 'a'*40})
            replies = [SimpleNamespace(status=200, body=json.dumps(value).encode()) for value in (release, {'data': row})]
            with patch.object(proof, 'read', side_effect=replies), patch.object(proof.subprocess, 'run', return_value=SimpleNamespace(returncode=0)), patch.object(proof.http.cookiejar.MozillaCookieJar, 'load'), patch('sys.argv', ['proof', '--merge-sha', 'b'*40, '--tour-id', route_id, '--route-id', route_id, '--cookie-jar', '/tmp/synthetic-proof-cookies']), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(proof.main(), 1 if broken else 0)
            self.assertEqual(json.loads(output.getvalue())['status'], 'failed' if broken else 'passed')


if __name__ == '__main__':
    unittest.main()
