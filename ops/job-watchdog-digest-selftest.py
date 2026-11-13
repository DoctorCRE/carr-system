#!/usr/bin/env python3
# Test job watchdog digest hooks.
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import job_watchdog as watchdog

ROOT = Path(__file__).resolve().parents[1]


class HookTests(unittest.TestCase):
    def test_social_delivery_digest_dedupes_across_restart_and_clears(self):
        config = watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / 'out/social-batch'
            folder.mkdir(parents=True)
            receipt = {'schema': 'carr-social-delivery/v1', 'observed_at': 1000,
                       'interval_seconds': 86400, 'coverage': 'observed',
                       'deferred': [], 'stop_reason': None,
                       'posts': {'week/draft': {'status': 'failed', 'platform': 'linkedin',
                                              'topic': 'Synthetic topic', 'submission_id': 'fixture-id',
                                              'errorMessage': 'Account expired'}}}
            path = folder / 'delivery.json'
            path.write_text(json.dumps(receipt))
            inbox = root / config['paths']['orchestrator_inbox']
            with patch.object(watchdog.time, 'time', return_value=1000):
                self.assertIn('linkedin Synthetic topic (fixture-id): Account expired', watchdog.digest(root, config))
                watchdog.deliver_findings(root, config, 1000)
                first = inbox.read_bytes()
                receipt['observed_at'] = 200000
                path.write_text(json.dumps(receipt))
                watchdog.deliver_findings(root, config, 200000)
                self.assertEqual(inbox.read_bytes(), first)
                receipt['posts']['week/draft']['status'] = 'published'
                path.write_text(json.dumps(receipt))
                watchdog.deliver_findings(root, config, 200001)
                self.assertEqual(inbox.read_text(), '')

    def test_social_delivery_missing_stale_and_unknown_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / 'out/social-batch'
            folder.mkdir(parents=True)
            self.assertIn('unknown', watchdog.social_delivery_findings(root, 1000)[0]['reason'])
            receipt = {'schema': 'carr-social-delivery/v1', 'observed_at': 1000,
                       'interval_seconds': 86400, 'coverage': 'unknown',
                       'deferred': ['fixture'], 'stop_reason': 'authentication', 'posts': {}}
            (folder / 'delivery.json').write_text(json.dumps(receipt))
            found = watchdog.social_delivery_findings(root, 91001)
            self.assertEqual(len(found), 3)
            self.assertTrue(any('overdue' in row['reason'] for row in found))
            self.assertTrue(any('authentication' in row['reason'] for row in found))

    def test_findings_reach_inbox_once_until_content_changes_or_reminder_is_due(self):
        config = watchdog.load_config(ROOT / "ops/config/job-watchdog.json")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            facts = {"jobs": [{"label": "fixture", "exit_code": 7, "log_line": "first error"}],
                     "disk_free_percent": 50}
            inbox = root / "out/watchdog/orchestrator-inbox.jsonl"
            def scan(now):
                with patch.object(watchdog, "collect", return_value=facts), patch.object(watchdog.time, "time", return_value=now):
                    self.assertEqual(watchdog.scan(root, config), 0)
                return [json.loads(line) for line in inbox.read_text().splitlines()] if inbox.exists() else []
            first = scan(1000)
            self.assertEqual(len(first), 1)
            self.assertEqual(first[0]["owner"], "orchestrator")
            self.assertIn("first error", first[0]["finding"]["reason"])
            self.assertEqual(scan(1001), first)
            facts["jobs"][0]["log_line"] = "changed error"
            changed = scan(1002)
            self.assertEqual(len(changed), 1)
            self.assertIn("changed error", changed[-1]["finding"]["reason"])
            age = 86400
            self.assertEqual(scan(1002 + age - 1), changed)
            self.assertNotEqual(scan(1002 + age), changed)
            facts["jobs"][0]["exit_code"] = 0
            self.assertEqual(scan(1003 + age), [])
            facts["jobs"][0]["exit_code"] = 7
            self.assertEqual(len(scan(1004 + age)), 1)

    def test_interrupted_inbox_write_recovers_and_delivers(self):
        config = watchdog.load_config(ROOT / "ops/config/job-watchdog.json")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            facts = {"jobs": [{"label": "fixture", "exit_code": 7, "log_line": "error"}], "disk_free_percent": 50}
            inbox = root / config["paths"]["orchestrator_inbox"]
            with patch.object(watchdog, "collect", return_value=facts), patch.object(watchdog.time, "time", return_value=1000):
                self.assertEqual(watchdog.scan(root, config), 0)
                complete = inbox.read_text()
                facts["jobs"][0]["log_line"] = "new error"
                with patch.object(watchdog.os, "replace", side_effect=OSError("interrupted")):
                    with self.assertRaises(OSError):
                        watchdog.scan(root, config)
                self.assertEqual(inbox.read_text(), complete)
                with inbox.open("a") as handle:
                    handle.write('{"key":"partial"')
                facts["jobs"][0]["log_line"] = "new error"
                self.assertEqual(watchdog.scan(root, config), 0)
            rows = [json.loads(line) for line in inbox.read_text().splitlines()]
            self.assertIn("new error", rows[-1]["finding"]["reason"])
            self.assertTrue(complete)

    def test_inbox_compacts_reminders_and_keeps_all_pending_findings(self):
        config = watchdog.load_config(ROOT / "ops/config/job-watchdog.json")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            facts = {"jobs": [{"label": label, "exit_code": 7, "log_line": "error"} for label in ("one", "two")], "disk_free_percent": 50}
            inbox = root / config["paths"]["orchestrator_inbox"]
            for day in range(30):
                facts["jobs"][0]["log_line"] = f"error {day}"
                with patch.object(watchdog, "collect", return_value=facts), patch.object(watchdog.time, "time", return_value=1000 + day * 86400):
                    self.assertEqual(watchdog.scan(root, config), 0)
            rows = [json.loads(line) for line in inbox.read_text().splitlines()]
            self.assertEqual(len(rows), 2)
            self.assertEqual({row["finding"]["subject"] for row in rows}, {"one", "two"})
            self.assertIn("error 29", next(row for row in rows if row["finding"]["subject"] == "one")["finding"]["reason"])

    def test_partner_prompt_has_no_watchdog_hook(self):
        hooks = json.loads((ROOT / "ops/config/hooks.json").read_text())
        self.assertFalse(any("job-watchdog-digest.py" in h["command"] for entry in hooks["UserPromptSubmit"] for h in entry["hooks"]))
        self.assertFalse((ROOT / "hooks/job-watchdog-digest.py").exists())

    def test_digest_is_five_lines_and_ignores_retired_findings(self):
        config = watchdog.load_config(ROOT / "ops/config/job-watchdog.json")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / config["paths"]["findings"]
            path.parent.mkdir(parents=True)
            rows = [{"key": f"branch:{i}", "kind": "branch_idle", "reason": "old branch", "cleared_at": None} for i in range(20)]
            rows.extend({"key": f"job_failed:fixture-{i}:exit_7", "kind": "job_failed", "exit_code": 7,
                         "reason": f"fixture-{i} exit 7: first line\nsecond line", "cleared_at": None} for i in range(10))
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            context = watchdog.digest(root, config)
            self.assertEqual(len(context.splitlines()), 5)
            self.assertIn("10 open finding(s)", context)
            self.assertIn("7 more", context)
            self.assertNotIn("branch", context)

    def test_social_visibility_cannot_crowd_failures_or_overdue_observer(self):
        config = watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / 'out/social-batch'
            folder.mkdir(parents=True)
            posts = {f'2026-10-26/{i}': {'status': 'published', 'visibility': 'cannot_verify',
                     'platform': 'linkedin', 'topic': f'old topic {i}', 'submission_id': str(i),
                     'publicUrl': 'https://www.linkedin.com/blocked'} for i in range(20)}
            posts['2026-11-02/failure'] = {'status': 'failed', 'platform': 'linkedin', 'topic': 'new failure',
                                    'submission_id': 'new', 'errorMessage': 'Account expired'}
            receipt = {'schema': 'carr-social-delivery/v1', 'observed_at': 1, 'interval_seconds': 86400,
                       'coverage': 'observed', 'deferred': [], 'stop_reason': None, 'posts': posts}
            (folder / 'delivery.json').write_text(json.dumps(receipt))
            context = watchdog.digest(root, config)
            self.assertLessEqual(len(context.splitlines()), 5)
            self.assertIn('Account expired', context)
            self.assertIn('observer run is overdue', context)
            self.assertIn('20 public links cannot be verified', context)
            self.assertNotIn('old topic', context)
            receipt['coverage'] = 'unknown'
            receipt['stop_reason'] = 'authentication'
            (folder / 'delivery.json').write_text(json.dumps(receipt))
            context = watchdog.digest(root, config)
            self.assertLessEqual(len(context.splitlines()), 5)
            self.assertIn('observer run is overdue', context)
            self.assertIn('Account expired', context)
            self.assertIn('out/social-batch/delivery.json', context)
            self.assertNotIn('see out/watchdog/findings.jsonl', context)

    def test_digest_prefers_newer_social_failures_and_names_mixed_overflow_files(self):
        config = watchdog.load_config(ROOT / 'ops/config/job-watchdog.json')
        rows = [{'key': f'social-delivery:2026-10-{day}/post', 'kind': 'job_degraded',
                 'source': 'social-delivery', 'reason': f'old failure {day}'} for day in range(10, 16)]
        rows.append({'key': 'social-delivery:2026-11-02/post', 'kind': 'job_degraded',
                     'source': 'social-delivery', 'reason': 'new failure'})
        rows.append({'key': 'other', 'kind': 'job_degraded', 'reason': 'other actionable finding'})
        with patch.object(watchdog, 'active_findings', return_value=rows):
            context = watchdog.digest(ROOT, config)
        self.assertEqual(len(context.splitlines()), 5)
        self.assertEqual(context.splitlines()[1], '- new failure')
        self.assertIn('out/social-batch/delivery.json', context.splitlines()[-1])
        self.assertIn(config['paths']['findings'], context.splitlines()[-1])

    def test_launchd_cadence_is_config_projection(self):
        config = json.loads((ROOT / "ops/config/job-watchdog.json").read_text())
        plist = plistlib.loads((ROOT / "ops/launchd/com.carr.job-watchdog.plist").read_bytes())
        result = subprocess.run([sys.executable, "-m", "lib.launchd_calendar", "audit",
                                 "ops/launchd/com.carr.job-watchdog.plist"],
                                cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("StartInterval", plist)
        # Two-minute cadence covers the entire hour exactly once.
        self.assertEqual(plist["StartCalendarInterval"],
                         [{"Minute": minute} for minute in range(0, 60, 2)])
        self.assertEqual(plist["ProgramArguments"][-1], "scan")
        self.assertIn("{{REPO}}/bin/run-scheduled.sh", plist["ProgramArguments"])
        services = json.loads((ROOT / "ops/config/services.json").read_text())
        declaration = next(s for s in services["services"] if s["key"] == "job-watchdog")["environments"][0]
        self.assertEqual(declaration["expected_cadence_seconds"], config["thresholds"]["scan_seconds"])
        self.assertEqual(declaration["cadence_grace_seconds"], config["thresholds"]["scan_grace_seconds"])


if __name__ == "__main__":
    unittest.main()
