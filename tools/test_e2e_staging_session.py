import importlib.util
import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("e2e_staging_session", REPO / "tools/e2e-staging-session.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
SHA = "a" * 40
TOKEN = "synthetic-provider-token"
SECRET = "synthetic-e2e-session-secret-for-tests-only"


def fake_checkout(root: Path, *, wrangler: bool = True) -> Path:
    (root / "mcp-server").mkdir(parents=True)
    (root / "mcp-server/wrangler.toml").write_text((REPO / "mcp-server/wrangler.toml").read_text())
    if wrangler:
        binary = root / "mcp-server/node_modules/.bin/wrangler"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
    return root


class StagingSessionTests(unittest.TestCase):
    def test_timeout_stops_children_before_refusal_and_suppresses_output(self):
        for detached in (False, True):
            with self.subTest(detached=detached), tempfile.TemporaryDirectory() as directory:
                marker = Path(directory) / "late-effect"
                ready = Path(directory) / "ready"
                child = (
                    "import pathlib, signal, time; "
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                    f"pathlib.Path({str(ready)!r}).write_text('ready'); "
                    "time.sleep(0.8); "
                    f"pathlib.Path({str(marker)!r}).write_text('synthetic')"
                )
                launcher = (
                    "import subprocess, sys, time; "
                    f"p = subprocess.Popen([sys.executable, '-c', {child!r}], "
                    f"start_new_session={detached!r}); "
                    f"print({SECRET!r}, flush=True); "
                    f"print({TOKEN!r}, file=sys.stderr, flush=True); "
                    "p.wait()"
                )

                def short_run(args, **kwargs):
                    if str(REPO / "bin/with-timeout.py") in args:
                        args = [*args[:2], "0.2", *args[3:]]
                        kwargs["timeout"] = 3
                    else:
                        kwargs["timeout"] = 0.2
                    return subprocess.run(args, **kwargs)

                output, errors = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                    with self.assertRaises(MODULE.StagingRefusal) as caught:
                        MODULE.checked_run([sys.executable, "-c", launcher], run=short_run)
                self.assertEqual(ready.read_text(), "ready")
                self.assertNotIn(SECRET, str(caught.exception) + output.getvalue() + errors.getvalue())
                self.assertNotIn(TOKEN, str(caught.exception) + output.getvalue() + errors.getvalue())
                time.sleep(1)
                self.assertFalse(marker.exists(), "child performed an effect after timeout refusal")

    def test_checked_run_preserves_output_input_and_exit_failure(self):
        command = [sys.executable, "-c", "import sys; print(sys.stdin.read().upper(), end='')"]
        self.assertEqual(MODULE.checked_run(command, input="synthetic\n"), "SYNTHETIC\n")
        with self.assertRaisesRegex(MODULE.StagingRefusal, "command failed; output suppressed"):
            MODULE.checked_run([sys.executable, "-c", "raise SystemExit(7)"])

    def test_apply_requires_the_exact_carr_source_before_loading_credentials(self):
        with patch.object(MODULE, "source_identity", return_value=SHA):
            for arguments in [["--apply"], ["--apply", "--source-sha", "b" * 40]]:
                with self.assertRaises(MODULE.StagingRefusal):
                    MODULE.main(arguments)

    def test_app_candidate_builds_and_publishes_without_reading_a_session_secret(self):
        app_sha = "b" * 40
        app_repo = Path("/synthetic/doctorcre-app")
        calls = []

        def run(args, **kwargs):
            calls.append((args, kwargs))
            return "https://github.com/jbookout/doctorcre-app.git\n" if args[:3] == ["git", "remote", "get-url"] else ""

        with patch.object(MODULE, "source_identity", side_effect=[SHA, app_sha]), \
                patch.object(MODULE, "checked_run", side_effect=run), \
                patch.object(Path, "lstat", side_effect=AssertionError("deploy must not inspect a session secret file")), \
                patch.object(MODULE, "load_carr_tokens", return_value={"CLOUDFLARE_API_TOKEN": TOKEN}), \
                patch.object(MODULE, "deploy_candidate"), patch.object(MODULE, "smoke"), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(MODULE.main(["--apply", "--source-sha", SHA,
                                         "--app-repo", str(app_repo), "--app-sha", app_sha]), 0)
        build, publish = calls[-2:]
        self.assertEqual(build[0], ["npm", "run", "build"])
        self.assertNotIn("CLOUDFLARE_API_TOKEN", build[1]["env"])
        self.assertEqual(publish[0], ["node", "scripts/release-staging.mjs", "--source-sha", app_sha])
        self.assertEqual(publish[1]["env"]["CLOUDFLARE_API_TOKEN"], TOKEN)
        self.assertEqual(build[1]["cwd"], app_repo)
        self.assertEqual(publish[1]["cwd"], app_repo)

    def test_config_refuses_production_and_shared_kv(self):
        source = (REPO / "mcp-server/wrangler.toml").read_text()
        MODULE.validate_config(source)
        for changed in [
            source.replace('name = "carr-mcp-staging"', 'name = "carr-mcp"'),
            source.replace('routes = []', 'routes = [{pattern = "app.doctorcre.com", custom_domain = true}]'),
            source.replace('CARR_ENV = "staging"', 'CARR_ENV = "production"'),
            source.replace('id = "faa513fd07ec45d88abc5949f323aec5"', 'id = "b033d3b5c454445080e9d0ab51ff8f3c"'),
        ]:
            with self.subTest(changed=changed[-250:]):
                with self.assertRaises(MODULE.StagingRefusal):
                    MODULE.validate_config(changed)

    def test_provider_operations_are_fixed_staging_and_do_not_print_secrets(self):
        calls = []
        def run(args, **kwargs):
            calls.append((list(map(str, args[3:])), kwargs))
            return subprocess.CompletedProcess(args, 0, SECRET, TOKEN)
        child_env = MODULE.provider_environment({"PATH": "/bin", "DATABASE_URL_WRITER": "forbidden", "CLOUDFLARE_API_TOKEN": "wrong"}, TOKEN)
        self.assertEqual(child_env["CLOUDFLARE_API_TOKEN"], TOKEN)
        self.assertNotIn("DATABASE_URL_WRITER", child_env)
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(output):
            MODULE.deploy_candidate(fake_checkout(Path(directory) / "repo"), SHA, child_env, run=run)
        self.assertNotIn(TOKEN, output.getvalue())
        self.assertNotIn(SECRET, output.getvalue())
        wrangler = [call for call in calls if "wrangler" in Path(call[0][0]).name]
        self.assertEqual(len(wrangler), 1)
        self.assertEqual(wrangler[0][0][1:], ["deploy", "--env", "staging", "--var", "GIT_SHA:" + SHA])
        github = [call for call in calls if call[0][0] == "gh"]
        self.assertEqual(github, [])
        for args, kwargs in calls:
            self.assertNotIn("secret", args)
            self.assertNotIn("input", kwargs)
        for args, kwargs in wrangler:
            self.assertEqual(args[args.index("--env") + 1], "staging")
            self.assertNotIn(SECRET, " ".join(args))
            self.assertTrue(kwargs["capture_output"])
            self.assertEqual(kwargs["env"]["CI"], "true")

    def test_failed_provider_output_is_suppressed(self):
        def run(args, **kwargs):
            command = list(map(str, args[3:]))
            failed = "wrangler" in Path(command[0]).name
            return subprocess.CompletedProcess(args, int(failed), SECRET, TOKEN)
        with tempfile.TemporaryDirectory() as directory, \
                self.assertRaises(MODULE.StagingRefusal) as caught:
            MODULE.deploy_candidate(fake_checkout(Path(directory) / "repo"), SHA,
                                    MODULE.provider_environment({}, TOKEN), run=run)
        self.assertNotIn(TOKEN, str(caught.exception))
        self.assertNotIn(SECRET, str(caught.exception))

    def test_missing_wrangler_is_refused_with_a_specific_message_before_any_command(self):
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, "", "")
        with tempfile.TemporaryDirectory() as directory:
            repo = fake_checkout(Path(directory) / "repo", wrangler=False)
            with self.assertRaises(MODULE.StagingRefusal) as caught:
                MODULE.deploy_candidate(repo, SHA, MODULE.provider_environment({}, TOKEN), run=run)
            message = str(caught.exception)
            self.assertIn(str(repo / "mcp-server/node_modules/.bin/wrangler"), message)
            self.assertIn("run `npm ci` in " + str(repo / "mcp-server"), message)
            self.assertNotIn(TOKEN, message)
            self.assertNotIn(SECRET, message)
        self.assertEqual(calls, [])

    def test_source_requires_a_clean_exact_committed_candidate(self):
        def run(args, **kwargs):
            output = SHA + "\n" if "rev-parse" in args else " M mcp-server/src/dealroom-web.js\n"
            return subprocess.CompletedProcess(args, 0, output, "")
        with self.assertRaises(MODULE.StagingRefusal):
            MODULE.source_identity(REPO, run=run)

    def test_smoke_reads_only_the_release_and_requires_the_exact_staging_source(self):
        calls = []
        def fetch(path, **kwargs):
            calls.append((path, kwargs))
            return 200, {}, {"env": {"value": "staging"}, "git_sha": {"value": SHA}}
        MODULE.smoke(SHA, fetch=fetch)
        self.assertEqual(calls, [("/release", {})])
        for status, environment, sha in [(401, "staging", SHA), (200, "production", SHA),
                                       (200, "staging", "b" * 40)]:
            with self.subTest(status=status, environment=environment, sha=sha):
                with self.assertRaises(MODULE.StagingRefusal):
                    MODULE.smoke(SHA, fetch=lambda path: (
                        status, {}, {"env": {"value": environment}, "git_sha": {"value": sha}}))

    def test_staging_fetch_identifies_the_browser_compatible_qa_client(self):
        calls = []
        class Response(io.BytesIO):
            status = 200
            headers = {"content-type": "application/json"}
        class Opener:
            def open(self, request, timeout):
                calls.append(request)
                if not request.get_header("User-agent", "").startswith("Mozilla/5.0"):
                    raise MODULE.urllib.error.HTTPError(request.full_url, 403, "edge browser integrity", {}, None)
                return Response(b'{"ok":true}')
        with patch.object(MODULE.urllib.request, "build_opener", return_value=Opener()):
            status, _, body = MODULE.staging_fetch("/release")
        self.assertEqual((status, body), (200, {"ok": True}))
        self.assertEqual(calls[0].full_url, "https://" + MODULE.CARR_HOST + "/release")
        self.assertIn("DoctorCRE-Staging-E2E", calls[0].get_header("User-agent"))
        self.assertIsNone(calls[0].get_header("Authorization"))


if __name__ == "__main__":
    unittest.main()
