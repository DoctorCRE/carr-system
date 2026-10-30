#!/usr/bin/env python3
"""Exercise the actual Codex job launcher without Git, model, or network calls."""
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
DELIVERED = ("Delivery: ok missing=none branch=test-job start=start head=pushed "
             "pr=https://github.com/o/r/pull/1\n")


class CodexJobTests(unittest.TestCase):
    def launch(self, *, status=0, repo="DoctorCRE/carr-system", configs=(), result=True,
               brief="Card: WR557.S2.J2\nTitle: Require readable titles\nDescription: Require readable titles for job cards.\noriginal brief",
               dispatch_ok=True, settings=""):
        picker = types.ModuleType("skill_picker")
        picker.prepare = Mock(return_value=("prepared brief", []))
        output, errors = io.StringIO(), io.StringIO()
        self.calls = calls = []
        heads = iter(["start\n", "pushed\n"])  # the start point, then the delivered HEAD
        scratch = tempfile.TemporaryDirectory()  # the startup prune never sees the real temp dir
        self.addCleanup(scratch.cleanup)
        Path(scratch.name, "config.toml").write_text(settings)

        def execute(argv, **kwargs):
            calls.append((argv, kwargs))
            if "dispatch-job" in argv:
                return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({
                    "ok": dispatch_ok, "card_id": "WR557.S2.J2",
                    "runner_job_id": json.loads(argv[-1])["runner_job_id"]}))
            if argv[:2] == ["codex", "exec"]:
                if result:
                    Path(argv[argv.index("-o") + 1]).write_text("job result\n")
                return subprocess.CompletedProcess(argv, status)
            stdout = (next(heads) if argv[3:4] == ["rev-parse"] else
                      {"ls-remote": "pushed\trefs/heads/test-job\n",
                       "--head": "https://github.com/o/r/pull/1\n"}.get(argv[3:4] and argv[3], ""))
            return subprocess.CompletedProcess(argv, 0, stdout=stdout)

        def spawn(argv, **kwargs):
            code = execute(argv, **kwargs).returncode
            return types.SimpleNamespace(pid=12345, wait=lambda **options: code)

        args = ["codex-job", repo, "test-job", brief]
        for config in configs:
            args.extend(["-c", config])
        with (
            patch.dict(os.environ, {"CODEX_HOME": scratch.name}),
            patch.dict(sys.modules, {"skill_picker": picker}),
            patch("tempfile.gettempdir", return_value=scratch.name),
            patch("os.execv"),  # keep the test interpreter; no runtime re-exec
            patch("subprocess.run", side_effect=execute),
            patch("subprocess.Popen", side_effect=spawn),
            patch("os.killpg", side_effect=ProcessLookupError),
            patch.object(sys, "argv", args),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(errors),
        ):
            launcher = runpy.run_path(str(ROOT / "bin/codex-job"))
            code = launcher["main"]()
        calls = [call for call in calls if "dispatch-job" not in call[0]]
        if not calls:
            return code, errors.getvalue(), [], picker.prepare
        self.addCleanup(shutil.rmtree, Path(calls[0][0][-1]).parent, True)
        codex = next(call for call in calls if call[0][:2] == ["codex", "exec"])
        return code, output.getvalue(), [*calls[:2], codex], picker.prepare

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
                                      calls[-1][0][-1]])
        self.assertTrue(calls[-1][0][-1].endswith("\n\nprepared brief"))
        self.assertEqual(calls[-1][1]["stdin"], subprocess.DEVNULL)
        prepare.assert_called_once_with("Card: WR557.S2.J2\nTitle: Require readable titles\nDescription: Require readable titles for job cards.\noriginal brief", Path(clone))
        self.assertEqual((code, output), (0, "job result\n" + DELIVERED))
        self.assertFalse(Path(clone).parent.exists(), "temporary clone must be cleaned up")

    def test_launch_registers_exact_card_and_execution_facts(self):
        self.launch(configs=('model=fixture-model', 'model_reasoning_effort=high'))
        dispatch = next(argv for argv, _ in self.calls if 'dispatch-job' in argv)
        facts = json.loads(dispatch[-1])
        self.assertEqual((facts['work_request'], facts['slice'], facts['job']), ('WR-000557', 2, 2))
        self.assertEqual((facts['repository'], facts['executor'], facts['model'], facts['effort']),
                         ('DoctorCRE/carr-system', 'codex', 'fixture-model', 'high'))
        self.assertLess(self.calls.index(next(call for call in self.calls if 'dispatch-job' in call[0])),
                        self.calls.index(next(call for call in self.calls if call[0][:2] == ['codex', 'exec'])))

    def test_title_and_description_headers_are_required_and_passed_through(self):
        brief = "Card: WR557.S2.J2\nTitle: Require readable titles\nDescription: Require readable titles for job cards.\nTask body"
        self.launch(brief=brief)
        facts = json.loads(next(argv[-1] for argv, _ in self.calls if 'dispatch-job' in argv))
        self.assertEqual(facts['card_title'], 'Require readable titles')
        self.assertEqual(facts['description'], 'Require readable titles for job cards.')
        for header in ('Title: Require readable titles\n', 'Description: Require readable titles for job cards.\n'):
            with self.subTest(header=header):
                code, errors, calls, _ = self.launch(brief=brief.replace(header, ''))
                self.assertEqual(code, 1)
                self.assertIn(header.split(':')[0] + ':', errors)
                self.assertEqual(calls, [])

    def test_execution_facts_use_config_defaults_and_cli_overrides(self):
        self.launch(settings='model = "configured-model"\nmodel_reasoning_effort = "medium"\n',
                    configs=('model_reasoning_effort="high"',))
        facts = json.loads(next(argv[-1] for argv, _ in self.calls if 'dispatch-job' in argv))
        self.assertEqual((facts['model'], facts['effort']), ('configured-model', 'high'))

    def test_invalid_or_unregistered_cards_never_launch(self):
        for brief, allowed, card in (('no card', True, '<missing>'),
                                     ('Card: V2 cleanup', True, 'V2 cleanup'),
                                     ('Card: WR557.S2.J2\nTitle: Build cards\nDescription: Build cards.', False, 'WR557.S2.J2')):
            with self.subTest(brief=brief):
                code, errors, calls, _ = self.launch(brief=brief, dispatch_ok=allowed)
                self.assertEqual(code, 1)
                self.assertEqual(calls, [])
                self.assertEqual(errors, f'codex-job: {card}: job registration refused\n')

    def test_same_runner_retry_has_one_registration_identity(self):
        receipts = set()
        for _ in range(2):
            self.launch()
            facts = json.loads(next(argv[-1] for argv, _ in self.calls if 'dispatch-job' in argv))
            receipts.add((facts['idempotency_key'], facts['runner_job_id']))
        self.assertEqual(len(receipts), 1)

    def launch_with_stderr(self, target):
        saved = os.dup(2)
        try:
            with open(target, "wb") as sink:
                os.dup2(sink.fileno(), 2)
            return self.launch()
        finally:
            os.dup2(saved, 2)
            os.close(saved)

    def test_brief_opens_with_the_job_log_for_attach(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory).resolve() / "job.log"
            _, _, calls, _ = self.launch_with_stderr(log)
        first, _, rest = calls[-1][0][-1].partition("\n")
        self.assertEqual(first, f"Job log: {log} (pass it to attach-memory as transcript_ref).")
        self.assertEqual(rest, "\nprepared brief")

    def test_brief_names_no_job_log_when_stderr_is_not_a_file(self):
        _, _, calls, _ = self.launch_with_stderr(os.devnull)
        self.assertTrue(calls[-1][0][-1].startswith("Job log: none (stderr is not a file).\n\n"))

    def test_child_failure_is_returned_with_its_final_message(self):
        code, output, _, _ = self.launch(status=7)
        self.assertEqual((code, output), (7, "job result\n" + DELIVERED))

    def test_missing_result_keeps_child_exit_without_inventing_output(self):
        code, output, _, _ = self.launch(status=9, result=False)
        self.assertEqual((code, output), (9, DELIVERED))

    def test_explicit_git_url_is_preserved(self):
        _, _, calls, _ = self.launch(repo="https://github.com/DoctorCRE/carr-system.git")
        self.assertEqual(calls[0][0][-2], "https://github.com/DoctorCRE/carr-system.git")


if __name__ == "__main__":
    unittest.main()
