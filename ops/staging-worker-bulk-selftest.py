#!/usr/bin/env python3
"""Replay staging Worker bulk publication failures without provider access."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("staging_bulk_provision", ROOT / "tools/provision-staging-app-writer.py")
assert spec is not None and spec.loader is not None
p = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = p
spec.loader.exec_module(p)

TRANSIENT = "Internal authentication error: internal server error [code: 10500]"


class BulkPublication(unittest.TestCase):
    def setUp(self):
        self.values = {name: "synthetic-candidate" for name in p.WORKER_DATABASE_SECRET_NAMES}

    def bulk(self, run):
        p.bulk_worker_database_secrets(self.values, wrangler="wrangler", run=run, environ={})

    def test_transient_provider_failure_replays_identical_payload_before_cutover_continues(self):
        run = Mock(side_effect=[subprocess.CompletedProcess([], 1, "", TRANSIENT),
                                subprocess.CompletedProcess([], 0, "", "")])
        verify = Mock()
        with patch("time.sleep") as sleep:
            p.publish_worker_cutover(
                self.values, {name: None for name in self.values}, preserve=lambda: None,
                bulk=lambda values: p.bulk_worker_database_secrets(
                    values, wrangler="wrangler", run=run, environ={}), verify=verify,
            )
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0], run.call_args_list[1])
        sleep.assert_called_once_with(2)
        verify.assert_called_once_with()

    def test_repeated_internal_error_stops_after_one_retry_without_leaking_output(self):
        run = Mock(return_value=subprocess.CompletedProcess([], 1, "private-sentinel", TRANSIENT))
        with patch("time.sleep") as sleep:
            with self.assertRaises(p.ProvisioningRefusal) as refused:
                self.bulk(run)
        self.assertEqual(run.call_count, 2)
        sleep.assert_called_once_with(2)
        self.assertNotIn("private-sentinel", str(refused.exception))
        self.assertNotIn(TRANSIENT, str(refused.exception))

    def test_unknown_authentication_and_mixed_errors_are_not_retried(self):
        for output in ["unknown failure", "authentication error [code: 10000]",
                       TRANSIENT + "\nauthentication error [code: 10000]"]:
            with self.subTest(output=output), patch("time.sleep") as sleep:
                run = Mock(return_value=subprocess.CompletedProcess([], 1, "", output))
                with self.assertRaises(p.ProvisioningRefusal):
                    self.bulk(run)
                self.assertEqual(run.call_count, 1)
                sleep.assert_not_called()

    def test_uncertain_timeout_is_not_retried(self):
        run = Mock(side_effect=subprocess.TimeoutExpired([], 60, output="private-sentinel"))
        with patch("time.sleep") as sleep:
            with self.assertRaises(p.ProvisioningRefusal) as refused:
                self.bulk(run)
        self.assertEqual(run.call_count, 1)
        sleep.assert_not_called()
        self.assertNotIn("private-sentinel", str(refused.exception))


if __name__ == "__main__":
    unittest.main()
