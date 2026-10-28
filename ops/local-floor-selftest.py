#!/usr/bin/env python3
"""Exercise the real local Git hooks and push floor in disposable repositories."""
from pathlib import Path
import os
import re
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
        self.copy("ops/ci-pushfloor.py")
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
        self.commit("ops/ci.sh", "ops/ci-pushfloor.py", "ops/node_test_summary.py",
                    "ops/no-client-deliverables-gate.py",
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

    def verdict(self, result):
        match = re.search(r"(OK|FAIL)\s+pushfloor\s+(.*)", re.sub(r"\x1b\[[0-9;]*m", "", result.stdout))
        self.assertIsNotNone(match, result.stdout + result.stderr)
        return match.groups()

    def stub(self, name, call, status=0):
        self.write(name, "from pathlib import Path\nimport sys\n"
                   "with Path('calls').open('a') as log:\n"
                   f"    log.write(' '.join([{call!r}, *sys.argv[1:]]) + '\\n')\n"
                   f"raise SystemExit({status})\n")
        self.commit(name)
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")

    def test_summary_names_the_checks_that_ran(self):
        result = self.run_floor("hooks/example.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.verdict(result)[0], "OK")
        self.assertRegex(self.verdict(result)[1], r"^\d+s · baseline, path shape \+ types gate-closure$")

    def test_failures_are_named_with_remedies_and_failed_logs_kept(self):
        self.write("ops/no-client-deliverables-gate.py", "print('tracked client path')\nraise SystemExit(1)\n")
        with (self.repo / "ops/example-selftest.py").open("a") as source:
            source.write("raise SystemExit(7)\n")
        result = self.run_floor("hooks/example.py")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.verdict(result)[0], "FAIL")
        self.assertRegex(self.verdict(result)[1],
                         r"^failed: no-client-deliverables example-selftest \(\d+s\)$")
        self.assertIn("tracked client path", result.stderr)
        self.assertIn("git rm it", result.stderr)
        self.assertIn("fails its own acceptance test: .venv/bin/python ops/example-selftest.py",
                      result.stderr)
        logs = Path(re.search(r"ci-retained-logs: (.*)", result.stderr).group(1))
        self.addCleanup(shutil.rmtree, logs, True)
        log = logs / "pushfloor-no-client-deliverables.log"
        self.assertEqual(log.read_text(), "tracked client path\n")
        self.assertEqual(Path(f"{log}.rc").read_text(), "1\n")
        self.assertTrue((logs / "pushfloor-example-selftest.log.rc").is_file())
        self.assertFalse((logs / "pushfloor-hook-deletion.log").exists())

    def test_absent_mypy_is_reported_as_not_run(self):
        self.write("bin/type-check.sh", "#!/bin/sh\nexit 78\n").chmod(0o755)
        result = self.run_floor("hooks/example.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("not run\x1b[0m  types — mypy absent", result.stderr)
        self.assertRegex(self.verdict(result)[1], r"path shape \+ gate-closure$")

    def test_path_hygiene_judges_only_paths_new_to_main(self):
        self.stub("ops/githooks/path-hygiene-check.py", "hygiene")
        self.write("notes/new-note.txt", "new\n")
        result = self.run_floor("notes/new-note.txt", "hooks/example.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls(), ["hygiene --paths notes/new-note.txt", "paired"])

    def test_migration_change_runs_the_structure_gate(self):
        self.stub("ops/migration-order-gate.py", "order", status=3)
        self.write("migrations/0001_fixture.sql", "select 1;\n")
        result = self.run_floor("migrations/0001_fixture.sql")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.calls(), ["order"])
        self.assertIn("reserve a forward migration number", result.stderr)
        self.assertRegex(self.verdict(result)[1], r"^failed: migration-structure \(\d+s\)$")

    def assert_gate_only_change_closes_gates(self, gate_file):
        self.stub("ops/hook-env-isolation-selftest.py", "isolation")
        self.write(gate_file, "{}\n")
        result = self.run_floor(gate_file)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls(), ["isolation"])
        self.assertRegex(self.verdict(result)[1], r"path shape \+ gate-closure$")

    def test_codex_hooks_config_only_change_closes_gates(self):
        self.assert_gate_only_change_closes_gates("ops/config/codex-hooks.json")

    def test_git_hook_only_change_closes_gates(self):
        self.assert_gate_only_change_closes_gates("ops/githooks/example-hook")

    def test_missing_atlas_packages_route_to_npm_ci(self):
        self.write("bin/node", "#!/bin/sh\nprintf '%s\\n' \"Error [ERR_MODULE_NOT_FOUND]: "
                   "Cannot find package 'atlas-dependency'\"\nexit 1\n").chmod(0o755)
        result = self.run_floor("mcp-server/src/scac-mutation-registry.current.generated.js")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Cannot find package 'atlas-dependency'", result.stderr)
        self.assertIn("missing Node packages; run npm ci --prefix mcp-server", result.stderr)
        self.assertNotIn("active registry changed", result.stderr)
        self.assertRegex(self.verdict(result)[1], r"^failed: scac-atlas \(\d+s\)$")

    def test_detector_change_runs_its_qualification_once(self):
        self.stub("security/test_example_guard.py", "qualify", status=4)
        self.write("security/allowlist.json", "{}\n")
        result = self.run_floor("security/allowlist.json")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.calls(), ["qualify"])
        self.assertIn("fails its qualification", result.stderr)
        self.assertRegex(self.verdict(result)[1], r"^failed: secret-test_example_guard \(\d+s\)$")

    def test_unrelated_change_skips_detector_qualification(self):
        self.stub("security/test_example_guard.py", "qualify", status=4)
        result = self.run_floor("hooks/example.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls(), ["paired"])

    def test_new_test_file_runs_collection_check(self):
        self.stub("ops/ci-selftest.py", "collection")
        self.write("notes/test_fixture.mjs", "export {};\n")
        result = self.run_floor("notes/test_fixture.mjs")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls(), ["collection --collection-only"])
        self.assertRegex(self.verdict(result)[1], r"path shape \+ test-collection$")

    def test_a_runner_that_dies_without_a_verdict_fails_the_class(self):
        self.write("ops/ci-pushfloor.py", "raise SystemExit(2)\n")
        result = self.run_floor("hooks/example.py")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.verdict(result),
                         ("FAIL", "push floor runner exited 2 without a verdict"))


class PrePrTests(RepositoryFixture):
    """./run.sh pre-pr: the three checks hosted CI failed on in the 2026-10-28 retro."""

    def setUp(self):
        super().setUp()
        self.copy("ops/ci-pushfloor.py")
        self.copy("ops/review-gate.py")
        self.scanner("security/leak_guard.py", "leak")
        self.scanner("hooks/gate-integrity.py", "integrity")
        self.write("bin/gh", "#!/bin/sh\nexit 1\n").chmod(0o755)
        self.child_env["PATH"] = str(self.repo / "bin") + os.pathsep + os.defpath
        self.commit("ops/ci-pushfloor.py", "ops/review-gate.py", "security/leak_guard.py",
                    "hooks/gate-integrity.py", "bin/gh")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.base = self.git("rev-parse", "HEAD").stdout.strip()
        self.write("app.py", "change = 1\n")
        self.commit("app.py")
        self.head = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("update-ref", "refs/remotes/origin/main", self.base)

    def scanner(self, name, call, status=0):
        self.write(name, "from pathlib import Path\nimport sys\n"
                   "with Path('calls').open('a') as log:\n"
                   f"    log.write(' '.join([{call!r}, *sys.argv[1:]]) + '\\n')\n"
                   f"raise SystemExit({status})\n")

    def pre_pr(self, body="Card: WR576.S1.J1\n", *extra):
        args = []
        if body is not None:
            (self.repo.parent / "body.md").write_text(body)
            args = ["--body", str(self.repo.parent / "body.md")]
        return subprocess.run(["python3", "ops/ci-pushfloor.py", "--pre-pr", *args, *extra],
                               cwd=self.repo, env=self.child_env, text=True,
                               capture_output=True, timeout=30)

    def test_clean_branch_passes_with_full_merge_base_range(self):
        result = self.pre_pr()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls(), [f"leak --range {self.base}..{self.head}", "integrity --strict"])
        self.assertEqual(result.stdout, "")

    def test_card_line_uses_the_review_gate_rule(self):
        for body in ("Card: WR576.S1.J1 extra\n", "Summary only\n", " Card: WR576.S1.J1\n"):
            with self.subTest(body=body):
                result = self.pre_pr(body)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertEqual(len(result.stdout.splitlines()), 1)
                self.assertIn("Card: WR<n>.S<n>.J<n>", result.stdout)
        self.assertEqual(self.pre_pr("Intro\nCard: WR576.S1.J1\nEnd\n").returncode, 0)

    def test_docs_only_branch_needs_no_card(self):
        self.git("reset", "-q", "--hard", self.base)
        self.write("docs/note.md", "note\n")
        self.commit("docs/note.md")
        self.assertEqual(self.pre_pr("no card\n").returncode, 0)

    def test_no_body_and_no_pull_request_says_to_pass_one(self):
        result = self.pre_pr(None)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("--body", result.stdout)

    def test_open_pull_request_body_is_read_from_gh(self):
        self.write("bin/gh", "#!/bin/sh\nprintf 'Card: WR576.S1.J1\\n'\n").chmod(0o755)
        result = self.pre_pr(None)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_each_failure_is_one_line_with_its_remedy(self):
        self.scanner("security/leak_guard.py", "leak", status=1)
        self.scanner("hooks/gate-integrity.py", "integrity", status=1)
        result = self.pre_pr("none\n")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 3, result.stdout)
        self.assertIn(f"--range {self.base}..{self.head}", lines[0])
        self.assertIn("gate-integrity.py --bless", lines[1])
        self.assertIn("Card:", lines[2])


if __name__ == "__main__":
    unittest.main()
