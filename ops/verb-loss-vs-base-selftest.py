#!/usr/bin/env python3
"""ops/verb-loss-vs-base.sh refuses a registry that silently drops a verb its base shipped."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from git_env import fixture_env

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "ops/verb-loss-vs-base.sh"
RETIRED = "accept-benchmark-manifest-draft"


def registry(*names: str) -> str:
    return "export const TOOLS = {" + ", ".join(f'"{n}": {{}}' for n in names) + "};\n"


class VerbLossVsBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.env = fixture_env()
        self.git("init", "-q")
        self.write(registry("alpha", "beta", RETIRED))
        self.git("add", "mcp-server")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD").strip()

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *argv: str) -> str:
        return subprocess.run(["git", "-C", str(self.repo), *argv], check=True,
                              capture_output=True, text=True, env=self.env).stdout

    def write(self, text: str) -> None:
        path = self.repo / "mcp-server/src/tools.js"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def run_guard(self) -> subprocess.CompletedProcess:
        return subprocess.run(["sh", str(SCRIPT), str(self.repo), self.base],
                              capture_output=True, text=True, env=self.env)

    def test_unlisted_loss_is_refused_by_name(self):
        self.write(registry("alpha"))
        result = self.run_guard()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("unlisted verb loss: beta", result.stderr)

    def test_additions_pass_and_print_the_count(self):
        self.write(registry("alpha", "beta", "gamma"))
        result = self.run_guard()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "3")

    def test_loss_listed_in_the_retirement_ledger_passes(self):
        self.write(registry("alpha", "beta"))
        result = self.run_guard()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "2")

    def test_unreadable_base_fails_closed(self):
        self.write(registry("alpha", "beta"))
        result = subprocess.run(["sh", str(SCRIPT), str(self.repo), "0" * 40],
                                capture_output=True, text=True, env=self.env)
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
