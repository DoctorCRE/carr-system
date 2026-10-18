import copy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('audit', Path(__file__).parents[1] / 'tools/ci/ci-failure-audit.py')
assert spec is not None and spec.loader is not None
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.row = {'databaseId': 42, 'attempt': 1, 'headSha': 'a' * 40,
                    'conclusion': 'failure'}
        self.calls = []
        self.race = False
        self.date = datetime(2026, 10, 15, tzinfo=timezone.utc)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name)

    def gh(self, command, **kwargs):
        self.calls.append(command)
        if command[1:3] == ['run', 'list']:
            output = json.dumps([self.row])
        elif '--json' in command:
            row = copy.deepcopy(self.row)
            requested = int(command[command.index('--attempt') + 1]) if '--attempt' in command else row['attempt']
            row['attempt'] = requested + 1 if self.race else requested
            output = json.dumps(row)
        else:
            attempt = command[command.index('--attempt') + 1] if '--attempt' in command else str(self.row['attempt'])
            output = 'failure evidence for attempt ' + attempt
        return subprocess.CompletedProcess(command, 0, output, '')

    def collect(self):
        with patch.object(audit.subprocess, 'run', side_effect=self.gh):
            return audit.collect('owner/repo', self.date, self.date, self.cache, workers=1)

    def test_two_attempts_keep_distinct_evidence_and_fetch_exact_attempt(self):
        self.collect()
        self.row['attempt'] = 2
        self.collect()
        for attempt in (1, 2):
            target = self.cache / 'owner-repo' / '42' / str(attempt)
            self.assertEqual((target / 'failed.log').read_text(), f'failure evidence for attempt {attempt}')
            self.assertEqual(json.loads((target / 'failed.json').read_text())['attempt'], attempt)
        views = [call for call in self.calls if call[1:3] == ['run', 'view']]
        self.assertTrue(all('--attempt' in call for call in views))

    def test_inventory_to_fetch_attempt_race_is_rejected(self):
        self.race = True
        with self.assertRaisesRegex(RuntimeError, 'identity'):
            self.collect()
        self.assertFalse(list(self.cache.glob('owner-repo/42/*/failed.log')))

    def test_cached_log_digest_is_verified_before_reuse(self):
        self.collect()
        next(self.cache.glob('owner-repo/42/**/failed.log')).write_text('changed evidence')
        self.calls.clear()
        with self.assertRaisesRegex(RuntimeError, 'digest'):
            self.collect()
        self.assertFalse(any(call[1:3] == ['run', 'view'] for call in self.calls))

    def test_cached_metadata_sha_is_verified_against_inventory(self):
        self.collect()
        self.row['headSha'] = 'b' * 40
        with self.assertRaisesRegex(RuntimeError, 'identity'):
            self.collect()

    def test_cached_metadata_digest_is_verified_before_reuse(self):
        self.collect()
        next(self.cache.glob('owner-repo/42/**/failed.json')).write_text('{}')
        with self.assertRaisesRegex(RuntimeError, 'digest'):
            self.collect()

    def test_failed_log_download_does_not_publish_partial_entry(self):
        original = self.gh
        def interrupted(command, **kwargs):
            if '--log-failed' in command:
                raise subprocess.TimeoutExpired(command, 180)
            return original(command, **kwargs)
        with patch.object(audit.subprocess, 'run', side_effect=interrupted):
            with self.assertRaises(subprocess.TimeoutExpired):
                audit.collect('owner/repo', self.date, self.date, self.cache, workers=1)
        self.assertFalse((self.cache / 'owner-repo/42/1').exists())


if __name__ == '__main__':
    unittest.main()
