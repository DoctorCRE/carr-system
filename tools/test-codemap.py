"""Exercise the codebase map command against tracked fixture scripts."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops"))
from git_env import fixture_env


class CodemapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.env = fixture_env()
        self.git("init", "-q")
        (self.repo / "tools").mkdir()
        if (ROOT / "tools/codemap.py").exists():
            shutil.copyfile(ROOT / "tools/codemap.py", self.repo / "tools/codemap.py")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], env=self.env,
                              text=True, capture_output=True, check=True)

    def track(self, path, content):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self.git("add", "--", path)
        return target

    def run_map(self, *args, cwd=None):
        return subprocess.run([sys.executable, str(self.repo / "tools/codemap.py"), *args],
                              cwd=cwd or self.repo, env=self.env, text=True,
                              capture_output=True)

    def baseline(self):
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "--allow-empty", "-qm", "Synthetic baseline")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")

    def test_check_requires_descriptions_only_for_changed_scripts(self):
        legacy = self.track("ops/legacy.py", "print('legacy')\n")
        self.baseline()
        self.track("tools/new tool.py", "print('new')\n")
        result = self.run_map("--check")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout,
                         "tools/new tool.py: add an opening description under 100 characters\n")
        (self.repo / "tools/new tool.py").write_text("# Run the new tool.\nprint('new')\n")
        self.assertEqual(self.run_map("--check").returncode, 0)
        legacy.write_text("print('changed')\n")
        result = self.run_map("--check", "origin/main")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("ops/legacy.py:", result.stdout)

    def test_check_covers_commits_renames_and_only_the_agreed_script_paths(self):
        self.track("ops/rename.py", "print('rename')\n")
        self.track("tools/deleted.py", "print('delete')\n")
        self.baseline()
        self.git("mv", "ops/rename.py", "ops/renamed tool.py")
        self.git("rm", "tools/deleted.py")
        for name in ("hooks/check.py", "tools/nested/tool.py", "mcp-server/src/top.ts",
                     "mcp-server/src/nested/module.ts", "migrations/fixture.py",
                     "outside/tool.py", "ops/data.json"):
            self.track(name, "print('fixture')\n")
        for name in ("bin/launcher", "ops/githooks/new-hook", "hooks/launch", "tools/run"):
            self.track(name, "#!/bin/sh\ntrue\n")
            self.git("update-index", "--chmod=+x", name)
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-qm", "Synthetic script changes")
        result = self.run_map("--check")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            f"{name}: add an opening description under 100 characters" for name in (
                "bin/launcher", "hooks/check.py", "hooks/launch", "mcp-server/src/top.ts",
                "ops/githooks/new-hook", "ops/renamed tool.py", "tools/nested/tool.py", "tools/run")])

    def test_check_uses_merge_base_instead_of_changes_from_main(self):
        self.baseline()
        branch_base = self.git("rev-parse", "HEAD").stdout.strip()
        self.track("ops/main-only.py", "print('main only')\n")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-qm", "Synthetic main change")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.git("checkout", "--detach", branch_base)
        self.track("ops/branch.py", "# Run the branch fixture.\n")
        result = self.run_map("--check")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(result.stdout, "")

    def test_check_accepts_99_characters_and_rejects_100(self):
        self.baseline()
        target = self.track("tools/length.py", "# " + "a" * 99 + "\n")
        result = self.run_map("--check")
        self.assertEqual(result.returncode, 0, result.stderr)
        target.write_text("# " + "a" * 100 + "\n")
        result = self.run_map("--check")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("tools/length.py:", result.stdout)

    def test_check_covers_commonjs_and_applescript_headers(self):
        self.baseline()
        headers = {
            "tools/new.cjs": "// Run the CommonJS fixture.\n",
            "tools/unary.py": "# Exercise Python unary operators.\n",
            "bin/new.applescript": "-- Run the AppleScript fixture.\n",
            "tools/block.applescript": "(* Run the block comment fixture. *)\n",
        }
        for name in headers:
            self.track(name, "--value\n" if name.endswith(".py") else "return 0\n")
        result = self.run_map("--check")
        self.assertEqual(result.returncode, 1, result.stderr)
        for name in headers:
            self.assertIn(name + ":", result.stdout)
        for name, header in headers.items():
            (self.repo / name).write_text(header + "return 0\n")
        result = self.run_map("--check")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_check_covers_symlink_replaced_with_regular_script(self):
        target = self.repo / "tools/replaced.py"
        target.symlink_to("missing.py")
        self.git("add", "--", "tools/replaced.py")
        self.baseline()
        target.unlink()
        self.track("tools/replaced.py", "print('replacement')\n")
        result = self.run_map("--check")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("tools/replaced.py:", result.stdout)

    def test_check_fails_closed_for_an_unavailable_base(self):
        self.baseline()
        result = self.run_map("--check", "missing-base")
        self.assertEqual(result.returncode, 2)
        self.assertIn("codemap:", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_map_groups_and_sorts_tracked_paths(self):
        self.track("tools/z.py", "# Describe the last tool.\nprint('z')\n")
        self.track("start.sh", "# Start the application.\ntrue\n")
        self.track("tools/a.py", '"""Describe the first tool."""\n')
        (self.repo / "untracked.py").write_text("# Ignore this file.\n")
        result = self.run_map(cwd=Path(self.tmp.name))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "[root]\nstart.sh — Start the application.\n\n"
                         "[tools]\ntools/a.py — Describe the first tool.\n"
                         "tools/z.py — Describe the last tool.\n")

    def test_reads_only_opening_descriptions_after_interpreter_and_encoding(self):
        headers = {
            "a.py": '#!/usr/bin/python3\n# coding: utf-8\n"""Describe Python.\nMore detail.\n"""\n',
            "b.sh": "#!/bin/sh\n# Describe shell.\ntrue\n",
            "c.zsh": "#!/bin/zsh\n\n# Describe Z shell.\ntrue\n",
            "d.js": "// Describe JavaScript.\nfunction x() {}\n",
            "e.mjs": "/* Describe modules. */\nexport {};\n",
            "f.ts": "/**\n * Describe TypeScript.\n */\nexport {};\n",
            "g.py": "# -*- coding: utf-8 -*-\n'''\nDescribe another Python script.\n'''\n",
        }
        for name, content in headers.items():
            self.track(name, content)
        self.track("body.py", "print('body')\n# This is not an opening description.\n")
        result = self.run_map()
        self.assertEqual(result.returncode, 0, result.stderr)
        for summary in ("Describe Python.", "Describe shell.", "Describe Z shell.",
                        "Describe JavaScript.", "Describe modules.", "Describe TypeScript.",
                        "Describe another Python script."):
            self.assertIn(" — " + summary + "\n", result.stdout)
        self.assertIn("body.py — [missing description]\n", result.stdout)

    def test_discovers_executable_bin_files_without_following_symlinks(self):
        self.track("a.py", "# Describe the first root script.\n")
        self.track("z.sh", "# Describe the last root script.\n")
        self.track("bin/launch command", "# Launch the application.\n")
        self.git("update-index", "--chmod=+x", "bin/launch command")
        self.track("bin/plain", "# This file is not executable.\n")
        self.track("notes.txt", "# This file is not a script.\n")
        external = Path(self.tmp.name) / "external.py"
        external.write_text("# This external file must not be read.\n")
        (self.repo / "link.py").symlink_to(external)
        self.git("add", "--", "link.py")
        result = self.run_map()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "[root]\na.py — Describe the first root script.\n"
                         "z.sh — Describe the last root script.\n\n[bin]\n"
                         "bin/launch command — Launch the application.\n")

    def test_missing_paths_and_map_reflect_current_edits_and_removals(self):
        edited = self.track("edit me.py", "print('missing')\n")
        removed = self.track("scripts/removed.sh", "true\n")
        result = self.run_map("--missing")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, "edit me.py\nscripts/removed.sh\n")
        edited.write_text("# Describe the edited script.\nprint('updated')\n")
        removed.unlink()
        result = self.run_map()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("edit me.py — Describe the edited script.\n", result.stdout)
        self.assertNotIn("removed.sh", result.stdout)
        result = self.run_map("--missing")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.track("new.py", "print('missing')\n")
        self.assertEqual(self.run_map("--missing").stdout, "new.py\n")
        self.git("rm", "--cached", "--", "new.py")
        self.assertEqual(self.run_map("--missing").stdout, "")

    def test_run_wrapper_forwards_missing_flag_from_outside_repository(self):
        wrapper = self.repo / "run.sh"
        shutil.copyfile(ROOT / "run.sh", wrapper)
        wrapper.chmod(0o755)
        self.track("missing.py", "print('missing')\n")
        result = subprocess.run([str(wrapper), "map", "--missing"],
                                cwd=self.tmp.name, env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, "missing.py\n")

    def test_strict_ci_fails_then_passes_for_changed_script_descriptions(self):
        (self.repo / "ops").mkdir()
        (self.repo / "hooks").mkdir()
        checker = self.repo / "ops/ci.sh"
        shutil.copyfile(ROOT / "ops/ci.sh", checker)
        checker.chmod(0o755)
        shutil.copyfile(ROOT / "ops/ci-gates.py", self.repo / "ops/ci-gates.py")
        shutil.copyfile(ROOT / "ops/ci-secret-scan.py", self.repo / "ops/ci-secret-scan.py")
        (self.repo / "hooks/gate-integrity.py").write_text("raise SystemExit(0)\n")
        self.baseline()
        missing = self.track("tools/missing.py", "print('missing')\n")
        for content, expected in (("print('missing')\n", 1),
                                  ("# Run the fixture tool.\nprint('fixture')\n", 0)):
            missing.write_text(content)
            result = subprocess.run([str(checker), "--strict", "--only", "gates"],
                                    cwd=self.tmp.name, env=self.env, text=True,
                                    capture_output=True, timeout=30)
            self.assertEqual(result.returncode, expected, result.stderr + result.stdout)
            if expected:
                self.assertIn("tools/missing.py:", result.stderr + result.stdout)
                self.assertIn("codemap", result.stdout)


if __name__ == "__main__":
    unittest.main()
