#!/usr/bin/env python3
"""Fixture evidence exercises the scheduled-job report used by health and watchdog."""
import copy
import importlib.util
import os
import sys
import unittest
import json
import plistlib
import re
import subprocess
import tempfile
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
import scheduled_jobs as jobs
import launchd_calendar

EVERY_TWO_MINUTES = {"StartCalendarInterval": [{"Minute": m} for m in range(0, 60, 2)]}


def launchctl_print(label, argv, interval, state="not running", extra=""):
    """The launchctl print shape the collector reads, rendered from a definition."""
    lines = ["service = {", f"\tstate = {state}", "\targuments = {"]
    lines += [f"\t\t{arg}" for arg in argv] + ["\t}"]
    if "StartInterval" in interval:
        lines.append(f"\trun interval = {interval['StartInterval']} seconds")
    calendar = interval.get("StartCalendarInterval") or []
    calendar = [calendar] if isinstance(calendar, dict) else calendar
    if calendar or interval.get("WatchPaths"):
        lines.append("\tevent triggers = {")
        for n, entry in enumerate(calendar):
            lines += [f"\t\t{label}.{n} => {{", "\t\t\tkeepalive = 0",
                      "\t\t\tstream = com.apple.launchd.calendarinterval", "\t\t\tdescriptor = {"]
            lines += [f'\t\t\t\t"{k}" => {v}' for k, v in entry.items()] + ["\t\t\t}", "\t\t}"]
        if interval.get("WatchPaths"):
            lines += ["\t\tcom.apple.launchd.WatchPaths => {", "\t\t\tstream = com.apple.fsevents.matching",
                      "\t\t\tdescriptor = {", '\t\t\t\t"WatchPaths" => [']
            lines += [f'\t\t\t\t\t{i} = "{p}"' for i, p in enumerate(interval["WatchPaths"])]
            lines += ["\t\t\t\t]", "\t\t\t}", "\t\t}"]
        lines.append("\t}")
    return "\n".join(lines + [extra, "\tlast exit code = 0", "}"])


def healthy_snapshot(manifest, role, now):
    """Evidence a correctly installed machine of this role would produce."""
    snapshot = {"plists": {}, "launchctl_list": "PID\tStatus\tLabel\n", "launchctl_disabled": "",
                "launchctl_print": {}, "log_mtimes": {}, "cron": "", "errors": [], "machine_role": role,
                "git": {"ahead": 0, "behind": 0, "branch": "main", "remote_matches": True}}
    for job in manifest["jobs"]:
        enabled, installed = jobs.expectation(job, role)
        if not installed:
            continue
        label, argv, interval = job["label"], jobs.expand(job["program_arguments"]), jobs.expand(job["interval"])
        plist = {"Label": label, "ProgramArguments": argv, **interval}
        for key, path in zip(("StandardOutPath", "StandardErrorPath"), job.get("log_paths") or []):
            if path:
                plist[key] = jobs.expand(path)
        if not enabled:
            plist["Disabled"] = True
        snapshot["plists"][label] = plist
        if enabled:
            snapshot["launchctl_list"] += f"-\t0\t{label}\n"
            snapshot["launchctl_print"][label] = launchctl_print(label, argv, interval)
            snapshot["log_mtimes"][jobs.activity_path(job)] = now
    return snapshot


def launchd_scope_places(label):
    return "secondary" if label + ".plist" in jobs.launchd_scope.SECONDARY_ONLY else "primary"


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.manifest = {
            "schema_version": 1, "canonical_checkout": "/machine/carr-system",
            "jobs": [{"label": "com.carr.test", "scheduler": "launchd",
                      "program_arguments": ["/bin/sh", "/machine/carr-system/bin/task.sh"],
                      "required_checkout": "/machine/carr-system",
                      "interval": copy.deepcopy(EVERY_TWO_MINUTES), "expected_enabled": True,
                      "expected_installed": True, "log_path": "/logs/test.log",
                      "log_max_age_seconds": 600, "owner": "orchestrator",
                      "done_signal": "ops.run task/launchd.run"}]}
        argv = ["/bin/sh", "/machine/carr-system/bin/task.sh"]
        self.snapshot = {
            "plists": {"com.carr.test": {
                "ProgramArguments": list(argv), **copy.deepcopy(EVERY_TWO_MINUTES),
                "StandardOutPath": "/logs/test.log"}},
            "launchctl_list": "PID\tStatus\tLabel\n-\t0\tcom.carr.test\n",
            "launchctl_disabled": 'disabled services = { "com.carr.test" => enabled }',
            "launchctl_print": {"com.carr.test": launchctl_print("com.carr.test", argv, EVERY_TWO_MINUTES)},
            "log_mtimes": {"/logs/test.log": 990},
            "cron": "", "errors": [], "machine_role": "secondary",
            "git": {"ahead": 0, "behind": 0, "branch": "main", "remote_matches": True}}

    def codes(self, now=1000):
        return [r["code"] for r in jobs.report(self.manifest, self.snapshot, now)]

    def use_program(self, argv, loaded=None):
        self.manifest["jobs"][0]["program_arguments"] = list(argv)
        self.snapshot["plists"]["com.carr.test"]["ProgramArguments"] = list(argv)
        self.snapshot["launchctl_print"]["com.carr.test"] = launchctl_print(
            "com.carr.test", loaded or argv, EVERY_TWO_MINUTES)

    def test_wrong_runtime_checkout_is_actionable_even_when_disk_is_correct(self):
        self.snapshot["launchctl_print"]["com.carr.test"] = self.snapshot["launchctl_print"]["com.carr.test"].replace("/machine/carr-system/", "/machine/carr-system-feature/")
        rows = jobs.report(self.manifest, self.snapshot, now=1000)
        self.assertEqual([(r["label"], r["code"]) for r in rows],
                         [("com.carr.test", "program_drift"), ("com.carr.test", "wrong_checkout")])
        line = jobs.render(rows)[-1]
        for text in ("loop scheduled_jobs:com.carr.test:wrong_checkout", "owner orchestrator",
                     "fix:", "verify:", "auto-clear:", "job-watchdog.py scan"):
            self.assertIn(text, line)

    def test_working_directory_in_a_nested_worktree_or_symlinked_clone_is_drift(self):
        for actual in ("/machine/carr-system/.claude/worktrees/other", "/tmp/other-clone"):
            with self.subTest(actual=actual):
                self.snapshot["plists"]["com.carr.test"]["WorkingDirectory"] = "/shortcut"
                self.snapshot["checkout_roots"] = {"/shortcut": actual}
                self.assertIn("wrong_checkout", self.codes())

    def test_loaded_cadence_must_match_after_an_unreloaded_disk_edit(self):
        hourly = {"StartCalendarInterval": [{"Minute": 0}]}
        self.snapshot["launchctl_print"]["com.carr.test"] = launchctl_print(
            "com.carr.test", ["/bin/sh", "/machine/carr-system/bin/task.sh"], hourly)
        self.assertEqual(self.codes(), ["loaded_interval_drift"])

    def test_loaded_watch_paths_are_compared_with_the_declaration(self):
        argv = ["/bin/sh", "/machine/carr-system/bin/task.sh"]
        declared = {"WatchPaths": ["/drop/a"], "RunAtLoad": False}
        self.manifest["jobs"][0].update(interval=declared, log_max_age_seconds=None,
                                        freshness_exception="event-driven")
        self.snapshot["plists"]["com.carr.test"] = {"ProgramArguments": argv, **declared,
                                                    "StandardOutPath": "/logs/test.log"}
        self.snapshot["launchctl_print"]["com.carr.test"] = launchctl_print("com.carr.test", argv, declared)
        self.assertEqual(self.codes(), [])
        self.snapshot["launchctl_print"]["com.carr.test"] = launchctl_print(
            "com.carr.test", argv, {"WatchPaths": ["/drop/old"]})
        self.assertEqual(self.codes(), ["loaded_interval_drift"])

    def test_program_named_only_as_data_does_not_satisfy_execution(self):
        self.snapshot["plists"]["com.carr.test"]["ProgramArguments"] = ["/bin/echo", "/machine/carr-system/bin/task.sh"]
        self.snapshot["launchctl_print"]["com.carr.test"] = launchctl_print(
            "com.carr.test", ["/bin/echo", "/machine/carr-system/bin/task.sh"], EVERY_TWO_MINUTES)
        self.assertEqual(self.codes(), ["program_drift"])

    def test_installed_checkout_environment_is_checked_before_reload(self):
        self.snapshot["plists"]["com.carr.test"]["EnvironmentVariables"] = {"CARR_REPO": "/other-clone"}
        self.assertEqual(self.codes(), ["wrong_checkout"])
        self.snapshot["plists"]["com.carr.test"]["EnvironmentVariables"] = {"CARR_REPO": "/machine/carr-system"}
        self.assertEqual(self.codes(), [])

    def test_disabled_override_does_not_hide_a_running_forbidden_job(self):
        self.manifest["jobs"][0].update(expected_enabled=False, expected_installed=False)
        self.snapshot["plists"] = {}
        self.snapshot["launchctl_list"] = "PID Status Label\n123 0 com.carr.test\n"
        self.snapshot["launchctl_disabled"] = '"com.carr.test" => true'
        self.snapshot["launchctl_print"]["com.carr.test"] = launchctl_print(
            "com.carr.test", ["/bin/sh", "/machine/carr-system/bin/task.sh"], EVERY_TWO_MINUTES, state="running")
        self.assertEqual(self.codes(), ["unexpected_running"])

    def test_registration_of_an_uninstalled_job_is_reported_despite_override(self):
        self.manifest["jobs"][0].update(expected_enabled=False, expected_installed=False)
        self.snapshot["plists"] = {}
        self.snapshot["launchctl_disabled"] = '"com.carr.test" => true'
        self.assertEqual(self.codes(), ["unexpected_enabled"])

    def test_structured_arguments_keep_paths_with_spaces(self):
        self.use_program(["/bin/sh", "/machine/carr-system/bin/task with space.sh"])
        self.assertEqual(self.codes(), [])
        self.use_program(["/bin/sh", "/machine/carr-system/bin/task with space.sh"],
                         loaded=["/bin/sh", "/machine/carr-system/bin/task", "with", "space.sh"])
        self.assertEqual(self.codes(), ["program_drift"])

    def test_collector_reads_plist_and_only_runs_observation_commands(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            agents = home / "Library/LaunchAgents"
            agents.mkdir(parents=True)
            plist = self.snapshot["plists"]["com.carr.test"]
            plist["Label"] = "com.carr.test"
            plist["EnvironmentVariables"] = {"CARR_REPO": "/machine/carr-system"}
            (agents / "com.carr.test.plist").write_bytes(plistlib.dumps(plist))
            def run(argv, **kwargs):
                if argv[:2] == ["launchctl", "list"]: output = self.snapshot["launchctl_list"]
                elif argv[:2] == ["launchctl", "print-disabled"]: output = self.snapshot["launchctl_disabled"]
                elif argv[:2] == ["launchctl", "print"]: output = self.snapshot["launchctl_print"]["com.carr.test"]
                elif "--left-right" in argv: output = "0\t2\n"
                elif "symbolic-ref" in argv: output = "main\n"
                elif "ls-remote" in argv: output = "a"*40 + "\trefs/heads/main\n"
                elif "--show-toplevel" in argv: return subprocess.CompletedProcess(argv, 128, "", "not a git repository")
                elif "rev-parse" in argv: output = "a"*40 + "\n"
                else: output = ""
                return subprocess.CompletedProcess(argv, 0, output, "")
            with patch.object(Path, "home", return_value=home), \
                 patch.object(jobs.machine_role, "is_primary", return_value=False), \
                 patch.object(jobs.subprocess, "run", side_effect=run) as calls:
                snap = jobs.collect(self.manifest)
            self.assertEqual(snap["git"]["behind"], 2)
            self.assertEqual(snap["plists"]["com.carr.test"]["Label"], "com.carr.test")
            self.assertTrue({"/machine/carr-system/bin/task.sh", "/machine/carr-system"}
                            <= jobs.checkout_candidates(snap))
            commands = [c.args[0] for c in calls.call_args_list]
            for argv in commands:
                self.assertNotIn("fetch", argv)
                self.assertNotIn("enable", argv)
                self.assertNotIn("bootstrap", argv)
                self.assertNotIn("load", argv)

    def test_unrunnable_crontab_is_one_evidence_error(self):
        def run(argv, **kwargs):
            if argv[0] == "crontab":
                raise OSError("blocked")
            return subprocess.CompletedProcess(argv, 0, "", "")
        with tempfile.TemporaryDirectory() as raw, \
             patch.object(Path, "home", return_value=Path(raw)), \
             patch.object(jobs.machine_role, "is_primary", return_value=False), \
             patch.object(jobs.subprocess, "run", side_effect=run):
            snap = jobs.collect(self.manifest)
        self.assertEqual(snap["errors"].count("crontab"), 1)

    def test_reports_missing_unknown_disabled_failed_stale_and_behind(self):
        missing = copy.deepcopy(self.manifest["jobs"][0])
        missing["label"] = "local.carr-missing"
        self.manifest["jobs"].append(missing)
        self.snapshot["launchctl_list"] += "-\t7\tcom.carr.unknown\n"
        self.snapshot["launchctl_disabled"] = '"com.carr.test" => disabled'
        self.snapshot["launchctl_print"]["com.carr.test"] += "\n last exit code = 9\n"
        self.snapshot["log_mtimes"]["/logs/test.log"] = 10
        self.snapshot["git"]["behind"] = 4
        rows = jobs.report(self.manifest, self.snapshot, now=1000)
        self.assertEqual({(r["label"], r["code"]) for r in rows}, {
            ("local.carr-missing", "missing_job"), ("com.carr.unknown", "unknown_job"),
            ("com.carr.test", "disabled_but_expected"), ("com.carr.test", "failing_exit"),
            ("com.carr.test", "stale_log"), ("canonical", "behind_main")})

    def test_complete_healthy_snapshot_has_no_drift(self):
        self.assertEqual(jobs.report(self.manifest, self.snapshot, now=1000), [])

    def test_unpublished_or_diverged_canonical_commits_are_reported(self):
        self.snapshot["git"]["ahead"] = 1
        self.assertEqual(self.codes(), ["ahead_of_main"])
        self.snapshot["git"]["behind"] = 2
        self.assertEqual(self.codes(), ["behind_main", "ahead_of_main"])

    def test_launchctl_environment_path_does_not_become_a_runtime_checkout(self):
        self.snapshot["launchctl_print"]["com.carr.test"] += "\n environment = {\n PATH => /opt/homebrew/bin:/usr/bin\n }\n"
        self.snapshot["checkout_roots"] = {"/opt/homebrew/bin:/usr/bin": "/opt/homebrew"}
        self.assertEqual(jobs.report(self.manifest, self.snapshot, now=1000), [])

    def test_declared_retirement_does_not_fail_for_absent_disabled_job(self):
        job = self.manifest["jobs"][0]
        job.update(expected_enabled=False, expected_installed=False)
        self.snapshot["plists"] = {}
        self.snapshot["launchctl_list"] = "PID Status Label\n"
        self.snapshot["launchctl_disabled"] = '"com.carr.test" => true'
        self.assertEqual(jobs.report(self.manifest, self.snapshot, now=1000), [])

    def test_machine_placement_comes_from_the_launchd_scope_policy(self):
        self.snapshot.update(plists={}, launchctl_list="")
        with patch.object(jobs.launchd_scope, "PRIMARY_ONLY", {"com.carr.test.plist"}):
            self.assertEqual(self.codes(), [])
            self.snapshot["machine_role"] = "primary"
            self.assertEqual(self.codes(), ["missing_job"])
        with patch.object(jobs.launchd_scope, "SECONDARY_ONLY", {"com.carr.test.plist"}):
            self.assertEqual(self.codes(), [])

    def test_cron_schedule_and_unknown_commands_are_checked_without_exposing_arguments(self):
        self.manifest["jobs"] = [self.cron_job()]
        self.snapshot["cron"] = "TOKEN=private-value\n*/5 * * * * /machine/carr-system/bin/task.sh\n"
        rows = jobs.report(self.manifest, self.snapshot, now=1000)
        self.assertIn("interval_drift", [r["code"] for r in rows])
        self.snapshot["cron"] += "@daily /tmp/unknown.sh --token private-value\n"
        rows = jobs.report(self.manifest, self.snapshot, now=1000)
        self.assertIn("unknown_job", [r["code"] for r in rows])
        self.assertNotIn("private-value", "\n".join(jobs.render(rows)))

    def cron_job(self, command="/machine/carr-system/bin/task.sh"):
        self.snapshot.update(plists={}, launchctl_list="", launchctl_disabled="", launchctl_print={})
        job = {**self.manifest["jobs"][0], "scheduler": "cron", "interval": "0 * * * *",
               "program_path": "/machine/carr-system/bin/task.sh", "log_max_age_seconds": None,
               "freshness_exception": "fixture"}
        job.pop("program_arguments", None)
        job["label"] = jobs.cron_label(command)
        return job

    def test_every_cron_firing_is_compared(self):
        self.manifest["jobs"] = [self.cron_job()]
        self.snapshot["cron"] = "0 * * * * /machine/carr-system/bin/task.sh\n"
        self.assertEqual(self.codes(), [])
        self.snapshot["cron"] = ("*/5 * * * * /machine/carr-system/bin/task.sh\n"
                                 "0 * * * * /machine/carr-system/bin/task.sh\n")
        self.assertEqual(self.codes(), ["duplicate_entry", "interval_drift"])
        self.snapshot["cron"] = "0 * * * * /machine/carr-system/bin/task.sh\n" * 2
        self.assertEqual(self.codes(), ["duplicate_entry"])

    def test_cron_working_checkout_is_enforced(self):
        command = "cd /other-clone && /machine/carr-system/bin/task.sh"
        self.manifest["jobs"] = [self.cron_job(command)]
        self.snapshot["cron"] = f"0 * * * * {command}\n"
        self.snapshot["checkout_roots"] = {"/other-clone": "/other-clone"}
        self.assertEqual(self.codes(), ["wrong_checkout"])
        command = "cd /machine/carr-system && /machine/carr-system/bin/task.sh"
        self.manifest["jobs"] = [self.cron_job(command)]
        self.snapshot["cron"] = f"0 * * * * {command}\n"
        self.assertEqual(self.codes(), [])

    def test_read_failures_stay_visible_and_cli_returns_nonzero(self):
        self.snapshot["errors"] = ["launchctl_disabled"]
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            snapshot = root / "snapshot.json"
            snapshot.write_text(json.dumps(self.snapshot))
            result = subprocess.run([sys.executable, str(ROOT / "ops/scheduled-jobs-check.py"),
                                     "--fixture", str(snapshot),
                                     "--now", "1000", "--json"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            rows = json.loads(result.stdout)
            self.assertEqual([(r["label"], r["code"]) for r in rows if r["code"] == "evidence_unavailable"],
                             [("launchctl_disabled", "evidence_unavailable")])

    def test_portable_watch_paths_compare_with_live_absolute_paths(self):
        argv = ["/bin/sh", "/machine/carr-system/bin/task.sh"]
        self.manifest["jobs"][0]["interval"] = {"WatchPaths": ["~/Recordings"]}
        self.manifest["jobs"][0].update(log_max_age_seconds=None, freshness_exception="event-driven")
        live = {"WatchPaths": [str(Path.home() / "Recordings")]}
        self.snapshot["plists"]["com.carr.test"] = {"ProgramArguments": argv, **live,
                                                    "StandardOutPath": "/logs/test.log"}
        self.snapshot["launchctl_print"]["com.carr.test"] = launchctl_print("com.carr.test", argv, live)
        self.assertEqual(jobs.report(self.manifest, self.snapshot, now=1000), [])

    def test_watchdog_files_the_same_loop_key_and_does_not_clear_blinded_evidence(self):
        sys.path.insert(0, str(ROOT / "lib"))
        import job_watchdog as watchdog
        config = watchdog.load_config(ROOT / "ops/config/job-watchdog.json")
        self.snapshot["git"]["behind"] = 3
        rows = jobs.report(self.manifest, self.snapshot, now=1000)
        found = watchdog.detect({"scheduled_jobs": rows}, config, 1000)
        self.assertEqual(found[0]["key"], "scheduled_jobs:canonical:behind_main")
        self.assertEqual(found[0]["owner"], "orchestrator")
        self.assertIn("fleet-sync", found[0]["next_action"])

    def test_health_jobs_section_prints_bound_action_and_records_finding(self):
        self.snapshot["git"]["behind"] = 3
        fixture = {"now": "2026-10-05T17:00:00Z", "jobs": [], "job_definitions": [],
                   "scheduled_jobs": self.snapshot}
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path = root / "health.json"
            path.write_text(json.dumps(fixture))
            result = subprocess.run([sys.executable, str(ROOT / "tools/health-check.py"),
                                     "--section", "jobs", "--fixture", str(path)],
                                    capture_output=True, text=True, timeout=15)
            self.assertIn("scheduled_jobs:canonical:behind_main", result.stdout)
            self.assertIn("auto-clear:", result.stdout)

    def test_incomplete_watchdog_scan_retains_previous_drift(self):
        import job_watchdog as watchdog
        config = watchdog.load_config(ROOT / "ops/config/job-watchdog.json")
        row = {"key": "scheduled_jobs:canonical:behind_main", "kind": "scheduled_job_drift",
               "subject": "canonical", "reason": "behind", "next_action": "repair fleet-sync",
               "owner": "orchestrator", "needs_joe": None}
        class Effects:
            def report(self, finding):
                return {}
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            effects = Effects()
            watchdog.reconcile(root, config, [row], effects, 1000)
            error = watchdog.finding("collection_error", "scheduled_jobs", "read failed", config,
                                     blinds=["scheduled_job_drift"])
            watchdog.reconcile(root, config, [error], effects, 1001)
            prior = watchdog.read_latest(root / config["paths"]["findings"])[row["key"]]
            self.assertIsNone(prior["cleared_at"])
            watchdog.reconcile(root, config, [], effects, 1002)
            prior = watchdog.read_latest(root / config["paths"]["findings"])[row["key"]]
            self.assertIsNotNone(prior["cleared_at"])

    def test_complete_scan_closes_the_drift_loop_and_retries_a_failed_closure(self):
        import job_watchdog as watchdog
        config = watchdog.load_config(ROOT / "ops/config/job-watchdog.json")
        row = {"key": "scheduled_jobs:canonical:behind_main", "kind": "scheduled_job_drift",
               "subject": "canonical", "reason": "behind", "next_action": "repair fleet-sync",
               "owner": "orchestrator", "needs_joe": None}
        class Effects:
            closed, fail = [], True
            def report(self, finding):
                return {"loop_id": "fixture-loop"}
            def clear(self, finding):
                if self.fail:
                    raise RuntimeError("record layer down")
                self.closed.append(finding["loop_id"])
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            effects = Effects()
            watchdog.reconcile(root, config, [row], effects, 1000)
            watchdog.reconcile(root, config, [], effects, 1001)
            findings = watchdog.read_latest(root / config["paths"]["findings"])
            self.assertIsNone(findings[row["key"]]["cleared_at"])
            self.assertEqual(findings["record_error:" + row["key"]]["kind"], "record_error")
            effects.fail = False
            watchdog.reconcile(root, config, [], effects, 1002)
            self.assertEqual(effects.closed, ["fixture-loop"])
            findings = watchdog.read_latest(root / config["paths"]["findings"])
            self.assertIsNotNone(findings[row["key"]]["cleared_at"])

    def test_recovery_closes_versioned_loop_and_recurrence_gets_a_new_episode(self):
        import job_watchdog as watchdog
        config = watchdog.load_config(ROOT / "ops/config/job-watchdog.json")
        row = {"key": "scheduled_jobs:canonical:behind_main", "kind": "scheduled_job_drift",
               "subject": "canonical", "reason": "behind", "next_action": "repair fleet-sync",
               "owner": "orchestrator", "needs_joe": None, "first_seen": "episode-one"}
        responses = [json.dumps({"ok": True, "loop_id": "fixture-loop"}),
                     json.dumps({"ok": True, "loop": {"loop_id": "fixture-loop", "status": "open", "version": 4}}),
                     json.dumps({"ok": True, "status": "done"}),
                     json.dumps({"ok": True, "loop_id": "fixture-loop-two"})]
        with tempfile.TemporaryDirectory() as raw:
            effects = watchdog.Effects(Path(raw), config)
            with patch.object(watchdog, "command", side_effect=responses) as calls:
                receipt = effects.report(row)
                self.assertEqual(receipt["loop_id"], "fixture-loop")
                effects.clear({**row, **receipt})
                payload = json.loads(calls.call_args_list[-1].args[0][-1])
                self.assertEqual(payload["base_version"], 4)
                self.assertEqual(payload["resolution"], "done")
                self.assertIn("complete scheduled-job scan", payload["outcome"])
                effects.report({**row, "first_seen": "episode-two"})
                first = json.loads(calls.call_args_list[0].args[0][-1])
                second = json.loads(calls.call_args_list[-1].args[0][-1])
                self.assertNotEqual(first["idempotency_key"], second["idempotency_key"])


class ManifestTests(unittest.TestCase):
    def test_native_definitions_supply_program_and_schedule(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launchd = root / "ops/launchd"
            launchd.mkdir(parents=True)
            path = launchd / "com.carr.test.plist"
            definition = {"Label": "com.carr.test", "ProgramArguments": ["{{REPO}}/bin/task.sh"],
                          "StartCalendarInterval": {"Hour": 3, "Minute": 15},
                          "StandardOutPath": "{{REPO}}/out/task.log"}
            path.write_bytes(plistlib.dumps(definition))
            manifest = jobs.native_manifest(root)
            self.assertEqual(len(manifest["jobs"]), 1)
            job = manifest["jobs"][0]
            self.assertEqual(job["program_arguments"], ["~/carr-system/bin/task.sh"])
            self.assertEqual(job["interval"], {"StartCalendarInterval": {"Hour": 3, "Minute": 15}})
            definition["StartCalendarInterval"]["Hour"] = 5
            path.write_bytes(plistlib.dumps(definition))
            self.assertEqual(jobs.native_manifest(root)["jobs"][0]["interval"],
                             {"StartCalendarInterval": {"Hour": 5, "Minute": 15}})

    def test_definition_only_jobs_are_not_expected_to_run(self):
        manifest = jobs.native_manifest()
        for label in ("com.carr.repo-hygiene-janitor",):
            job = next(j for j in manifest["jobs"] if j["label"] == label)
            self.assertFalse(job["expected_enabled"])
            self.assertFalse(job["expected_installed"])


if __name__ == "__main__":
    unittest.main()
