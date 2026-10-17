#!/usr/bin/env python3
import importlib.util
import ast
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "storage_hygiene", ROOT / "tools" / "storage_hygiene.py")
assert SPEC is not None and SPEC.loader is not None
storage_hygiene = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = storage_hygiene
SPEC.loader.exec_module(storage_hygiene)


class StorageHygieneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="storage-hygiene-test-")
        self.root = Path(self.temp.name).resolve()
        self.tmp = self.root / "T"
        self.clones = self.root / "X" / "com.google.Chrome.code_sign_clone"
        self.replay = self.root / "cache" / "carr-gate-replay"
        for path in (self.tmp, self.clones, self.replay):
            path.mkdir(parents=True)
        self.now = 2_000_000.0

    def tearDown(self):
        self.temp.cleanup()

    def old_dir(self, parent, name):
        path = parent / name
        path.mkdir()
        os.utime(path, (self.now - 90_000, self.now - 90_000))
        return path

    def clone_only_plan(self):
        return storage_hygiene.plan_cleanup(
            clone_root=self.clones, tmp_root=self.tmp, replay_root=self.replay,
            now=self.now, older_than_seconds=86_400, max_items=10,
            is_open=lambda path: False, is_locked=lambda path: False,
            deadline=lambda: False)

    def test_default_scope_is_only_code_sign_clones(self):
        clone = self.old_dir(self.clones, "code_sign_clone.old")
        self.old_dir(self.tmp, "carr-old")
        self.old_dir(self.replay, "run-old")
        self.assertEqual([item.path for item in self.clone_only_plan().removable], [clone])

    @unittest.skipUnless(sys.platform == "darwin", "Darwin root discovery")
    def test_defaults_ignore_environment_temp_roots(self):
        expected = Path(subprocess.check_output(
            ["/usr/bin/getconf", "DARWIN_USER_TEMP_DIR"], text=True).strip()).resolve()
        with patch.dict(os.environ, {"TMPDIR": str(self.tmp), "TMP": str(self.tmp),
                                    "TEMP": str(self.tmp)}):
            tmp, clones, _ = storage_hygiene._defaults()
        self.assertEqual(tmp, expected)
        self.assertEqual(clones, expected.parent / "X" / "com.google.Chrome.code_sign_clone")

    def test_symlinked_clone_root_is_protected(self):
        outside = self.root / "outside"
        outside.mkdir()
        target = self.old_dir(outside, "code_sign_clone.old")
        self.clones.rmdir()
        self.clones.symlink_to(outside, target_is_directory=True)
        plan = self.clone_only_plan()
        self.assertEqual(plan.removable, [])
        self.assertIn(self.clones, plan.protected)
        self.assertTrue(target.exists())

    def test_symlinked_ancestor_is_protected(self):
        outside = self.root / "outside"
        self.clones.parent.rename(outside)
        self.clones.parent.symlink_to(outside, target_is_directory=True)
        plan = self.clone_only_plan()
        self.assertEqual(plan.removable, [])
        self.assertIn(self.clones, plan.protected)

    def test_apply_refuses_root_redirected_after_planning(self):
        target = self.old_dir(self.clones, "code_sign_clone.old")
        plan = self.clone_only_plan()
        saved = self.root / "saved-clones"
        self.clones.rename(saved)
        outside = self.root / "outside"
        outside.mkdir()
        victim = self.old_dir(outside, target.name)
        self.clones.symlink_to(outside, target_is_directory=True)
        result = storage_hygiene.apply_plan(
            plan, dry_run=False, ledger_path=self.root / "ledger.jsonl",
            record_finding=lambda payload: {"ok": True},
            is_open=lambda path: False, is_locked=lambda path: False)
        self.assertEqual(result.removed_count, 0)
        self.assertTrue(victim.exists())
        self.assertTrue((saved / target.name).exists())

    def test_incomplete_lsof_scans_protect_candidate(self):
        target = self.old_dir(self.clones, "code_sign_clone.old")
        for code in (0, 1):
            with self.subTest(code=code), patch.object(
                    storage_hygiene.subprocess, "run", return_value=subprocess.CompletedProcess(
                        [], code, "", "lsof: WARNING: can't stat() file system: Permission denied")):
                plan = storage_hygiene.plan_cleanup(
                    clone_root=self.clones, tmp_root=self.tmp, replay_root=self.replay,
                    now=self.now, older_than_seconds=86_400, max_items=10,
                    is_open=storage_hygiene.path_has_open_files,
                    is_locked=lambda path: False, deadline=lambda: False)
                self.assertEqual(plan.removable, [])
                self.assertIn(target, plan.protected)
        with patch.object(storage_hygiene.subprocess, "run", return_value=
                          subprocess.CompletedProcess([], 1, "", "")):
            self.assertFalse(storage_hygiene.path_has_open_files(target))

    def test_launchd_child_records_with_script_import_path(self):
        job = plistlib.loads((ROOT / "ops/launchd/com.carr.storage-hygiene.plist").read_bytes())
        script = job["ProgramArguments"][5].replace("{{REPO}}", str(ROOT))
        harness = self.root / "scheduled-fixture"
        harness.mkdir()
        # Instrument only filesystem/transport effects; execute launchd's actual child script.
        sitecustomize = """
import json, os, subprocess, sys
from pathlib import Path
from unittest.mock import patch

def configure(frame, event, arg):
    if event == 'call' and frame.f_code.co_name == 'main' and frame.f_code.co_filename == os.environ['FIXTURE_SCRIPT']:
        g = frame.f_globals
        g['REPO'] = Path(os.environ['FIXTURE_ROOT'])
        g['_defaults'] = lambda: (g['REPO'], g['REPO'], g['REPO'])
        g['plan_cleanup'] = lambda **kwargs: g['CleanupPlan']()
        g['storage_snapshot'] = lambda *args: (1_000_000_000_001, 0)
        def record_transport(argv, **kwargs):
            assert argv[1:3] == ['call', 'add-loop'], argv
            assert json.loads(argv[3])['kind'] == 'open_loop'
            assert json.loads(argv[3])['owner'] in ('joe', 'dell', 'claude'), argv[3]
            return subprocess.CompletedProcess(argv, 0, '{"ok":true}', '')
        patch('subprocess.run', side_effect=record_transport).start()
        sys.settrace(None)
    return configure
sys.settrace(configure)
"""
        (harness / "sitecustomize.py").write_text(sitecustomize)
        env = {key: value for key, value in os.environ.items()
               if key not in ("PYTHONPATH", "PYTHONHOME")}
        env.update(PYTHONPATH=str(harness), FIXTURE_SCRIPT=script,
                   FIXTURE_ROOT=str(harness))
        result = subprocess.run([sys.executable, script], cwd=ROOT, env=env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        row = json.loads((harness / "out/storage-hygiene.jsonl").read_text())
        self.assertEqual(row["record_status"], "recorded")
        self.assertIn("logical allocated size", result.stdout)
        self.assertIn("APFS", result.stdout)
        self.assertIn("free space", result.stdout)

    def test_apply_protects_path_when_lsof_warns_on_recheck(self):
        target = self.old_dir(self.clones, "code_sign_clone.old")
        plan = self.clone_only_plan()
        with patch.object(storage_hygiene.subprocess, "run", return_value=
                          subprocess.CompletedProcess([], 1, "", "Permission denied")):
            result = storage_hygiene.apply_plan(
                plan, dry_run=False, ledger_path=self.root / "ledger.jsonl",
                record_finding=lambda payload: {"ok": True})
        self.assertEqual(result.removed_count, 0)
        self.assertIn(target, plan.protected)
        self.assertTrue(target.exists())

    def test_apply_protects_root_redirected_during_open_scan(self):
        target = self.old_dir(self.clones, "code_sign_clone.old")
        plan = self.clone_only_plan()
        saved = self.root / "saved-clones"
        outside = self.root / "outside"
        outside.mkdir()
        victim = self.old_dir(outside, target.name)
        def redirect(path):
            self.clones.rename(saved)
            self.clones.symlink_to(outside, target_is_directory=True)
            return False
        result = storage_hygiene.apply_plan(
            plan, dry_run=False, ledger_path=self.root / "ledger.jsonl",
            record_finding=lambda payload: {"ok": True}, is_open=redirect)
        self.assertEqual(result.removed_count, 0)
        self.assertTrue(victim.exists())
        self.assertTrue((saved / target.name).exists())

    def test_ledger_labels_logical_size_and_physical_estimate_method(self):
        ledger = self.root / "ledger.jsonl"
        storage_hygiene.apply_plan(
            storage_hygiene.CleanupPlan(), dry_run=True, ledger_path=ledger,
            record_finding=lambda payload: self.fail("dry-run record"),
            eligible_logical_bytes=8192)
        row = json.loads(ledger.read_text())
        self.assertEqual(row["eligible_logical_bytes"], 8192)
        self.assertIsNone(row["physical_reclaim_bytes"])
        self.assertIn("APFS", row["physical_estimate_method"])
        self.assertIn("free space", row["physical_estimate_method"])

    def test_plan_is_allowlisted_old_closed_and_bounded(self):
        clone = self.old_dir(self.clones, "code_sign_clone.old")
        active = self.old_dir(self.clones, "code_sign_clone.active")
        scratch = self.old_dir(self.tmp, "flash-old")
        unknown = self.old_dir(self.tmp, "other-old")
        replay = self.old_dir(self.replay, "run-old")
        plan = storage_hygiene.plan_cleanup(
            clone_root=self.clones, tmp_root=self.tmp, replay_root=self.replay,
            include_scratch=True, include_replay=True,
            now=self.now, older_than_seconds=86_400, max_items=10,
            is_open=lambda path: path == active,
            is_locked=lambda path: False,
            deadline=lambda: False,
        )
        self.assertEqual({item.path for item in plan.removable}, {clone, scratch, replay})
        self.assertIn(active, plan.protected)
        self.assertIn(unknown, plan.unrecognized)

    def test_dry_run_never_removes_and_live_run_only_removes_plan(self):
        target = self.old_dir(self.tmp, "carr-old")
        ledger = self.root / "ledger.jsonl"
        plan = storage_hygiene.plan_cleanup(
            clone_root=self.clones, tmp_root=self.tmp, replay_root=self.replay,
            include_scratch=True, include_replay=True,
            now=self.now, older_than_seconds=86_400, max_items=10,
            is_open=lambda path: False, is_locked=lambda path: False,
            deadline=lambda: False,
        )
        dry = storage_hygiene.apply_plan(plan, dry_run=True, ledger_path=ledger,
                                         record_finding=lambda payload: self.fail("dry run wrote record"))
        self.assertTrue(target.exists())
        self.assertEqual(dry.removed_count, 0)
        live = storage_hygiene.apply_plan(plan, dry_run=False, ledger_path=ledger,
                                          record_finding=lambda payload: {"ok": True},
                                          is_open=lambda path: False,
                                          is_locked=lambda path: False)
        self.assertFalse(target.exists())
        self.assertEqual(live.removed_count, 1)
        rows = [json.loads(line) for line in ledger.read_text().splitlines()]
        self.assertEqual([row["mode"] for row in rows], ["dry-run", "apply"])

    def test_live_run_rechecks_open_state_before_removal(self):
        target = self.old_dir(self.tmp, "carr-became-active")
        ledger = self.root / "ledger.jsonl"
        plan = storage_hygiene.plan_cleanup(
            clone_root=self.clones, tmp_root=self.tmp, replay_root=self.replay,
            include_scratch=True, include_replay=True,
            now=self.now, older_than_seconds=86_400, max_items=10,
            is_open=lambda path: False, is_locked=lambda path: False,
            deadline=lambda: False,
        )
        result = storage_hygiene.apply_plan(
            plan, dry_run=False, ledger_path=ledger,
            record_finding=lambda payload: {"ok": True},
            is_open=lambda path: path == target,
            is_locked=lambda path: False)
        self.assertTrue(target.exists())
        self.assertIn(target, plan.protected)
        self.assertEqual(result.removed_count, 0)

    def test_unrecognized_count_is_exact_and_path_report_is_bounded(self):
        for index in range(28):
            self.old_dir(self.tmp, f"unknown-{index}")
        plan = storage_hygiene.plan_cleanup(
            clone_root=self.clones, tmp_root=self.tmp, replay_root=self.replay,
            include_scratch=True, include_replay=True,
            now=self.now, older_than_seconds=86_400, max_items=10,
            is_open=lambda path: False, is_locked=lambda path: False,
            deadline=lambda: False,
        )
        self.assertEqual(plan.unrecognized_count, 28)
        self.assertEqual(len(plan.unrecognized), storage_hygiene.UNKNOWN_SAMPLE_LIMIT)

    def test_item_cap_rotates_across_authorized_categories(self):
        clones = {self.old_dir(self.clones, f"code_sign_clone.{index}")
                  for index in range(4)}
        scratch = self.old_dir(self.tmp, "carr-old")
        replay = self.old_dir(self.replay, "run-old")
        plan = storage_hygiene.plan_cleanup(
            clone_root=self.clones, tmp_root=self.tmp, replay_root=self.replay,
            include_scratch=True, include_replay=True,
            now=self.now, older_than_seconds=86_400, max_items=3,
            is_open=lambda path: False, is_locked=lambda path: False,
            deadline=lambda: False,
        )
        selected = {item.path for item in plan.removable}
        self.assertEqual(len(selected & clones), 1)
        self.assertIn(scratch, selected)
        self.assertIn(replay, selected)

    def test_item_cap_stops_before_extra_candidates(self):
        for index in range(3):
            self.old_dir(self.tmp, f"carr-{index}")
        plan = storage_hygiene.plan_cleanup(
            clone_root=self.clones, tmp_root=self.tmp, replay_root=self.replay,
            include_scratch=True, include_replay=True,
            now=self.now, older_than_seconds=86_400, max_items=2,
            is_open=lambda path: False, is_locked=lambda path: False,
            deadline=lambda: False,
        )
        self.assertEqual(len(plan.removable), 2)
        self.assertEqual(plan.stop_reason, "item-cap")

    def test_live_run_rechecks_age_before_removal(self):
        target = self.old_dir(self.tmp, "carr-freshened")
        ledger = self.root / "ledger.jsonl"
        plan = storage_hygiene.plan_cleanup(
            clone_root=self.clones, tmp_root=self.tmp, replay_root=self.replay,
            include_scratch=True, include_replay=True,
            now=self.now, older_than_seconds=86_400, max_items=10,
            is_open=lambda path: False, is_locked=lambda path: False,
            deadline=lambda: False,
        )
        os.utime(target, (self.now, self.now))
        result = storage_hygiene.apply_plan(
            plan, dry_run=False, ledger_path=ledger,
            record_finding=lambda payload: {"ok": True},
            is_open=lambda path: False, is_locked=lambda path: False,
            now=lambda: self.now, older_than_seconds=86_400)
        self.assertTrue(target.exists())
        self.assertIn(target, plan.protected)
        self.assertEqual(result.removed_count, 0)

    def test_live_run_stops_deletion_at_the_time_cap(self):
        first = self.old_dir(self.tmp, "carr-first")
        second = self.old_dir(self.tmp, "carr-second")
        ledger = self.root / "ledger.jsonl"
        plan = storage_hygiene.plan_cleanup(
            clone_root=self.clones, tmp_root=self.tmp, replay_root=self.replay,
            include_scratch=True, include_replay=True,
            now=self.now, older_than_seconds=86_400, max_items=10,
            is_open=lambda path: False, is_locked=lambda path: False,
            deadline=lambda: False,
        )
        checks = iter((False, True))
        result = storage_hygiene.apply_plan(
            plan, dry_run=False, ledger_path=ledger,
            record_finding=lambda payload: {"ok": True},
            is_open=lambda path: False, is_locked=lambda path: False,
            deadline=lambda: next(checks))
        self.assertEqual(result.removed_count, 1)
        self.assertEqual(sum(path.exists() for path in (first, second)), 1)
        self.assertEqual(plan.stop_reason, "time-cap")

    def test_health_row_binds_threshold_to_investigation(self):
        row = storage_hygiene.health_row(
            used_bytes=1_000_000_000_001, clone_count=8465,
            threshold_bytes=1_000_000_000_000)
        self.assertIn("WARN", row)
        self.assertIn("8465", row)
        self.assertIn("opens the storage investigation loop", row)

    def test_live_clean_run_is_not_labeled_dry_run(self):
        ledger = self.root / "ledger.jsonl"
        result = storage_hygiene.apply_plan(
            storage_hygiene.CleanupPlan(), dry_run=False, ledger_path=ledger,
            record_finding=lambda payload: self.fail("clean run wrote a record"),
            disk_used=0)
        self.assertEqual(result.record_status, "not-needed")
        self.assertEqual(json.loads(ledger.read_text())["record_status"], "not-needed")

    def test_record_failure_still_writes_episode_ledger(self):
        self.old_dir(self.tmp, "carr-old")
        ledger = self.root / "ledger.jsonl"
        plan = storage_hygiene.plan_cleanup(
            clone_root=self.clones, tmp_root=self.tmp, replay_root=self.replay,
            include_scratch=True, include_replay=True,
            now=self.now, older_than_seconds=86_400, max_items=10,
            is_open=lambda path: False, is_locked=lambda path: False,
            deadline=lambda: False)
        result = storage_hygiene.apply_plan(
            plan, dry_run=False, ledger_path=ledger,
            record_finding=lambda payload: (_ for _ in ()).throw(RuntimeError("offline")),
            is_open=lambda path: False, is_locked=lambda path: False)
        row = json.loads(ledger.read_text())
        self.assertEqual(result.record_status, "record-failed")
        self.assertEqual(row["record_status"], "record-failed")
        self.assertTrue(row["finding_episode"])

    def test_gate_replay_lock_is_observed_without_creating_one(self):
        run = self.old_dir(self.replay, "run-locked")
        lock = run / ".active.lock"
        lock.write_text("")
        with lock.open("r+") as handle:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertTrue(storage_hygiene.replay_is_locked(run))
        unlocked = self.old_dir(self.replay, "run-unlocked")
        self.assertFalse(storage_hygiene.replay_is_locked(unlocked))
        self.assertFalse((unlocked / ".active.lock").exists())

    def test_health_check_exposes_storage_row(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "health-check.py"),
             "--section", "storage"], cwd=ROOT, capture_output=True, text=True)
        self.assertIn(result.returncode, (0, 1), result.stderr)
        self.assertIn("storage hygiene", result.stdout)
        if sys.platform == "darwin":
            self.assertIn("opens the storage investigation loop", result.stdout)
        else:
            self.assertIn("not applicable outside macOS", result.stdout)

    def test_launchd_job_uses_scheduled_wrapper_and_is_not_run_at_load(self):
        path = ROOT / "ops" / "launchd" / "com.carr.storage-hygiene.plist"
        job = plistlib.loads(path.read_bytes())
        self.assertEqual(job["Label"], "com.carr.storage-hygiene")
        self.assertEqual(job["ProgramArguments"][:4], [
            "/bin/zsh", "{{REPO}}/bin/run-scheduled.sh",
            "storage-hygiene", "launchd.run"])
        self.assertIn("{{REPO}}/tools/storage_hygiene.py", job["ProgramArguments"])
        self.assertFalse(job["RunAtLoad"])
        self.assertEqual(job["StandardOutPath"], "{{REPO}}/out/storage-hygiene.log")

    def test_flash_tests_use_managed_temporary_directories(self):
        for relative in ("tools/test-flash-run-sandbox.py",
                         "tools/test-flash-run-escalate.py",
                         "tools/test-flash-script.py"):
            tree = ast.parse((ROOT / relative).read_text())
            bare = [node.lineno for node in ast.walk(tree)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "tempfile"
                    and node.func.attr == "mkdtemp"]
            self.assertEqual(bare, [], f"{relative} has bare mkdtemp calls at {bare}")


if __name__ == "__main__":
    unittest.main()
