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


if __name__ == "__main__":
    unittest.main()
