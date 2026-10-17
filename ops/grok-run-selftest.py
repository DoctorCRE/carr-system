#!/usr/bin/env python3
"""Exercise the sanctioned runner's public text, receipt and exit contracts."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest
import sys
import importlib.util
import io
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/room-bridge"))
import grok_wire

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "bin/grok-run.sh"
FIXTURES = ROOT / "ops/fixtures/grok-run"
spec = importlib.util.spec_from_file_location("grok_runner", ROOT / "bin/grok_run.py")
assert spec is not None and spec.loader is not None
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class GrokRunTests(unittest.TestCase):
    def test_full_report_survives_a_later_empty_message(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "stream.ndjson"
            fixture.write_text('\n'.join(map(json.dumps, [
                {"type": "text", "data": "First report."},
                {"type": "usage", "messageId": "one"},
                {"type": "text", "data": "Second report."},
                {"type": "usage", "messageId": "two"},
                {"type": "usage", "messageId": "empty"},
                {"type": "end", "stopReason": "end_turn", "modelUsage": {
                    "grok-4.7-build": {"modelCalls": 3}}}])) + '\n')
            proc = subprocess.run(["bash", str(RUNNER), "--prompt", "test"],
                env={**os.environ, "GROK_RUN_FAKE_NDJSON": str(fixture)},
                capture_output=True, text=True, timeout=10)
            self.assertEqual(proc.returncode, 0)
            self.assertEqual(proc.stdout, "First report.\n\nSecond report.\n")
    def test_default_runner_refuses_unsafe_urls_before_preflight_or_invocation(self):
        for writable in (False, True):
            for url in ("https://fixture-user:fixture-secret@example.com/post",
                        "https://example.com/post?access_token=fixture-secret",
                        "http://127.0.0.1/source", "http://[::1]/source",
                        "http://service.internal/source"):
                with self.subTest(writable=writable, url=url), tempfile.TemporaryDirectory() as td:
                    receipt_path = Path(td) / "receipt.json"
                    argv = ["grok-run", "--prompt", "Explain " + url]
                    if writable:
                        argv.append("--writable")
                    with mock.patch.object(sys, "argv", argv), \
                            mock.patch.dict(os.environ, {"GROK_RUN_RECEIPT": str(receipt_path)}, clear=True), \
                            mock.patch.object(runner, "preflight", return_value="1.0.0") as preflight, \
                            mock.patch.object(runner, "invoke_cli", return_value=subprocess.CompletedProcess(
                                [], 0, (FIXTURES / "good.ndjson").read_text(), "")) as provider, \
                            mock.patch.object(sys, "stdout", io.StringIO()) as stdout, \
                            mock.patch.object(sys, "stderr", io.StringIO()) as stderr:
                        self.assertEqual(runner.main(), 6)
                        preflight.assert_not_called()
                        provider.assert_not_called()
                        self.assertEqual(stdout.getvalue(), "")
                        receipt = json.loads(receipt_path.read_text())
                        self.assertEqual((receipt["status"], receipt["code"], receipt["detail"]),
                                         ("failed", 6, "invalid_retrieval_url"))
                        diagnostics = stdout.getvalue() + stderr.getvalue() + receipt_path.read_text()
                        self.assertNotIn("fixture-user", diagnostics)
                        self.assertNotIn("fixture-secret", diagnostics)

    def test_link_in_prose_prompt_does_not_enable_retrieval_even_when_writable(self):
        for writable in (False, True):
            run, receipt = self.run_fixture("good.ndjson", "--prompt",
                "Council brief: weigh https://github.com/example/repo/pull/1 and answer in prose.",
                *(["--writable"] if writable else []))
            self.assertEqual(run.returncode, 0)
            self.assertEqual(run.stdout, "Hello world\n")
            self.assertEqual(receipt["actual_models"], ["grok-4.7-build"])

    def test_explicit_retrieval_outcomes_share_codes_and_receipt_schema(self):
        url = "https://example.com/source"
        end = json.loads((FIXTURES / "hook-turns.ndjson").read_text().splitlines()[-1])
        source = json.dumps({"retrieval": {"requested_urls": [url], "source_urls": [url],
            "sources": [{"url": url, "text": "Original source"}],
            "unresolved_portions": [], "status": "complete"}})
        schemas = []
        for text, terminal, code in ((source, end, 0), ("Noted", end, 6),
                (source, {**end, "stopReason": "cancelled"}, 4),
                (source, {**end, "modelUsage": {"grok-4.6": {"modelCalls": 1}}}, 5)):
            with tempfile.TemporaryDirectory() as td:
                fixture = Path(td) / "stream.ndjson"
                fixture.write_text(json.dumps({"type": "text", "data": text}) + "\n" + json.dumps(terminal))
                receipt_path = Path(td) / "receipt.json"
                env = dict(os.environ, GROK_RUN_FAKE_NDJSON=str(fixture), GROK_RUN_RECEIPT=str(receipt_path))
                run = subprocess.run(["bash", str(RUNNER), "--retrieve", "--prompt", "Read " + url],
                    env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(run.returncode, code, run.stderr)
                outcome = json.loads(run.stdout)
                self.assertEqual(outcome["code"], code)
                receipt = json.loads(receipt_path.read_text())
                self.assertIn("cli_version", receipt)
                self.assertIn("actual_models", receipt)
                self.assertIn("num_turns", receipt)
                schemas.append(set(receipt))
                self.assertNotIn("Original source", receipt_path.read_text())
        self.assertTrue(all(schema == schemas[0] for schema in schemas))

    def test_explicit_retrieval_sign_in_refusal_retains_code_three(self):
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(sys, "argv", ["grok-run", "--retrieve", "--prompt", "Read https://example.com"]), \
                mock.patch.dict(os.environ, {"GROK_RUN_RECEIPT": str(Path(td) / "receipt.json")}, clear=True), \
                mock.patch.object(runner, "preflight", side_effect=runner.PreflightError("Sign in", 3)), \
                mock.patch.object(runner, "sign_in_alert") as alert, \
                mock.patch.object(sys, "stdout", io.StringIO()) as stdout, \
                mock.patch.object(sys, "stderr", io.StringIO()):
            self.assertEqual(runner.main(), 3)
            self.assertEqual(json.loads(stdout.getvalue())["detail"], "grok_sign_in_required")
            self.assertEqual(json.loads((Path(td) / "receipt.json").read_text())["code"], 3)
            alert.assert_called_once()

    def test_credential_url_is_refused_before_preflight_without_secret_diagnostics(self):
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(sys, "argv", ["grok-run", "--retrieve", "--prompt",
                    "Retrieve https://fixture-user:fixture-secret@example.com/post"]), \
                mock.patch.dict(os.environ, {"GROK_RUN_RECEIPT": str(Path(td) / "receipt.json")}, clear=True), \
                mock.patch.object(runner, "preflight", side_effect=runner.PreflightError("fixture")) as preflight, \
                mock.patch.object(sys, "stdout", io.StringIO()) as stdout, \
                mock.patch.object(sys, "stderr", io.StringIO()) as stderr:
            self.assertEqual(runner.main(), 6)
            preflight.assert_not_called()
            result = json.loads(stdout.getvalue())
            self.assertEqual(result["detail"], "invalid_retrieval_url")
            diagnostics = stdout.getvalue() + stderr.getvalue() + Path(result["diagnostic_path"]).read_text()
            self.assertNotIn("fixture-user", diagnostics)
            self.assertNotIn("fixture-secret", diagnostics)

    def test_retrieval_default_receipt_survives_stderr_redirection(self):
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ, HOME=td, GROK_RUN_FAKE_NDJSON=str(FIXTURES / "hook-turns.ndjson"))
            env.pop("GROK_RUN_RECEIPT", None)
            run = subprocess.run(["bash", str(RUNNER), "--effort", "low", "--max-turns", "2",
                "--timeout-seconds", "10", "--retrieve", "--prompt", "Retrieve https://example.com/source"],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=10)
            self.assertEqual(run.returncode, 6)
            result = json.loads(run.stdout)
            self.assertEqual(result["provider_metadata"]["effort"], "low")
            path = Path(result["diagnostic_path"])
            self.assertTrue(path.is_relative_to(Path(td) / ".local/state/carr/grok-runs"))
            receipt = json.loads(path.read_text())
            self.assertEqual((receipt["effort"], receipt["max_turns"], receipt["timeout_seconds"]), ("low", 2, 10))
            self.assertNotIn("Hello world", path.read_text())

    def test_retrieval_preflight_failure_has_sanitized_file_receipt(self):
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(sys, "argv", ["grok-run", "--retrieve", "--prompt", "Retrieve https://example.com/source"]), \
                mock.patch.dict(os.environ, {"GROK_RUN_RECEIPT": str(Path(td) / "receipt.json")}, clear=True), \
                mock.patch.object(runner, "preflight", side_effect=runner.PreflightError("grok-run: CLI upgrade failed")), \
                mock.patch.object(sys, "stdout", io.StringIO()) as stdout, \
                mock.patch.object(sys, "stderr", io.StringIO()):
            self.assertEqual(runner.main(), 1)
            result = json.loads(stdout.getvalue())
            self.assertEqual(result["detail"], "grok_preflight_failed")
            self.assertEqual(json.loads(Path(result["diagnostic_path"]).read_text())["status"], "failed")

    def test_retrieval_failure_keeps_receipt_when_stderr_is_discarded(self):
        with tempfile.TemporaryDirectory() as td:
            fixture = Path(td) / "answer.ndjson"
            end = json.loads((FIXTURES / "hook-turns.ndjson").read_text().splitlines()[-1])
            fixture.write_text(json.dumps({"type": "text", "data": "No further action."}) + "\n" + json.dumps(end))
            receipt_path = Path(td) / "receipt.json"
            env = dict(os.environ, GROK_RUN_FAKE_NDJSON=str(fixture), GROK_RUN_RECEIPT=str(receipt_path))
            run = subprocess.run(["bash", str(RUNNER), "--retrieve", "--prompt", "Retrieve https://example.com/post"],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=10)
            self.assertEqual(run.returncode, 6)
            result = json.loads(run.stdout)
            self.assertEqual(result["retrieval"]["status"], "unusable_retrieval")
            receipt = json.loads(receipt_path.read_text())
            self.assertEqual(receipt["detail"], "unusable_retrieval")
            self.assertEqual(result["diagnostic_path"], str(receipt_path))

    def test_sign_in_exit_alerts_once_without_provider_diagnostics(self):
        with mock.patch.object(sys, "argv", ["grok-run", "--prompt", "test"]), \
                mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(runner, "preflight", side_effect=runner.PreflightError("Grok needs sign-in: run grok login", 3)), \
                mock.patch.object(runner, "sign_in_alert", create=True) as alert, \
                mock.patch.object(sys, "stderr", io.StringIO()) as stderr:
            self.assertEqual(runner.main(), 3)
            alert.assert_called_once_with()
            self.assertEqual(stderr.getvalue().strip(), "Grok needs sign-in: run grok login")

    def test_health_probe_owns_the_sign_in_alert(self):
        with mock.patch.object(sys, "argv", ["grok-run", "--prompt", "test", "--no-sign-in-alert"]), \
                mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(runner, "preflight", side_effect=runner.PreflightError("Grok needs sign-in", 3)), \
                mock.patch.object(runner, "sign_in_alert") as alert, \
                mock.patch.object(sys, "stderr", io.StringIO()):
            self.assertEqual(runner.main(), 3)
            alert.assert_not_called()

    def test_alert_failure_preserves_sign_in_exit_and_reports_failure(self):
        with mock.patch.object(sys, "argv", ["grok-run", "--prompt", "test"]), \
                mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(runner, "preflight", side_effect=runner.PreflightError("Grok needs sign-in: run grok login", 3)), \
                mock.patch.object(runner, "sign_in_alert", create=True, side_effect=RuntimeError("private diagnostic")), \
                mock.patch.object(sys, "stderr", io.StringIO()) as stderr:
            self.assertEqual(runner.main(), 3)
            self.assertIn("alert FAILED", stderr.getvalue())
            self.assertNotIn("private diagnostic", stderr.getvalue())

    def test_wrapper_report_and_desk_last_message_have_distinct_contracts(self):
        raw = (FIXTURES / "hook-turns.ndjson").read_text()
        parsed = grok_wire.parse_result(raw, 0, require_identity=False)
        text, code = parsed["result"], parsed["code"]
        self.assertEqual(code, 0)
        self.assertIn("Fetched post: the announced skill is example-pro.", text)
        self.assertTrue(text.startswith("I will fetch the post.\n\n"))
        self.assertEqual(text.count("Noted. Standing by."), 5)
        self.assertEqual(grok_wire.parse_result(raw, 0)["result"], "Noted. Standing by.")

    def test_short_literal_answers_are_preserved(self):
        end = json.loads((FIXTURES / "hook-turns.ndjson").read_text().splitlines()[-1])
        for ack in ("Noted. Standing by.", "Noted. No tools were called.", "No action."):
            with self.subTest(ack=ack):
                raw = [json.dumps({"type": "text", "data": ack}), json.dumps(end)]
                parsed = grok_wire.parse_result("\n".join(raw), 0)
                text, code = parsed["result"], parsed["code"]
                self.assertEqual(text, ack)
                self.assertEqual(code, 0)

    def test_corrections_and_literal_answers_through_both_public_callers(self):
        end = json.loads((FIXTURES / "hook-turns.ndjson").read_text().splitlines()[-1])
        correction = "The rule boot stopped after page 3. Pages 1 and 2 are already complete; retry page 3."
        for answer in (correction, "No action.", "The lifecycle warning is noted. Restart the failed job."):
            for earlier in ([], [{"type": "text", "data": "The boot stopped after page 1."}, {"type": "usage"}]):
                with self.subTest(answer=answer, earlier=bool(earlier)):
                    raw = '\n'.join(map(json.dumps, [*earlier, {"type": "text", "data": answer}, {"type": "usage"}, end]))
                    provider = mock.Mock(return_value=subprocess.CompletedProcess([], 0, raw, ''))
                    desk = grok_wire.run_task({"model": "grok-4.7", "effort": "high", "sandbox": "read-only"}, 'Explain the result', run=provider)
                    self.assertEqual(desk.get("result"), answer)
                    self.assertEqual(desk["status"], "completed")
                    for writable in (False, True):
                        argv = ['grok-run', '--prompt', 'Explain the result'] + (['--writable'] if writable else [])
                        with mock.patch.object(sys, 'argv', argv), mock.patch.object(runner, 'preflight', return_value='fixture'), \
                                mock.patch.object(runner, 'invoke_cli', return_value=provider.return_value), \
                                mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(sys, 'stdout', io.StringIO()) as stdout, \
                                mock.patch.object(sys, 'stderr', io.StringIO()):
                            self.assertEqual(runner.main(), 0)
                            self.assertEqual(stdout.getvalue().strip(), ("The boot stopped after page 1.\n\n" if earlier else "") + answer)

    def test_latest_short_substantive_answer_wins(self):
        events = [json.loads(line) for line in (FIXTURES / "hook-turns.ndjson").read_text().splitlines()]
        events[-1:-1] = [{"type": "text", "data": "The answer is 42."}, {"type": "usage"}]
        parsed = grok_wire.parse_result("\n".join(map(json.dumps, events)), 0)
        text, code = parsed["result"], parsed["code"]
        self.assertEqual((text, code), ("The answer is 42.", 0))

    def test_timeout_option_reaches_provider_and_preserves_default(self):
        for value in (None, "1", "600", "1800"):
            with self.subTest(timeout=value):
                argv = ["grok-run", "--prompt", "test"]
                if value is not None:
                    argv += ["--timeout-seconds", value]
                provider = mock.Mock(return_value=subprocess.CompletedProcess(
                    [], 0, (FIXTURES / "good.ndjson").read_text(), ""))
                with mock.patch.object(sys, "argv", argv), \
                        mock.patch.dict(os.environ, {}, clear=True), \
                        mock.patch.object(runner, "preflight", return_value="1.0.10"), \
                        mock.patch.object(runner, "invoke_cli", side_effect=
                            lambda *a, **kw: grok_wire.invoke_cli(*a, **kw, run=provider)), \
                        mock.patch.object(sys, "stdout", io.StringIO()), \
                        mock.patch.object(sys, "stderr", io.StringIO()):
                    self.assertEqual(runner.main(), 0)
                self.assertEqual(provider.call_args.kwargs["timeout"],
                                 180 if value is None else int(value))

    def run_fixture(self, fixture, *args, receipt_file=False):
        with tempfile.TemporaryDirectory(prefix="grok-run-test-") as directory:
            env = dict(os.environ)
            env.pop("GROK_RUN_RECEIPT", None)
            env["GROK_RUN_FAKE_NDJSON"] = str(FIXTURES / fixture)
            if receipt_file:
                env["GROK_RUN_RECEIPT"] = str(Path(directory) / "receipt.json")
            run = subprocess.run(["bash", str(RUNNER), "--prompt", "test", *args],
                                 env=env, capture_output=True, text=True, timeout=10)
            if receipt_file:
                receipt = json.loads(Path(env["GROK_RUN_RECEIPT"]).read_text())
                self.assertEqual(run.stderr, "")
            else:
                receipt = json.loads(run.stderr)
            return run, receipt

    def test_good_run_joins_text_and_reads_final_model_usage(self):
        run, receipt = self.run_fixture("good.ndjson")
        self.assertEqual(run.returncode, 0)
        self.assertEqual(run.stdout, "Hello world\n")
        self.assertEqual({key: receipt[key] for key in ("requested_model", "actual_models",
            "stopReason", "num_turns", "cost_usd", "cli_version")}, {
            "requested_model": "grok-4.7", "actual_models": ["grok-4.7-build"],
            "stopReason": "end_turn", "num_turns": 2, "cost_usd": 0.012,
            "cli_version": "fixture",
        })

    def test_cancelled_run_refuses_completion(self):
        run, receipt = self.run_fixture("cancelled.ndjson")
        self.assertEqual(run.returncode, 4)
        self.assertEqual(receipt["stopReason"], "cancelled")

    def test_accounting_after_completed_response_preserves_runner_and_desk_answer(self):
        run, receipt = self.run_fixture("accounting-after-response.ndjson")
        self.assertEqual(run.returncode, 0)
        self.assertEqual(run.stdout, "Final answer\n")
        self.assertEqual(receipt["stopReason"], "end_turn")
        self.assertEqual(receipt["actual_models"], ["grok-4.7-build"])
        desk = grok_wire.run_task(
            {"model": "grok-4.7", "effort": "high", "sandbox": "read-only"}, "test",
            run=lambda argv, **kw: subprocess.CompletedProcess(
                argv, 0, (FIXTURES / "accounting-after-response.ndjson").read_text(), ""))
        self.assertEqual(desk["status"], "completed")
        self.assertEqual(desk["result"], "Final answer")

    def test_wrong_model_refuses_substitution(self):
        run, receipt = self.run_fixture("wrong-model.ndjson")
        self.assertEqual(run.returncode, 5)
        self.assertEqual(receipt["actual_models"], ["grok-4.6"])

    def test_mixed_model_usage_refuses_substitution(self):
        run, receipt = self.run_fixture("mixed-model.ndjson")
        self.assertEqual(run.returncode, 5)
        self.assertEqual(receipt["actual_models"], ["grok-4.6", "grok-4.7-build"])

    def test_lookalike_model_prefix_refuses_substitution(self):
        run, receipt = self.run_fixture("lookalike-model.ndjson")
        self.assertEqual(run.returncode, 5)
        self.assertEqual(receipt["actual_models"], ["grok-4.70"])

    def test_empty_text_has_no_narration_and_can_write_receipt_file(self):
        run, _ = self.run_fixture("empty-text.ndjson", receipt_file=True)
        self.assertEqual(run.returncode, 0)
        self.assertEqual(run.stdout, "")

    def test_recorded_live_cli_cost_and_text(self):
        run, receipt = self.run_fixture("live-ok.ndjson")
        self.assertEqual(run.returncode, 0)
        self.assertEqual(run.stdout, "\n\n".join(["OK"] * 9) + "\n")
        self.assertEqual(receipt["actual_models"], ["grok-4.7-build"])
        self.assertEqual(receipt["num_turns"], 9)
        self.assertEqual(receipt["cost_usd"], 0.04628488)

    def test_truncated_stream_is_incomplete_with_receipt(self):
        run, receipt = self.run_fixture("truncated.ndjson")
        self.assertEqual(run.returncode, 4)
        self.assertIsNone(receipt["stopReason"])
        self.assertEqual(receipt["actual_models"], [])

    def test_malformed_stream_cannot_claim_success(self):
        run, receipt = self.run_fixture("malformed.ndjson")
        self.assertEqual(run.returncode, 4)
        self.assertEqual(receipt["stopReason"], "invalid_stream")

    def test_text_after_end_cannot_certify_completion(self):
        run, receipt = self.run_fixture("end-then-text.ndjson")
        self.assertEqual(run.returncode, 4)
        self.assertEqual(receipt["stopReason"], "invalid_stream")
        self.assertEqual(run.stdout, "answer\n")

    def test_error_after_end_cannot_certify_completion(self):
        run, receipt = self.run_fixture("end-then-error.ndjson")
        self.assertEqual(run.returncode, 4)
        self.assertEqual(receipt["stopReason"], "invalid_stream")
        self.assertNotIn("private diagnostic", run.stderr)

    def test_duplicate_ends_cannot_certify_completion(self):
        run, receipt = self.run_fixture("duplicate-end.ndjson")
        self.assertEqual(run.returncode, 4)
        self.assertEqual(receipt["stopReason"], "invalid_stream")

    def test_runner_and_desk_reject_the_same_review_fixtures(self):
        for fixture in ("mixed-model.ndjson", "lookalike-model.ndjson", "end-then-text.ndjson",
                        "end-then-error.ndjson", "duplicate-end.ndjson"):
            with self.subTest(fixture=fixture):
                shell, _ = self.run_fixture(fixture)
                raw = (FIXTURES / fixture).read_text()
                desk = grok_wire.run_task(
                    {"model": "grok-4.7", "effort": "high", "sandbox": "read-only"}, "test",
                    run=lambda argv, **kw: subprocess.CompletedProcess(argv, 0, raw, ""))
                self.assertNotEqual(shell.returncode, 0)
                self.assertEqual(desk["status"], "failed")

    def run_cli(self, *args, installed="1.0.9", latest="1.0.10", auth=True,
                registry=True, upgrade=True, cli_exit=0):
        with tempfile.TemporaryDirectory(prefix="grok-cli-test-") as directory:
            scratch = Path(directory)
            grok = scratch / "grok"
            grok.write_text(textwrap.dedent('''\
                #!/usr/bin/env python3
                import json, os, pathlib, sys
                root = pathlib.Path(__file__).parent
                args = sys.argv[1:]
                with (root / "calls.jsonl").open("a") as log:
                    log.write(json.dumps(["grok", *args]) + "\\n")
                if args == ["--version"]:
                    print("grok " + (os.environ["LATEST"] if (root / "upgraded").exists() else os.environ["INSTALLED"]))
                elif args == ["models"]:
                    print("grok-4.7" if os.environ["AUTH"] == "yes" else "Not authenticated. Run grok login")
                    sys.exit(0 if os.environ["AUTH"] == "yes" else 1)
                else:
                    print(pathlib.Path(os.environ["FIXTURE"]).read_text(), end="")
                    sys.exit(int(os.environ["CLI_EXIT"]))
                '''))
            npm = scratch / "npm"
            npm.write_text(textwrap.dedent('''\
                #!/usr/bin/env python3
                import json, os, pathlib, sys
                root = pathlib.Path(__file__).parent
                args = sys.argv[1:]
                with (root / "calls.jsonl").open("a") as log:
                    log.write(json.dumps(["npm", *args]) + "\\n")
                if args[0] == "view":
                    print(os.environ["LATEST"])
                    sys.exit(0 if os.environ["REGISTRY"] == "yes" else 1)
                if os.environ["UPGRADE"] == "yes":
                    (root / "upgraded").touch()
                sys.exit(0 if os.environ["UPGRADE"] == "yes" else 1)
                '''))
            grok.chmod(0o755)
            npm.chmod(0o755)
            prompt_file = scratch / "prompt.txt"
            prompt_file.write_text("file prompt\nwith two lines")
            env = dict(os.environ, PATH=f"{scratch}:{os.environ['PATH']}",
                       INSTALLED=installed, LATEST=latest, AUTH="yes" if auth else "no",
                       REGISTRY="yes" if registry else "no", UPGRADE="yes" if upgrade else "no",
                       FIXTURE=str(FIXTURES / "good.ndjson"), CLI_EXIT=str(cli_exit))
            for name in ("GROK_RUN_FAKE_NDJSON", "GROK_RUN_RECEIPT"):
                env.pop(name, None)
            selected = [str(prompt_file) if arg == "PROMPT_FILE" else arg for arg in args]
            launch = ["bash", str(RUNNER), *selected]
            if not auth:
                # The fake provider's auth refusal exercises main's contract;
                # transports are tested separately, never against the live store.
                harness = "import sys; sys.path.insert(0, sys.argv.pop(1)); import grok_run; grok_run.sign_in_alert=lambda:None; sys.exit(grok_run.main())"
                launch = [sys.executable, "-c", harness, str(ROOT / "bin"), *selected]
            run = subprocess.run(launch, env=env,
                                 capture_output=True, text=True, timeout=10)
            calls_file = scratch / "calls.jsonl"
            calls = [json.loads(line) for line in calls_file.read_text().splitlines()] if calls_file.exists() else []
            return run, calls

    def test_live_path_upgrades_and_passes_safe_defaults(self):
        run, calls = self.run_cli("--prompt", "literal $HOME `x`")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout, "Hello world\n")
        self.assertIn(["npm", "install", "-g", "@xai-official/grok@1.0.10"], calls)
        self.assertEqual(calls.count(["grok", "--version"]), 2)
        self.assertEqual(calls[-1], ["grok", "--model", "grok-4.7", "--reasoning-effort", "high",
                                   "--max-turns", "60", "--always-approve", "--sandbox", "read-only",
                                   "--output-format", "streaming-json", "--print",
                                   "Do not call any CARR or record-layer tool; do not write anything unless asked.\n\nliteral $HOME `x`"])
        self.assertEqual(json.loads(run.stderr)["cli_version"], "1.0.10")

    def test_runner_and_desk_use_the_same_default_invocation(self):
        shell, calls = self.run_cli("--prompt", "test", installed="1.0.10")
        self.assertEqual(shell.returncode, 0, shell.stderr)
        desk_calls = []

        def provider(argv, **kwargs):
            desk_calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, (FIXTURES / "live-ok.ndjson").read_text(), "")

        desk = grok_wire.run_task(
            {"model": "grok-4.7", "effort": "high", "sandbox": "read-only"}, "test", run=provider)
        self.assertEqual(desk["status"], "completed")
        self.assertEqual(len(desk_calls), 1)
        self.assertEqual(calls[-1][:-1], desk_calls[0][0][:-1])
        self.assertTrue(calls[-1][-1].endswith("\n\ntest"))
        self.assertTrue(desk_calls[0][0][-1].endswith("\n\ntest"))

    def test_writable_prompt_file_and_options(self):
        run, calls = self.run_cli("--writable", "--effort", "low", "--max-turns", "3",
                                   "--prompt-file", "PROMPT_FILE", installed="1.0.10")
        self.assertEqual(run.returncode, 0, run.stderr)
        invocation = calls[-1]
        self.assertEqual(invocation[invocation.index("--sandbox") + 1], "workspace")
        self.assertEqual(invocation[invocation.index("--reasoning-effort") + 1], "low")
        self.assertEqual(invocation[invocation.index("--max-turns") + 1], "3")
        self.assertTrue(invocation[-1].endswith("file prompt\nwith two lines"))
        self.assertFalse(any(call[:2] == ["npm", "install"] for call in calls))

    def test_authentication_failure_is_exit_three_and_never_logs_in(self):
        run, calls = self.run_cli("--prompt", "test", installed="1.0.10", auth=False)
        self.assertEqual(run.returncode, 3)
        self.assertEqual(run.stdout, "")
        self.assertEqual(run.stderr, "Grok needs sign-in: run grok login\n")
        self.assertEqual(calls[-1], ["grok", "models"])
        self.assertNotIn(["grok", "login"], calls)

    def test_registry_unreachable_warns_and_continues(self):
        run, calls = self.run_cli("--prompt", "test", registry=False)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("registry unreachable", run.stderr)
        self.assertEqual(json.loads(run.stderr.splitlines()[-1])["cli_version"], "1.0.9")
        self.assertFalse(any(call[:2] == ["npm", "install"] for call in calls))

    def test_failed_upgrade_refuses_to_run(self):
        run, calls = self.run_cli("--prompt", "test", upgrade=False)
        self.assertNotEqual(run.returncode, 0)
        self.assertFalse(any("--print" in call for call in calls))

    def test_cli_failure_cannot_be_masked_by_success_end(self):
        run, _ = self.run_cli("--prompt", "test", installed="1.0.10", cli_exit=7)
        self.assertNotEqual(run.returncode, 0)

    def test_bad_options_fail_before_preflight(self):
        for args in (("--effort", "extreme", "--prompt", "x"),
                     ("--max-turns", "0", "--prompt", "x"),
                     ("--prompt", "x", "--prompt-file", "PROMPT_FILE")):
            run, calls = self.run_cli(*args)
            self.assertEqual(run.returncode, 2)
            self.assertEqual(calls, [])

    def test_invalid_timeouts_fail_before_preflight(self):
        for value in ("0", "-1", "1801", "nan", "inf", "1.5"):
            with self.subTest(timeout=value):
                run, calls = self.run_cli("--prompt", "test", "--timeout-seconds", value)
                self.assertEqual(run.returncode, 2)
                self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
