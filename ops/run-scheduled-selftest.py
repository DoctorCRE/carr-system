#!/usr/bin/env python3
"""Exercise the scheduled wrapper and durable spool through the real CLI."""
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ScheduledRunTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ("bin/run-scheduled.sh", "tools/ops-spool.py"):
            target = self.root / name
            target.parent.mkdir(exist_ok=True)
            shutil.copy2(ROOT / name, target)
        (self.root / ".venv/bin").mkdir(parents=True)
        (self.root / ".venv/bin/python").symlink_to(sys.executable)
        self.spool = self.root / "out/run-spool.sqlite3"
        self.env = {"HOME": str(self.root), "PATH": os.environ["PATH"],
                    "CARR_RUN_SPOOL_DB": str(self.spool)}

    def invoke(self, code, options=()):
        return subprocess.run(["/bin/zsh", str(self.root / "bin/run-scheduled.sh"), *options,
                               "test-service", "launchd.run", "/bin/sh", "-c",
                               f"printf child-out; printf child-err >&2; exit {code}"],
                              cwd=self.root, env=self.env, text=True, capture_output=True, timeout=15)

    def invoke_intake(self, receipt, code=1, router_lines=()):
        child = self.root / "child.py"
        child.write_text("import sys\n"
                         "print('SYNTHETIC_PRIVATE_BODY')\n"
                         "print('synthetic intake failure', file=sys.stderr)\n"
                         f"print({('outlook-intake-receipt ' + json.dumps(receipt))!r})\n"
                         + ''.join(f"print({line!r})\n" for line in router_lines)
                         + f"sys.exit({code})\n")
        return subprocess.run(["/bin/zsh", str(self.root / "bin/run-scheduled.sh"),
                               "outlook-intake", "outlook.intake", sys.executable, str(child)],
                              cwd=self.root, env=self.env, text=True, capture_output=True, timeout=15)

    def test_failed_intake_stderr_is_appended_to_existing_log(self):
        log = self.root / "out/run-scheduled.log"
        log.parent.mkdir()
        log.write_text("previous run\n")
        result = self.invoke_intake({"state": "failed", "reason": "writes_failed", "counts": {}})
        self.assertEqual(result.returncode, 1)
        self.assertIn("previous run\nsynthetic intake failure\n", log.read_text())

    def test_intake_receipt_preserves_reason_and_counts_without_private_output(self):
        result = self.invoke_intake({"state": "failed", "reason": "writes_failed",
                                     "counts": {"writes_failed": 3, "unresolved_messages": 2,
                                                "unresolved_addresses": 4, "unresolved_calendar": 1, "error_timeout": 3}})
        args = json.loads(self.rows()[0][3])
        self.assertEqual(args[args.index("--failure-class") + 1], "writes_failed")
        detail = json.loads(args[args.index("--detail") + 1])
        self.assertEqual(detail, {"state": "failed", "reason": "writes_failed", "counts": {
            "writes_failed": 3, "unresolved_messages": 2, "unresolved_addresses": 4,
            "unresolved_calendar": 1, "error_timeout": 3}})
        self.assertEqual(result.returncode, 1)
        observed = (result.stdout + result.stderr + json.dumps(self.rows())
                    + (self.root / "out/run-scheduled.log").read_text())
        self.assertNotIn("SYNTHETIC_PRIVATE", observed)
        self.assertFalse(list((self.root / "out").glob("outlook-intake-receipt.*")))

    def test_decoder_degradation_records_one_failure_until_recovery(self):
        receipt = {"state": "degraded", "reason": "decoder_degraded", "counts": {"decoder_alarm": 1}}
        first = self.invoke_intake(receipt, code=1)
        self.assertEqual(first.returncode, 1)
        args = json.loads(self.rows()[-1][3])
        self.assertEqual(args[args.index("--failure-class") + 1], "decoder_degraded")
        self.assertEqual(json.loads(args[args.index("--detail") + 1]), receipt)
        receipt["counts"]["decoder_alarm"] = 0
        second = self.invoke_intake(receipt, code=0)
        self.assertEqual(second.returncode, 0)
        self.assertEqual(len(self.rows()), 1)
        self.assertIn('"state": "degraded"', second.stdout)

    def test_calendar_receipts_preserve_counts_and_failure_class(self):
        for reason, code in (('writes_failed', 1), ('calendar_lifecycle_unsupported', 0)):
            receipt = {'state': 'failed' if code else 'succeeded', 'reason': reason,
                       'counts': {'calendar_lifecycle_unsupported': 4,
                                  'calendar_agenda_items': 5, 'calendar_agenda_canceled': 1,
                                  'calendar_agenda_conflicts': 1, 'calendar_agenda_other_accounts': 2,
                                  'calendar_agenda_published': 0, 'calendar_agenda_failed': 1,
                                  'calendar_agenda_time_changed': 1, 'calendar_agenda_recurring': 2,
                                  'calendar_agenda_trimmed': 0, 'calendar_agenda_undecoded': 3,
                                  'error_agenda_version_conflict': 0,
                                  'calendar_requests_quarantined': 2,
                                  'hydration_pending': 1, 'hydration_expired': 0,
                                  'error_next_action_conflict': 0, 'error_legacy_action_guard_required': 0}}
            with self.subTest(reason=reason):
                result = self.invoke_intake(receipt, code=code)
                self.assertEqual(result.returncode, code)
                args = json.loads(self.rows()[-1][3])
                self.assertEqual(json.loads(args[args.index('--detail') + 1]), receipt)
                if code:
                    self.assertEqual(args[args.index('--failure-class') + 1], reason)

    def test_intake_invalid_receipts_fail_closed_without_private_output(self):
        cases = [
            {"state": "failed", "reason": "SYNTHETIC_PRIVATE_REASON", "counts": {}},
            {"state": "failed", "reason": ["SYNTHETIC_PRIVATE_REASON"], "counts": {}},
            {"state": "succeeded", "reason": "processed", "counts": {}},
            {"state": "failed", "reason": "writes_failed", "counts": {"SYNTHETIC_PRIVATE_KEY": 1}},
            {"state": "failed", "reason": "writes_failed", "counts": {"writes_failed": "SYNTHETIC_PRIVATE_VALUE"}},
            {"state": "failed", "reason": "writes_failed", "counts": {"writes_failed": True}},
            {"state": "failed", "reason": "writes_failed", "counts": {"writes_failed": -1}},
            {"state": "failed", "reason": "writes_failed", "counts": {"writes_failed": 10**20}},
            ["SYNTHETIC_PRIVATE_RECEIPT"],
        ]
        for receipt in cases:
            with self.subTest(receipt=receipt):
                result = self.invoke_intake(receipt)
                args = json.loads(self.rows()[-1][3])
                self.assertEqual(args[args.index("--failure-class") + 1], "exit_1")
                self.assertEqual(args[args.index("--detail") + 1], "outlook.intake exited 1")
                observed = result.stdout + result.stderr + (self.root / "out/run-scheduled.log").read_text()
                self.assertNotIn("SYNTHETIC_PRIVATE", observed)

    def test_intake_success_receipt_does_not_change_child_state(self):
        result = self.invoke_intake({"state": "succeeded", "reason": "processed",
                                     "counts": {"unresolved_addresses": 2}}, code=0)
        args = json.loads(self.rows()[0][3])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.rows()[0][2], "succeeded")
        self.assertNotIn("--failure-class", args)
        self.assertEqual(json.loads(args[args.index("--detail") + 1])["counts"],
                         {"unresolved_addresses": 2})

    def test_router_receipt_is_visible_without_changing_intake_result(self):
        intake = {"state": "succeeded", "reason": "processed", "counts": {"emails": 1}}
        for state, counts in (("succeeded", {"emails": 1, "routed": 1, "recorded": 2}),
                              ("failed", {"error": 1}), ("skipped", {})):
            with self.subTest(state=state):
                router = {"state": state, "counts": counts, "body": "SYNTHETIC_PRIVATE_BODY"}
                result = self.invoke_intake(intake, code=0, router_lines=[
                    'email-router-receipt ' + json.dumps(router)])
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout.splitlines(), [
                    'outlook-intake-receipt ' + json.dumps(intake, sort_keys=True),
                    'email-router-receipt ' + json.dumps({"state": state, "counts": counts}, sort_keys=True)])
                args = json.loads(self.rows()[-1][3])
                self.assertEqual(self.rows()[-1][2], "succeeded")
                self.assertEqual(json.loads(args[args.index("--detail") + 1]), intake)
                self.assertNotIn("--failure-class", args)
                self.assertNotIn("SYNTHETIC_PRIVATE", result.stdout + result.stderr + json.dumps(self.rows()))

    def test_router_invalid_receipts_do_not_leak_or_change_intake_failure(self):
        intake = {"state": "failed", "reason": "writes_failed", "counts": {"writes_failed": 1}}
        cases = ["SYNTHETIC_PRIVATE_INVALID_JSON", [],
                 {"state": ["SYNTHETIC_PRIVATE_STATE"], "counts": {}},
                 {"state": "SYNTHETIC_PRIVATE_STATE", "counts": {}},
                 {"state": "failed", "counts": []},
                 {"state": "failed", "counts": {"SYNTHETIC_PRIVATE_KEY": 1}},
                 *({"state": "failed", "counts": {"error": value}}
                   for value in (True, -1, 10**20, 1.5, "SYNTHETIC_PRIVATE_VALUE"))]
        for router in cases:
            with self.subTest(router=router):
                raw = router if isinstance(router, str) else json.dumps(router)
                result = self.invoke_intake(intake, router_lines=['email-router-receipt ' + raw])
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout.splitlines(), [
                    'outlook-intake-receipt ' + json.dumps(intake, sort_keys=True)])
                args = json.loads(self.rows()[-1][3])
                self.assertEqual(args[args.index("--failure-class") + 1], "writes_failed")
                self.assertEqual(json.loads(args[args.index("--detail") + 1]), intake)
                observed = (result.stdout + result.stderr + json.dumps(self.rows())
                            + (self.root / "out/run-scheduled.log").read_text())
                self.assertNotIn("SYNTHETIC_PRIVATE", observed)
                self.assertFalse(list((self.root / "out").glob("outlook-intake-receipt.*")))

    def rows(self):
        with closing(sqlite3.connect(self.spool)) as connection:
            return connection.execute("select service,run_key,state,argv from spool").fetchall()

    def test_success_records_one_result_without_a_second_receipt(self):
        result = self.invoke(0)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "child-out", "child-err"))
        rows = self.rows()
        self.assertEqual([r[:3] for r in rows], [("test-service", "launchd.run", "succeeded")])
        self.assertNotIn("--evidence-ref", json.loads(rows[0][3]))
        self.assertFalse((self.root / "out/run-scheduled-receipts").exists())

    def test_failed_delivery_queues_the_failure_and_preserves_the_child_exit(self):
        result = self.invoke(7)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (7, "child-out", "child-err"))
        rows = self.rows()
        self.assertEqual([r[:3] for r in rows], [("test-service", "launchd.run", "failed")])
        args = json.loads(rows[0][3])
        self.assertEqual(args[args.index("--failure-class") + 1], "exit_7")

    def test_configuration_skip_and_timeout_keep_their_meaning(self):
        for code, state in ((78, "skipped"), (124, "timed_out"), (137, "timed_out"), (143, "cancelled")):
            with self.subTest(code=code):
                result = self.invoke(code)
                self.assertEqual(result.returncode, code)
                self.assertEqual(self.rows()[-1][2], state)

    def test_success_heartbeat_throttles_only_recording_and_failure_resets_it(self):
        options = ("--heartbeat-interval", "3600")
        self.assertEqual(self.invoke(0, options).returncode, 0)
        again = self.invoke(0, options)
        self.assertEqual((again.returncode, again.stdout), (0, "child-out"))
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.invoke(7, options).returncode, 7)
        self.assertEqual(self.rows()[-1][2], "failed")
        self.assertEqual(self.invoke(0, options).returncode, 0)
        self.assertEqual(len(self.rows()), 3)


if __name__ == "__main__":
    unittest.main()
