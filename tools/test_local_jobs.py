#!/usr/bin/env python3
"""Explicit local job execution remains independent of watchdog scans."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tools"))
from progress_board_test_support import seed_captured_task



class RunnerTests(unittest.TestCase):
    def test_board_github_opt_out_survives_re_registration(self):
        import local_jobs as w
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seed_captured_task(root / "out", "carr-v5", "synthetic-card", "executor")
            calls = root / "github-calls"
            gh = root / "gh"
            gh.write_text("#!" + sys.executable + "\nfrom pathlib import Path\n"
                          + "Path(" + repr(str(calls)) + ").open('a').write('call\\n')\n"
                          + "print('{}')\n")
            gh.chmod(0o755)
            config_path = root / "config.json"
            c = w.load_config(ROOT / "ops/config/local-jobs.json")
            c["board_github_enabled"] = False
            config_path.write_text(json.dumps(c))
            real_run = subprocess.run
            observed_env = []
            def observe(argv, **kwargs):
                observed_env.append(kwargs["env"])
                return real_run(argv, **kwargs)
            with patch.object(w.subprocess, "run", side_effect=observe), patch.dict(os.environ, {"PATH": str(root) + os.pathsep + os.environ["PATH"],
                                         "PROGRESS_BOARD_SKIP_GH": "0"}):
                for status in ("running", "review"):
                    # Every registration starts from disk, without an inherited opt-out.
                    w.board_task(root, w.load_config(config_path), "synthetic-card", "executor",
                                 status, "Synthetic evidence")
            self.assertTrue(observed_env)
            self.assertTrue(all(e.get("PROGRESS_BOARD_SKIP_GH") == "1" for e in observed_env))
            self.assertFalse(calls.exists(), "re-registration must spend zero GitHub requests")
            board = json.loads((root / "out/boards/carr-v5.json").read_text())
            self.assertEqual(board["tasks"]["synthetic-card"]["status"], "review")
            self.assertFalse(w.load_config(ROOT / "ops/config/local-jobs.json")["board_github_enabled"])

    def test_command_gets_eof_and_exit_is_recorded_on_board(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, CARR_JOB_ROOT=directory, CARR_JOB_BOARD="test")
            seed_captured_task(Path(directory) / "out", "test", "stdin-card")
            result = subprocess.run(["bash", str(ROOT / "bin/agent-run.sh"), "stdin-card",
                                     "deterministic", "1", "--", sys.executable, "-c",
                                     "import sys; print('EOF=' + repr(sys.stdin.read())); sys.exit(7)"],
                                    input="must not reach child", text=True, capture_output=True, env=env)
            self.assertEqual(result.returncode, 7, result.stderr)
            rows = [json.loads(s) for s in (Path(directory) / "out/jobs/registry.jsonl").read_text().splitlines()]
            self.assertEqual(rows[0]["card"], "stdin-card")
            self.assertGreater(rows[0]["pid"], 0)
            self.assertEqual(rows[-1]["exit_code"], 7)
            self.assertIn("EOF=''", rows[-1]["log_tail"])
            board = json.loads((Path(directory) / "out/boards/test.json").read_text())
            self.assertEqual(board["tasks"]["stdin-card"]["status"], "blocked")

if __name__ == "__main__":
    unittest.main()
