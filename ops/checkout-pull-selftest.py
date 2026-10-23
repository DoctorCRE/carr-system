#!/usr/bin/env python3
"""Run checkout refresh against local origins and a synthetic HOME only."""
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CheckoutPullTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        self.env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin",
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
        self.repositories = []
        for name in ("carr-system", "doctorcre-app"):
            origin, seed, repo = [self.home / f"{name}{suffix}"
                                  for suffix in ("-origin", "-seed", "")]
            self.git(self.home, "init", "--bare", "--initial-branch=main", str(origin))
            self.git(self.home, "clone", str(origin), str(seed))
            (seed / "fixture").write_text("initial\n")
            self.git(seed, "add", "fixture")
            self.git(seed, "commit", "-m", "initial fixture")
            self.git(seed, "push", "origin", "main")
            self.git(self.home, "clone", str(origin), str(repo))
            self.git(repo, "config", "core.hooksPath", "/dev/null")
            (seed / "fixture").write_text("updated\n")
            self.git(seed, "commit", "-am", "updated fixture")
            self.git(seed, "push", "origin", "main")
            self.repositories.append((repo, seed, origin))
        script = ROOT / "bin/checkout-pull.sh"
        if script.exists():
            target = self.home / "carr-system/bin/checkout-pull.sh"
            target.parent.mkdir()
            shutil.copy2(script, target)
            exclude = self.repositories[0][0] / ".git/info/exclude"
            exclude.write_text("/bin/checkout-pull.sh\n")

    def git(self, directory, *args):
        result = subprocess.run([
            "/usr/bin/git", "-c", "user.name=Synthetic", "-c", "user.email=fixture@example.test",
            "-c", "core.hooksPath=/dev/null", "-C", str(directory), *args],
            env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def refresh(self):
        text = (ROOT / "ops/launchd/com.carr.checkout-pull.plist").read_text()
        job = plistlib.loads(text.replace("{{HOME}}", str(self.home)).encode())
        return subprocess.run(job["ProgramArguments"], env=self.env,
                              capture_output=True, text=True, timeout=15)

    def assert_sibling_updated(self):
        sibling, seed, _ = self.repositories[1]
        self.assertEqual(self.git(sibling, "rev-parse", "HEAD"), self.git(seed, "rev-parse", "HEAD"))

    def test_wrong_branch_fails_without_moving_it_and_refreshes_sibling(self):
        repo, _, _ = self.repositories[0]
        self.git(repo, "checkout", "-b", "feature")
        before = self.git(repo, "rev-parse", "HEAD")
        result = self.refresh()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.git(repo, "rev-parse", "HEAD"), before)
        self.assertEqual(self.git(repo, "branch", "--show-current"), "feature")
        self.assert_sibling_updated()

    def test_clean_main_refreshes_both_checkouts(self):
        result = self.refresh()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for repo, seed, _ in self.repositories:
            self.assertEqual(self.git(repo, "rev-parse", "HEAD"), self.git(seed, "rev-parse", "HEAD"))
        self.assertEqual(self.refresh().returncode, 0)

    def test_dirty_checkout_fails_and_preserves_work(self):
        repo, _, _ = self.repositories[0]
        (repo / "fixture").write_text("local edits\n")
        result = self.refresh()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((repo / "fixture").read_text(), "local edits\n")
        self.assert_sibling_updated()

    def test_untracked_work_is_refused_even_when_hidden_by_git_config(self):
        repo, _, _ = self.repositories[0]
        self.git(repo, "config", "status.showUntrackedFiles", "no")
        (repo / "local-work").write_text("unsaved work\n")
        before = self.git(repo, "rev-parse", "HEAD")
        self.assertNotEqual(self.refresh().returncode, 0)
        self.assertEqual(self.git(repo, "rev-parse", "HEAD"), before)
        self.assert_sibling_updated()

    def test_explicit_origin_main_ignores_another_tracking_branch(self):
        repo, seed, _ = self.repositories[0]
        self.git(seed, "branch", "other", "HEAD~1")
        self.git(seed, "push", "origin", "other")
        self.git(repo, "config", "branch.main.merge", "refs/heads/other")
        self.assertEqual(self.refresh().returncode, 0)
        self.assertEqual(self.git(repo, "rev-parse", "HEAD"), self.git(seed, "rev-parse", "main"))
        self.assert_sibling_updated()

    def test_divergent_main_fails_without_discarding_commit(self):
        repo, _, _ = self.repositories[0]
        (repo / "local-work").write_text("committed work\n")
        self.git(repo, "add", "local-work")
        self.git(repo, "commit", "-m", "local fixture")
        before = self.git(repo, "rev-parse", "HEAD")
        self.assertNotEqual(self.refresh().returncode, 0)
        self.assertEqual(self.git(repo, "rev-parse", "HEAD"), before)
        self.assert_sibling_updated()

    def test_fetch_failure_stays_failed_while_sibling_refreshes(self):
        repo, _, _ = self.repositories[0]
        self.git(repo, "remote", "set-url", "origin", str(self.home / "absent-origin"))
        self.assertNotEqual(self.refresh().returncode, 0)
        self.assert_sibling_updated()

    def test_linked_worktree_is_refused_at_canonical_path(self):
        repo, seed, _ = self.repositories[1]
        shutil.rmtree(repo)
        self.git(seed, "worktree", "add", "--detach", str(repo), "HEAD~1")
        before = self.git(repo, "rev-parse", "HEAD")
        self.assertNotEqual(self.refresh().returncode, 0)
        self.assertEqual(self.git(repo, "rev-parse", "HEAD"), before)
        first, first_seed, _ = self.repositories[0]
        self.assertEqual(self.git(first, "rev-parse", "HEAD"), self.git(first_seed, "rev-parse", "HEAD"))


if __name__ == "__main__":
    unittest.main()
