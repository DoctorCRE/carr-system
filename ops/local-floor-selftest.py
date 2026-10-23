#!/usr/bin/env python3
"""Exercise the real local Git hooks and push floor in disposable repositories."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from git_env import fixture_env

ROOT = Path(__file__).resolve().parents[1]


class RepositoryFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="carr-local-floor-")
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name) / "repo with spaces"
        self.repo.mkdir()
        # No inherited hookdir, bypass flags, Git locations, or host credentials.
        self.child_env = fixture_env({"PATH": os.defpath})
        self.git("init", "-q", "-b", "feature")
        self.git("config", "user.name", "jbookout")
        self.git("config", "user.email", "jbookout@users.noreply.github.com")
        self.message = Path(temporary.name) / "message"
        self.message.write_text("Local floor fixture\n")

    def git(self, *args, check=True):
        return subprocess.run(["git", *args], cwd=self.repo, env=self.child_env,
                              text=True, capture_output=True, check=check, timeout=15)

    def write(self, name, source):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
        return path

    def copy(self, name):
        target = self.repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)

    def commit(self, *paths, check=True):
        self.git("add", "--", *paths)
        return self.git("commit", "-q", "-F", str(self.message), check=check)

    def calls(self):
        log = self.repo / "calls"
        return log.read_text().splitlines() if log.exists() else []


class PreCommitTests(RepositoryFixture):
    HELPERS = ("ops/ci-secret-scan.py", "ops/githooks/path-hygiene-check.py",
               "ops/githooks/index-upkeep-check.py")

    def setUp(self):
        super().setUp()
        self.write("source.txt", "baseline\n")
        self.commit("source.txt")
        self.copy("ops/githooks/pre-commit")
        for name in self.HELPERS:
            self.helper(name)
        self.git("config", "core.hooksPath", "ops/githooks")
        self.write("source.txt", "changed\n")
        self.git("add", "--", "source.txt")

    def helper(self, name, status=0):
        self.write(name, "from pathlib import Path\nimport sys\n"
                   "with Path('calls').open('a') as log:\n"
                   f"    log.write({name!r} + ' ' + ' '.join(sys.argv[1:]) + '\\n')\n"
                   f"raise SystemExit({status})\n")

    def try_commit(self):
        return self.git("commit", "-q", "-F", str(self.message), check=False)

    def test_feature_commit_runs_all_helpers_with_staged_scanner(self):
        result = self.try_commit()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [self.HELPERS[0] + " --staged",
                                       self.HELPERS[1] + " ", self.HELPERS[2] + " "])

    def test_main_commit_is_refused_and_index_preserved(self):
        self.git("branch", "-m", "main")
        before = self.git("rev-parse", "HEAD").stdout
        result = self.try_commit()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("COMMIT REFUSED — this is 'main'", result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD").stdout, before)
        self.assertEqual(self.git("diff", "--cached", "--name-only").stdout, "source.txt\n")
        self.assertEqual(len(self.calls()), 3)

    def assert_helper_refuses(self, index):
        before = self.git("rev-parse", "HEAD").stdout
        self.helper(self.HELPERS[index], status=1)
        result = self.try_commit()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.calls()), index + 1)
        self.assertEqual(self.git("rev-parse", "HEAD").stdout, before)
        self.assertEqual(self.git("diff", "--cached", "--name-only").stdout, "source.txt\n")
        return result

    def test_scanner_refuses_commit(self):
        result = self.assert_helper_refuses(0)
        self.assertIn("staged content carries a shaped credential", result.stderr)

    def test_path_check_refuses_commit(self):
        self.assert_helper_refuses(1)

    def test_index_check_refuses_commit(self):
        self.assert_helper_refuses(2)


class PushFloorTests(RepositoryFixture):
    def setUp(self):
        super().setUp()
        self.copy("ops/ci.sh")
        self.copy("ops/node_test_summary.py")
        for name in ("ops/no-client-deliverables-gate.py", "ops/hook-deletion-check.py",
                     "ops/ci-selftest.py"):
            self.write(name, "raise SystemExit(0)\n")
        type_check = self.write("bin/type-check.sh", "#!/bin/sh\nexit 0\n")
        type_check.chmod(0o755)
        node = self.write("bin/node", "#!/bin/sh\nprintf '%s\\n' \"$*\" >> calls\nexit 7\n")
        node.chmod(0o755)
        self.child_env["PATH"] = str(self.repo / "bin") + os.pathsep + os.defpath
        self.write("hooks/example.py", "# Fixture hook.\n")
        self.write("ops/example-selftest.py", "from pathlib import Path\n"
                   "with Path('calls').open('a') as log:\n    log.write('paired\\n')\n")
        self.write("mcp-server/src/scac-mutation-registry.current.generated.js", "// baseline\n")
        self.commit("ops/ci.sh", "ops/node_test_summary.py", "ops/no-client-deliverables-gate.py",
                    "ops/hook-deletion-check.py", "ops/ci-selftest.py", "bin/type-check.sh",
                    "bin/node", "hooks/example.py", "ops/example-selftest.py",
                    "mcp-server/src/scac-mutation-registry.current.generated.js")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")

    def run_floor(self, *changed):
        for name in changed:
            with (self.repo / name).open("a") as source:
                source.write("\n")
        self.commit(*changed)
        self.child_env["CARR_CI_RANGE"] = "origin/main..HEAD"
        return subprocess.run(["bash", "ops/ci.sh", "--strict", "--only", "pushfloor"],
                              cwd=self.repo, env=self.child_env, text=True,
                              capture_output=True, timeout=30)

    def test_current_scac_registry_selects_atlas_and_propagates_failure(self):
        result = self.run_floor("mcp-server/src/scac-mutation-registry.current.generated.js")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls(), ["--test mcp-server/test/atlas-inventory-graph-read.test.mjs "
                                       "mcp-server/test/atlas-inventory-graph-web.test.mjs"])
        self.assertIn("scac-atlas", result.stdout + result.stderr)

    def test_hook_and_paired_test_changes_run_pair_once(self):
        result = self.run_floor("hooks/example.py", "ops/example-selftest.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls(), ["paired"])

    def test_hook_only_change_runs_pair(self):
        result = self.run_floor("hooks/example.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls(), ["paired"])

    def test_changed_pair_failure_is_reported_once(self):
        with (self.repo / "ops/example-selftest.py").open("a") as source:
            source.write("raise SystemExit(7)\n")
        result = self.run_floor("hooks/example.py", "ops/example-selftest.py")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("example-selftest", result.stdout + result.stderr)
        self.assertEqual(self.calls(), ["paired"])

    def test_paired_test_only_change_runs_pair(self):
        result = self.run_floor("ops/example-selftest.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls(), ["paired"])


if __name__ == "__main__":
    unittest.main()
