import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('incident', Path(__file__).parents[1] / 'ops/ci-noise-incident.py')
incident = importlib.util.module_from_spec(spec)
spec.loader.exec_module(incident)


class Issues:
    def __init__(self):
        self.rows = []
        self.mutations = []

    def __call__(self, path, payload=None):
        if payload is None:
            return self.rows
        self.mutations.append((path, payload))
        if path.endswith('/issues'):
            row = {'number': len(self.rows) + 1, 'state': 'open', **payload}
            self.rows.append(row)
        else:
            row = self.rows[0]
            row.update(payload)
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
        api.rows = [{'number': n, 'title': incident.TITLE, 'body': incident.MARKER, 'state': 'open'} for n in [1, 2]]
        with self.assertRaisesRegex(RuntimeError, 'duplicate'):
            incident.publish('owner/repo', 1, '', '', api)
        self.assertEqual(api.mutations, [])


if __name__ == '__main__':
    unittest.main()
