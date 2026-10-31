#!/usr/bin/env python3
"""Read-only local watchdog behavior through its collection and scan interfaces."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import job_watchdog as watchdog


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = watchdog.load_config(ROOT / "ops/config/job-watchdog.json")
        self.source = self.root / "source"
        self.plists = self.source / "ops/launchd"
        self.plists.mkdir(parents=True)
        self.log = self.root / "job.log"
        self.log.write_text("older detail\nlast failure\n")
        (self.plists / "com.carr.fixture.plist").write_bytes(plistlib.dumps({
            "Label": "com.carr.fixture", "StartCalendarInterval": {"Minute": 0},
            "StandardErrorPath": str(self.log)}))

    def test_outcome_alarms_reach_findings_and_digest_then_clear(self):
        base = {'jobs': [], 'disk_free_percent': 50, 'outlook': {
            'timezone': 'America/Chicago', 'stored': 8,
            'filing_observed_at': '2026-10-23T15:00:00Z',
            'journey_live_at': '2026-10-20T15:00:00Z',
            'last_email_in_filed_at': '2026-10-23T15:00:00Z',
            'reply_draft_count': 0, 'coverage_failed': True}}
        monday = datetime(2026, 10, 26, 15, 0, 1, tzinfo=timezone.utc).timestamp()
        with patch.object(watchdog, 'collect', return_value=base), patch.object(watchdog.time, 'time', return_value=monday):
            self.assertEqual(watchdog.scan(self.root, self.config), 0)
        active = watchdog.read_latest(self.root / self.config['paths']['findings'])
        self.assertEqual({f['key'] for f in active.values() if not f.get('cleared_at')}, {
            'job_degraded:outlook:no_email_in', 'job_degraded:outlook:no_reply_draft',
            'job_degraded:outlook:coverage_failed'})
        context = watchdog.digest(self.root, self.config)
        for message in ('no email_in filed', 'zero reply_draft rows', 'daily coverage check failed'):
            self.assertIn(message, context)
        base['outlook'].update(last_email_in_filed_at='2026-10-26T15:00:00Z',
                               reply_draft_count=1, coverage_failed=False)
        with patch.object(watchdog, 'collect', return_value=base), patch.object(watchdog.time, 'time', return_value=monday):
            self.assertEqual(watchdog.scan(self.root, self.config), 0)
        self.assertEqual(watchdog.digest(self.root, self.config), '')

    def test_outcomes_fire_individually_and_respect_weekend_boundary(self):
        outlook = {'timezone': 'America/Chicago',
                   'filing_observed_at': '2026-10-23T15:00:00Z',
                   'journey_live_at': '2026-10-20T15:00:00Z',
                   'last_email_in_filed_at': '2026-10-23T15:00:00Z',
                   'reply_draft_count': 1, 'coverage_failed': False}
        facts = {'jobs': [], 'disk_free_percent': 50, 'outlook': outlook}
        for clock in ('2026-10-24T23:00:00Z', '2026-10-25T23:00:00Z', '2026-10-26T15:00:00Z'):
            self.assertEqual(watchdog.detect(facts, datetime.fromisoformat(clock).timestamp()), [])
        now = datetime.fromisoformat('2026-10-26T15:00:01Z').timestamp()
        self.assertEqual([f['key'] for f in watchdog.detect(facts, now)], ['job_degraded:outlook:no_email_in'])
        outlook['last_email_in_filed_at'] = '2026-10-26T15:00:00Z'
        for field, value, alarm in (('coverage_failed', True, 'coverage_failed'),
                                     ('reply_draft_count', 0, 'no_reply_draft')):
            with self.subTest(alarm=alarm):
                old = outlook[field]
                outlook[field] = value
                self.assertEqual([f['key'] for f in watchdog.detect(facts, now)], ['job_degraded:outlook:' + alarm])
                outlook[field] = old
        outlook.update(last_email_in_filed_at=None, journey_live_at=None, filing_observed_at=None, reply_draft_count=0)
        self.assertEqual(watchdog.detect(facts, now), [])
        outlook.update(filing_observed_at='2026-10-23T15:00:00Z', reply_draft_count=1)
        self.assertEqual([f['key'] for f in watchdog.detect(facts, now)], ['job_degraded:outlook:no_email_in'])

    def test_outcome_deadline_preserves_mailbox_wall_time_across_dst(self):
        facts = {'jobs': [], 'disk_free_percent': 50, 'outlook': {
            'timezone': 'America/Chicago', 'last_email_in_filed_at': '2026-10-30T15:00:00Z',
            'journey_live_at': None, 'reply_draft_count': 0, 'coverage_failed': False}}
        for clock, count in (('2026-11-02T15:59:59Z', 0), ('2026-11-02T16:00:00Z', 0),
                             ('2026-11-02T16:00:01Z', 1)):
            self.assertEqual(len(watchdog.detect(facts, datetime.fromisoformat(clock).timestamp())), count)

    def test_collection_reads_only_outcome_verb_and_persisted_coverage_verdict(self):
        state = self.root / 'out/outlook-intake/state.json'
        state.parent.mkdir(parents=True)
        state.write_text(json.dumps({'stored': 5, 'coverage_failed': True, 'filing_observed_at': 1792767600}))
        settings = SimpleNamespace(repo=self.root, account='fixture@example.test', timezone='America/Chicago',
                                   transport_environment=lambda: {})
        server = {'schema_version': 'email-outcomes.v1', 'journey_live_at': '2026-10-20T15:00:00Z',
                  'last_email_in_filed_at': '2026-10-20T15:00:00Z', 'reply_draft_count': 0}
        response = subprocess.CompletedProcess([], 0, json.dumps(server), '')
        launchctl = subprocess.CompletedProcess([], 0, 'PID Status Label\n- 0 com.carr.outlook-intake\n', '')
        with patch.object(watchdog, 'SOURCE', self.source), patch.object(watchdog, 'load_settings', return_value=settings), \
             patch.object(watchdog.subprocess, 'run', side_effect=[launchctl, response]) as calls:
            facts = watchdog.collect(self.root, self.config)
        self.assertTrue(facts['outlook']['coverage_failed'])
        request = calls.call_args_list[-1]
        self.assertEqual(request.args[0], [str(self.root / 'run.sh'), 'call', 'email-router-queue', '-'])
        self.assertEqual(json.loads(request.kwargs['input']), {'purpose': 'outcomes', 'account': 'fixture@example.test'})
        with patch.object(watchdog, 'collect', return_value=facts), \
             patch.object(watchdog.time, 'time', return_value=datetime.fromisoformat('2026-10-26T15:00:01Z').timestamp()):
            self.assertEqual(watchdog.scan(self.root, self.config), 0)
        self.assertIn('daily coverage check failed', watchdog.digest(self.root, self.config))
        with patch.object(watchdog, 'collect', return_value={'jobs': [], 'disk_free_percent': 50}):
            self.assertEqual(watchdog.scan(self.root, self.config), 0)
        self.assertIn('daily coverage check failed', watchdog.digest(self.root, self.config))

    def test_failed_outcome_read_is_one_finding_and_other_alarms_still_land(self):
        launchctl = subprocess.CompletedProcess(
            [], 0, 'PID Status Label\n- 0 com.carr.outlook-intake\n- 1 com.carr.fixture\n', '')
        settings = SimpleNamespace(repo=self.root, account='fixture@example.test', timezone='America/Chicago',
                                   transport_environment=lambda: {})
        with patch.object(watchdog, 'SOURCE', self.source), patch.object(watchdog, 'load_settings', return_value=settings), \
             patch.object(watchdog.subprocess, 'run', side_effect=[launchctl]):
            facts = watchdog.collect(self.root, self.config)
        keys = {f['key'] for f in watchdog.detect(facts)}
        self.assertIn('job_degraded:outlook:outcome_read_failed', keys)
        self.assertIn('job_failed:com.carr.fixture:exit_1', keys)
        with patch.object(watchdog, 'collect', return_value=facts):
            self.assertEqual(watchdog.scan(self.root, self.config), 0)
        self.assertIn('outcome read failed', watchdog.digest(self.root, self.config))

    def test_only_failed_scheduled_jobs_and_low_disk_are_collected(self):
        response = subprocess.CompletedProcess([], 0,
            "PID\tStatus\tLabel\n-\t7\tcom.carr.fixture\n-\t1\tcom.apple.other\n", "")
        with patch.object(watchdog, "SOURCE", self.source), \
             patch.object(watchdog.subprocess, "run", return_value=response) as calls, \
             patch.object(watchdog.shutil, "disk_usage", return_value=(100, 91, 9)):
            found = watchdog.detect(watchdog.collect(self.root, self.config))
        self.assertEqual({f["kind"] for f in found}, {"job_failed", "disk_low"})
        failure = next(f for f in found if f["kind"] == "job_failed")
        self.assertEqual(failure["subject"], "com.carr.fixture")
        self.assertEqual(failure["exit_code"], 7)
        self.assertEqual(failure["log_line"], "last failure")
        self.assertEqual([call.args[0] for call in calls.call_args_list], [["launchctl", "list"]])

    def test_missing_job_cannot_clear_its_previous_failed_run(self):
        failed = {"jobs": [{"label": "com.carr.fixture", "exit_code": 7, "log_line": "failed"}], "disk_free_percent": 50}
        absent = {"jobs": [], "disk_free_percent": 50}
        with patch.object(watchdog, "SOURCE", self.source), \
             patch.object(watchdog, "collect", side_effect=[failed, absent]):
            self.assertEqual(watchdog.scan(self.root, self.config), 0)
            self.assertEqual(watchdog.scan(self.root, self.config), 0)
        self.assertIn("com.carr.fixture exit 7", watchdog.digest(self.root, self.config))

    def test_deleted_job_definition_clears_its_failure(self):
        failed = {"jobs": [{"label": "com.carr.fixture", "exit_code": 2, "log_line": "script removed"}], "disk_free_percent": 50}
        absent = {"jobs": [], "disk_free_percent": 50}
        with patch.object(watchdog, "SOURCE", self.source), \
             patch.object(watchdog, "collect", side_effect=[failed, absent]):
            watchdog.scan(self.root, self.config)
            (self.plists / "com.carr.fixture.plist").unlink()
            watchdog.scan(self.root, self.config)
        self.assertEqual(watchdog.digest(self.root, self.config), "")

    def test_repeated_failure_updates_one_cause_and_success_clears_it(self):
        def facts(code, line):
            return {"jobs": [{"label": "com.carr.fixture", "exit_code": code, "log_line": line}], "disk_free_percent": 50}
        with patch.object(watchdog, "collect", side_effect=[facts(7, "first"), facts(7, "second"), facts(0, "ok")]):
            self.assertEqual(watchdog.scan(self.root, self.config), 0)
            self.assertEqual(watchdog.scan(self.root, self.config), 0)
            context = watchdog.digest(self.root, self.config)
            self.assertIn("1 open finding", context)
            self.assertIn("second", context)
            self.assertEqual(watchdog.scan(self.root, self.config), 0)
        self.assertEqual(watchdog.digest(self.root, self.config), "")
        rows = watchdog.read_latest(self.root / self.config["paths"]["findings"])
        self.assertEqual(len(rows), 1)
        self.assertIsNotNone(next(iter(rows.values()))["cleared_at"])

    def test_failed_collection_keeps_findings_without_collection_error_finding(self):
        failed = {"jobs": [{"label": "com.carr.fixture", "exit_code": 7, "log_line": "failed"}], "disk_free_percent": 50}
        with patch.object(watchdog, "collect", side_effect=[failed, OSError("launchctl unavailable")]):
            self.assertEqual(watchdog.scan(self.root, self.config), 0)
            self.assertEqual(watchdog.scan(self.root, self.config), 1)
        self.assertIn("1 open finding", watchdog.digest(self.root, self.config))
        rows = watchdog.read_latest(self.root / self.config["paths"]["findings"])
        self.assertEqual({f["kind"] for f in rows.values()}, {"job_failed"})

    def test_a_degraded_receipt_stays_open_until_a_healthy_run(self):
        def receipt(state, **counts):
            return "outlook-intake-receipt " + json.dumps(
                {"counts": counts, "reason": "decoder_degraded" if state == "degraded" else "processed",
                 "state": state}, sort_keys=True)
        router = 'email-router-receipt {"counts": {}, "state": "succeeded"}'
        reply = subprocess.CompletedProcess([], 0, "PID\tStatus\tLabel\n-\t0\tcom.carr.fixture\n", "")
        logs = ["\n".join((receipt("degraded", decoder_alarm=1, decoder_block_verification_bp=9879,
                                    decoder_sender_bp=4000, emails=3), router, "")),
                "\n".join((receipt("degraded", decoder_alarm=0, decoder_sender_bp=3900), router, "")),
                "\n".join((receipt("succeeded", decoder_sender_bp=9100), router, ""))]
        with patch.object(watchdog, "SOURCE", self.source), \
             patch.object(watchdog.subprocess, "run", return_value=reply), \
             patch.object(watchdog.shutil, "disk_usage", return_value=(100, 50, 50)):
            self.log.write_text(logs[0])
            self.assertEqual(watchdog.scan(self.root, self.config), 0)
            context = watchdog.digest(self.root, self.config)
            self.assertIn("com.carr.fixture degraded (decoder_degraded)", context)
            self.assertIn("block_verification 98.8%, sender 40.0%", context)
            self.assertNotIn("emails", context)
            self.log.write_text(logs[1])
            watchdog.scan(self.root, self.config)
            self.assertIn("sender 39.0%", watchdog.digest(self.root, self.config))
            self.log.write_text(logs[2])
            watchdog.scan(self.root, self.config)
        self.assertEqual(watchdog.digest(self.root, self.config), "")

    def test_three_consecutive_reconcile_failures_raise_and_clear_alarm(self):
        def receipt(reason, state):
            return "outlook-intake-receipt " + json.dumps({"state": state, "reason": reason, "counts": {}})
        failure = receipt("reconcile_failed", "failed")
        success = receipt("reconciled", "succeeded")
        reply = subprocess.CompletedProcess([], 0, "PID\tStatus\tLabel\n-\t1\tcom.carr.fixture\n", "")
        with patch.object(watchdog, "SOURCE", self.source), \
             patch.object(watchdog.subprocess, "run", return_value=reply), \
             patch.object(watchdog.shutil, "disk_usage", return_value=(100, 50, 50)):
            for lines, expected in [([failure, failure], False), ([failure, success, failure, failure], False),
                                    ([failure, failure, failure], True), ([failure, failure, failure, success], False)]:
                self.log.write_text("\n".join(lines) + "\n")
                watchdog.scan(self.root, self.config)
                self.assertEqual("reconciliation failed three consecutive times" in watchdog.digest(self.root, self.config), expected)
    def test_failed_coverage_receipt_remains_visible_after_a_normal_run(self):
        def receipt(state, reason, counts):
            return 'outlook-intake-receipt ' + json.dumps({'state': state, 'reason': reason,
                'counts': {'run': counts, 'backlog': {}}})
        self.assertIn('coverage_failed', watchdog.degraded_receipt([
            receipt('failed', 'coverage_failed', {'coverage_checked': 1, 'coverage_failed': 1}),
            receipt('succeeded', 'processed', {})]))
        self.assertIsNone(watchdog.degraded_receipt([
            receipt('failed', 'coverage_failed', {'coverage_checked': 1, 'coverage_failed': 1}),
            receipt('succeeded', 'processed', {'coverage_checked': 1, 'coverage_failed': 0})]))

    def test_a_split_receipt_reports_rates_from_the_run_counts(self):
        line = "outlook-intake-receipt " + json.dumps(
            {"counts": {"run": {"decoder_alarm": 1, "decoder_sender_bp": 4000, "emails": 3},
                        "backlog": {"unmatched": 2}},
             "reason": "decoder_degraded", "state": "degraded"}, sort_keys=True)
        self.assertEqual(watchdog.degraded_receipt([line]), "(decoder_degraded): sender 40.0%")

    def test_disk_clears_at_ten_percent(self):
        for free, count in ((9.99, 1), (10, 0), (50, 0)):
            with self.subTest(free=free):
                self.assertEqual(len(watchdog.detect({"jobs": [], "disk_free_percent": free})), count)

    def test_missing_loaded_job_is_not_reported_as_success(self):
        reply = subprocess.CompletedProcess([], 0, "PID\tStatus\tLabel\n", "")
        with patch.object(watchdog, "SOURCE", self.source), \
             patch.object(watchdog.subprocess, "run", return_value=reply), \
             patch.object(watchdog.shutil, "disk_usage", return_value=(100, 50, 50)):
            self.assertEqual(watchdog.collect(self.root, self.config)["jobs"], [])

    def test_keepalive_servers_are_not_scheduled_jobs(self):
        (self.plists / "server.plist").write_bytes(plistlib.dumps({"Label": "com.carr.server", "KeepAlive": True}))
        reply = subprocess.CompletedProcess([], 0, "PID\tStatus\tLabel\n-\t1\tcom.carr.server\n", "")
        with patch.object(watchdog, "SOURCE", self.source), \
             patch.object(watchdog.subprocess, "run", return_value=reply), \
             patch.object(watchdog.shutil, "disk_usage", return_value=(100, 50, 50)):
            self.assertEqual(watchdog.collect(self.root, self.config)["jobs"], [])

    def test_next_completed_run_replaces_the_previous_exit_cause(self):
        observations = [{"jobs": [{"label": "com.carr.fixture", "exit_code": code, "log_line": "failed"}], "disk_free_percent": 50} for code in (7, 8)]
        with patch.object(watchdog, "collect", side_effect=observations):
            watchdog.scan(self.root, self.config)
            watchdog.scan(self.root, self.config)
        context = watchdog.digest(self.root, self.config)
        self.assertIn("1 open finding", context)
        self.assertIn("exit 8", context)
        self.assertNotIn("exit 7", context)

    def test_retired_findings_leave_the_prompt_and_history_survives(self):
        path = self.root / self.config["paths"]["findings"]
        path.parent.mkdir(parents=True)
        legacy = json.dumps({"key": "branch_idle:old", "kind": "branch_idle", "reason": "old warning", "cleared_at": None}) + "\n"
        path.write_text(legacy)
        with patch.object(watchdog, "collect", return_value={"jobs": [], "disk_free_percent": 50}):
            self.assertEqual(watchdog.scan(self.root, self.config), 0)
        self.assertTrue(path.read_text().startswith(legacy))
        self.assertEqual(watchdog.digest(self.root, self.config), "")


if __name__ == "__main__":
    unittest.main()
