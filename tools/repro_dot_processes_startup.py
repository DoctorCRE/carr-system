#!/usr/bin/env python3
"""Replay a child that gets no CPU before run_bounded reaches its deadline."""
from contextlib import ExitStack
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch


def main():
    spec = importlib.util.spec_from_file_location('dot_process_tests', Path(__file__).with_name('test_dot_processes.py'))
    tests = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tests)
    processes = tests.processes
    baseline = processes._inventory()
    # An unscheduled child owns a pipe but has not written its inventory file.
    with tempfile.TemporaryFile() as stdout, ExitStack() as stack:
        child = Mock(pid=700099, stdout=stdout, returncode=-9)
        stack.enter_context(patch.object(subprocess, 'Popen', return_value=child))
        stack.enter_context(patch.object(processes, '_inventory', return_value=baseline))
        stack.enter_context(patch.object(processes, '_process', side_effect=baseline.get))
        stack.enter_context(patch.object(processes.time, 'monotonic', side_effect=[0, 1]))
        suite = unittest.TestSuite([tests.BoundedProcessTests(
            'test_immediate_orphan_initially_at_scratch_with_fixture_inventory')])
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
