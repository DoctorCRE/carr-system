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

    def test_reconciled_intake_receipt_is_recorded_as_expected(self):
        receipt = {'state': 'succeeded', 'reason': 'reconciled', 'counts': {'mail_ids': 2}}
        result = self.invoke_intake(receipt, code=0)
        self.assertEqual(result.returncode, 0)
        args = json.loads(self.rows()[0][3])
        self.assertEqual(json.loads(args[args.index('--detail') + 1]), receipt)

    def test_runtime_fields_reach_ops_detail(self):
        for behind in (3, None):
            receipt = {"state": "succeeded", "reason": "processed", "counts": {},
                       "runtime_sha": "abc1234", "runtime_behind": behind}
            result = self.invoke_intake(receipt, code=0)
            self.assertEqual(result.returncode, 0)
            args = json.loads(self.rows()[-1][3])
            self.assertEqual(json.loads(args[args.index('--detail') + 1]), receipt)

    def test_invalid_runtime_fields_do_not_enter_ops_detail(self):
        for sha, behind in (('PRIVATE_TEXT', 2), ('abc1234', -1), ('abc1234', True),
                            ('abc1234', 'PRIVATE_TEXT')):
            receipt = {'state': 'succeeded', 'reason': 'processed', 'counts': {},
                       'runtime_sha': sha, 'runtime_behind': behind}
            self.invoke_intake(receipt, code=0)
            args = json.loads(self.rows()[-1][3])
            self.assertEqual(json.loads(args[args.index('--detail') + 1]),
                             {'state': 'succeeded', 'reason': 'processed', 'counts': {}})

    def test_other_outlook_receipts_reach_ops_detail(self):
        for service in ('outlook-triage', 'outlook-outbox'):
            receipt = {'state': 'succeeded', 'reason': 'processed', 'counts': {},
                       'runtime_sha': 'abc1234', 'runtime_behind': None,
                       'runtime_note': 'main_unknown_locally'}
            child = self.root / 'other.py'
            child.write_text(f"print({(service + '-receipt ' + json.dumps(receipt))!r})")
            result = subprocess.run(['/bin/zsh', str(self.root / 'bin/run-scheduled.sh'),
                                     service, service + '.run', sys.executable, str(child)],
                                    cwd=self.root, env=self.env, text=True, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0)
            args = json.loads(self.rows()[-1][3])
            self.assertEqual(json.loads(args[args.index('--detail') + 1]), receipt)

    def test_outbox_deferral_preserves_monitoring_counts_without_failure_class(self):
        receipt = {'state': 'skipped', 'reason': 'deferred',
                   'counts': {'deferred': 1, 'failed': 0, 'quarantined': 2}}
        child = self.root / 'outbox.py'
        child.write_text('import sys\n' +
                         f"print({('outlook-outbox-receipt ' + json.dumps(receipt))!r})\nsys.exit(78)\n")
        result = subprocess.run(['/bin/zsh', str(self.root / 'bin/run-scheduled.sh'),
            'outlook-outbox', 'outlook-outbox.run', sys.executable, str(child)],
            cwd=self.root, env=self.env, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 78)
        args = json.loads(self.rows()[-1][3])
        self.assertEqual(args[args.index('--state') + 1], 'skipped')
        self.assertNotIn('--failure-class', args)
        self.assertEqual(json.loads(args[args.index('--detail') + 1]), receipt)

    def test_locked_screen_skip_preserves_reason_without_failure_class(self):
        receipt = {'state': 'skipped', 'reason': 'screen_locked', 'counts': {}}
        child = self.root / 'locked.py'
        child.write_text(f"print({('outlook-triage-receipt ' + json.dumps(receipt))!r})\n"
                         "raise SystemExit(78)\n")
        result = subprocess.run(['/bin/zsh', str(self.root / 'bin/run-scheduled.sh'),
            'outlook-triage', 'outlook.triage', sys.executable, str(child)], cwd=self.root,
            env=self.env, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 78)
        args = json.loads(self.rows()[-1][3])
        self.assertEqual(self.rows()[-1][2], 'skipped')
        self.assertEqual(json.loads(args[args.index('--detail') + 1]), receipt)
        self.assertNotIn('--failure-class', args)

    def test_receipt_tempfile_failure_still_runs_service(self):
        (self.root / 'out').write_text('synthetic unavailable receipt directory')
        marker = self.root / 'service-ran'
        result = subprocess.run(['/bin/zsh', str(self.root / 'bin/run-scheduled.sh'),
            'outlook-outbox', 'outbox.run', sys.executable, '-c',
            f'from pathlib import Path; Path({str(marker)!r}).touch()'], cwd=self.root,
            env=self.env, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(marker.exists())

    def test_failed_outbox_keeps_exit_failure_class_and_real_reason(self):
        receipt = dict(state='failed', reason='send_failed', counts=dict(sent=1, failed=1))
        child = self.root / 'failed-outbox.py'
        child.write_text(f"print({('outlook-outbox-receipt ' + json.dumps(receipt))!r})\nraise SystemExit(1)\n")
        result = subprocess.run(['/bin/zsh', str(self.root / 'bin/run-scheduled.sh'),
            'outlook-outbox', 'outbox.run', sys.executable, str(child)], cwd=self.root,
            env=self.env, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 1)
        args = json.loads(self.rows()[-1][3])
        self.assertEqual(args[args.index('--failure-class') + 1], 'exit_1')
        self.assertEqual(json.loads(args[args.index('--detail') + 1]), receipt)

    def test_null_runtime_sha_preserves_note(self):
        self.invoke_intake(dict(state='succeeded', reason='processed', counts={},
                                runtime_sha=None, runtime_behind=None, runtime_note='history_incomplete'), code=0)
        args = json.loads(self.rows()[-1][3])
        self.assertEqual(json.loads(args[args.index('--detail') + 1])['runtime_note'], 'history_incomplete')

    def test_service_status_and_stderr_reach_private_service_logs(self):
        for service, status in (('outlook-outbox', 'sent=1 failed=1'),
                                ('outlook-triage', 'moved=1 left=2')):
            log = self.root / 'out' / (service + '-launchd.log')
            log.parent.mkdir(exist_ok=True)
            child = self.root / 'logging.py'
            child.write_text('import sys\n' + f'print({status!r})\n' +
                             'print("synthetic service error", file=sys.stderr)\n')
            with log.open('w') as output:
                os.chmod(log, 0o600)
                result = subprocess.run(['/bin/zsh', str(self.root / 'bin/run-scheduled.sh'),
                    service, service + '.run', sys.executable, str(child)], cwd=self.root,
                    env=self.env, stdout=output, stderr=output, timeout=15)
            self.assertEqual(result.returncode, 0)
            self.assertIn(status, log.read_text())
            self.assertIn('synthetic service error', log.read_text())
            self.assertEqual(log.stat().st_mode & 0o777, 0o600)
            self.assertNotIn('synthetic service error', (self.root / 'out/run-scheduled.log').read_text())

    def test_link_failure_reason_reaches_receipt_and_spool(self):
        receipt = {"state": "failed", "reason": "email_links_read_failed", "counts": {"email_links_pending": 2}}
        result = self.invoke_intake(receipt)
        self.assertEqual(json.loads(result.stdout.split('outlook-intake-receipt ', 1)[1]), receipt)
        args = json.loads(self.rows()[-1][3])
        self.assertEqual(json.loads(args[args.index('--detail') + 1]), receipt)

    def test_intake_tool_detail_and_router_exception_types_survive_wrapper(self):
        receipt = {'state': 'failed', 'reason': 'decode_failed', 'counts': {},
                   'detail': 'hxprobe exit=42 code=exit_nonzero'}
        router = {'state': 'failed', 'counts': {'model_failed': 2},
                  'model_failed_types': {'ModuleNotFoundError': 2}}
        result = self.invoke_intake(receipt, router_lines=['email-router-receipt ' + json.dumps(router)])
        args = json.loads(self.rows()[0][3])
        self.assertEqual(json.loads(args[args.index('--detail') + 1]), receipt)
        self.assertEqual(json.loads(result.stdout.split('email-router-receipt ', 1)[1]), router)

    def test_router_counts_and_refusal_codes_reach_output(self):
        intake = {"state": "succeeded", "reason": "processed", "counts": {}}
        router = {"state": "succeeded", "counts": {"inherited": 2, "contact_only": 1, "refused": 2},
                  "refusal_codes": {"email_deal_quote_invalid": 2}}
        result = self.invoke_intake(intake, code=0, router_lines=['email-router-receipt ' + json.dumps(router)])
        self.assertEqual(json.loads(result.stdout.split('email-router-receipt ', 1)[1]), router)

    def test_split_run_and_backlog_counts_are_validated_and_preserved(self):
        receipt = {"state": "degraded", "reason": "decoder_degraded",
                   "counts": {"run": {"decoder_alarm": 1, "logged": 2, "lead_proposed": 1},
                              "backlog": {"unresolved_addresses": 5, "calendar_copies_in_store": 940,
                                          "calendar_expired_total": 102}}}
        first = self.invoke_intake(receipt, code=1)
        args = json.loads(self.rows()[-1][3])
        self.assertEqual(args[args.index("--failure-class") + 1], "decoder_degraded")
        self.assertEqual(json.loads(args[args.index("--detail") + 1]), receipt)
        self.assertEqual(json.loads(first.stdout.split("outlook-intake-receipt ", 1)[1]), receipt)
        receipt["counts"]["run"]["decoder_alarm"] = 0
        second = self.invoke_intake(receipt, code=0)
        self.assertEqual(len(self.rows()), 1, "a quiet degraded run records no second failure")
        self.assertIn('"state": "degraded"', second.stdout)

    def test_split_counts_reject_private_names_and_nested_values(self):
        for counts in ({"run": {"lee@example.test": 1}, "backlog": {}},
                       {"run": {"logged": "2"}, "backlog": {}},
                       {"run": {"logged": 1}, "backlog": {"unmatched": {"deep": 1}}},
                       {"run": {"logged": 1}}):
            with self.subTest(counts=counts):
                result = self.invoke_intake({"state": "succeeded", "reason": "processed", "counts": counts}, code=0)
                self.assertNotIn("lee@example.test", result.stdout)
                self.assertNotIn("outlook-intake-receipt", result.stdout)

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

    def test_intake_detail_rejects_private_text_and_unknown_codes(self):
        for detail in ('lee@example.test Subject: Lease offer /Users/x/Deals/Acme Medical/LOI.docx',
                       'hxprobe exit=42 code=unknown',
                       'hxprobe exit=42 code=exit_nonzero\nlee@example.test',
                       'other exit=42 code=exit_nonzero'):
            with self.subTest(detail=detail):
                receipt = {'state': 'failed', 'reason': 'decode_failed', 'counts': {}, 'detail': detail}
                result = self.invoke_intake(receipt)
                args = json.loads(self.rows()[-1][3])
                expected = {k: v for k, v in receipt.items() if k != 'detail'}
                self.assertEqual(json.loads(args[args.index('--detail') + 1]), expected)
                self.assertNotIn(detail, result.stdout)

    def test_decoder_deferred_and_health_error_receipts_are_preserved(self):
        deferred = {'state': 'degraded', 'reason': 'writes_deferred',
                    'counts': {'decoder_alarm': 1, 'writes_deferred': 1}}
        self.invoke_intake(deferred, code=1)
        args = json.loads(self.rows()[-1][3])
        self.assertEqual(json.loads(args[args.index('--detail') + 1]), deferred)
        unavailable = {'state': 'succeeded', 'reason': 'processed',
                       'counts': {'decoder_health_error_typeerror': 1}}
        result = self.invoke_intake(unavailable, code=0)
        self.assertIn('decoder_health_error_typeerror', result.stdout)

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
            {"state": "failed", "reason": "writes_failed", "counts": {"SYNTHETIC_PRIVATE@example.test".lower(): 1}},
            {"state": "failed", "reason": "writes_failed", "counts": {"synthetic_private_" + "x" * 60: 1}},
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

    def test_router_receipt_filters_unknown_names_and_keeps_known_codes(self):
        intake = {"state": "succeeded", "reason": "processed", "counts": {}}
        router = {"state": "succeeded", "counts": {"refused": 2, "SYNTHETIC_PRIVATE_COUNT": 7},
                  "refusal_codes": {"email_thread_ambiguous": 1, "SYNTHETIC_PRIVATE_CODE": 1}}
        result = self.invoke_intake(intake, code=0, router_lines=[
            'email-router-receipt ' + json.dumps(router)])
        self.assertEqual(result.stdout.splitlines()[-1], 'email-router-receipt ' + json.dumps({
            "state": "succeeded", "counts": {"refused": 2},
            "refusal_codes": {"email_thread_ambiguous": 1}}, sort_keys=True))
        self.assertNotIn("SYNTHETIC_PRIVATE", result.stdout + result.stderr)

    def test_router_invalid_receipts_do_not_leak_or_change_intake_failure(self):
        intake = {"state": "failed", "reason": "writes_failed", "counts": {"writes_failed": 1}}
        cases = ["SYNTHETIC_PRIVATE_INVALID_JSON", [],
                 {"state": ["SYNTHETIC_PRIVATE_STATE"], "counts": {}},
                 {"state": "SYNTHETIC_PRIVATE_STATE", "counts": {}},
                 {"state": "failed", "counts": []},
                 {"state": "failed", "counts": {}, "refusal_codes": {"email_deal_quote_invalid": "SYNTHETIC_PRIVATE"}},
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
