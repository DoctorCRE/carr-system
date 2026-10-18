#!/usr/bin/env python3
"""Exercise the existing reaper against isolated git repositories."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops"))
sys.path.insert(0, str(ROOT / "lib"))
from git_env import fixture_env

spec = importlib.util.spec_from_file_location("reaper", ROOT / "hooks/worktree-self-plumb.py")
assert spec and spec.loader
hook = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = hook
spec.loader.exec_module(hook)


class ReaperTest(unittest.TestCase):
    def successor_fixture(self, body=""):
        fixture = Fixture()
        base = fixture.git("rev-parse", "main")
        tree = fixture.tree("old")
        old = fixture.commit("feature", cwd=tree)
        landed = fixture.commit("feature")
        fixture.git("push", "origin", "main")
        provider = FakeProvider([pull(1, old, base, body=body), pull(2, landed, base, merged=landed)])
        return fixture, provider

    def test_startup_or_ignored_write_at_final_probe_preserves_tree(self):
        for startup in (True, False):
            with self.subTest(startup=startup):
                fixture = Fixture()
                tree = fixture.tree("old")
                fixture.git("config", "core.excludesFile", str(fixture.base / "ignore"))
                (fixture.base / "ignore").write_text("ignored\n")
                calls = []
                def probe(paths):
                    calls.append(paths)
                    if len(calls) == 3:
                        if startup:
                            hook.mark_alive(str(tree))
                        else:
                            (tree / "ignored").write_text("session work")
                    return set()
                fixture.reaper(process_probe=probe).run({"fixture/repo": fixture.repo}, execute=True)
                self.assertTrue(tree.exists())

    def test_startup_during_final_tree_scan_preserves_tree(self):
        fixture = Fixture()
        tree = fixture.tree("old")
        scan = hook.tree_age_s
        calls = []

        def scan_then_start(path):
            age = scan(path)
            calls.append(path)
            if len(calls) == 3:
                hook.mark_alive(path)
            return age

        with patch.object(hook, "tree_age_s", side_effect=scan_then_start):
            report = fixture.reaper().run({"fixture/repo": fixture.repo}, execute=True)

        self.assertEqual(len(calls), 3)
        self.assertTrue(tree.exists())
        self.assertEqual(fixture.git("worktree", "list", "--porcelain").count(str(tree)), 1)
        self.assertFalse(any(action.get("status") == "staged" for action in report["actions"]))

    def test_rebound_branch_at_same_head_preserves_tree(self):
        fixture = Fixture()
        tree = fixture.tree("old")
        provider = FakeProvider()
        reaper = fixture.reaper(provider=provider)
        row = next(r for r in reaper.snapshot("fixture/repo", fixture.repo) if r.get("path") == str(tree))
        fixture.git("branch", "other")
        fixture.git("symbolic-ref", "HEAD", "refs/heads/other", cwd=tree)
        pr = pull(1, row["head"], row["main"])
        pr["head"]["ref"] = "other"
        provider.rows.append(pr)
        self.assertEqual(reaper.apply("fixture/repo", fixture.repo, row, [])["status"], "preserved")
        self.assertTrue(tree.exists())

    def test_reverted_successor_cannot_close_restoration_pr(self):
        for body in ("", "Superseded by #2"):
            with self.subTest(body=body):
                fixture, provider = self.successor_fixture(body)
                fixture.git("revert", "--no-edit", "HEAD")
                fixture.git("push", "origin", "main")
                fixture.reaper(provider=provider).run({"fixture/repo": fixture.repo}, execute=True)
                self.assertFalse(provider.closed)

    def test_other_target_ref_or_repository_cannot_be_superseded(self):
        for field, value in (("ref", "release"), ("repo", {"full_name": "other/repo"})):
            with self.subTest(field=field):
                fixture, provider = self.successor_fixture()
                provider.rows[0]["base"][field] = value
                fixture.reaper(provider=provider).run({"fixture/repo": fixture.repo}, execute=True)
                self.assertFalse(provider.closed)

    def test_source_change_during_successor_read_preserves_pr(self):
        import copy
        for field in ("head", "base"):
            with self.subTest(field=field):
                fixture, provider = self.successor_fixture("Superseded by #2")
                reaper = fixture.reaper(provider=provider)
                row = next(r for r in reaper.snapshot("fixture/repo", fixture.repo) if r["kind"] == "pr")
                original = provider.pull
                def changing(repo, number):
                    result = copy.deepcopy(original(repo, number))
                    if number == 2:
                        provider.rows[0][field]["sha" if field == "head" else "ref"] = "changed"
                    return result
                provider.pull = changing
                self.assertEqual(reaper.apply("fixture/repo", fixture.repo, row, [])["status"], "preserved")
                self.assertFalse(provider.closed)

    def test_close_readback_checks_source_identity(self):
        fixture, provider = self.successor_fixture()
        reaper = fixture.reaper(provider=provider)
        row = next(r for r in reaper.snapshot("fixture/repo", fixture.repo) if r["kind"] == "pr")
        original = provider.close
        def changed(repo, number, successor):
            original(repo, number, successor)
            provider.rows[0]["head"]["sha"] = "changed"
        provider.close = changed
        with self.assertRaisesRegex(RuntimeError, "readback"):
            reaper.apply("fixture/repo", fixture.repo, row, [])

    def test_interrupted_close_recovery_checks_source_identity(self):
        from branch_retirement import append
        fixture, provider = self.successor_fixture()
        reaper = fixture.reaper(provider=provider)
        row = next(r for r in reaper.snapshot("fixture/repo", fixture.repo) if r["kind"] == "pr")
        append(fixture.repo / "out/orch/branch-janitor-actions.jsonl", {"candidate": row, "status": "intent"})
        provider.rows[0]["state"] = "closed"
        provider.rows[0]["head"]["sha"] = "changed"
        with self.assertRaisesRegex(RuntimeError, "identity"):
            reaper.recover_pending("fixture/repo", fixture.repo)

    def test_configured_protected_branch_binds_census_watchdog_and_guard(self):
        import io
        import runpy
        from unittest.mock import patch
        import job_watchdog as watchdog
        fixture = Fixture()
        fixture.git("branch", "reserved")
        fixture.git("push", "origin", "reserved")
        head = fixture.git("rev-parse", "reserved")
        config = watchdog.load_config(ROOT / "ops/config/job-watchdog.json")
        config["protected_branches"].append("reserved")
        config["repositories"] = ["fixture/repo"]
        config["repository_roots"] = {"fixture/repo": str(fixture.repo)}
        self.assertEqual(watchdog.repository_roots(config), {"fixture/repo": fixture.repo})
        rows = fixture.reaper(config=config).snapshot("fixture/repo", fixture.repo)
        reserved = [r for r in rows if r.get("name") == "reserved"]
        self.assertTrue(reserved)
        self.assertTrue(all(r["class"] == "live" and r["action"] is None for r in reserved))
        self.assertFalse(watchdog.detect({"branches": [{"repo": "fixture/repo", "name": "reserved", "updated": 0}]}, config, 100000))
        env = {"CARR_RETIRE_REF": "refs/heads/reserved", "CARR_RETIRE_HEAD": head}
        with patch.object(watchdog, "load_config", return_value=config), patch.dict(os.environ, env), \
                patch.object(sys, "stdin", io.StringIO(f"(delete) {'0' * 40} refs/heads/reserved {head}\n")):
            with self.assertRaises(SystemExit) as refused:
                runpy.run_path(str(ROOT / "ops/branch-janitor-hooks/pre-push"))
        self.assertEqual(refused.exception.code, 1)

    def test_locked_and_unmerged_detached_tree_survive(self):
        for locked in (True, False):
            with self.subTest(locked=locked):
                fixture = Fixture()
                tree = fixture.tree("old")
                fixture.commit("unique work", cwd=tree)
                if locked:
                    fixture.git("worktree", "lock", str(tree))
                else:
                    fixture.git("checkout", "--detach", cwd=tree)
                fixture.age(tree)
                report = fixture.reaper().run({"fixture/repo": fixture.repo}, execute=True)
                self.assertFalse(report["errors"])
                self.assertTrue(tree.exists())

    def test_stale_lock_recovery_cannot_replace_a_new_live_lock(self):
        import threading
        from unittest.mock import patch
        from branch_retirement import maintenance
        fixture = Fixture()
        lock = fixture.repo / "out/worktree-reap.lock"
        lock.parent.mkdir()
        lock.write_text("99999999")
        os.utime(lock, (0, 0))
        rendezvous = threading.Barrier(2)
        entered, release = threading.Event(), threading.Event()
        original = Path.rename
        def racing_rename(path, target):
            if path == lock:
                rendezvous.wait(timeout=2)
                if threading.current_thread().name == "second":
                    entered.wait(timeout=2)
            return original(path, target)
        overlaps, errors = [], []
        def caller():
            try:
                with maintenance(fixture.repo):
                    overlaps.append(entered.is_set())
                    entered.set()
                    release.wait(timeout=3)
            except RuntimeError:
                pass
            except Exception as exc:
                errors.append(exc)
        with patch.object(Path, "rename", racing_rename):
            first = threading.Thread(target=caller, name="first")
            second = threading.Thread(target=caller, name="second")
            first.start()
            second.start()
            second.join(timeout=2.5)
            release.set()
            first.join(timeout=4)
            second.join(timeout=4)
        self.assertFalse(errors, errors)
        self.assertEqual(overlaps, [False])

    def test_contended_run_cannot_replace_accepted_receipts(self):
        from branch_retirement import maintenance
        fixture = Fixture()
        reaper = fixture.reaper()
        reaper.run({"fixture/repo": fixture.repo})
        paths = [fixture.repo / "out/orch" / name for name in
                 ("branch-janitor-report.json", "branch-janitor-runs.jsonl")]
        before = [p.read_bytes() for p in paths]
        with maintenance(fixture.repo):
            refused = reaper.run({"fixture/repo": fixture.repo})
        self.assertTrue(refused["errors"])
        self.assertEqual([p.read_bytes() for p in paths], before)

    def test_receipts_publish_while_maintenance_is_held(self):
        from unittest.mock import patch
        import branch_retirement as retirement
        fixture = Fixture()
        original = retirement.save
        held = []
        def observe(path, row):
            if path.name == "branch-janitor-report.json":
                try:
                    with retirement.maintenance(fixture.repo):
                        held.append(False)
                except RuntimeError:
                    held.append(True)
            return original(path, row)
        with patch.object(retirement, "save", observe):
            fixture.reaper().run({"fixture/repo": fixture.repo})
        self.assertEqual(held, [True])

    def test_orphan_digest_frames_path_type_and_content(self):
        fixture = Fixture()
        tree = fixture.tree("old")
        (tree / "a").write_bytes(b"b")
        (tree / "b").write_bytes(b"c")
        reaper = fixture.reaper()
        first = reaper.work_content(tree)
        (tree / "a").write_bytes(b"")
        (tree / "b").write_bytes(b"bc")
        self.assertNotEqual(reaper.work_content(tree), first)

    def test_orphan_digest_preserves_tracked_trailing_whitespace(self):
        fixture = Fixture()
        tree = fixture.tree("old")
        reaper = fixture.reaper()
        (tree / "file").write_text("value\n \n")
        first = reaper.work_content(tree)
        (tree / "file").write_text("value\n  \n")
        self.assertNotEqual(reaper.work_content(tree), first)

    def test_janitor_has_deliberate_silence_policy_until_registered_limit(self):
        import job_watchdog as watchdog
        config = watchdog.load_config(ROOT / "ops/config/job-watchdog.json")
        job = {"id": "janitor", "card": "branch-janitor", "start": 1000, "log_mtime": 1000,
               "limit": 3600, "alive": True, "log_tail": ""}
        self.assertFalse(watchdog.detect({"jobs": [job]}, config, 1601))
        self.assertIn("job_over_limit", {f["kind"] for f in watchdog.detect({"jobs": [job]}, config, 4601)})
        job["card"] = "ordinary"
        self.assertIn("job_silent", {f["kind"] for f in watchdog.detect({"jobs": [job]}, config, 1601)})

    def test_reaper_uses_loaded_branch_idle_policy(self):
        from unittest.mock import patch
        import job_watchdog as watchdog
        fixture = Fixture()
        tree = fixture.tree("old")
        head = fixture.commit("unmerged", cwd=tree)
        config = watchdog.load_config(ROOT / "ops/config/job-watchdog.json")
        config["thresholds"]["branch_idle_seconds"] = 1
        now = int(fixture.git("show", "-s", "--format=%ct", head)) + 2
        with patch.object(watchdog, "load_config", return_value=config):
            reaper = fixture.reaper(clock=lambda: now)
            row = next(r for r in reaper.snapshot("fixture/repo", fixture.repo) if r.get("ref") == "refs/heads/old")
        self.assertEqual(row["class"], "abandoned")

    def test_unknown_owner_preserves_clean_tree(self):
        fixture = Fixture()
        tree = fixture.tree("old")
        fixture.reaper(ownership_probe=lambda path: "unknown").run({"fixture/repo": fixture.repo}, execute=True)
        self.assertTrue(tree.exists())

    def test_missing_process_evidence_keeps_health_incomplete(self):
        from branch_retirement import health
        fixture = Fixture()
        fixture.tree("old")
        roots = {repo: fixture.repo for repo in ("jbookout/carr-system", "jbookout/doctorcre-app", "jbookout/software-factory")}
        report = fixture.reaper(process_probe=lambda paths: None).run(roots, execute=True)
        self.assertTrue(report["errors"])
        self.assertTrue(health(fixture.repo)[1])

    def test_interrupted_staging_is_read_back_before_another_run(self):
        fixture = Fixture()
        tree = fixture.tree("old")
        reaper = fixture.reaper()
        original = reaper.apply
        def interrupted(*args):
            original(*args)
            raise RuntimeError("fixture interruption after effect")
        reaper.apply = interrupted
        first = reaper.run({"fixture/repo": fixture.repo}, execute=True)
        self.assertTrue(first["errors"])
        self.assertFalse(tree.exists())
        second = fixture.reaper().run({"fixture/repo": fixture.repo}, execute=True)
        self.assertFalse(second["errors"])
        self.assertEqual([r["status"] for r in second["recovered"]], ["observed_staged"])

    def test_dead_stale_lock_file_does_not_block_and_inode_survives(self):
        fixture = Fixture()
        lock = fixture.repo / "out/worktree-reap.lock"
        lock.parent.mkdir()
        lock.write_text("99999999")
        inode = lock.stat().st_ino
        report = fixture.reaper().run({"fixture/repo": fixture.repo})
        self.assertFalse(report["errors"])
        self.assertEqual(lock.stat().st_ino, inode)

    def test_inherited_git_location_cannot_redirect_census(self):
        from unittest.mock import patch
        target, other = Fixture(), Fixture()
        with patch.dict(os.environ, {"GIT_DIR": str(other.repo / ".git"),
                                     "GIT_WORK_TREE": str(other.repo)}):
            self.assertEqual(hook.canonical_root(str(target.repo)), str(target.repo))

    def test_scheduler_runs_existing_reaper_once_per_interval_and_preserves_live_job(self):
        sys.path.insert(0, str(ROOT / "lib"))
        import job_watchdog as watchdog
        fixture = Fixture()
        config = watchdog.load_config(ROOT / "ops/config/job-watchdog.json")
        config["repository_roots"]["jbookout/carr-system"] = str(fixture.repo)
        calls = []
        class Effects:
            def launch(self, finding, argv, cwd, *, job_id):
                calls.append(argv)
                self.job_id = job_id
                return {"wrapper_pid": os.getpid()}
        watchdog.schedule_reaper(fixture.repo, config, Effects(), 10000)
        watchdog.schedule_reaper(fixture.repo, config, Effects(), 10001)
        watchdog.schedule_reaper(fixture.repo, config, Effects(), 19000)
        self.assertEqual(len(calls), 1)
        self.assertIn(str(ROOT / "hooks/worktree-self-plumb.py"), calls[0])
        self.assertIn("--fleet", calls[0])

    def test_dead_schedule_wrapper_does_not_preserve_itself_as_a_none_identity(self):
        from unittest.mock import patch
        import job_watchdog as watchdog
        fixture = Fixture()
        config = watchdog.load_config(ROOT / "ops/config/job-watchdog.json")
        config["repository_roots"]["jbookout/carr-system"] = str(fixture.repo)
        ledger = fixture.repo / "out/orch/branch-janitor-schedule.jsonl"
        watchdog.append(ledger, {"key": "schedule", "at": 1000, "wrapper_pid": 99999999,
                                 "process_identity": None, "status": "started"})
        calls = []
        class Effects:
            def launch(self, finding, argv, cwd, *, job_id):
                calls.append(argv)
                return {"wrapper_pid": os.getpid()}
        watchdog.schedule_reaper(fixture.repo, config, Effects(), 9000)
        self.assertEqual(len(calls), 1)

    def test_fixture_scan_cannot_launch_the_live_fleet_scheduler(self):
        import job_watchdog as watchdog
        from unittest.mock import Mock
        fixture = Fixture()
        config = watchdog.load_config(ROOT / "ops/config/job-watchdog.json")
        effects = Mock()
        watchdog.schedule_reaper(fixture.repo, config, effects, 20000)
        effects.launch.assert_not_called()
        self.assertEqual(hook.reap_main(["--reap", "--fleet", "--repo", str(fixture.repo)]), 1)

    def test_merged_branch_can_retire_but_abandoned_branch_cannot(self):
        sys.path.insert(0, str(ROOT / "lib"))
        from branch_retirement import branch_verdict
        merged = branch_verdict(merged=True, successor=None, open_pr=False, idle=True)
        abandoned = branch_verdict(merged=False, successor=None, open_pr=False, idle=True)
        self.assertEqual(merged, "merged")
        self.assertEqual(abandoned, "abandoned")

    def test_dry_run_then_stage_preserves_orphan_bytes_and_branch(self):
        base = Path(tempfile.mkdtemp(prefix="branch-janitor-fixture-")).resolve()
        repo, remote, tree = base / "repo", base / "remote.git", base / "orphan"
        repo.mkdir()
        def git(*args, cwd=repo):
            return subprocess.run(["git", *map(str, args)], cwd=cwd, env=fixture_env(),
                                  text=True, capture_output=True, check=True).stdout.strip()
        git("init", "-q", "-b", "main")
        git("config", "user.email", "fixture@example.invalid")
        git("config", "user.name", "Fixture")
        (repo / "file").write_text("committed\n")
        git("add", "file")
        git("commit", "-qm", "initial")
        git("init", "--bare", remote)
        git("remote", "add", "origin", remote)
        git("push", "-u", "origin", "main")
        git("worktree", "add", "-b", "old", tree)
        (tree / "file").write_text("unsaved work\n")
        gitdir = Path(hook.index_gitdir(str(tree)))
        old = time.time() - 10 * 86400
        for p in (tree / "file", tree / ".git", gitdir / "index"):
            os.utime(p, (old, old))
        reaper = hook.fleet_reaper(repo, provider=FakeProvider(),
                                   process_probe=lambda paths: set(),
                                   ownership_probe=lambda path: "orphaned")
        report = reaper.run({"fixture/repo": repo}, execute=False)
        self.assertTrue(tree.exists())
        self.assertEqual(report["counts"]["fixture/repo"]["worktree"]["merged"], 1)
        report = reaper.run({"fixture/repo": repo}, execute=True)
        self.assertFalse(tree.exists())
        manifest = next((repo.parent / "_to_delete" / repo.name).glob("*/manifest.json"))
        staged = manifest.parent / "worktree"
        self.assertEqual((staged / "file").read_text(), "unsaved work\n")
        self.assertEqual(json.loads(manifest.read_text())["original_path"], str(tree))
        self.assertEqual(git("rev-parse", "old"), git("rev-parse", "main"))
        self.assertEqual(git("rev-parse", "--show-toplevel", cwd=staged), str(staged))

    def test_changed_remote_tip_is_not_deleted(self):
        fixture = Fixture()
        fixture.git("push", "origin", "main:refs/heads/merged")
        reaper = fixture.reaper()
        row = next(r for r in reaper.snapshot("fixture/repo", fixture.repo)
                   if r.get("ref") == "refs/remotes/origin/merged")
        fixture.commit("new work")
        fixture.git("push", "origin", "main:refs/heads/merged")
        result = reaper.apply("fixture/repo", fixture.repo, row, [])
        self.assertEqual(result["status"], "preserved")
        self.assertIn("refs/heads/merged", fixture.git("ls-remote", "origin"))

    def test_running_process_and_unknown_dirty_owner_preserve_worktree(self):
        for busy, owner in ((True, "orphaned"), (False, "unknown"), (False, "owned")):
            with self.subTest(busy=busy, owner=owner):
                fixture = Fixture()
                tree = fixture.tree("old")
                (tree / "file").write_text("unsaved\n")
                fixture.age(tree)
                reaper = fixture.reaper(process_probe=lambda paths: {str(tree)} if busy else set(),
                                        ownership_probe=lambda path: owner)
                report = reaper.run({"fixture/repo": fixture.repo}, execute=True)
                self.assertFalse(report["errors"])
                self.assertTrue(tree.exists())
                self.assertEqual(next(r for r in report["rows"] if r.get("path") == str(tree))["class"], "live")

    def test_process_starting_during_content_verification_preserves_tree(self):
        fixture = Fixture()
        tree = fixture.tree("old")
        calls = []
        def probe(paths):
            calls.append(paths)
            return {str(tree)} if len(calls) >= 3 else set()
        report = fixture.reaper(process_probe=probe).run({"fixture/repo": fixture.repo}, execute=True)
        self.assertTrue(tree.exists())
        self.assertTrue(report["errors"])

    def test_remote_retirement_is_read_back_and_local_branch_survives(self):
        fixture = Fixture()
        fixture.git("branch", "merged")
        fixture.git("push", "origin", "merged")
        report = fixture.reaper().run({"fixture/repo": fixture.repo}, execute=True)
        self.assertFalse(report["errors"])
        self.assertNotIn("refs/heads/merged", fixture.git("ls-remote", "origin"))
        self.assertEqual(fixture.git("rev-parse", "merged"), fixture.git("rev-parse", "main"))
        retired = next(a for a in report["actions"] if a["status"] == "retired")
        self.assertEqual(fixture.git("rev-parse", retired["backup_ref"]), fixture.git("rev-parse", "main"))

    def test_remote_advertisement_guard_rejects_race_or_any_update(self):
        guard = ROOT / "ops/branch-janitor-hooks/pre-push"
        env = {**fixture_env(), "CARR_RETIRE_REF": "refs/heads/old", "CARR_RETIRE_HEAD": "a" * 40}
        for local, head in (("0" * 40, "b" * 40), ("b" * 40, "a" * 40)):
            result = subprocess.run([sys.executable, str(guard)], env=env, text=True,
                input=f"(delete) {local} refs/heads/old {head}\n", capture_output=True)
            self.assertEqual(result.returncode, 1)
        result = subprocess.run([sys.executable, str(guard)], env=env, text=True,
            input=f"(delete) {'0' * 40} refs/heads/old {'a' * 40}\n", capture_output=True)
        self.assertEqual(result.returncode, 0)

    def test_superseded_pr_closes_with_merged_successor_by_link_or_full_patch(self):
        for body in ("Superseded by #2", ""):
            with self.subTest(body=body):
                fixture = Fixture()
                base = fixture.git("rev-parse", "main")
                tree = fixture.tree("old")
                old = fixture.commit("feature", cwd=tree)
                landed = fixture.commit("feature")
                fixture.git("commit", "--amend", "-qm", "successor")
                landed = fixture.git("rev-parse", "main")
                fixture.git("push", "origin", "main")
                provider = FakeProvider([
                    pull(1, old, base, body=body),
                    pull(2, landed, base, merged=landed)])
                reaper = fixture.reaper(provider=provider)
                report = reaper.run({"fixture/repo": fixture.repo}, execute=True)
                self.assertFalse(report["errors"], report["errors"])
                self.assertEqual(provider.closed, [(1, 2)])
                self.assertEqual(report["counts"]["fixture/repo"]["pr"]["superseded"], 1)

    def test_unmerged_successor_and_partial_patch_cannot_close_pr(self):
        fixture = Fixture()
        base = fixture.git("rev-parse", "main")
        tree = fixture.tree("old")
        old = fixture.commit("unique work", cwd=tree)
        provider = FakeProvider([pull(1, old, base, body="Superseded by #2"), pull(2, base, base)])
        report = fixture.reaper(provider=provider).run({"fixture/repo": fixture.repo}, execute=True)
        self.assertFalse(report["errors"])
        self.assertFalse(provider.closed)
        self.assertEqual(report["counts"]["fixture/repo"]["pr"]["live"], 2)

    def test_patch_match_preserves_semantic_whitespace_and_full_file_content(self):
        fixture = Fixture()
        base = fixture.git("rev-parse", "main")
        tree = fixture.tree("different")
        first = fixture.commit("value = 1")
        second = fixture.commit("value=1", cwd=tree)
        reaper = fixture.reaper()
        self.assertNotEqual(reaper.patch(fixture.repo, base, first),
                            reaper.patch(fixture.repo, base, second))

    def test_watchdog_clears_idle_finding_after_fixture_remote_retirement(self):
        import job_watchdog as watchdog
        fixture = Fixture()
        fixture.git("push", "origin", "main:refs/heads/claude/old")
        config = watchdog.load_config(ROOT / "ops/config/job-watchdog.json")
        class Effects:
            def report(self, finding):
                return {}
        facts = {"branches": [{"repo": "fixture/repo", "name": "claude/old", "updated": 0}]}
        found = watchdog.detect(facts, config, 100000)
        watchdog.reconcile(fixture.repo, config, found, Effects(), 100000)
        report = fixture.reaper().run({"fixture/repo": fixture.repo}, execute=True)
        self.assertFalse(report["errors"])
        self.assertFalse(fixture.git("ls-remote", "origin", "refs/heads/claude/old"))
        watchdog.reconcile(fixture.repo, config, watchdog.detect({"branches": []}, config, 100100), Effects(), 100100)
        latest = watchdog.read_latest(fixture.repo / config["paths"]["findings"])
        self.assertEqual(latest[found[0]["key"]]["cleared_at"], watchdog.stamp(100100))

    def test_health_row_has_bound_action_and_clears_on_complete_evidence(self):
        from branch_retirement import health, save, repository_roots
        fixture = Fixture()
        line, failed = health(fixture.repo, now=1000)
        self.assertTrue(failed)
        self.assertIn("owner orchestrator", line)
        self.assertIn("auto-clear", line)
        save(fixture.repo / "out/orch/branch-janitor-report.json", {
            "execute": True, "at": 1000, "errors": [], "rows": [], "actions": [],
            "counts": {repo: {} for repo in repository_roots()}})
        line, failed = health(fixture.repo, now=1001)
        self.assertFalse(failed)
        self.assertTrue(line.startswith("OK"))


def pull(number, head, base, *, body="", merged=None):
    return {"number": number, "state": "closed" if merged else "open", "body": body,
            "merged_at": "2026-01-01T00:00:00Z" if merged else None, "merge_commit_sha": merged,
            "html_url": f"https://github.com/fixture/repo/pull/{number}",
            "head": {"sha": head, "ref": "old" if number == 1 else "successor",
                     "repo": {"full_name": "fixture/repo"}},
            "base": {"sha": base, "ref": "main", "repo": {"full_name": "fixture/repo"}}}


class Fixture:
    def __init__(self):
        self.base = Path(tempfile.mkdtemp(prefix="branch-janitor-fixture-")).resolve()
        self.repo, self.remote = self.base / "repo", self.base / "remote.git"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Fixture")
        self.commit("initial")
        self.git("init", "--bare", str(self.remote))
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "-u", "origin", "main")

    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd or self.repo, env=fixture_env(),
                              text=True, capture_output=True, check=True).stdout.strip()

    def commit(self, text, cwd=None):
        path = cwd or self.repo
        (path / "file").write_text(text + "\n")
        self.git("add", "file", cwd=path)
        self.git("commit", "-qm", text, cwd=path)
        return self.git("rev-parse", "HEAD", cwd=path)

    def tree(self, name):
        tree = self.base / name
        self.git("worktree", "add", "-b", name, str(tree))
        self.age(tree)
        return tree

    def age(self, tree):
        old = time.time() - 10 * 86400
        for p in (tree / "file", tree / ".git", Path(hook.index_gitdir(str(tree))) / "index"):
            os.utime(p, (old, old))

    def reaper(self, **kwargs):
        options = {"provider": FakeProvider(), "process_probe": lambda paths: set(),
                   "ownership_probe": lambda path: "orphaned"}
        return hook.fleet_reaper(self.repo, **{**options, **kwargs})


class FakeProvider:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.closed = []

    def pulls(self, repo):
        return self.rows

    def pull(self, repo, number):
        return next(r for r in self.rows if r["number"] == number)

    def open_pulls(self, repo, branch):
        return [r for r in self.rows if r["state"] == "open" and r["head"]["ref"] == branch]

    def close(self, repo, number, successor):
        self.closed.append((number, successor))
        next(r for r in self.rows if r["number"] == number)["state"] = "closed"

    def verify_repository(self, repo, path):
        pass


if __name__ == "__main__":
    unittest.main()
