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

    def assert_runtime_refused(self, data):
        body = plistlib.dumps(data).decode()
        self.assertIsNotNone(installer.launchd_path_refusal(body))
        self.assertIsNotNone(installer.launchd_template_refusal(body))
        agents = self.root / "Library/LaunchAgents"
        agents.mkdir(parents=True, exist_ok=True)
        name = "com.carr.fixture.plist"
        target = agents / name
        target.write_text(body)
        with patch.object(installer, "LAUNCHD_SRC", str(agents)), contextlib.redirect_stdout(io.StringIO()):
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

    def test_git_ownership_error_is_refused(self):
        denied = subprocess.CompletedProcess([], 128, "", "fatal: detected dubious ownership in repository")
        with patch.object(installer.subprocess, "run", return_value=denied):
            self.assert_runtime_refused({"ProgramArguments": [str(self.repo / "runner.sh")]})

    def test_non_repository_system_path_is_allowed(self):
        body = plistlib.dumps({"ProgramArguments": ["/bin/bash", "--version"]}).decode()
        self.assertIsNone(installer.launchd_path_refusal(body))

    def test_board_installer_refuses_feature_checkout_without_writing(self):
        agents = self.root / "Library/LaunchAgents"
        agents.mkdir(parents=True)
        dest = agents / "local.carr-progress-board.plist"
        old = {"Label": "local.carr-progress-board", "ProgramArguments": ["/bin/bash", "old.sh"], "StartInterval": 900}
        dest.write_bytes(plistlib.dumps(old))
        with patch.object(installer, "REPO", str(self.repo)), patch.object(installer, "HOME", str(self.root)), patch.object(installer, "install_launchd_plist", return_value="loaded") as install:
            self.assertEqual(installer.cmd_install_progress_board(True, repo=str(self.feature)), 1)
            install.assert_not_called()
        self.assertEqual(plistlib.loads(dest.read_bytes()), old)


if __name__ == "__main__":
    unittest.main()
