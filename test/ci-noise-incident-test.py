import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

spec = importlib.util.spec_from_file_location('incident', Path(__file__).parents[1] / 'ops/ci-noise-incident.py')
assert spec is not None and spec.loader is not None
incident = importlib.util.module_from_spec(spec)
spec.loader.exec_module(incident)


class Issues:
    def __init__(self):
        self.rows = []
        self.mutations = []

    def __call__(self, path, payload=None):
        if '/labels' in path:
            return [{'name': 'ci-edge-liveness'}]
        if payload is None:
            return self.rows
        self.mutations.append((path, payload))
        if path.endswith('/issues'):
            row = {'number': len(self.rows) + 1, 'state': 'open',
                   'user': {'id': 41898282, 'login': 'github-actions[bot]', 'type': 'Bot'}, **payload}
            row['labels'] = [{'name': label} for label in row.get('labels', [])]
            self.rows.append(row)
        else:
            row = next(row for row in self.rows if path.endswith('/' + str(row['number'])))
            row.update(payload)
        if 'assignees' in payload:
            row['assignees'] = [{'login': login} for login in payload['assignees']]
        return row


class IncidentTests(unittest.TestCase):
    def test_outage_updates_once_recovers_and_reopens_without_comments(self):
        api = Issues()
        publish = lambda code: incident.publish('owner/repo', code, 'probe evidence', 'https://example.test/run', api)
        self.assertEqual(publish(0), {'state': 'healthy', 'issue': None, 'probe_exit': 0})
        for _ in range(24):
            self.assertEqual(publish(1)['probe_exit'], 1)
        self.assertEqual(len(api.rows), 1)
        self.assertEqual(sum(path.endswith('/issues') for path, _ in api.mutations), 1)
        self.assertFalse(any('/comments' in path for path, _ in api.mutations))
        self.assertIn('ALARM', api.rows[0]['body'])
        publish(78)
        self.assertIn('BLIND', api.rows[0]['body'])
        publish(0)
        self.assertEqual(api.rows[0]['state'], 'closed')
        count = len(api.mutations)
        publish(0)
        self.assertEqual(len(api.mutations), count)
        publish(1)
        self.assertEqual(api.rows[0]['state'], 'open')
        self.assertEqual(len(api.rows), 1)

    def test_delivery_errors_and_unexpected_probe_exits_stay_failed(self):
        def unavailable(*args):
            raise RuntimeError('GitHub unavailable')
        with self.assertRaisesRegex(RuntimeError, 'GitHub unavailable'):
            incident.publish('owner/repo', 1, '', '', unavailable)
        with self.assertRaises(ValueError):
            incident.publish('owner/repo', 2, '', '', Issues())

    def test_duplicate_markers_refuse_silent_selection(self):
        api = Issues()
        incident.publish('owner/repo', 1, '', '', api)
        api.rows.append({**api.rows[0], 'number': 2})
        api.mutations.clear()
        with self.assertRaisesRegex(RuntimeError, 'duplicate'):
            incident.publish('owner/repo', 1, '', '', api)
        self.assertEqual(api.mutations, [])

    def test_foreign_issue_text_and_label_cannot_claim_incident(self):
        for code in (0, 1):
            with self.subTest(code=code):
                api = Issues()
                foreign = {'number': 99, 'title': incident.TITLE, 'body': incident.MARKER,
                           'state': 'open', 'assignees': [],
                           'labels': [{'name': 'ci-edge-liveness'}],
                           'user': {'id': 123, 'login': 'someone', 'type': 'User'}}
                api.rows.append(foreign.copy())
                result = incident.publish('owner/repo', code, '', '', api)
                self.assertEqual(api.rows[0], foreign)
                self.assertNotEqual(result['issue'], 99)

    def test_trusted_issue_assignment_is_restored(self):
        api = Issues()
        incident.publish('owner/repo', 1, '', '', api)
        api.rows[0]['assignees'] = []
        incident.publish('owner/repo', 1, '', '', api)
        self.assertEqual(api.mutations[-1][1]['assignees'], ['jbookout'])

    def run_main(self, probe):
        with tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            try:
                os.chdir(directory)
                with patch.dict(os.environ, {'GITHUB_REPOSITORY': 'owner/repo',
                                             'GITHUB_SERVER_URL': 'https://github.com',
                                             'GITHUB_RUN_ID': '123'}), \
                     patch.object(incident.sys, 'argv', ['ci-noise-incident.py']), \
                     patch.object(incident.subprocess, 'run', return_value=probe,
                                  side_effect=probe if isinstance(probe, Exception) else None), \
                     patch.object(incident, 'publish') as publish, \
                     contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    publish.return_value = {'issue': 1}
                    code = incident.main()
                artifact = json.loads(Path('edge-liveness-probe.json').read_text())
                return code, artifact, publish
            finally:
                os.chdir(previous)

    def test_main_rejects_exit_one_traceback_without_completed_verdict(self):
        code, artifact, publish = self.run_main(subprocess.CompletedProcess(
            [], 1, '', 'Traceback: UndefinedTable secret-dsn'))
        self.assertNotEqual(code, 0)
        self.assertEqual(artifact['status'], 'ERROR')
        self.assertNotIn('secret-dsn', json.dumps(artifact))
        publish.assert_not_called()

    def test_main_normalizes_only_a_completed_alarm(self):
        verdict = {'schema': 'edge-liveness-verdict/v1', 'probe_exit': 1, 'status': 'ALARM'}
        code, artifact, publish = self.run_main(subprocess.CompletedProcess(
            [], 1, json.dumps(verdict), 'private diagnostic'))
        self.assertEqual(code, 0)
        self.assertEqual(artifact['status'], 'ALARM')
        self.assertNotIn('private diagnostic', json.dumps(artifact))
        publish.assert_called_once()

    def test_main_rejects_mismatched_and_malformed_verdicts(self):
        for stdout in ('[]', '{}', 'not json', json.dumps({
                'schema': 'edge-liveness-verdict/v1', 'probe_exit': 0, 'status': 'HEALTHY'})):
            with self.subTest(stdout=stdout):
                code, artifact, publish = self.run_main(subprocess.CompletedProcess([], 1, stdout, ''))
                self.assertEqual(code, 1)
                self.assertEqual(artifact['status'], 'ERROR')
                publish.assert_not_called()

    def test_timeout_retains_unknown_artifact_and_fails(self):
        code, artifact, publish = self.run_main(subprocess.TimeoutExpired('probe', 180))
        self.assertEqual(code, 1)
        self.assertEqual(artifact['status'], 'ERROR')
        self.assertIsNone(artifact['probe_exit'])
        publish.assert_not_called()

    def test_pagination_finds_trusted_issue_on_later_page(self):
        api = Issues()
        incident.publish('owner/repo', 1, '', '', api)
        paths = []
        def paginated(path, payload=None):
            paths.append(path)
            if payload is None and '/issues?' in path and path.endswith('page=1'):
                return [{'user': {'id': 123}} for _ in range(100)]
            return api(path, payload)
        self.assertEqual(incident.publish('owner/repo', 1, '', '', paginated)['issue'], 1)
        self.assertTrue(any(path.endswith('page=2') for path in paths))
        self.assertEqual(len(api.rows), 1)

    def test_malformed_issue_response_fails_delivery(self):
        for response in ({'message': 'not a list'}, [None]):
            with self.subTest(response=response), self.assertRaisesRegex(RuntimeError, 'malformed'):
                incident.publish('owner/repo', 1, '', '', lambda path: response)

    def test_real_probe_process_distinguishes_query_crash_and_completed_alarm(self):
        runner = Path(__file__).parents[1] / 'ops/ci-noise-probe.py'
        for query in ('raise RuntimeError("query failed")', 'pass'):
            with self.subTest(query=query), tempfile.TemporaryDirectory() as directory:
                Path(directory, 'psycopg.py').write_text(
                    'class Connection:\n'
                    ' def __enter__(self): return self\n'
                    ' def __exit__(self, *args): pass\n'
                    ' def cursor(self): return self\n'
                    f' def execute(self, *args): {query}\n'
                    ' def fetchall(self): return [("service", "production", "high", "launchd", '
                    '"stale", "bad", "observed", 24)]\n'
                    'def connect(*args, **kwargs): return Connection()\n')
                result = subprocess.run([sys.executable, str(runner)], capture_output=True, text=True,
                                        env={**os.environ, 'PYTHONPATH': directory,
                                             'LIVENESS_DATABASE_URL': 'fixture'}, timeout=10)
                self.assertEqual(result.returncode, 1)
                if query == 'pass':
                    self.assertEqual(json.loads(result.stdout), {
                        'schema': 'edge-liveness-verdict/v1', 'probe_exit': 1, 'status': 'ALARM'})
                else:
                    self.assertEqual(result.stdout, '')
                    self.assertIn('Traceback', result.stderr)

    def test_edge_workflow_keeps_read_only_defaults(self):
        from tools.test_workflow_checks_write_grant import violations
        workflow = (Path(__file__).parents[1] / '.github/workflows/edge-liveness.yml').read_text()
        self.assertEqual(violations('edge-liveness.yml', workflow), [])
        self.assertIn('    permissions:\n      contents: read\n      issues: write', workflow)


if __name__ == '__main__':
    unittest.main()
