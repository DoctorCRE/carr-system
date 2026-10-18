"""Offline regressions for the link bake-off's repository and monitor contracts."""
import importlib.util
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / 'tools/link-model-bakeoff'
sys.path.insert(0, str(HARNESS))
import bench


class OfflineRegressions(unittest.TestCase):
    def test_migration_module_does_not_shadow_production(self):
        self.assertFalse((HARNESS / 'migrate.py').exists())
        self.assertIn('from linkfork_migration import rehearsal', (HARNESS / 'run.py').read_text())

    def test_sql_selftest_has_a_reviewable_ci_route(self):
        spec = importlib.util.spec_from_file_location('collector', ROOT / 'ops/ci-selftest.py')
        collector = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(collector)
        route = 'tools/link-model-bakeoff/pg18-selftest.py'
        self.assertTrue((ROOT / route).is_file())
        self.assertIsNotNone(collector.TEST_FILE_NAME.fullmatch('pg18-selftest.py'))
        self.assertIn(route, collector.UNCOLLECTED_BY_DECISION)
        self.assertIn('PostgreSQL 18', collector.UNCOLLECTED_BY_DECISION[route])

    def test_monitor_failures_reach_the_caller(self):
        class Writer:
            autocommit = True
            def __enter__(self):
                return self
            def __exit__(self, *_):
                pass
            def transaction(self):
                return self
            def execute(self, *_):
                time.sleep(.001)

        class BrokenMonitor(Writer):
            def execute(self, *_):
                raise OSError('sampling failed')

        class Connections:
            def __init__(self, connect_fails):
                self.connect_fails = connect_fails
            def connect(self, application_name='linkfork'):
                if application_name == 'linkfork-monitor':
                    if self.connect_fails:
                        raise OSError('monitor connect failed')
                    return BrokenMonitor()
                return Writer()

        def cycle(*_):
            time.sleep(.003)
            return [1, 1, 1]

        for connect_fails in (True, False):
            with self.subTest(connect_fails=connect_fails):
                with patch.object(bench, 'write_cycle', cycle):
                    with self.assertRaisesRegex(RuntimeError, 'lock monitor'):
                        bench.writes(Connections(connect_fails), {'doctrine_section':[None]}, count=1, repeats=1)


if __name__ == '__main__':
    unittest.main()
