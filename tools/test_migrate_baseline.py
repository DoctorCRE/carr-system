"""Runner behavior when the immutable historical SQL is absent."""
import contextlib
import io
import os
import sys
import types
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import migrate
from migration_baseline import MigrationBaseline, runner_sha256

ROOT = Path(__file__).resolve().parents[1]


class RunnerBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = MigrationBaseline.load(ROOT)
        cls.applied = dict(cls.baseline.migrations)
        cls.suffix = ('0889_future.sql', 'select 1;\n', runner_sha256('select 1;\n'))

    def test_covered_files_need_no_executable_placeholder(self):
        migrate.validate_applied_ledger([self.suffix], self.applied, self.baseline)
        self.assertEqual(migrate.pending_migrations([self.suffix], self.applied), [self.suffix])
        self.assertEqual(len(self.applied), len(self.baseline.migrations))

    def test_missing_original_coverage_refuses_before_any_execution(self):
        applied = dict(self.applied)
        applied.pop(self.baseline.cutoff)
        with self.assertRaisesRegex(migrate.AppliedMigrationLedgerError, 'missing applied coverage'):
            migrate.validate_applied_ledger([self.suffix], applied, self.baseline)

    def test_historical_hash_drift_and_unknown_ledger_rows_still_refuse(self):
        applied = dict(self.applied)
        applied[self.baseline.cutoff] = 'f' * 64
        with self.assertRaisesRegex(migrate.AppliedMigrationLedgerError, 'checksum differs'):
            migrate.validate_applied_ledger([self.suffix], applied, self.baseline)
        applied = dict(self.applied, **{'0000_baseline.sql': 'f' * 64})
        with self.assertRaisesRegex(migrate.AppliedMigrationLedgerError, 'missing from the tree'):
            migrate.validate_applied_ledger([self.suffix], applied, self.baseline)

    def test_logical_prefix_checks_include_absent_history(self):
        applied = dict(self.applied, **{'0890_later.sql': runner_sha256('select 2;')})
        later = ('0890_later.sql', 'select 2;', runner_sha256('select 2;'))
        with self.assertRaisesRegex(migrate.AppliedMigrationLedgerError, 'reordered'):
            migrate.validate_applied_ledger([self.suffix, later], applied, self.baseline)

    def test_loader_validates_manifest_union_without_synthesizing_sql(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / 'migrations').mkdir()
            (repo / 'db').mkdir()
            (repo / 'db/migration-baseline.json').write_bytes((ROOT / 'db/migration-baseline.json').read_bytes())
            with patch.object(migrate, 'MIGRATIONS_DIR', repo / 'migrations'):
                self.assertEqual(migrate.load_migrations(), [])
                (repo / 'migrations' / self.suffix[0]).write_text(self.suffix[1])
                self.assertEqual(migrate.load_migrations(), [self.suffix])
                (repo / 'migrations' / self.baseline.cutoff).write_text('select 0;')
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    migrate.load_migrations()

    def test_through_accepts_an_exact_covered_filename_without_executable_sql(self):
        selected, held = migrate.migrations_through(
            [self.suffix], [self.suffix], self.baseline.cutoff, self.baseline)
        self.assertEqual(selected, [])
        self.assertEqual(held, [self.suffix])

    def test_complete_production_shape_is_a_read_only_no_op(self):
        class Connection:
            def __init__(self, applied):
                self.info = types.SimpleNamespace(host='localhost')
                self.applied = applied
                self.statements = []
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def cursor(self): return self
            def execute(self, statement): self.statements.append(statement)
            def fetchone(self): return ('schema_migrations',)
            def fetchall(self): return list(self.applied.items())

        for applied, refuses in ((self.applied, False), ({}, True)):
            conn = Connection(applied)
            with patch.dict(sys.modules, psycopg=types.SimpleNamespace(connect=lambda url: conn)), \
                    patch.dict(os.environ, DATABASE_URL='postgresql://localhost/carr_test'), \
                    patch.object(sys, 'argv', ['migrate.py', '--apply', '--yes']), \
                    patch.object(migrate, 'load_migrations', return_value=[]), \
                    patch.object(migrate, 'load_baseline', return_value=self.baseline), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                if refuses:
                    with self.assertRaises(SystemExit): migrate.main()
                else:
                    migrate.main()
            self.assertEqual(conn.statements, ["select to_regclass('schema_migrations')",
                                               'select filename, sha256 from schema_migrations'])

    def test_retained_history_can_still_replay_in_isolated_upgrade_fixtures(self):
        migration = ('0888_supersede_triaged_work_request.sql', 'select 1;', runner_sha256('select 1;'))
        migrate.validate_applied_ledger([migration], {})
        self.assertEqual(migrate.pending_migrations([migration], {}), [migration])


if __name__ == '__main__':
    unittest.main()
