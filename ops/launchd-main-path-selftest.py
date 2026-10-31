#!/usr/bin/env python3
"""LaunchAgent runtime paths must never select a feature branch checkout."""
import importlib.util
import contextlib
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
sys.path.insert(0, str(ROOT / "ops"))
from git_env import fixture_env
spec = importlib.util.spec_from_file_location("main_path_installer", ROOT / "ops/config-as-code.py")
assert spec is not None and spec.loader is not None
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class LaunchdMainPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "canonical"
        self.feature = self.root / "feature"
        repo_patch = patch.object(installer, "REPO", str(self.repo))
        repo_patch.start()
        self.addCleanup(repo_patch.stop)
        self.env = fixture_env()
        self.git("init", "-b", "main", str(self.repo))
        self.git("-C", str(self.repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "seed")
        self.git("-C", str(self.repo), "worktree", "add", "-b", "repair", str(self.feature))
        for root in (self.repo, self.feature):
            (root / "ops").mkdir()
            (root / "ops/progress-board-render.sh").write_text("#!/bin/bash\n")
            (root / ".venv/bin").mkdir(parents=True)
            interpreter = root / ".venv/bin/python"
            interpreter.write_text("#!/bin/sh\n")
            interpreter.chmod(0o755)

    def git(self, *args):
        return subprocess.run(["git", *args], env=self.env, check=True, capture_output=True, text=True)

    def test_template_rejects_each_feature_runtime_path(self):
        for field in ("WorkingDirectory", "Program", "ProgramArguments"):
            with self.subTest(field=field):
                data = {"Label": "local.test", "ProgramArguments": ["/bin/bash"], "WorkingDirectory": str(self.repo)}
                data[field] = ["/bin/bash", str(self.feature / "ops/progress-board-render.sh")] if field == "ProgramArguments" else str(self.feature) if field == "WorkingDirectory" else str(self.feature / "ops/progress-board-render.sh")
                refusal = installer.launchd_template_refusal(plistlib.dumps(data).decode())
                self.assertIsNotNone(refusal, f"accepted feature path in {field}")
                self.assertIn("main", refusal)
        safe = {"Label": "local.test", "ProgramArguments": ["/bin/bash", str(self.repo / "ops/progress-board-render.sh")], "WorkingDirectory": str(self.repo)}
        self.assertIsNone(installer.launchd_template_refusal(plistlib.dumps(safe).decode()))

    def test_symlink_and_missing_program_cannot_hide_feature_checkout(self):
        link = self.root / "runner"
        link.symlink_to(self.feature / "ops/progress-board-render.sh")
        for program in (link, self.feature / "ops/not-built-yet/runner.sh"):
            body = plistlib.dumps({"Label": "local.test", "ProgramArguments": [str(program)]}).decode()
            self.assertIsNotNone(installer.launchd_template_refusal(body))

    def test_dependency_checkout_on_its_release_branch_is_allowed(self):
        dependency = self.root / "dependency"
        self.git("init", "-b", "stable", str(dependency))
        body = plistlib.dumps({"Label": "local.test", "ProgramArguments": [str(dependency / "python")]}).decode()
        with patch.object(installer, "LAUNCHD_DEPENDENCY_CHECKOUTS", (str(dependency),), create=True):
            self.assertIsNone(installer.launchd_template_refusal(body))

    def test_outlook_job_may_run_from_its_configured_installer_checkout(self):
        # bin/install-outlook-intake.sh keeps a standalone checkout detached at
        # a main commit; only Outlook jobs may run from it.
        sys.path.insert(0, str(ROOT / "ops"))
        from outlook_test_support import write_settings
        home, runtime = self.root / "home", self.root / "outlook-runtime"
        self.git("init", str(runtime))
        write_settings(home, runtime)

        def detach_at(source):
            sha = self.git("-C", str(source), "rev-parse", "HEAD").stdout.strip()
            self.git("-C", str(runtime), "fetch", "--quiet", "--depth=1", str(source), sha)
            self.git("-C", str(runtime), "checkout", "--quiet", "--detach", sha)

        def body(label):
            return plistlib.dumps({"Label": label, "ProgramArguments": ["/usr/bin/true"],
                                   "WorkingDirectory": str(runtime)}).decode()
        with patch.object(installer, "HOME", str(home)):
            detach_at(self.repo)
            self.assertIsNone(installer.launchd_path_refusal(body("com.carr.outlook-triage")))
            self.assertIsNotNone(installer.launchd_path_refusal(body("com.carr.not-outlook")))
            # A commit that never reached main must not run the Outlook jobs.
            self.git("-C", str(self.feature), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                     "commit", "--allow-empty", "-m", "unmerged")
            detach_at(self.feature)
            refusal = installer.launchd_path_refusal(body("com.carr.outlook-triage"))
            self.assertIsNotNone(refusal)
            self.assertIn("not on", refusal)

    def assert_runtime_refused(self, data):
        body = plistlib.dumps(data).decode()
        with self.subTest(seam="runtime guard"):
            self.assertIsNotNone(installer.launchd_path_refusal(body))
        with self.subTest(seam="template installation guard"):
            self.assertIsNotNone(installer.launchd_template_refusal(body))
        agents = self.root / "Library/LaunchAgents"
        agents.mkdir(parents=True, exist_ok=True)
        name = "com.carr.fixture.plist"
        target = agents / name
        target.write_text(body)
        with self.subTest(seam="installed audit"), \
                patch.object(installer, "LAUNCHD_SRC", str(agents)), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(installer.cmd_check_launchd_main_paths(), 1)
        self.assertEqual(target.read_text(), body, "audit must be read-only")

    def test_source_validation_is_independent_of_checkout_branch(self):
        source = self.repo / "ops/launchd/com.carr.fixture.plist"
        source.parent.mkdir()
        body = plistlib.dumps({"Label": "com.carr.fixture", "WorkingDirectory": str(self.repo),
                              "ProgramArguments": ["/bin/bash", str(self.repo / "ops/progress-board-render.sh")]}).decode()
        source.write_text(body)
        for checkout in ("repair-source", "--detach"):
            args = ["checkout", "-b", checkout] if checkout != "--detach" else ["checkout", checkout]
            self.git("-C", str(self.repo), *args)
            with self.subTest(checkout=checkout):
                self.assertEqual(installer.refused_launchd_templates(str(self.repo)), [])
                self.assertIsNotNone(installer.launchd_template_refusal(body))

    def test_relative_runtime_paths_cannot_hide_feature_checkout(self):
        for field in ("Program", "ProgramArguments"):
            with self.subTest(field=field):
                data = {"Label": "com.carr.fixture", "WorkingDirectory": str(self.repo),
                        "ProgramArguments": ["/bin/bash"]}
                relative = "../feature/ops/progress-board-render.sh"
                data[field] = ["/bin/bash", relative] if field == "ProgramArguments" else relative
                self.assert_runtime_refused(data)
        safe = {"WorkingDirectory": str(self.repo), "ProgramArguments": ["/bin/bash", "ops/progress-board-render.sh", "--apply", "tick"]}
        self.assertIsNone(installer.launchd_template_refusal(plistlib.dumps(safe).decode()))

    def test_standalone_feature_clone_is_refused(self):
        clone = self.root / "clone"
        self.git("clone", str(self.repo), str(clone))
        self.git("-C", str(clone), "checkout", "-b", "feature")
        script = clone / "runner.sh"
        script.write_text("#!/bin/sh\n")
        self.assert_runtime_refused({"ProgramArguments": [str(script)]})

    def test_broken_git_metadata_is_refused(self):
        broken = self.root / "broken"
        broken.mkdir()
        (broken / ".git").write_text(f"gitdir: {self.root / 'unavailable'}\n")
        self.assert_runtime_refused({"ProgramArguments": [str(broken / "runner.sh")]})

    def test_corrupt_head_is_refused_before_installation_or_audit(self):
        self.git("-C", str(self.repo), "checkout", "-b", "feature")
        (self.repo / ".git/HEAD").write_text("corrupt HEAD\n")
        for program in (self.repo / "ops/progress-board-render.sh",
                        self.repo / "ops/not-built-yet/runner.sh"):
            with self.subTest(program=program):
                self.assert_runtime_refused({"ProgramArguments": [str(program)]})

    def test_corrupt_nested_repository_inside_main_checkout_is_refused(self):
        nested = self.repo / "nested"
        self.git("init", "-b", "feature", str(nested))
        (nested / "runner.sh").write_text("#!/bin/sh\n")
        (nested / "ops").mkdir()
        (nested / "ops/progress-board-render.sh").write_text("#!/bin/bash\n")
        (nested / ".venv/bin").mkdir(parents=True)
        (nested / ".venv/bin/python").write_text("#!/bin/sh\n")
        (nested / ".venv/bin/python").chmod(0o755)
        (nested / ".git/HEAD").write_text("corrupt HEAD\n")
        for data in ({"WorkingDirectory": str(nested), "ProgramArguments": ["/bin/bash"]},
                     {"Program": str(nested / "runner.sh")},
                     {"ProgramArguments": [str(nested / "runner.sh")]},
                     {"ProgramArguments": [str(nested / "not-built-yet/runner.sh")]}):
            with self.subTest(plist=data):
                self.assert_runtime_refused(data)

    def test_git_ownership_error_is_refused(self):
        denied = subprocess.CompletedProcess([], 128, "", "fatal: detected dubious ownership in repository")
        with patch.object(installer.subprocess, "run", return_value=denied):
            self.assert_runtime_refused({"ProgramArguments": [str(self.repo / "runner.sh")]})

    def test_corrupt_worktree_head_is_refused(self):
        gitdir = Path((self.feature / ".git").read_text().strip().removeprefix("gitdir: "))
        (gitdir / "HEAD").write_text("corrupt HEAD\n")
        self.assert_runtime_refused({"ProgramArguments": [str(self.feature / "ops/progress-board-render.sh")]})

    def test_unreadable_metadata_probe_is_refused(self):
        (self.repo / ".git/HEAD").write_text("corrupt HEAD\n")
        metadata = str(self.repo / ".git")
        lstat = os.lstat

        def unreadable(path, *args, **kwargs):
            if os.fspath(path) == metadata:
                raise PermissionError("Git metadata is unreadable")
            return lstat(path, *args, **kwargs)

        with patch.object(installer.os, "lstat", side_effect=unreadable):
            self.assert_runtime_refused({"ProgramArguments": [str(self.repo / "ops/progress-board-render.sh")]})

    def test_non_repository_system_path_is_allowed(self):
        body = plistlib.dumps({"ProgramArguments": ["/bin/bash", "--version"]}).decode()
        self.assertIsNone(installer.launchd_path_refusal(body))

    def test_non_repository_existing_and_missing_paths_are_allowed(self):
        ordinary = self.root / "ordinary"
        ordinary.mkdir()
        runner = ordinary / "runner.sh"
        runner.write_text("#!/bin/sh\n")
        for program in (runner, ordinary / "not-built-yet/runner.sh"):
            with self.subTest(program=program):
                body = plistlib.dumps({"WorkingDirectory": str(ordinary),
                                       "ProgramArguments": [str(program)]}).decode()
                self.assertIsNone(installer.launchd_path_refusal(body))
                self.assertIsNone(installer.launchd_template_refusal(body))
                agents = self.root / "Library/LaunchAgents"
                agents.mkdir(parents=True, exist_ok=True)
                target = agents / "com.carr.fixture.plist"
                target.write_text(body)
                with patch.object(installer, "LAUNCHD_SRC", str(agents)), \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(installer.cmd_check_launchd_main_paths(), 0)
                self.assertEqual(target.read_text(), body, "audit must be read-only")



if __name__ == "__main__":
    unittest.main()
