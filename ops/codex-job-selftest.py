#!/usr/bin/env python3
"""Exercise the actual Codex job launcher without Git, model, or network calls."""
import contextlib
import io
from pathlib import Path
import runpy
import subprocess
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


class CodexJobTests(unittest.TestCase):
    def launch(self, *, status=0, repo="DoctorCRE/carr-system", configs=(), result=True):
        picker = types.ModuleType("skill_picker")
        picker.prepare = Mock(return_value=("prepared brief", []))
        output, errors = io.StringIO(), io.StringIO()
        calls = []

        def execute(argv, **kwargs):
            calls.append((argv, kwargs))
            if argv[:2] == ["codex", "exec"]:
                if result:
                    Path(argv[argv.index("-o") + 1]).write_text("job result\n")
                return subprocess.CompletedProcess(argv, status)
            return subprocess.CompletedProcess(argv, 0)

        args = ["codex-job", repo, "test-job", "original brief"]
        for config in configs:
            args.extend(["-c", config])
        with (
            patch.dict(sys.modules, {"skill_picker": picker}),
            patch("os.execv"),  # keep the test interpreter; no runtime re-exec
            patch("subprocess.run", side_effect=execute),
            patch.object(sys, "argv", args),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            launcher = runpy.run_path(str(ROOT / "bin/codex-job"))
            code = launcher["main"]()
        return code, output.getvalue(), calls, picker.prepare

    def test_final_codex_argv_uses_hook_trust_and_prepared_brief(self):
        code, output, calls, prepare = self.launch(configs=("model_reasoning_effort=high", "model=test"))
        clone = calls[0][0][-1]
        self.assertEqual(calls[0][0], ["git", "clone", "--quiet", "--depth", "1", "--",
                                      "https://github.com/DoctorCRE/carr-system.git", clone])
        self.assertEqual(calls[1][0], ["git", "-C", clone, "checkout", "--quiet", "-b", "test-job"])
        self.assertEqual(calls[-1][0], ["codex", "exec", "-C", clone,
                                      "--dangerously-bypass-hook-trust", "-o",
                                      str(Path(clone).parent / "result.md"),
                                      "-c", "model_reasoning_effort=high", "-c", "model=test",
                                      "--", "prepared brief"])
        self.assertEqual(calls[-1][1]["stdin"], subprocess.DEVNULL)
        prepare.assert_called_once_with("original brief", Path(clone))
        self.assertEqual((code, output), (0, "job result\n"))
        self.assertFalse(Path(clone).parent.exists(), "temporary clone must be cleaned up")

    def test_child_failure_is_returned_with_its_final_message(self):
        code, output, _, _ = self.launch(status=7)
        self.assertEqual((code, output), (7, "job result\n"))

    def test_missing_result_keeps_child_exit_without_inventing_output(self):
        code, output, _, _ = self.launch(status=9, result=False)
        self.assertEqual((code, output), (9, ""))

    def test_explicit_git_url_is_preserved(self):
        _, _, calls, _ = self.launch(repo="https://github.com/DoctorCRE/carr-system.git")
        self.assertEqual(calls[0][0][-2], "https://github.com/DoctorCRE/carr-system.git")


if __name__ == "__main__":
    unittest.main()
