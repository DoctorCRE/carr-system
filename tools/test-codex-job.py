"""Exercise the launcher with fixture files and deterministic subprocess results."""
import contextlib
import io
import json
from pathlib import Path
import runpy
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import skill_picker


class CodexJobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.origin = self.root / "origin"
        self.origin.mkdir()
        (self.origin / "hello.txt").write_text("hello\n")
        self.exit_code = 0
        self.terminate_job = False
        self.branch = None
        self.handlers = {}

    def run_command(self, command, **kwargs):
        """Fake Git and Codex at the launcher's existing subprocess seam."""
        if command[:2] == ["git", "clone"]:
            self.assertEqual(command[:-1], ["git", "clone", "--quiet", "--depth", "1", "--", str(self.origin)])
            self.assertTrue(kwargs["check"])
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            shutil.copytree(self.origin, command[-1])
        elif command[0] == "git":
            self.assertTrue(kwargs["check"])
            self.assertEqual(command[1], "-C")
            self.assertTrue((Path(command[2]) / "hello.txt").exists())
            if command[3] == "checkout":
                self.assertEqual(command[3:6], ["checkout", "--quiet", "-b"])
                self.branch = command[6]
            else:
                self.assertEqual(command[3:], ["config", "user.email", "jbookout@users.noreply.github.com"])
        else:
            self.assertEqual(command[:2], ["codex", "exec"])
            args = command[1:]
            clone = Path(args[args.index("-C") + 1])
            self.assertEqual((clone / "hello.txt").read_text(), "hello\n")
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertIs(kwargs["stdout"], sys.stderr)
            (self.root / "observed.json").write_text(json.dumps({
                "args": args, "clone": str(clone), "branch": self.branch, "stdin": "",
            }))
            if self.terminate_job:
                self.handlers[signal.SIGTERM](signal.SIGTERM, None)
            Path(args[args.index("-o") + 1]).write_text("top-level files: 1\n")
            print("Codex progress", file=sys.stderr)
            return subprocess.CompletedProcess(command, self.exit_code)
        return subprocess.CompletedProcess(command, 0)

    def run_job(self, *extra):
        argv = [str(ROOT / "bin/codex-job"), str(self.origin), "test/count",
                "Count the top-level files and stop.", *extra]
        # Skill selection has its own fake-SDK suite. This launcher test needs
        # only its prepared brief, never the user's catalog or credential file.
        def install_handler(signum, handler):
            self.handlers[signum] = handler

        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(sys, "prefix", str(ROOT / ".venv")), \
                mock.patch.object(signal, "signal", side_effect=install_handler) as signals, \
                mock.patch.object(sys, "stdin", io.StringIO("do not append this to the brief")), \
                mock.patch.object(skill_picker, "prepare", side_effect=lambda brief, _: ("Skills:\n\n" + brief, [])) as prepare, \
                mock.patch.object(subprocess, "run", side_effect=self.run_command), \
                contextlib.redirect_stdout(io.StringIO()) as stdout, \
                contextlib.redirect_stderr(io.StringIO()) as stderr:
            with self.assertRaises(SystemExit) as exited:
                runpy.run_path(str(ROOT / "bin/codex-job"), run_name="__main__")
            code = exited.exception.code
            signals.assert_called_once_with(signal.SIGTERM, self.handlers[signal.SIGTERM])
            observed = json.loads((self.root / "observed.json").read_text())
            prepare.assert_called_once_with(argv[3], Path(observed["clone"]))
        return subprocess.CompletedProcess(argv, code, stdout.getvalue(), stderr.getvalue())

    def test_fresh_branch_final_message_and_cleanup(self):
        result = self.run_job('-c', 'model_reasoning_effort=low')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'top-level files: 1\n')
        observed = json.loads((self.root / 'observed.json').read_text())
        self.assertEqual(observed['branch'], 'test/count')
        self.assertEqual(observed['stdin'], '')
        self.assertIn('--dangerously-bypass-hook-trust', observed['args'])
        self.assertIn('model_reasoning_effort=low', observed['args'])
        self.assertTrue(observed['args'][-1].startswith('Skills:'))
        self.assertIn('Count the top-level files and stop.', observed['args'][-1])
        self.assertNotIn('-m', observed['args'])
        self.assertFalse(Path(observed['clone']).parent.exists())

    def test_failed_codex_preserves_its_exit_code_and_final_message_then_cleans_up(self):
        self.exit_code = 7
        result = self.run_job()
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertEqual(result.stdout, 'top-level files: 1\n')
        observed = json.loads((self.root / 'observed.json').read_text())
        self.assertFalse(Path(observed['clone']).parent.exists())

    def test_termination_cleans_up_the_clone(self):
        self.terminate_job = True
        result = self.run_job()
        self.assertTrue((self.root / 'observed.json').exists())
        self.assertEqual(result.returncode, 143, result.stderr)
        observed = json.loads((self.root / 'observed.json').read_text())
        self.assertFalse(Path(observed['clone']).parent.exists())


if __name__ == '__main__':
    unittest.main()
