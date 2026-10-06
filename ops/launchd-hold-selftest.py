#!/usr/bin/env python3
import ast
import contextlib
import importlib.util
import io
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
REAL_RUN = subprocess.run
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("cac_hold", ROOT / "ops/config-as-code.py")
assert spec is not None and spec.loader is not None
cac = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cac)


class HoldTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.hold = self.home / ".config/carr/launchd-hold"
        self.hold.parent.mkdir(parents=True)
        self.label = "com.carr.synthetic-held"
        self.hold.write_text(self.label + " API budget exhausted\n")
        self.dest = self.home / (self.label + ".plist")
        self.body = plistlib.dumps({"Label": self.label, "ProgramArguments": ["/usr/bin/true"],
                                   "StartCalendarInterval": [{"Minute": 0}]}).decode()
        self.dest.write_text(self.body)
        self.loaded, self.disabled, self.calls = True, True, []
        self.addCleanup(patch.stopall)
        patch.dict(cac.launchd_hold.DEFINITION_ONLY, {}, clear=True).start()
        patch.dict(os.environ, HOME=str(self.home)).start()
        patch.object(cac, "HOME", str(self.home)).start()
        patch.object(cac.subprocess, "run", side_effect=self.launchctl).start()

    def launchctl(self, argv, **kwargs):
        self.calls.append(argv)
        mode = argv[1]
        rc, out = 0, ""
        if mode == "print":
            rc = 0 if self.loaded else 113
            out = f"path = {self.dest}\n" if self.loaded else f'Could not find service "{self.label}"'
        elif mode == "print-disabled":
            out = f'"{self.label}" => {str(self.disabled).lower()}\n'
        elif mode in {"unload", "bootout"}:
            self.loaded = False
        elif mode == "disable":
            self.disabled = True
        elif mode in {"load", "bootstrap", "enable", "kickstart"}:
            self.loaded, self.disabled = True, False
        return subprocess.CompletedProcess(argv, rc, out, out if rc else "")

    def test_calendar_command_reconciles_a_hold_once(self):
        row = {"name": self.dest.name, "action": "hold", "label": self.label, "why": "held"}
        patch.object(cac, "launchd_calendar_reinstall_plan", return_value=[row]).start()
        self.assertEqual(cac.cmd_reinstall_launchd_calendar(["--apply", "--launchctl", "launchctl"]), 0)
        self.assertEqual(sum(call[1] == "disable" for call in self.calls), 1)

    def test_late_invalid_hold_keeps_install_failure_report(self):
        self.hold.write_text("")
        def invalid_after_unload(argv, **kwargs):
            result = self.launchctl(argv, **kwargs)
            if argv[1] == "unload":
                self.hold.write_text(self.label + "\n")
            return result
        cac.subprocess.run.side_effect = invalid_after_unload
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            outcome = cac.install_launchd_plist(self.dest.name, str(self.dest), self.body, False)
        self.assertEqual(outcome, "failed")
        self.assertIn("fix by hand", output.getvalue())
        self.assertTrue(Path(str(self.dest) + ".pending-reload").exists())
        self.assertFalse(any(c[1] == "load" for c in self.calls))

    def test_late_invalid_hold_restores_calendar_body_and_reports_unloaded(self):
        self.hold.write_text("")
        def invalid_after_bootout(argv, **kwargs):
            result = self.launchctl(argv, **kwargs)
            if argv[1] == "bootout":
                self.hold.write_text(self.label + "\n")
            return result
        cac.subprocess.run.side_effect = invalid_after_bootout
        previous = self.body.replace("/usr/bin/true", "/usr/bin/false")
        row = {"name": self.dest.name, "dest": str(self.dest), "label": self.label,
               "body": self.body, "previous": previous}
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with self.assertRaises(cac.AgentLeftUnloaded):
                cac.reinstall_calendar_agent(row, "launchctl", f"gui/{os.getuid()}", True)
        self.assertEqual(self.dest.read_text(), previous)
        self.assertIn("RESTORE FAILED", output.getvalue())
        self.assertIn("fix by hand", output.getvalue())

    def test_check_does_not_misattribute_unrelated_failure(self):
        patch.object(cac, "_cmd_check", side_effect=OSError("broken plist read")).start()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with self.assertRaisesRegex(OSError, "broken plist read"):
                cac.cmd_check()
        self.assertNotIn("HOLD INVALID", output.getvalue())

    def test_pull_identifies_invalid_hold(self):
        self.hold.write_text(self.label + "\n")
        patch.object(cac, "codex_configuration_state", return_value="absent").start()
        patch.object(cac, "secondary_scheduled_task_violations", return_value=[]).start()
        patch.object(cac, "pairs", side_effect=ValueError("invalid hold")).start()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(cac.cmd_pull(False), 1)
        self.assertIn("HOLD INVALID", output.getvalue())
        self.assertNotIn("continuity configuration", output.getvalue())

    def test_pull_uses_validated_holds_when_file_changes_during_collection(self):
        def hooks():
            self.hold.write_text(self.label + "\n")
            return {}
        patch.object(cac, "live_hooks_block", side_effect=hooks).start()
        patch.object(cac, "codex_configuration_state", return_value="absent").start()
        patch.object(cac, "secondary_scheduled_task_violations", return_value=[]).start()
        patch.object(cac, "TASKS_SRC", str(self.home / "absent-tasks")).start()
        patch.object(cac, "tracked_scheduled_task_paths", return_value={}).start()
        patch.object(cac, "carr_plists", return_value=[self.dest.name]).start()
        patch.object(cac, "launchd_repo_path", return_value=str(self.dest)).start()
        hooks_repo = self.home / "hooks.json"
        hooks_repo.write_text("{}\n")
        patch.object(cac, "HOOKS_REPO", str(hooks_repo)).start()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(cac.cmd_pull(False), 0)
        self.assertNotIn("INVALID", output.getvalue())
        self.assertIn("0 item(s) would be written", output.getvalue())

    def test_watchdog_uses_launchd_hold_before_config(self):
        self.hold.write_text("com.carr.job-watchdog operator repair\n")
        patch.stopall()
        result = REAL_RUN([sys.executable, str(ROOT / "tools/job-watchdog.py"),
                           "--config", str(self.home / "missing.json"), "scan"],
                          env=dict(os.environ, HOME=str(self.home)), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("HELD com.carr.job-watchdog", result.stdout)

    def test_watchdog_legacy_off_file_does_not_silence_it(self):
        (self.hold.parent / "job-watchdog.off").touch()
        self.hold.write_text("")
        patch.stopall()
        result = REAL_RUN([sys.executable, str(ROOT / "tools/job-watchdog.py"),
                           "--config", str(self.home / "missing.json"), "scan"],
                          env=dict(os.environ, HOME=str(self.home)), capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)

    def test_plist_marker_is_not_a_second_definition_registry(self):
        self.hold.write_text("")
        body = self.body.replace("<plist", "<!-- carr-launchd-definition-only: unused -->\n<plist", 1)
        self.assertEqual(cac.install_launchd_plist(self.dest.name, str(self.dest), body, False), "loaded")

    def test_fleet_has_no_separate_hold_command_or_baseline_mode(self):
        self.assertNotIn("launchd-held", (ROOT / "bin/fleet-sync.sh").read_text())
        self.assertNotIn("CARR_TEST_" + "HOLD_BASELINE", Path(__file__).read_text())

    def test_full_install_reconciles_held_plist_once(self):
        config = self.home / "hooks.json"
        config.write_text('{"hooks": {}}')
        templates = self.home / "templates"
        templates.mkdir()
        (templates / self.dest.name).write_text(self.body)
        settings = self.home / "settings.json"
        settings.write_text('{"hooks": {}}')
        for name, value in {"SETTINGS": str(settings), "HOOKS_REPO": str(config),
                            "LAUNCHD_REPO": str(templates), "LAUNCHD_SRC": str(self.home),
                            "REPO": str(self.home), "IS_PRIMARY": False}.items():
            patch.object(cac, name, value).start()
        patch.object(cac, "LAUNCHD_ALT_REPO", {}).start()
        patch.object(cac, "codex_configuration_state", return_value="absent").start()
        patch.object(cac, "tracked_scheduled_task_paths", return_value={}).start()
        patch.object(cac, "scheduled_task_install_plan", return_value={"modified": [], "exact": []}).start()
        patch.object(cac.continuity_config, "load", return_value=None).start()
        patch.object(cac.continuity_config, "read_mode", return_value=None).start()
        patch.object(cac, "claude_continuity_state", return_value=(None, None)).start()
        patch.object(cac.continuity_config, "render_effective_hooks", return_value={}).start()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(cac.cmd_install(True), 0)
        self.assertEqual(sum(c[1] == "disable" for c in self.calls), 1)
        self.assertEqual(output.getvalue().count("HELD " + self.label), 1)
        self.assertFalse(self.loaded)
        self.assertTrue(self.disabled)
        self.assertFalse(any(c[1] in {"load", "bootstrap", "kickstart"} for c in self.calls))
        self.hold.write_text(self.label + "\n")
        self.calls.clear()
        self.assertEqual(cac.cmd_install(True), 1)
        self.assertEqual(self.calls, [])

    def test_board_hold_and_invalid_hold_are_reported(self):
        self.label = "local.carr-progress-board"
        self.hold.write_text(self.label + " repair\n")
        self.assertEqual(cac.cmd_install_progress_board(False), 0)
        self.assertEqual(self.calls, [])
        self.assertEqual(cac.cmd_install_progress_board(True), 0)
        self.assertFalse(self.loaded)
        self.assertTrue(self.disabled)
        self.hold.write_text(self.label + "\n")
        self.assertEqual(cac.cmd_install_progress_board(True), 1)

    def test_invalid_calendar_hold_refuses_before_mutation(self):
        self.hold.write_text(self.label + "\n")
        self.assertEqual(cac.cmd_reinstall_launchd_calendar(["--apply"]), 1)
        self.assertEqual(self.calls, [])

    def test_parser_refusals_and_comments(self):
        for text in (self.label + " repair\n" + self.label + " duplicate\n",
                     self.label + " @2999-01-01T00:00:00Z repair\n",
                     self.label + " @2020-01-01T00:00:00 repair\n",
                     self.label + " @2020-not-a-date repair\n"):
            with self.subTest(text=text):
                self.hold.write_text(text)
                with self.assertRaises(ValueError):
                    cac.launchd_hold.read_holds(self.home)
        self.hold.write_text("  # ignored\n\n" + self.label + " repair # keep reason\n")
        self.assertEqual(cac.launchd_hold.read_holds(self.home)[self.label].reason, "repair # keep reason")

    def test_ensure_off_failure_paths_refuse_activation(self):
        for failure in ("disable", "bootout", "still-loaded"):
            with self.subTest(failure=failure):
                self.loaded, self.disabled, self.calls = True, False, []
                def failed(argv, **kwargs):
                    if argv[1] == failure:
                        self.calls.append(argv)
                        return subprocess.CompletedProcess(argv, 1, "", "fixture failure")
                    result = self.launchctl(argv, **kwargs)
                    if failure == "still-loaded" and argv[1] == "bootout":
                        self.loaded = True
                    return result
                cac.subprocess.run.side_effect = failed
                self.assertEqual(cac.install_launchd_plist(self.dest.name, str(self.dest), self.body, True), "failed")
                self.assertFalse(any(c[1] in {"load", "bootstrap", "kickstart"} for c in self.calls))

    def test_health_row_propagates_warning_and_record_call(self):
        from lib import launchd_hold_health
        from types import SimpleNamespace
        tree = ast.parse((ROOT / "tools/health-check.py").read_text())
        block = next(node for node in tree.body if isinstance(node, ast.Try) and any(
            isinstance(child, ast.ImportFrom) and any(alias.name == "launchd_hold_health" for alias in child.names)
            for child in ast.walk(node)))
        calls = []
        def checked(home, verb):
            verb("add-loop", {"fixture": True})
            return "WARN launchd holds fixture alarm", 1
        scope = {"sys": sys, "os": os, "REPO_ROOT": str(ROOT), "rc": 0,
                 "_jev_outage": SimpleNamespace(call_verb=lambda name, payload, **kwargs: calls.append(name))}
        with patch.object(launchd_hold_health, "check", side_effect=checked):
            exec(compile(ast.Module(body=[block], type_ignores=[]), "health-row", "exec"), scope)
        self.assertEqual(scope["rc"], 1)
        self.assertEqual(calls, ["add-loop"])

    def test_health_row_fallback_names_the_same_owner(self):
        from lib import launchd_hold_health
        from types import SimpleNamespace
        tree = ast.parse((ROOT / "tools/health-check.py").read_text())
        block = next(node for node in tree.body if isinstance(node, ast.Try) and any(
            isinstance(child, ast.ImportFrom) and any(alias.name == "launchd_hold_health" for alias in child.names)
            for child in ast.walk(node)))
        scope = {"sys": sys, "os": os, "REPO_ROOT": str(ROOT), "rc": 0,
                 "_jev_outage": SimpleNamespace(call_verb=lambda *args, **kwargs: {})}
        output = io.StringIO()
        with patch.object(launchd_hold_health, "check", side_effect=RuntimeError("fixture failure")):
            with contextlib.redirect_stdout(output):
                exec(compile(ast.Module(body=[block], type_ignores=[]), "health-row", "exec"), scope)
        self.assertEqual(scope["rc"], 1)
        self.assertIn("owner claude (Platform Engineer)", output.getvalue())

    def test_definition_only_check_uses_plist_label(self):
        self.hold.write_text("")
        cac.launchd_hold.DEFINITION_ONLY[self.label + ".plist"] = "repair"
        patch.object(cac, "carr_plists", return_value=["renamed.plist"]).start()
        patch.object(cac, "launchd_repo_path", return_value=str(self.dest)).start()
        self.assertEqual(cac.definition_only_installed_plists(), ["renamed.plist"])

    def test_detached_guard_reads_the_same_definition_registry(self):
        patch.stopall()
        label = "com.carr.repo-hygiene-janitor"
        fake = self.home / "launchctl-fixture"
        fake.write_text("#!/bin/sh\ncase \"$1\" in\n"
                        "print) echo 'Could not find service \"" + label + "\"'; exit 113;;\n"
                        "print-disabled) echo '\"" + label + "\" => true';;\nesac\n")
        fake.chmod(0o755)
        result = REAL_RUN([sys.executable, str(ROOT / "lib/launchd_hold.py"), label,
                           str(fake), f"gui/{os.getuid()}"],
                          env=dict(os.environ, HOME=str(self.home)), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("DEFINITION ONLY " + label, result.stdout)

    def test_install_keeps_held_job_disabled_and_unloaded(self):
        self.assertEqual(cac.install_launchd_plist(self.dest.name, str(self.dest), self.body, True), "held")
        self.assertTrue(self.disabled)
        self.assertFalse(self.loaded)
        self.assertFalse(any(c[1] in {"load", "bootstrap", "enable", "kickstart"} for c in self.calls))

    def test_reinstall_with_kickstart_keeps_hold(self):
        row = {"name": self.dest.name, "dest": str(self.dest), "label": self.label,
               "body": self.body, "previous": self.body}
        cac.reinstall_calendar_agent(row, "launchctl", f"gui/{os.getuid()}", True)
        self.assertTrue(self.disabled)
        self.assertFalse(self.loaded)
        self.assertFalse(any(c[1] in {"bootstrap", "kickstart"} for c in self.calls))

    def test_hold_added_during_bootout_prevents_bootstrap(self):
        self.hold.write_text("")
        def late_hold(argv, **kwargs):
            result = self.launchctl(argv, **kwargs)
            if argv[1] == "bootout":
                self.hold.write_text(self.label + " operator stopped it during reload\n")
            return result
        cac.subprocess.run.side_effect = late_hold
        row = {"name": self.dest.name, "dest": str(self.dest), "label": self.label,
               "body": self.body, "previous": self.body}
        self.assertTrue(cac.reinstall_calendar_agent(row, "launchctl", f"gui/{os.getuid()}", True))
        self.assertTrue(self.disabled)
        self.assertFalse(self.loaded)
        self.assertFalse(any(c[1] in {"bootstrap", "kickstart"} for c in self.calls))

    def test_unheld_matching_job_remains_loaded(self):
        self.hold.write_text("")
        self.disabled = False
        self.assertEqual(cac.install_launchd_plist(self.dest.name, str(self.dest), self.body, True), "kept")
        self.assertTrue(self.loaded)
        self.assertFalse(self.disabled)
        self.assertEqual([c[1] for c in self.calls], ["print"])

    def test_disabled_readback_accepts_words_and_rejects_another_label(self):
        for key, expected in ((self.label, "held"), ("other" + self.label, "failed")):
            self.calls.clear()
            def words(argv, **kwargs):
                result = self.launchctl(argv, **kwargs)
                if argv[1] == "print-disabled":
                    result.stdout = f'{key} => disabled\n'
                return result
            cac.subprocess.run.side_effect = words
            self.assertEqual(cac.install_launchd_plist(self.dest.name, str(self.dest), self.body, True), expected)

    def test_invalid_hold_file_refuses_without_launchctl(self):
        self.hold.write_text(self.label + "\n")
        self.assertEqual(cac.install_launchd_plist(self.dest.name, str(self.dest), self.body, True), "failed")
        self.assertEqual(self.calls, [])

    def test_check_reports_held_with_reason_age(self):
        patch.object(cac, "carr_plists", return_value=[self.dest.name]).start()
        patch.object(cac, "LAUNCHD_SRC", str(self.home)).start()
        patch.object(cac, "launchd_repo_path", return_value=str(self.dest)).start()
        patch.object(cac, "live_hooks_block", return_value={}).start()
        patch.object(cac, "codex_configuration_state", return_value="absent").start()
        patch.object(cac, "TASKS_SRC", str(self.home / "absent")).start()
        patch.object(cac, "tracked_scheduled_task_paths", return_value={}).start()
        self.assertFalse(any(label.startswith("launchd ") for label, _, _ in cac.pairs()))
        patch.object(cac, "pairs", return_value=[]).start()
        for name in ("hook_scripts_untracked", "secondary_scheduled_task_violations",
                     "refused_launchd_templates", "pending_launchd_reloads"):
            patch.object(cac, name, return_value=[]).start()
        patch.object(cac, "PREREQUISITE_CHECK", return_value=[]).start()
        patch.object(cac.continuity_config, "load", return_value={}).start()
        patch.object(cac.continuity_config, "read_mode", return_value=None).start()
        patch.object(cac, "git_hooks_path_report", return_value="fixture hooks").start()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(cac.cmd_check(), 0)
        self.assertTrue(output.getvalue().splitlines()[0].startswith("config-as-code: OK"))
        self.assertIn("HELD", output.getvalue())
        self.assertIn("API budget exhausted", output.getvalue())
        self.assertIn("age", output.getvalue())
        self.assertNotIn("DRIFT", output.getvalue())

    def test_definition_only_registry_cannot_load(self):
        self.hold.write_text("")
        cac.launchd_hold.DEFINITION_ONLY[self.dest.name] = "awaiting repair"
        self.assertEqual(cac.install_launchd_plist(self.dest.name, str(self.dest), self.body, False), "held")
        self.assertTrue(self.disabled)
        self.assertFalse(self.loaded)

    def test_fleet_sync_logs_hold_and_does_not_enable(self):
        patch.stopall()
        fleet_spec = importlib.util.spec_from_file_location("fleet_hold_fixture", ROOT / "ops/fleet-sync-selftest.py")
        fleet = importlib.util.module_from_spec(fleet_spec)
        fleet_spec.loader.exec_module(fleet)
        clone = fleet.build(str(self.home))
        fake_dir = self.home / "stubs"
        fake_dir.mkdir()
        state = self.home / "state.json"
        state.write_text('{"loaded":true,"disabled":true,"calls":[]}')
        fake = fake_dir / "launchctl"
        fake.write_text(f'''#!{sys.executable}
import json, sys
from pathlib import Path
p=Path({str(state)!r}); s=json.loads(p.read_text()); mode=sys.argv[1]
s['calls'].append(sys.argv[1:]); rc=0
if mode=='print':
    if sys.argv[-1].endswith('com.carr.fleet-sync'): rc=113
    elif s['loaded']: print('path = '+{str(self.dest)!r})
    else: print('Could not find service "{self.label}"', file=sys.stderr); rc=113
elif mode=='print-disabled': print('"{self.label}" => '+str(s['disabled']).lower())
elif mode=='disable': s['disabled']=True
elif mode in ('unload','bootout'): s['loaded']=False
else: s['disabled']=False; s['loaded']=True
p.write_text(json.dumps(s)); sys.exit(rc)
''')
        fake.chmod(0o755)
        installer = Path(clone) / "ops/config-as-code.py"
        installer.write_text(f'''import importlib.util, sys
s=importlib.util.spec_from_file_location('real_cac',{str(ROOT / 'ops/config-as-code.py')!r})
m=importlib.util.module_from_spec(s); s.loader.exec_module(m)
sys.exit(0 if m.install_launchd_plist({self.dest.name!r},{str(self.dest)!r},{self.body!r},True)=='held' else 1)
''')
        result = fleet.run_sync(clone, {"HOME": str(self.home), "PATH": str(fake_dir)+os.pathsep+os.environ["PATH"]})
        import json
        actual = json.loads(state.read_text())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("HELD " + self.label, result.stdout)
        self.assertTrue(actual["disabled"])
        self.assertFalse(actual["loaded"])
        self.assertFalse(any(c[0] in {"enable", "load", "bootstrap", "kickstart"} for c in actual["calls"]))

        staged = self.home / "staged.plist"
        staged.write_text(self.body)
        import hashlib
        for reason in ("local hold",):
            state.write_text('{"loaded":true,"disabled":false,"calls":[]}')
            previous_hash = hashlib.sha256(self.dest.read_bytes()).hexdigest()
            gone = subprocess.Popen(["/usr/bin/true"])
            gone.wait()
            result = REAL_RUN(["/bin/bash", "-c", cac.SELF_RELOAD_SCRIPT, "carr-self-reload",
                               str(gone.pid), str(fake), f"gui/{os.getuid()}", self.label,
                               str(staged), str(self.dest), str(self.home / "handoff.log"), "3",
                               previous_hash, sys.executable, str(ROOT / "lib/launchd_hold.py"), str(self.dest)],
                              env=dict(os.environ, HOME=str(self.home)), capture_output=True, text=True, timeout=10)
            actual = json.loads(state.read_text())
            self.assertEqual(result.returncode, 0, (self.home / "handoff.log").read_text())
            self.assertTrue(actual["disabled"])
            self.assertFalse(actual["loaded"])
            self.assertFalse(any(c[0] == "bootstrap" for c in actual["calls"]))


if __name__ == "__main__":
    unittest.main()
