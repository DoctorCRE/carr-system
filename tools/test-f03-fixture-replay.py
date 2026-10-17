#!/usr/bin/env python3
"""F03 lane binding must not replay the complete acceptance corpus."""
import importlib.util
import pathlib
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch, MagicMock

REPO = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('f03_production', REPO / 'tools/test-f03-production-migration.py')
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class FixtureReplayTests(unittest.TestCase):
    def test_preflight_has_setup_and_rollback_without_the_case_corpus(self):
        lane = ('WR-SYNTHETIC', uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
        for path in module.POSTGRES_FIXTURES:
            with self.subTest(path=path.name):
                sql = module.binding_preflight(module.rendered_fixture(path, lane), path)
                self.assertIn("begin;", sql)
                self.assertTrue(sql.endswith("rollback;\n"))
                self.assertNotIn("create temporary table f03_seam_case", sql)
                self.assertNotIn("create temporary table f03p_state_case", sql)
                self.assertNotIn("REPLACE-WITH-SCRATCH", sql)

    def test_bind_digest_before_running_full_corpus_once(self):
        lane = ('WR-SYNTHETIC', uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
        digest = 'sha256:' + 'a' * 64
        bound = False
        full_runs = 0

        def run(command, **kwargs):
            nonlocal bound, full_runs
            if '-q' in command:
                return subprocess.CompletedProcess(command, 0,
                    'lane-digest-mismatch: set the lane job payload plan_digest to ' + digest, '')
            full_runs += 1
            output = 'All F03 cases matched' if bound else (
                'PART B SKIPPED: set the lane job payload plan_digest to ' + digest)
            return subprocess.CompletedProcess(command, 0, output, '')

        connection = MagicMock()
        def update(*args, **kwargs):
            nonlocal bound
            bound = True
        connection.__enter__.return_value.execute.side_effect = update
        with patch.object(module.subprocess, 'run', side_effect=run), patch('psycopg.connect', return_value=connection):
            module.run_sql_fixture('postgresql://carr_ci@127.0.0.1:55440/carr_ci', 'psql',
                                   module.POSTGRES_FIXTURES[0], lane)
        self.assertEqual(full_runs, 1, 'digest discovery must not repeat the full corpus')


if __name__ == '__main__':
    unittest.main()
