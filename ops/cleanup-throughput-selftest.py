"""Run the full cleanup fixture at one-tenth scale within CI's test budget."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('cleanup_scale',
    Path(__file__).resolve().parent / 'fixtures/cleanup-throughput.py')
assert spec is not None and spec.loader is not None
scenario = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scenario)


def load_tests(loader, tests, pattern):
    scenario.CleanupThroughputTests.clone_count = 30
    scenario.CleanupThroughputTests.worktree_count = 40
    scenario.CleanupThroughputTests.budget_scale = 10
    return loader.loadTestsFromModule(scenario)


if __name__ == '__main__':
    unittest.main()
