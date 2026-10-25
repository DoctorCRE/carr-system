#!/usr/bin/env python3
"""Run the migration command against a synthetic transactional database adapter."""
import contextlib
import io
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import migrate
from migration_baseline import MigrationBaseline, runner_sha256


class DatabaseError(Exception):
    pass


class LockNotAvailable(DatabaseError):
    pass


class QueryCanceled(DatabaseError):
    pass


class Connection:
    def __init__(self, phase, errors):
        self.info = types.SimpleNamespace(host='localhost')
        self.phase = phase
        self.errors = iter(errors)
        self.staged = []
        self.committed = []
        self.rollbacks = 0
        self.attempts = 0

    def __enter__(self): return self
    def __exit__(self, *args): pass
    def cursor(self): return self
    def fetchone(self): return ('schema_migrations',)
    def fetchall(self): return list(BASELINE.migrations)

    def execute(self, sql, params=None):
        if sql.startswith('select fixture_second'):
            self.attempts += 1
            if self.phase == 'execute':
                error = next(self.errors, None)
                if error:
                    raise error('synthetic refusal')
        if sql.startswith('select fixture_') or params:
            self.staged.append((sql, params))
        if sql.endswith('; commit;'):
            self.committed.extend(self.staged)
            self.staged.clear()

    def commit(self):
        if self.staged and self.attempts and self.phase == 'commit':
            error = next(self.errors, None)
            if error:
                raise error('synthetic refusal')
        self.committed.extend(self.staged)
        self.staged.clear()

    def rollback(self):
        self.rollbacks += 1
        self.staged.clear()


BASELINE = MigrationBaseline.load(Path(__file__).resolve().parents[1])
MIGRATIONS = [(f'999{n}_fixture.sql', f'select fixture_{word}', runner_sha256(f'select fixture_{word}'))
              for n, word in enumerate(('prior', 'first', 'second'))]


class LockRetryTests(unittest.TestCase):
    def run_command(self, phase, errors, migrations=MIGRATIONS):
        conn = Connection(phase, errors)
        sleeps = []
        output, error_output = io.StringIO(), io.StringIO()
        def sleep(delay):
            self.assertEqual(conn.staged, [], 'rollback must release the batch before waiting')
            sleeps.append(delay)
        driver = types.SimpleNamespace(connect=lambda url: conn, Error=DatabaseError,
                                      errors=types.SimpleNamespace(LockNotAvailable=LockNotAvailable,
                                                                   QueryCanceled=QueryCanceled))
        with patch.dict(sys.modules, psycopg=driver), \
                patch.dict('os.environ', DATABASE_URL='postgresql://localhost/synthetic'), \
                patch.object(sys, 'argv', ['migrate.py', '--apply', '--yes']), \
                patch.object(migrate, 'load_migrations', return_value=migrations), \
                patch.object(migrate, 'load_baseline', return_value=BASELINE), \
                patch.object(migrate, 'ATOMIC_MIGRATION_GROUPS', ((MIGRATIONS[1][0], MIGRATIONS[2][0]),)), \
                patch('time.sleep', side_effect=sleep), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(error_output):
            try:
                migrate.main()
                status = 0
            except SystemExit as exc:
                status = exc.code
        return conn, sleeps, status, output.getvalue(), error_output.getvalue()

    def test_execute_lock_replays_whole_batch_without_replaying_prior_commit(self):
        conn, sleeps, status, output, _ = self.run_command('execute', [LockNotAvailable])
        self.assertEqual(status, 0)
        self.assertEqual(conn.attempts, 2)
        self.assertEqual(sleeps, [2])
        self.assertEqual([sql for sql, params in conn.committed if params is None],
                         ['select fixture_prior', 'select fixture_first', 'select fixture_second'])
        self.assertEqual([params for sql, params in conn.committed if params],
                         [(name, digest) for name, sql, digest in MIGRATIONS])
        self.assertEqual(output.count('atomic migration group committed:'), 1)

    def test_commit_lock_replays_whole_batch_without_duplicate_effects(self):
        conn, sleeps, status, _, _ = self.run_command('commit', [LockNotAvailable] * 3)
        self.assertEqual(status, 0)
        self.assertEqual(conn.attempts, 4)
        self.assertEqual(sleeps, [2, 5, 10])
        self.assertEqual(len(conn.committed), 6)
        self.assertEqual(conn.rollbacks, 3)

    def test_lock_exhaustion_preserves_execute_and_commit_failure_contracts(self):
        for phase, message in [('execute', 'could not acquire its lock'),
                               ('commit', 'commit refused for migration batch [9991_fixture.sql, 9992_fixture.sql] and the whole batch was rolled back')]:
            with self.subTest(phase=phase):
                conn, sleeps, status, _, error = self.run_command(phase, [LockNotAvailable] * 4)
                self.assertEqual(status, 1)
                self.assertEqual(conn.attempts, 4)
                self.assertEqual(conn.rollbacks, 4)
                self.assertEqual(sleeps, [2, 5, 10])
                self.assertEqual(len(conn.committed), 2)
                self.assertIn(message, error)
                if phase == 'execute':
                    self.assertIn('ABANDONED (its whole transaction batch was rolled back)', error)

    def test_transaction_control_batch_stops_at_first_lock_timeout(self):
        for phase in ('execute', 'commit'):
            for index in (1, 2):
                with self.subTest(phase=phase, transaction_file=index):
                    migrations = list(MIGRATIONS)
                    name, sql, _ = migrations[index]
                    sql += '; commit;'
                    migrations[index] = (name, sql, runner_sha256(sql))
                    conn, sleeps, status, _, _ = self.run_command(
                        phase, [LockNotAvailable], migrations)
                    self.assertEqual(status, 1)
                    self.assertEqual(conn.attempts, 1)
                    self.assertEqual(sleeps, [])
                    self.assertEqual(conn.rollbacks, 1)

    def test_other_failures_stop_without_retry(self):
        for phase, exception in [('commit', DatabaseError), ('execute', QueryCanceled)]:
            with self.subTest(phase=phase):
                conn, sleeps, status, _, error = self.run_command(phase, [exception])
                self.assertEqual(status, 1)
                self.assertEqual(conn.attempts, 1)
                self.assertEqual(sleeps, [])
                self.assertEqual(len(conn.committed), 2)
                self.assertIn('rolled back', error)


if __name__ == '__main__':
    unittest.main()
